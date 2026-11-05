"""Watch function calls and record what they were given and returned."""
import dis
import inspect
import os
import sys
import threading
import time

from .fingerprint import UNKNOWN, fingerprint, fingerprint_size, immutable_label
from .verdict import judge, summarize
from .risk import risks_of_function
from .simulate import suggest_maxsize

# The folder that holds this very file. We never record our own code.
OWN_FOLDER = os.path.dirname(os.path.abspath(__file__))
# Remember the order of at most this many calls per function (keeps memory small).
MAX_SEQUENCE = 100_000
MAX_INPUTS = 4096
MAX_FUNCTIONS = 1024
MAX_DECISIONS = 4096
MAX_OPEN_FRAMES = 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
# Functions that pause and resume (generators, async functions) fire the "call" event on
# every resume, so their numbers would be wrong. We skip them.
SKIP_FLAGS = (
    inspect.CO_GENERATOR
    | inspect.CO_COROUTINE
    | inspect.CO_ITERABLE_COROUTINE
    | inspect.CO_ASYNC_GENERATOR
)

def clean(path):
    """Make a path comparable, including case and symbolic-link normalization."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_under(path, folder):
    try:
        return os.path.commonpath((path, folder)) == folder
    except ValueError:  # different Windows drives
        return False

def is_hashable(value):
    """True if Python can use this value as a dictionary key (which lru_cache needs)."""
    try:
        hash(value)
    except TypeError:
        return False
    return True


def normalized_cache_key(code, args):
    """Check real argument values without hashing synthetic *args/**kwargs containers."""
    fixed = code.co_argcount + code.co_kwonlyargcount
    extra = args[fixed] if code.co_flags & inspect.CO_VARARGS else ()
    keywords = tuple(args[-1].items()) if code.co_flags & inspect.CO_VARKEYWORDS else ()
    return args[:fixed], extra, keywords

def cache_advice(record):
    """The extra lines for a CANDIDATE: a size suggestion, or a note on why we give none."""
    if record.get("cache_conflict"):
        return "\n    note: standard cache keys merge inputs that this function distinguishes."
    if record.get("unknown_results") or record.get("mutable_results"):
        return "\n    note: returned values need a human check before sharing cached results."
    if record.get("failed"):
        return "\n    note: failed calls are not cached."
    if record.get("incomplete"):
        return "\n    note: tracing stopped before some calls completed."
    if record.get("recursive"):
        return "\n    note: recursive calls overlap; no independent cache estimate is available."
    if record.get("limited"):
        return "\n    note: observation limits were reached; no cache size is estimated."
    if record.get("contextual"):
        return "\n    note: factory or captured context requires review; separate function caches cannot be inferred."
    if record.get("sequence_truncated"):
        return (
            f"\n    note: call sequence truncated after {len(record['sequence'])} calls;"
            " no cache size or hit rate is estimated."
        )
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
        f"\n    size estimate: capacity ~{size} for normalized inputs"
        f"  (modeled hits {rate * 100:.0f}%; assumes consistent call spelling)"
        "\n    note: positional/keyword spelling and defaults can change real cache keys;"
        " verify actual hits with cache_info()."
    )


def format_row(filename, name, line, record):
    """Build the report text for one function. Returns (wasted_seconds, text)."""
    risks = risks_of_function(filename, line)
    label, reason = judge(record, risks)
    summary = summarize(record)
    unique, repeats, average, wasted = (
        summary.unique, summary.repeats, summary.average, summary.wasted
    )
    where = f"{os.path.basename(filename)}:{line}"
    text = (
        f"[{label}] {name} ({where}): {record['calls']} calls, {unique} unique, "
        f"{repeats} repeats, avg {average * 1000:.2f} ms, "
        f"repeat work ~{wasted * 1000:.1f} ms\n"
        f"    why: {reason}"
    )
    if label == "CANDIDATE":  # for anything we are unsure about, we say less
        text += cache_advice(record)
    if record.get("failed"):
        text += f"\n    note: {record['failed']} call(s) raised; excluded from cache estimates."
    if record.get("incomplete"):
        text += f"\n    note: {record['incomplete']} call(s) were still running when tracing stopped."
    if record.get("recursive"):
        text += "\n    note: recursive overlap is excluded from elapsed time; savings are not estimated."
    if record.get("sequence_truncated") and label != "CANDIDATE":
        text += (
            f"\n    note: call sequence truncated after {len(record['sequence'])} calls;"
            " no cache size or hit rate is estimated."
        )
    return wasted, text

class Tracer:
    def __init__(self, root=None):
        self.root = clean(root or os.getcwd())
        self.own_folder = clean(OWN_FOLDER)
        # (file, function name, first line) -> what we know about it
        self.records = {}
        # running function id -> (its record, its input label, start time)
        self._open = {}
        # code object -> True/False: is this the user's code? Decided once, then remembered.
        self._decisions = {}
        self.observation_errors = 0
        self._observer_time = 0.0
        self._event_time = 0.0
        self._retained_bytes = 0
        self.memory_limited = False
        self._running = False
        self._owner_thread = None
        self._previous_profile = None
        self._installed_hook = self._hook

    def start(self):
        owner = threading.get_ident()
        if self._running:
            if owner != self._owner_thread:
                raise RuntimeError("this tracer is already active in another thread")
            if sys.getprofile() is not self._installed_hook:
                raise RuntimeError("this tracer's hook was replaced; stop it before restarting")
            return self
        previous = sys.getprofile()
        if isinstance(getattr(previous, "__self__", None), Tracer):
            raise RuntimeError("another Tracer is already active in this thread")
        if previous is not None and not callable(previous):
            raise RuntimeError("a native profiler is active; stop it before starting Tracer")
        self._previous_profile = previous
        self._owner_thread = owner
        sys.setprofile(self._installed_hook)
        self._running = True
        return self

    def stop(self):
        if not self._running:
            return
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("stop the tracer in the thread where it was started")
        if sys.getprofile() is self._installed_hook:
            sys.setprofile(self._previous_profile)
        else:
            self.observation_errors += 1
        for entry in self._open.values():
            record = entry[0]
            record["incomplete"] += 1
            record["active"] -= 1
        self._open.clear()
        self._running = False
        self._owner_thread = self._previous_profile = None

    def __enter__(self):
        if self._running:
            raise RuntimeError("tracing contexts cannot be nested on the same tracer")
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()


    def _is_user_code(self, code):
        if code.co_flags & SKIP_FLAGS:
            return False
        
        filename = code.co_filename
        if filename.startswith("<"):
            return False
        if code.co_name.startswith("<"):
            return False
        path = clean(filename)
        if is_under(path, self.own_folder):
            return False
        if any(part in ("site-packages", "dist-packages") for part in path.split(os.sep)):
            return False
        return is_under(path, self.root)

    def _hook(self, frame, event, arg):
        entered = time.perf_counter()
        self._event_time = entered - self._observer_time
        try:
            if event == "call":
                self._on_call(frame)
            elif event == "return":
                self._on_return(frame, arg)
        except Exception:
            # Observing a program must never introduce an ordinary exception in it.
            self.observation_errors += 1
            entry = self._open.pop(id(frame), None)
            if entry is not None:
                entry[0]["active"] -= 1
        finally:
            self._observer_time += time.perf_counter() - entered

    def _on_call(self, frame):
        code = frame.f_code
        decision = self._decisions.get(code)
        if decision is None:
            decision = self._is_user_code(code)
            if len(self._decisions) < MAX_DECISIONS:
                self._decisions[code] = decision
        if not decision:
            return
        if len(self._open) >= MAX_OPEN_FRAMES:
            self.memory_limited = True
            return

        count = code.co_argcount + code.co_kwonlyargcount
        if code.co_flags & inspect.CO_VARARGS:
            count += 1
        if code.co_flags & inspect.CO_VARKEYWORDS:
            count += 1
        names = code.co_varnames[:count]
        args = tuple(frame.f_locals[name] for name in names)
        captured = tuple(frame.f_locals.get(name, UNKNOWN) for name in code.co_freevars)
        label = fingerprint(args + captured)
        cache_key = normalized_cache_key(code, args) if label is not UNKNOWN else None

        key = (code.co_filename, code.co_name, code.co_firstlineno)
        record = self.records.get(key)
        if record is None:
            if len(self.records) >= MAX_FUNCTIONS or self._retained_bytes + 1024 > MAX_TOTAL_BYTES:
                self.memory_limited = True
                return
            record = {
                "calls": 0,
                "unknown": 0,
                "inputs": {},
                "results": {},
                "time": 0.0,
                "varied": False,
                "sequence": [],
                "unhashable": 0,
                "cache_keys": {},
                "cache_conflict": False,
                "unknown_results": 0,
                "mutable_results": 0,
                "successful": 0,
                "failed": 0,
                "failed_time": 0.0,
                "incomplete": 0,
                "recursive": False,
                "active": 0,
                "input_times": {},
                "repeat_time": 0.0,
                "input_ids": {},
                "retained_bytes": 0,
                "limited": False,
                "sequence_truncated": False,
                "contextual": bool(code.co_freevars or code.co_flags & inspect.CO_NESTED),
            }
            self._reserve(record, 1024)
            self.records[key] = record
        record["calls"] += 1
        outermost = record["active"] == 0
        record["recursive"] |= not outermost
        record["active"] += 1

        # This logical clock excludes callback work, including child callbacks.
        self._open[id(frame)] = (record, label, self._event_time, cache_key, outermost)

    def _reserve(self, record, size):
        if (
            record["retained_bytes"] + size > MAX_RECORD_BYTES
            or self._retained_bytes + size > MAX_TOTAL_BYTES
        ):
            record["limited"] = self.memory_limited = True
            return False
        record["retained_bytes"] += size
        self._retained_bytes += size
        return True

    def _on_return(self, frame, returned_value):
        entry = self._open.pop(id(frame), None)
        if entry is None:  # a function we chose not to record
            return
        record, label, started, cache_key, outermost = entry
        record["active"] -= 1
        elapsed = max(0.0, self._event_time - started)
        if outermost:
            record["time"] += elapsed

        # Exceptional exits also report arg=None. An actual return instruction
        # establishes that None was returned successfully.
        offset = frame.f_lasti
        if returned_value is None and (
            offset < 0 or not dis.opname[frame.f_code.co_code[offset]].startswith("RETURN_")
        ):
            record["failed"] += 1
            if outermost:
                record["failed_time"] += elapsed
            return

        record["successful"] += 1

        if label is UNKNOWN:  # inputs we cannot compare, so results are not comparable either
            record["unknown"] += 1
            return
        input_id = record["input_ids"].get(label)
        if input_id is None:
            if len(record["inputs"]) >= MAX_INPUTS or not self._reserve(
                record, 2 * fingerprint_size(label) + 512
            ):
                record["limited"] = self.memory_limited = True
                record["unknown"] += 1
                return
            input_id = len(record["input_ids"])
            record["input_ids"][label] = input_id
        previous_calls = record["inputs"].get(label, 0)
        record["inputs"][label] = previous_calls + 1
        record["input_times"][label] = record["input_times"].get(label, 0.0) + elapsed
        if previous_calls and not record["recursive"]:
            record["repeat_time"] += elapsed
        if len(record["sequence"]) >= MAX_SEQUENCE or not self._reserve(record, 16):
            record["sequence_truncated"] = True
        else:
            record["sequence"].append(input_id)
        if not is_hashable(cache_key):
            record["unhashable"] += 1
        elif not record["contextual"]:
            first_id = record["cache_keys"].setdefault(cache_key, input_id)
            if first_id != input_id:
                record["cache_conflict"] = True
        result_label = fingerprint(returned_value)
        if result_label is UNKNOWN:
            record["unknown_results"] += 1
            return
        if not immutable_label(result_label):
            record["mutable_results"] += 1
        # Remember the first result for this input. If a later one differs, flag it.
        if label not in record["results"]:
            if not self._reserve(record, fingerprint_size(result_label) + 128):
                record["unknown_results"] += 1
                return
            record["results"][label] = result_label
        first = record["results"][label]
        if first != result_label:
            record["varied"] = True

    def report(self, min_calls=2):
        rows = []
        for (filename, name, line), record in self.records.items():
            if record["calls"] >= min_calls:
                if self.observation_errors:
                    record = dict(record, observation_errors=self.observation_errors)
                rows.append(format_row(filename, name, line, record))

        if self.observation_errors:
            print(f"Warning: {self.observation_errors} observation(s) failed; advice is incomplete.")
        if self.memory_limited:
            print("Warning: observation memory limits were reached; some data was omitted.")

        if not rows:
            print("No function was called often enough to report.")
            return
        rows.sort(reverse=True)  # biggest wasted time first
        for _, text in rows:
            print(text)
        print()
        print("Counts group normalized argument values, not the original call spelling.")
        print("Times are elapsed under profiling, with callback work and recursive overlap excluded.")
        print("Function times include callees; times and repeated work across rows are not additive.")
        print("Based on this one run. Advice, not a guarantee: review before caching.")
