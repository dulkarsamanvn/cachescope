"""Regressions for edge cases found during the project review."""

import functools
import os
import sys
import time

import pytest

from cachescope.tracer import Tracer, cache_advice, format_row
from cachescope.verdict import judge

HERE = os.path.dirname(os.path.abspath(__file__))


def observe(function, arguments):
    tracer = Tracer(root=HERE)
    tracer.start()
    try:
        for args in arguments:
            function(*args)
    finally:
        tracer.stop()
    return next(record for key, record in tracer.records.items() if key[1] == function.__name__)


def type_sensitive(value, unused):
    time.sleep(0.002)
    return type(value).__name__


def nested_type_sensitive(value):
    time.sleep(0.002)
    return type(value[0]).__name__


def test_numeric_key_collisions_do_not_get_cache_recommendations():
    record = observe(type_sensitive, [(1, 0), (1.0, 0)] * 4)
    assert judge(record)[0] == "MAYBE"
    assert "lru_cache(maxsize" not in cache_advice(record)
    cached = functools.lru_cache(2)(type_sensitive)
    assert cached(1, 0) == cached(1.0, 0) == "int"


def test_nested_numeric_key_collisions_are_also_detected():
    record = observe(nested_type_sensitive, [((1,),), ((1.0,),)] * 4)
    assert record["cache_conflict"]
    assert judge(record)[0] == "MAYBE"
    assert "lru_cache(maxsize" not in cache_advice(record)


def opaque_result(value):
    time.sleep(0.002)
    return object()


def list_result(value):
    time.sleep(0.002)
    return [value]


def nested_list_result(value):
    time.sleep(0.002)
    return (value, [value])


def test_unknown_outputs_are_not_claimed_to_be_stable():
    record = observe(opaque_result, [(1,)] * 8)
    assert record["unknown_results"] == 8
    assert judge(record)[0] == "MAYBE"
    assert "lru_cache(maxsize" not in cache_advice(record)


def test_mutable_outputs_are_not_recommended_for_sharing():
    for function in (list_result, nested_list_result):
        record = observe(function, [(1,)] * 8)
        assert record["mutable_results"] == 8
        assert judge(record)[0] == "MAYBE"
        assert "lru_cache(maxsize" not in cache_advice(record)


def test_fingerprint_failure_becomes_unknown(monkeypatch):
    import cachescope.fingerprint as module

    def changing_container(value, depth, budget):
        raise RuntimeError("dictionary changed size during iteration")

    monkeypatch.setattr(module, "_fingerprint", changing_container)
    assert module.fingerprint({"a": 1}) is module.UNKNOWN


def test_hook_failure_does_not_escape_or_disable_profiling(capsys):
    tracer = Tracer(root=HERE)
    original = tracer._on_call

    def broken_observation(frame):
        if frame.f_code is type_sensitive.__code__:
            raise RuntimeError("observer failed")
        original(frame)

    tracer._on_call = broken_observation
    tracer.start()
    try:
        assert type_sensitive(1, 0) == "int"
        assert sys.getprofile() is not None
    finally:
        tracer.stop()
    assert tracer.observation_errors == 1
    tracer.report()
    assert "observation(s) failed" in capsys.readouterr().out


def raising(value):
    time.sleep(0.002)
    raise ValueError(value)


def returns_none(value):
    time.sleep(0.002)
    return None


def test_exceptional_exits_are_not_successes_or_potential_hits():
    tracer = Tracer(root=HERE)
    tracer.start()
    try:
        for _ in range(8):
            try:
                raising(1)
            except ValueError:
                pass
    finally:
        tracer.stop()
    record = next(record for key, record in tracer.records.items() if key[1] == "raising")
    assert record["failed"] == 8
    assert record["successful"] == 0
    assert record["inputs"] == record["results"] == {}
    assert record["sequence"] == []
    assert judge(record)[0] == "SKIP"
    assert "lru_cache(maxsize" not in cache_advice(record)


def test_a_real_none_return_is_still_a_success():
    record = observe(returns_none, [(1,)] * 8)
    assert record["successful"] == 8
    assert record["failed"] == 0
    assert judge(record)[0] == "CANDIDATE"


