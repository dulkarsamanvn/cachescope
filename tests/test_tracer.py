import os
import time

from cachescope.tracer import Tracer, cache_advice
from cachescope.verdict import judge

HERE = os.path.dirname(os.path.abspath(__file__))


def square(n):
    return n * n


def take_object(obj):
    return 1


def make_a():
    def helper(x):
        return x

    return helper


def make_b():
    def helper(x):
        return x + 1

    return helper


def find(tracer, name):
    """Return the record of the function with this name, or None."""
    for (filename, func_name, line), record in tracer.records.items():
        if func_name == name:
            return record
    return None


def test_counts_calls_and_unique_inputs():
    tracer = Tracer(root=HERE)
    tracer.start()
    for i in range(10):
        square(i % 3)
    tracer.stop()

    record = find(tracer, "square")
    assert record["calls"] == 10
    assert len(record["inputs"]) == 3


def test_unknown_arguments_are_counted_but_not_compared():
    tracer = Tracer(root=HERE)
    tracer.start()
    for _ in range(4):
        take_object(object())
    tracer.stop()

    record = find(tracer, "take_object")
    assert record["calls"] == 4
    assert record["unknown"] == 4
    assert record["inputs"] == {}


def test_only_records_functions_under_root():
    tracer = Tracer(root=os.path.join(HERE, "nowhere"))
    tracer.start()
    square(2)
    tracer.stop()
    assert tracer.records == {}


def test_does_not_record_its_own_code():
    tracer = Tracer(root=HERE)
    tracer.start()
    square(2)
    tracer.stop()
    assert find(tracer, "stop") is None


def test_same_name_in_different_places_stays_separate():
    tracer = Tracer(root=HERE)
    a = make_a()
    b = make_b()
    tracer.start()
    a(1)
    b(1)
    tracer.stop()
    helpers = [key for key in tracer.records if key[1] == "helper"]
    assert len(helpers) == 2

def slow(x):
    time.sleep(0.01)
    return x


def give_object(x):
    return object()


def make_counter():
    state = {"n": 0}

    def counter(x):
        state["n"] += 1
        return state["n"]

    return counter


def test_time_is_recorded():
    tracer = Tracer(root=HERE)
    tracer.start()
    for _ in range(5):
        slow(1)
    tracer.stop()
    assert find(tracer, "slow")["time"] >= 0.04


def test_changing_closure_state_requires_review():
    counter = make_counter()
    tracer = Tracer(root=HERE)
    tracer.start()
    counter(1)
    counter(1)
    tracer.stop()
    assert find(tracer, "counter")["contextual"] is True
    assert judge(find(tracer, "counter"))[0] == "MAYBE"


def test_stable_result_is_not_flagged():
    tracer = Tracer(root=HERE)
    tracer.start()
    for _ in range(3):
        square(2)
    tracer.stop()
    assert find(tracer, "square")["varied"] is False


def test_uncomparable_results_are_not_flagged():
    tracer = Tracer(root=HERE)
    tracer.start()
    give_object(1)
    give_object(1)
    tracer.stop()
    assert find(tracer, "give_object")["varied"] is False

def slow_clock(x):
    time.sleep(0.005)
    started = time.time()
    return x


def test_report_downgrades_function_that_reads_the_clock(capsys):
    tracer = Tracer(root=HERE)
    tracer.start()
    for _ in range(5):
        slow_clock(1)
    tracer.stop()
    tracer.report()
    output = capsys.readouterr().out
    assert "[MAYBE] slow_clock" in output
    assert "reads the clock" in output


def test_report_calls_clean_slow_function_a_candidate(capsys):
    tracer = Tracer(root=HERE)
    tracer.start()
    for _ in range(5):
        slow(1)
    tracer.stop()
    tracer.report()
    assert "[CANDIDATE] slow (" in capsys.readouterr().out

def test_call_order_is_recorded():
    tracer = Tracer(root=HERE)
    tracer.start()
    for n in (1, 2, 1):
        square(n)
    tracer.stop()
    sequence = find(tracer, "square")["sequence"]
    assert len(sequence) == 3
    assert sequence[0] == sequence[2]
    assert sequence[0] != sequence[1]


