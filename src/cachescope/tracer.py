"""Watch function calls and record what they were given and returned."""
import inspect
import os
import sys
import time

from .fingerprint import UNKNOWN, fingerprint
from .verdict import judge
from .risk import risks_of_function
from .simulate import suggest_maxsize

# The folder that holds this very file. We never record our own code.
OWN_FOLDER = os.path.dirname(os.path.abspath(__file__))
# Remember the order of at most this many calls per function (keeps memory small).
MAX_SEQUENCE = 100_000
# Functions that pause and resume (generators, async functions) fire the "call" event on
# every resume, so their numbers would be wrong. We skip them.
SKIP_FLAGS = (
    inspect.CO_GENERATOR
    | inspect.CO_COROUTINE
    | inspect.CO_ITERABLE_COROUTINE
    | inspect.CO_ASYNC_GENERATOR
)

def clean(path):
    """Make a path comparable: absolute, and lower-case on Windows."""
    return os.path.normcase(os.path.abspath(path))

def is_hashable(value):
    """True if Python can use this value as a dictionary key (which lru_cache needs)."""
    try:
        hash(value)
    except TypeError:
        return False
    return True

def cache_advice(record):
    """The extra lines for a CANDIDATE: a size suggestion, or a note on why we give none."""
    if record["unhashable"]:
        return (
            "\n    note: some arguments are lists, dicts or sets, so @lru_cache "
            "would raise TypeError. Cache by hand, or convert them to tuples first."
        )
    suggestion = suggest_maxsize(record["sequence"])
    if not suggestion:
        return ""
    size, rate = suggestion
    return (
        f"\n    suggestion: @lru_cache(maxsize={size})"
        f"  (answers about {rate * 100:.0f}% of calls from the cache)"
    )


def format_row(filename, name, line, record):
    """Build the report text for one function. Returns (wasted_seconds, text)."""
    risks = risks_of_function(filename, line)
    label, reason = judge(record, risks)
    unique = len(record["inputs"])
    repeats = record["calls"] - record["unknown"] - unique
    average = record["time"] / record["calls"]
    wasted = repeats * average
    where = f"{os.path.basename(filename)}:{line}"
    text = (
        f"[{label}] {name} ({where}): {record['calls']} calls, {unique} unique, "
        f"{repeats} repeats, avg {average * 1000:.2f} ms, "
        f"wasted ~{wasted * 1000:.1f} ms\n"
        f"    why: {reason}"
    )
    if label == "CANDIDATE":  # for anything we are unsure about, we say less
        text += cache_advice(record)
    return wasted, text

class Tracer:
    def __init__(self, root=None):
        self.root = clean(root or os.getcwd()) + os.sep
        self.own_folder = clean(OWN_FOLDER) + os.sep
        # (file, function name, first line) -> what we know about it
        self.records = {}
        # running function id -> (its record, its input label, start time)
        self._open = {}

    def start(self):
        sys.setprofile(self._hook)

    def stop(self):
        sys.setprofile(None)
        self._open.clear()


    def _is_user_code(self, code):
        if code.co_flags & SKIP_FLAGS:
            return False
        
        filename = code.co_filename
        if filename.startswith("<"):
            return False
        if code.co_name.startswith("<"):
            return False
        path = clean(filename)
        if path.startswith(self.own_folder):
            return False
        if "site-packages" in path:
            return False
        return path.startswith(self.root)

    def _hook(self, frame, event, arg):
        if event == "call":
            self._on_call(frame)
        elif event == "return":
            self._on_return(frame, arg)

    def _on_call(self, frame):
        code = frame.f_code
        if not self._is_user_code(code):
            return

        count = code.co_argcount + code.co_kwonlyargcount
        if code.co_flags & inspect.CO_VARARGS:
            count += 1
        if code.co_flags & inspect.CO_VARKEYWORDS:
            count += 1
        names = code.co_varnames[:count]
        args = tuple(frame.f_locals[name] for name in names)
        label = fingerprint(args)

        key = (code.co_filename, code.co_name, code.co_firstlineno)
        record = self.records.setdefault(
            key,
            {
                "calls": 0,
                "unknown": 0,
                "inputs": {},
                "results": {},
                "time": 0.0,
                "varied": False,
                "sequence": [],
                "unhashable": 0,
            },
        )
        record["calls"] += 1
        if label is UNKNOWN:
            record["unknown"] += 1
        else:
            record["inputs"][label] = record["inputs"].get(label, 0) + 1
            if len(record["sequence"]) < MAX_SEQUENCE:
                record["sequence"].append(label)
            if not is_hashable(args):
                record["unhashable"] += 1

        # Start the stopwatch last, so the work above is not blamed on the function.
        self._open[id(frame)] = (record, label, time.perf_counter())

    def _on_return(self, frame, returned_value):
        entry = self._open.pop(id(frame), None)
        if entry is None:  # a function we chose not to record
            return
        record, label, started = entry
        record["time"] += time.perf_counter() - started

        if label is UNKNOWN:  # inputs we cannot compare, so results are not comparable either
            return
        result_label = fingerprint(returned_value)
        if result_label is UNKNOWN:
            return
        # Remember the first result for this input. If a later one differs, flag it.
        first = record["results"].setdefault(label, result_label)
        if first != result_label:
            record["varied"] = True

    def report(self, min_calls=2):
        rows = []
        for (filename, name, line), record in self.records.items():
            if record["calls"] >= min_calls:
                rows.append(format_row(filename, name, line, record))

        if not rows:
            print("No function was called often enough to report.")
            return
        rows.sort(reverse=True)  # biggest wasted time first
        for _, text in rows:
            print(text)
        print()
        print("Based on this one run. Advice, not a guarantee: review before caching.")