def test_return_observation_failure_does_not_corrupt_active_counts(monkeypatch):
    import cachescope.tracer as module

    original = module.exit_instruction
    failed = []

    def unreliable(frame):
        if frame.f_code is returns_none.__code__ and not failed:
            failed.append(True)
            raise RuntimeError("return observation failed")
        return original(frame)

    monkeypatch.setattr(module, "exit_instruction", unreliable)
    with Tracer(root=HERE) as tracer:
        assert returns_none(1) is None
        assert returns_none(1) is None
        assert sys.getprofile() is tracer._installed_hook
    record = next(record for key, record in tracer.records.items() if key[1] == "returns_none")
    assert record["calls"] == 2
    assert record["successful"] == 1
    assert record["active"] == 0
    assert not record["recursive"]
    assert tracer.observation_errors == 1


def defaulted(value=1):
    time.sleep(0.002)
    return value


def test_normalized_size_model_is_not_claimed_as_actual_cache_hits():
    tracer = Tracer(root=HERE)
    tracer.start()
    try:
        for _ in range(5):
            defaulted()
            defaulted(1)
            defaulted(value=1)
    finally:
        tracer.stop()
    record = next(record for key, record in tracer.records.items() if key[1] == "defaulted")
    advice = cache_advice(record)
    assert "normalized inputs" in advice
    assert "assumes consistent call spelling" in advice
    assert "verify actual hits" in advice
    assert "@lru_cache(maxsize" not in advice
    cached = functools.lru_cache(1)(defaulted)
    for _ in range(5):
        cached()
        cached(1)
        cached(value=1)
    assert cached.cache_info().hits == 0


def keyword_values(**options):
    return len(options)


def test_kwargs_container_does_not_make_hashable_values_unhashable():
    tracer = Tracer(root=HERE)
    tracer.start()
    try:
        keyword_values()
        keyword_values(a=1)
        keyword_values(a=1)
    finally:
        tracer.stop()
    record = next(record for key, record in tracer.records.items() if key[1] == "keyword_values")
    assert record["unhashable"] == 0
    assert "TypeError" not in cache_advice(record)


def test_unhashable_keyword_values_are_still_recognized():
    tracer = Tracer(root=HERE)
    tracer.start()
    try:
        keyword_values(a=[1])
    finally:
        tracer.stop()
    record = next(record for key, record in tracer.records.items() if key[1] == "keyword_values")
    assert record["unhashable"] == 1


def keyword_order(**options):
    return tuple(options)


def test_different_keyword_orders_are_not_a_false_unstable_result():
    tracer = Tracer(root=HERE)
    tracer.start()
    try:
        assert keyword_order(a=1, b=2) == ("a", "b")
        assert keyword_order(b=2, a=1) == ("b", "a")
    finally:
        tracer.stop()
    record = next(record for key, record in tracer.records.items() if key[1] == "keyword_order")
    assert len(record["inputs"]) == 2
    assert record["varied"] is False
    assert record["cache_conflict"] is False


def test_parent_time_excludes_child_observer_work(monkeypatch):
    import cachescope.tracer as module

    clock = [0.0]

    def child():
        clock[0] += 2.0

    def parent():
        child()
        clock[0] += 1.0

    tracer = Tracer(root=HERE)
    original = tracer._on_call

    def costly_observer(frame):
        original(frame)
        clock[0] += 10.0

    monkeypatch.setattr(module.time, "perf_counter", lambda: clock[0])
    tracer._on_call = costly_observer
    tracer.start()
    try:
        parent()
    finally:
        tracer.stop()
    records = {key[1]: record for key, record in tracer.records.items()}
    assert records["parent"]["time"] == 3.0
    assert records["child"]["time"] == 2.0


def recursive(value):
    time.sleep(0.002)
    return recursive(value - 1) if value else 0


def test_recursive_time_does_not_double_count_elapsed_or_claim_savings():
    started = time.perf_counter()
    record = observe(recursive, [(3,)] * 2)
    wall = time.perf_counter() - started
    assert record["recursive"]
    assert 0 < record["time"] <= wall
    assert judge(record)[0] == "MAYBE"
    assert "size estimate" not in cache_advice(record)
    wasted, text = format_row(__file__, "recursive", recursive.__code__.co_firstlineno, record)
    assert wasted == 0
    assert "savings are not estimated" in text


def skewed(value):
    if value == 0:
        time.sleep(0.05)
    return value


def test_expensive_unique_input_is_not_counted_as_repeated_work():
    from cachescope.verdict import summarize

    record = observe(skewed, [(0,)] + [(1,)] * 20)
    summary = summarize(record)
    assert summary.repeats == 19
    assert record["time"] >= 0.04
    assert summary.wasted < record["time"] / 10
    assert judge(record)[0] == "SKIP"
    wasted, text = format_row(__file__, "skewed", skewed.__code__.co_firstlineno, record)
    assert wasted == summary.wasted
    assert "repeat work" in text


