"""Measure how much slower cachescope makes a program."""

import os
import time

from cachescope.tracer import Tracer

HERE = os.path.dirname(os.path.abspath(__file__))


def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)


def tiny_calls():
    """Worst case: almost nothing but function calls."""
    for _ in range(5):
        fib(20)


def wait_then_answer(i):
    time.sleep(0.002)
    return i


def mostly_waiting():
    """Typical case: the program spends its time waiting (database, network, disk)."""
    for i in range(100):
        wait_then_answer(i % 10)


def run_plain(work):
    start = time.perf_counter()
    work()
    return time.perf_counter() - start


def run_traced(work):
    tracer = Tracer(root=HERE)
    start = time.perf_counter()
    tracer.start()
    try:
        work()
    finally:
        tracer.stop()
    return time.perf_counter() - start


def compare(label, work):
    work()  # warm up
    plain = min(run_plain(work) for _ in range(3))
    traced = min(run_traced(work) for _ in range(3))
    print(f"{label}: {plain:.3f} s plain, {traced:.3f} s traced, {traced / plain:.1f}x slower")


if __name__ == "__main__":
    compare("tiny calls    ", tiny_calls)
    compare("mostly waiting", mostly_waiting)