def test_report_suggests_a_cache_size_for_candidates(capsys):
    tracer = Tracer(root=HERE)
    tracer.start()
    for i in range(60):
        slow(i % 3)
    tracer.stop()
    tracer.report()
    assert "capacity ~4 for normalized inputs" in capsys.readouterr().out


def test_report_gives_no_size_for_functions_that_need_review(capsys):
    tracer = Tracer(root=HERE)
    tracer.start()
    for _ in range(5):
        slow_clock(1)
    tracer.stop()
    tracer.report()
    output = capsys.readouterr().out
    assert "[MAYBE] slow_clock" in output
    assert "lru_cache" not in output

def takes_list(items):
    time.sleep(0.005)
    return len(items)


def test_unhashable_arguments_are_counted():
    tracer = Tracer(root=HERE)
    tracer.start()
    for _ in range(3):
        takes_list([1, 2])
    tracer.stop()
    assert find(tracer, "takes_list")["unhashable"] == 3


def test_hashable_arguments_are_not_counted():
    tracer = Tracer(root=HERE)
    tracer.start()
    for _ in range(3):
        square(2)
    tracer.stop()
    assert find(tracer, "square")["unhashable"] == 0


def test_report_does_not_suggest_lru_cache_for_list_arguments(capsys):
    tracer = Tracer(root=HERE)
    tracer.start()
    for _ in range(5):
        takes_list([1, 2])
    tracer.stop()
    tracer.report()
    output = capsys.readouterr().out
    assert "[CANDIDATE] takes_list" in output
    assert "TypeError" in output
    assert "lru_cache(maxsize" not in output

def add_all(*numbers):
    return sum(numbers)


def with_keyword(name, *, shout=False):
    return name.upper() if shout else name


def with_extras(**options):
    return len(options)


def count_up(n):
    for i in range(n):
        yield i


def test_star_args_are_part_of_the_input():
    tracer = Tracer(root=HERE)
    tracer.start()
    add_all(1, 2)
    add_all(5, 5)
    add_all(1, 2)
    tracer.stop()
    record = find(tracer, "add_all")
    assert record["calls"] == 3
    assert len(record["inputs"]) == 2
    assert record["varied"] is False


def test_keyword_only_arguments_are_part_of_the_input():
    tracer = Tracer(root=HERE)
    tracer.start()
    with_keyword("a", shout=True)
    with_keyword("a", shout=False)
    tracer.stop()
    record = find(tracer, "with_keyword")
    assert len(record["inputs"]) == 2
    assert record["varied"] is False


def test_double_star_arguments_are_part_of_the_input():
    tracer = Tracer(root=HERE)
    tracer.start()
    with_extras(a=1)
    with_extras(a=2)
    tracer.stop()
    assert len(find(tracer, "with_extras")["inputs"]) == 2


def test_generator_resumes_are_one_logical_call():
    tracer = Tracer(root=HERE)
    tracer.start()
    list(count_up(4))
    tracer.stop()
    record = find(tracer, "count_up")
    assert record["calls"] == record["successful"] == 1
    assert record["suspensions"] == 4
    assert record["kind"] == "generator"

def test_advice_suggests_a_size_for_hashable_arguments():
    record = {"unhashable": 0, "sequence": ["a", "b"] * 10}
    assert "capacity ~2 for normalized inputs" in cache_advice(record)


def test_advice_explains_unhashable_arguments_instead_of_suggesting():
    record = {"unhashable": 2, "sequence": []}
    text = cache_advice(record)
    assert "TypeError" in text
    assert "lru_cache(maxsize" not in text


def test_advice_is_empty_when_a_cache_would_never_help():
    record = {"unhashable": 0, "sequence": list(range(10))}
    assert cache_advice(record) == ""

def test_decision_is_made_once_per_function():
    tracer = Tracer(root=HERE)
    asked = []
    original = tracer._is_user_code

    def counting(code):
        asked.append(code.co_name)
        return original(code)

    tracer._is_user_code = counting
    tracer.start()
    for _ in range(50):
        square(2)
    tracer.stop()
    assert asked.count("square") == 1