def large_key(value):
    return len(value)


def test_repeated_large_keys_use_compact_sequence_ids():
    record = observe(large_key, [(tuple(range(500)),)] * 100)
    assert len(record["inputs"]) == len(record["input_ids"]) == 1
    assert len(record["cache_keys"]) == len(record["results"]) == 1
    assert record["sequence"] == [0] * 100
    assert all(type(value) is int for value in record["sequence"])
    assert record["retained_bytes"] < 200_000


def test_distinct_inputs_are_bounded_and_marked_incomplete(monkeypatch):
    import cachescope.tracer as module

    monkeypatch.setattr(module, "MAX_INPUTS", 2)
    record = observe(type_sensitive, [(n, 0) for n in range(20)])
    assert len(record["inputs"]) == len(record["results"]) == 2
    assert len(record["cache_keys"]) == 2
    assert record["unknown"] == 18
    assert record["limited"]
    assert judge(record)[0] == "MAYBE"
    assert "size estimate" not in cache_advice(record)


def test_value_traversal_has_width_and_byte_budgets():
    from cachescope.fingerprint import MAX_ITEMS, MAX_VALUE_BYTES, UNKNOWN, fingerprint

    assert fingerprint(list(range(MAX_ITEMS + 1))) is UNKNOWN
    assert fingerprint("x" * MAX_VALUE_BYTES) is UNKNOWN
    assert fingerprint(1 << (MAX_VALUE_BYTES * 8)) is UNKNOWN
    assert fingerprint(b"immutable bytes") == ("bytes", b"immutable bytes")


def test_total_retention_budget_is_enforced(monkeypatch):
    import cachescope.tracer as module

    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 2048)
    record = observe(large_key, [(tuple(range(100)),)] * 3)
    assert record["limited"]
    assert record["retained_bytes"] <= 2048
    assert record["inputs"] == {}


def test_sequence_prefix_is_not_presented_as_a_whole_run_prediction(monkeypatch):
    import cachescope.tracer as module

    monkeypatch.setattr(module, "MAX_SEQUENCE", 10)
    record = observe(type_sensitive, [(n, 0) for n in [0] * 10 + list(range(1, 91))])
    assert record["successful"] == 100
    assert len(record["sequence"]) == 10
    assert record["sequence_truncated"]
    advice = cache_advice(record)
    assert "truncated after 10 calls" in advice
    assert "modeled hits" not in advice
    _, report = format_row(__file__, "type_sensitive", type_sensitive.__code__.co_firstlineno, record)
    assert "truncated after 10 calls" in report
    assert "modeled hits" not in report


def make_worker(factor):
    def worker(value):
        return value * factor

    return worker


def test_distinct_closures_are_not_misreported_as_unstable():
    a, b = make_worker(2), make_worker(3)
    tracer = Tracer(root=HERE)
    tracer.start()
    try:
        assert a(1) == 2
        assert b(1) == 3
    finally:
        tracer.stop()
    record = next(record for key, record in tracer.records.items() if key[1] == "worker")
    assert len(record["inputs"]) == 2
    assert record["varied"] is False
    assert judge(record)[0] == "MAYBE"
    assert "size estimate" not in cache_advice(record)


def make_stateless_worker():
    def worker(value):
        return value * 2

    return worker


def test_factory_functions_without_free_variables_do_not_assume_one_shared_cache():
    first, second = make_stateless_worker(), make_stateless_worker()
    assert not first.__code__.co_freevars
    tracer = Tracer(root=HERE)
    with tracer:
        first(1)
        second(1)
    record = next(record for key, record in tracer.records.items() if key[1] == "worker")
    assert record["contextual"]
    assert judge(record)[0] == "MAYBE"
    assert "size estimate" not in cache_advice(record)


def changing_clock(value):
    time.sleep(0.002)
    return time.perf_counter()


def test_observed_result_changes_without_closure_ambiguity_are_unsafe():
    record = observe(changing_clock, [(1,)] * 2)
    assert record["varied"]
    assert judge(record)[0] == "UNSAFE"


