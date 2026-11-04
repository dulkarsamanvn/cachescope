"""Decide what to say about a function, using the numbers we recorded."""

# Rules of thumb. Change them if they do not suit you.
MIN_AVG_SECONDS = 0.0001  # faster than 0.1 ms: a cache would not help
MIN_WASTED_SECONDS = 0.005  # under 5 ms to save in total: not worth the bother
MIN_REPEAT_RATIO = 0.5  # fewer than half the calls repeated: a weak case


def judge(record, risks=()):
    """Return (label, reason) for one function's record.

    `risks` is a list of danger signs found in the function's code.
    """
    calls = record["calls"]
    unknown = record["unknown"]
    unique = len(record["inputs"])
    repeats = calls - unknown - unique
    average = record["time"] / calls
    wasted = repeats * average

    # The first rule that matches wins, so the order matters.
    if record["varied"]:
        return "UNSAFE", "gave different results for the same input"

    if repeats == 0:
        if unknown:
            return "SKIP", "its arguments could not be compared"
        return "SKIP", "no input was ever repeated"

    if average < MIN_AVG_SECONDS:
        return "SKIP", "too fast for a cache to help"

    if wasted < MIN_WASTED_SECONDS:
        return "SKIP", "too little time to save"

    if risks:
        return "MAYBE", "needs a human check: " + "; ".join(risks)

    if unknown:
        return "MAYBE", f"{unknown} call(s) had arguments we could not compare"

    if repeats / calls < MIN_REPEAT_RATIO:
        return "MAYBE", "most inputs were new"

    return "CANDIDATE", "repeats a lot, takes time, results stable, no danger signs found"