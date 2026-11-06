"""Logical executions and active-segment timing for resumable functions."""

import asyncio
import os
import threading

import pytest

from cachescope.tracer import Tracer, cache_advice
from cachescope.verdict import judge, summarize

HERE = os.path.dirname(__file__)


def find(tracer, name):
    return next(record for key, record in tracer.records.items() if key[1] == name)


def numbers(n):
    for value in range(n):
        yield value


def delegated(n):
    yield from numbers(n)


async def async_value(value):
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return value * 2


async def async_numbers(n):
    for value in range(n):
        await asyncio.sleep(0)
        yield value


def test_generators_compare_initial_inputs_and_count_executions():
    with Tracer(root=HERE) as tracer:
        assert list(numbers(3)) == list(numbers(3)) == [0, 1, 2]
    record = find(tracer, "numbers")
    assert record["calls"] == record["successful"] == 2
    assert record["suspensions"] == 6
    assert record["active"] == record["failed"] == record["incomplete"] == 0
    assert list(record["inputs"].values()) == [2]
    assert record["results"] == {}
    assert not record["varied"]
    assert judge(record)[0] == "MAYBE"
    assert summarize(record).wasted == 0
    assert "no size is estimated" in cache_advice(record)


def test_yield_from_is_not_a_series_of_new_calls():
    with Tracer(root=HERE) as tracer:
        assert list(delegated(3)) == [0, 1, 2]
    for name in ("numbers", "delegated"):
        record = find(tracer, name)
        assert record["calls"] == record["successful"] == 1
        assert record["suspensions"] == 3
    assert not tracer._open


def test_coroutines_count_completed_results_across_multiple_awaits():
    async def main():
        assert await async_value(3) == 6
        assert await async_value(3) == 6

    with Tracer(root=HERE) as tracer:
        asyncio.run(main())
    record = find(tracer, "async_value")
    assert record["calls"] == record["successful"] == 2
    assert record["suspensions"] == 4
    assert record["unknown_results"] == 0
    assert not record["recursive"]
    assert not record["varied"]
    assert judge(record)[0] == "MAYBE"
    assert "async-aware" in judge(record)[1]


def test_interleaved_tasks_do_not_look_like_recursion():
    async def main():
        assert await asyncio.gather(async_value(3), async_value(3)) == [6, 6]

    with Tracer(root=HERE) as tracer:
        asyncio.run(main())
    record = find(tracer, "async_value")
    assert record["calls"] == record["successful"] == 2
    assert not record["recursive"]
    assert record["active"] == 0
    assert list(record["inputs"].values()) == [2]
    assert tracer.observation_errors == 0


def test_async_generators_track_yields_and_awaits_without_caching_streams():
    async def main():
        for _ in range(2):
            assert [value async for value in async_numbers(3)] == [0, 1, 2]

    with Tracer(root=HERE) as tracer:
        asyncio.run(main())
    record = find(tracer, "async_numbers")
    assert record["calls"] == record["successful"] == 2
    assert record["kind"] == "async generator"
    assert record["suspensions"] >= 6
    assert record["active"] == record["incomplete"] == 0
    assert record["results"] == {}
    assert not record["varied"]
    assert judge(record)[0] == "MAYBE"


@pytest.mark.parametrize("kind", ["generator", "coroutine"])
def test_suspended_time_is_excluded_using_a_controlled_clock(monkeypatch, kind):
    import cachescope.tracer as module

    clock = [0.0]

    class Pause:
        def __await__(self):
            yield None

    def generator():
        clock[0] += 2
        yield None
        clock[0] += 3

    async def coroutine():
        clock[0] += 2
        await Pause()
        clock[0] += 3

    monkeypatch.setattr(module.time, "perf_counter", lambda: clock[0])
    with Tracer(root=HERE) as tracer:
        execution = generator() if kind == "generator" else coroutine()
        assert execution.send(None) is None
        clock[0] += 1000  # another task, caller, or I/O runs during suspension
        with pytest.raises(StopIteration):
            execution.send(None)
    record = find(tracer, kind)
    assert record["calls"] == record["successful"] == 1
    assert record["time"] == 5
    assert not tracer._open


def test_partially_consumed_generator_is_incomplete_at_stop():
    with Tracer(root=HERE) as tracer:
        execution = numbers(3)
        assert next(execution) == 0
    record = find(tracer, "numbers")
    assert record["calls"] == record["incomplete"] == 1
    assert record["successful"] == record["active"] == 0
    assert record["inputs"] == {}
    execution.close()


def test_explicit_generator_close_does_not_leave_a_stale_execution():
    with Tracer(root=HERE) as tracer:
        for _ in range(5):
            execution = numbers(3)
            next(execution)
            execution.close()
    record = find(tracer, "numbers")
    assert record["calls"] == 5
    assert record["closed"] + record["failed"] == 5
    assert record["incomplete"] == record["active"] == record["successful"] == 0
    assert not tracer._open