def test_previous_python_profile_is_restored_even_after_an_exception():
    def previous(frame, event, arg):
        pass

    saved = sys.getprofile()
    sys.setprofile(previous)
    try:
        with pytest.raises(ValueError):
            with Tracer(root=HERE):
                raise ValueError("target failed")
        assert sys.getprofile() is previous
        Tracer(root=HERE).stop()
        assert sys.getprofile() is previous
    finally:
        sys.setprofile(saved)


def test_nested_tracers_are_rejected_without_disabling_the_owner():
    with Tracer(root=HERE) as owner:
        with pytest.raises(RuntimeError, match="another Tracer"):
            Tracer(root=HERE).start()
        assert sys.getprofile() is owner._installed_hook
        assert owner.start() is owner


def test_same_tracer_context_cannot_stop_an_outer_context_prematurely():
    tracer = Tracer(root=HERE)
    with tracer:
        with pytest.raises(RuntimeError, match="cannot be nested"):
            with tracer:
                pass
        assert tracer._running
        assert sys.getprofile() is tracer._installed_hook


def test_restart_does_not_claim_ownership_of_a_replaced_hook():
    def replacement(frame, event, arg):
        pass

    saved = sys.getprofile()
    tracer = Tracer(root=HERE).start()
    try:
        sys.setprofile(replacement)
        with pytest.raises(RuntimeError, match="hook was replaced"):
            tracer.start()
        assert sys.getprofile() is replacement
    finally:
        tracer.stop()
        sys.setprofile(saved)


def test_native_profiler_is_left_intact():
    import cProfile

    profiler = cProfile.Profile()
    tracer = Tracer(root=HERE)
    profiler.enable()
    try:
        previous = sys.getprofile()
        if previous is None:
            # Newer CPython cProfile uses independent sys.monitoring callbacks.
            tool = sys.monitoring.get_tool(sys.monitoring.PROFILER_ID)
            with tracer:
                assert sys.monitoring.get_tool(sys.monitoring.PROFILER_ID) == tool
        else:
            with pytest.raises(RuntimeError, match="native profiler"):
                tracer.start()
        assert sys.getprofile() is previous
    finally:
        tracer.stop()
        profiler.disable()


def test_stop_preserves_a_hook_installed_by_somebody_else():
    def replacement(frame, event, arg):
        pass

    saved = sys.getprofile()
    tracer = Tracer(root=HERE).start()
    try:
        sys.setprofile(replacement)
        tracer.stop()
        assert sys.getprofile() is replacement
        assert tracer.observation_errors == 1
        tracer.stop()
        assert sys.getprofile() is replacement
    finally:
        sys.setprofile(saved)


def stop_early(tracer):
    tracer.stop()


def test_calls_open_at_stop_are_explicitly_incomplete():
    tracer = Tracer(root=HERE).start()
    stop_early(tracer)
    record = next(record for key, record in tracer.records.items() if key[1] == "stop_early")
    assert record["incomplete"] == 1
    assert record["active"] == 0
    assert record["successful"] == 0
    assert judge(record)[0] == "MAYBE"


def test_filesystem_root_includes_descendant_code():
    from pathlib import Path

    tracer = Tracer(root=Path(__file__).anchor)
    assert tracer._is_user_code(type_sensitive.__code__)


def test_root_containment_and_package_component_matching(tmp_path):
    def code_at(path):
        namespace = {}
        exec(compile("def f():\n    return 1\n", str(path), "exec"), namespace)
        return namespace["f"].__code__

    root = tmp_path / "app"
    tracer = Tracer(root=root)
    assert tracer._is_user_code(code_at(root / "my-site-packages-app" / "code.py"))
    assert not tracer._is_user_code(code_at(root / "site-packages" / "code.py"))
    assert not tracer._is_user_code(code_at(tmp_path / "app-other" / "code.py"))


def test_concurrent_dictionary_inputs_do_not_crash_the_target():
    import threading

    values = dict.fromkeys(range(50_000), 1)
    stopped = threading.Event()

    def mutate():
        while not stopped.is_set():
            values[-1] = 0
            time.sleep(0)
            values.pop(-1, None)
            time.sleep(0)

    worker = threading.Thread(target=mutate)
    worker.start()
    tracer = Tracer(root=HERE)
    try:
        with tracer:
            for _ in range(10):
                assert large_key(values) >= 50_000
            assert sys.getprofile() is tracer._installed_hook
    finally:
        stopped.set()
        worker.join()
    record = next(record for key, record in tracer.records.items() if key[1] == "large_key")
    assert record["calls"] == record["unknown"] == 10
    assert tracer.observation_errors == 0
