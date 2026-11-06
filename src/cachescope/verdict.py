"""Decide what to say about a function, using the numbers we recorded."""

from typing import NamedTuple

# Rules of thumb. Change them if they do not suit you.
MIN_AVG_SECONDS = 0.0001  # faster than 0.1 ms: a cache would not help
MIN_WASTED_SECONDS = 0.005  # under 5 ms to save in total: not worth the bother
MIN_REPEAT_RATIO = 0.5  # fewer than half the calls repeated: a weak case


class Summary(NamedTuple):
    calls: int
    unique: int
    repeats: int
    average: float
    repeated_average: float
    wasted: float


def summarize(record):
    """Shared accounting for completed calls and observed repeated work."""
    calls = record.get("successful", record["calls"])
    unique = len(record["inputs"])
    repeats = max(0, sum(record["inputs"].values()) - unique)
    elapsed = max(0.0, record["time"] - record.get("failed_time", 0.0))
    average = elapsed / max(calls, 1)
    wasted = record.get("repeat_time", repeats * average)
    if record.get("recursive") or record.get("concurrent") or record.get("kind"):
        wasted = 0.0
    return Summary(calls, unique, repeats, average, wasted / max(repeats, 1), wasted)


def judge(record, risks=()):
    """Return (label, reason) for one function's record.

    `risks` is a list of danger signs found in the function's code.
    """
    summary = summarize(record)
    calls = summary.calls
    unknown = record["unknown"]
    repeats = summary.repeats
    average = summary.repeated_average
    wasted = summary.wasted

    # The first rule that matches wins, so the order matters.
    if record.get("contextual"):
        return "MAYBE", "function instances or captured state require review; shared cache identity cannot be inferred"

    if record.get("custom_inputs") or record.get("custom_results"):
        return "MAYBE", "custom object identity and visible state were observed; cache-key equivalence requires review"

    if record["varied"]:
        return "UNSAFE", "gave different results for the same input"

    if record.get("cache_conflict"):
        return "MAYBE", "standard cache keys merge inputs with different types"

    if record.get("observation_errors"):
        return "MAYBE", "some profiling observations failed"

    if record.get("recursive"):
        return "MAYBE", "recursive calls overlap; independent cache savings cannot be estimated"

    if record.get("concurrent"):
        return "MAYBE", "calls overlap across threads; a cache may still execute concurrent misses"

    if record.get("limited"):
        return "MAYBE", "observation limits were reached; the data is incomplete"

    if record.get("incomplete"):
        return "MAYBE", "tracing stopped before some calls completed"

    if record.get("closed"):
        return "MAYBE", "some executions were closed before completion"

    if record.get("kind") == "coroutine":
        return "MAYBE", "cache completed results with an async-aware strategy; coroutine objects cannot be reused"

    if record.get("kind"):
        return "MAYBE", "generator streams are consumable; caching materialized values requires review"

    if repeats == 0:
        if record.get("failed"):
            return "SKIP", "no repeated successful calls; exceptions are not cached"
        if unknown:
            return "SKIP", "its arguments could not be compared"
        return "SKIP", "no input was ever repeated"

    if average < MIN_AVG_SECONDS:
        return "SKIP", "too fast for a cache to help"

    if wasted < MIN_WASTED_SECONDS:
        return "SKIP", "too little time to save"

    if risks:
        return "MAYBE", "needs a human check: " + "; ".join(risks)

    if record.get("failed"):
        return "MAYBE", "some calls raised exceptions, which a cache would not retain"

    if record.get("unknown_results"):
        return "MAYBE", "some returned values could not be compared"

    if record.get("mutable_results"):
        return "MAYBE", "returns mutable values that a cache would share between callers"

    if unknown:
        return "MAYBE", f"{unknown} call(s) had arguments we could not compare"

    if repeats / calls < MIN_REPEAT_RATIO:
        return "MAYBE", "most inputs were new"

    return "CANDIDATE", "repeats a lot, takes time, results stable, no danger signs found"
