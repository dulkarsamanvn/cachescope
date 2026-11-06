"""Worker tracing, concurrent accounting, and profile-hook ownership."""

import os
import sys
import threading
import time

import pytest

from cachescope.tracer import Tracer, cache_advice, thread_profile
from cachescope.verdict import judge, summarize

HERE = os.path.dirname(__file__)


def worker_value(value):
    time.sleep(0.002)
    return value * 2


def record_for(tracer, name="worker_value"):
    return next(record for key, record in tracer.records.items() if key[1] == name)


def run_worker(target, *args):
    errors = []

    def run():
        try:
            target(*args)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert errors == []


def test_workers_are_explicitly_enabled():
    with Tracer(root=HERE) as disabled:
        run_worker(worker_value, 3)
    assert not any(key[1] == "worker_value" for key in disabled.records)
    with Tracer(root=HERE, threads=True) as enabled:
        for _ in range(3):
            run_worker(worker_value, 3)
    record = record_for(enabled)
    assert record["calls"] == record["successful"] == 3
    assert list(record["inputs"].values()) == [3]
    assert record["unknown"] == record["active"] == 0
    assert not record["recursive"]
    assert enabled.observation_errors == 0


def parallel_value(value):
    rendezvous.wait(timeout=5)
    return value * 2


def test_concurrent_calls_are_not_recursion_or_promised_savings(monkeypatch):
    monkeypatch.setitem(globals(), "rendezvous", threading.Barrier(2))
    with Tracer(root=HERE, threads=True) as tracer:
        workers = [threading.Thread(target=parallel_value, args=(3,)) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            assert not worker.is_alive()
    record = record_for(tracer, "parallel_value")
    assert record["successful"] == 2
    assert record["concurrent"]
    assert not record["recursive"]
    assert record["time"] > 0
    assert record["active"] == 0
    assert summarize(record).wasted == 0
    assert judge(record)[0] == "MAYBE"
    assert "no size is estimated" in cache_advice(record)


def test_previous_worker_hook_and_owner_hook_are_restored():
    saved = thread_profile()
    events = []

    def previous(frame, event, arg):
        if frame.f_code is worker_value.__code__ and event == "call":
            events.append(event)

    original_owner = sys.getprofile()
    threading.setprofile(previous)
    try:
        with Tracer(root=HERE, threads=True):
            run_worker(worker_value, 1)
            assert events == []
        assert thread_profile() is previous
        assert sys.getprofile() is original_owner
        run_worker(worker_value, 1)
        assert events == ["call"]
    finally:
        threading.setprofile(saved)


def test_live_worker_detaches_after_stop_and_does_not_join_a_restart():
    ready, release = threading.Event(), threading.Event()
    seen = []
    saved = thread_profile()

    def previous(frame, event, arg):
        pass

    def waiting():
        ready.set()
        release.wait(timeout=10)
        worker_value(1)
        seen.append(sys.getprofile())

    threading.setprofile(previous)
    tracer = Tracer(root=HERE, threads=True)
    worker = threading.Thread(target=waiting)
    try:
        tracer.start()
        worker.start()
        assert ready.wait(timeout=5)
        tracer.stop()
        tracer.start()
        release.set()
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert seen == [previous]
        assert not any(key[1] == "worker_value" for key in tracer.records)
        assert record_for(tracer, "waiting")["incomplete"] == 1
    finally:
        release.set()
        worker.join(timeout=10)
        tracer.stop()
        threading.setprofile(saved)


def test_existing_workers_keep_their_profile_hook():
    ready, release = threading.Event(), threading.Event()
    seen = []

    def existing():
        seen.append(sys.getprofile())
        ready.set()
        release.wait(timeout=10)
        worker_value(1)
        seen.append(sys.getprofile())

    worker = threading.Thread(target=existing)
    worker.start()
    try:
        assert ready.wait(timeout=5)
        with Tracer(root=HERE, threads=True) as tracer:
            release.set()
            worker.join(timeout=10)
            assert not worker.is_alive()
        assert seen[0] is seen[1]
        assert not any(key[1] == "worker_value" for key in tracer.records)
    finally:
        release.set()
        worker.join(timeout=10)


def test_worker_hook_replacement_is_preserved():
    saved = thread_profile()

    def replacement(frame, event, arg):
        pass

    tracer = Tracer(root=HERE, threads=True)
    try:
        tracer.start()
        threading.setprofile(replacement)
        tracer.stop()
        assert thread_profile() is replacement
        assert tracer.observation_errors == 1
    finally:
        tracer.stop()
        threading.setprofile(saved)


def test_nested_tracer_in_worker_is_rejected():
    def attempt():
        with pytest.raises(RuntimeError, match="another Tracer"):
            Tracer(root=HERE).start()

    with Tracer(root=HERE, threads=True):
        run_worker(attempt)


def test_worker_profiling_owner_conflict_is_rejected():
    ready, release = threading.Event(), threading.Event()

    def other_owner():
        with Tracer(root=HERE, threads=True):
            ready.set()
            release.wait(timeout=10)

    worker = threading.Thread(target=other_owner)
    worker.start()
    try:
        assert ready.wait(timeout=5)
        with pytest.raises(RuntimeError, match="owns worker-thread"):
            Tracer(root=HERE, threads=True).start()
    finally:
        release.set()
        worker.join(timeout=10)


def test_failed_start_restores_worker_default(monkeypatch):
    saved = thread_profile()
    tracer = Tracer(root=HERE, threads=True)

    def fail(hook):
        raise RuntimeError("cannot install")

    monkeypatch.setattr(sys, "setprofile", fail)
    with pytest.raises(RuntimeError, match="cannot install"):
        tracer.start()
    assert thread_profile() is saved
    assert not tracer._running