def failing_generator(n):
    yield n
    raise ValueError("failed after a yield")


def test_generator_exception_is_not_a_successful_completion():
    with Tracer(root=HERE) as tracer:
        with pytest.raises(ValueError):
            list(failing_generator(3))
    record = find(tracer, "failing_generator")
    assert record["calls"] == record["failed"] == 1
    assert record["successful"] == record["active"] == 0
    assert record["sequence"] == []


async def cancelled_value():
    await asyncio.sleep(0)
    raise asyncio.CancelledError()


def test_coroutine_cancellation_is_excluded_from_successes():
    with Tracer(root=HERE) as tracer:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(cancelled_value())
    record = find(tracer, "cancelled_value")
    assert record["calls"] == record["failed"] == 1
    assert record["successful"] == record["incomplete"] == record["active"] == 0


def test_generator_can_resume_on_another_observed_thread():
    with Tracer(root=HERE, threads=True) as tracer:
        execution = numbers(2)
        assert next(execution) == 0
        result = []
        worker = threading.Thread(target=lambda: result.extend(execution))
        worker.start()
        worker.join(timeout=10)
        assert not worker.is_alive()
    assert result == [1]
    record = find(tracer, "numbers")
    assert record["calls"] == record["successful"] == 1
    assert record["active"] == record["incomplete"] == 0
    assert not record["recursive"]
    assert tracer.observation_errors == 0


def test_suspended_executions_obey_open_frame_limit(monkeypatch):
    import cachescope.tracer as module

    monkeypatch.setattr(module, "MAX_OPEN_FRAMES", 2)
    with Tracer(root=HERE) as tracer:
        executions = [numbers(3) for _ in range(5)]
        for execution in executions:
            next(execution)
    assert tracer.memory_limited
    assert sum(record["incomplete"] for record in tracer.records.values()) <= 2
    assert not tracer._open
    for execution in executions:
        execution.close()


def test_execution_started_before_tracing_has_unknown_initial_inputs():
    execution = numbers(3)
    assert next(execution) == 0
    with Tracer(root=HERE) as tracer:
        assert list(execution) == [1, 2]
    record = find(tracer, "numbers")
    assert record["calls"] == record["successful"] == record["unknown"] == 1
    assert record["partial_start"]
    assert record["inputs"] == {}


def receiving(value):
    received = yield value
    yield received


def test_send_values_do_not_make_a_stream_look_like_an_unstable_return():
    with Tracer(root=HERE) as tracer:
        for sent in (10, 20):
            execution = receiving(3)
            assert next(execution) == 3
            assert execution.send(sent) == sent
            with pytest.raises(StopIteration):
                next(execution)
    record = find(tracer, "receiving")
    assert record["calls"] == record["successful"] == 2
    assert not record["varied"]
    assert judge(record)[0] == "MAYBE"


async def waiting_for_cancellation(ready):
    ready.set()
    await asyncio.sleep(60)


def test_task_cancel_while_awaiting_is_a_failed_execution():
    async def main():
        ready = asyncio.Event()
        task = asyncio.create_task(waiting_for_cancellation(ready))
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with Tracer(root=HERE) as tracer:
        asyncio.run(main())
    record = find(tracer, "waiting_for_cancellation")
    assert record["calls"] == record["failed"] == 1
    assert record["successful"] == record["incomplete"] == record["active"] == 0
    assert record["inputs"] == {}


def delegated_failure(n):
    yield from failing_generator(n)


def test_exception_propagated_through_yield_from_is_failed():
    with Tracer(root=HERE) as tracer:
        with pytest.raises(ValueError):
            list(delegated_failure(3))
    record = find(tracer, "delegated_failure")
    assert record["calls"] == record["failed"] == 1
    assert record["successful"] == record["incomplete"] == record["active"] == 0


def close_with_return(n):
    try:
        yield n
    except GeneratorExit:
        return n


def test_close_that_returns_a_value_is_still_an_early_close():
    with Tracer(root=HERE) as tracer:
        execution = close_with_return(3)
        next(execution)
        execution.close()
    record = find(tracer, "close_with_return")
    assert record["calls"] == record["closed"] == 1
    assert record["successful"] == record["failed"] == record["active"] == record["incomplete"] == 0
    assert record["inputs"] == {}


def test_throw_that_terminates_a_generator_is_failed():
    with Tracer(root=HERE) as tracer:
        execution = numbers(3)
        next(execution)
        with pytest.raises(ValueError):
            execution.throw(ValueError("injected failure"))
    record = find(tracer, "numbers")
    assert record["calls"] == record["failed"] == 1
    assert record["successful"] == record["closed"] == record["active"] == record["incomplete"] == 0
    assert record["suspensions"] == 1
