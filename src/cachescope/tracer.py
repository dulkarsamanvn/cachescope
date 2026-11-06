"""Watch function calls and record what they were given and returned."""
import dis
import inspect
import os
import sys
import threading
import time
import types
from dataclasses import dataclass

from .fingerprint import (
    UNKNOWN, ObjectFingerprinter, contains_objects, fingerprint,
    fingerprint_size, immutable_label,
)
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
_PROFILE_LOCK = threading.RLock()


def thread_profile():
    # getprofile() was added in 3.10; 3.9 stores the same setting here.
    getter = getattr(threading, "getprofile", None)
    return getter() if getter else threading._profile_hook


def tracer_for_hook(hook):
    owner = getattr(hook, "__self__", None)
    return owner if isinstance(owner, Tracer) else getattr(hook, "_cachescope_owner", None)


class _ThreadState:
    def __init__(self, session):
        self.session = session
        self.observer_time = 0.0
        self.event_time = 0.0
        self.active = {}
        self.closures = {}


@dataclass
class _Call:
    record: dict
    label: object
    started: float
    cache_key: object
    outermost: bool
    state: _ThreadState
    frame: object = None
    elapsed: float = 0.0
    accounted: float = 0.0
    suspended: bool = False
    closing: bool = False
    suspensions: int = 0


def execution_kind(code):
    if code.co_flags & inspect.CO_ASYNC_GENERATOR:
        return "async generator"
    if code.co_flags & (inspect.CO_COROUTINE | inspect.CO_ITERABLE_COROUTINE):
        return "coroutine"
    if code.co_flags & inspect.CO_GENERATOR:
        return "generator"
    return None


def exit_instruction(frame):
    offset, bytecode = frame.f_lasti, frame.f_code.co_code
    if offset < 0:
        return ""
    name = dis.opname[bytecode[offset]]
    # CPython 3.9/3.10 backs up before YIELD_FROM when suspending.
    if offset + 2 < len(bytecode) and dis.opname[bytecode[offset + 2]] == "YIELD_FROM":
        return "YIELD_FROM"
    if name == "YIELD_FROM":
        # A normal suspension rewinds before this opcode. Remaining on the
        # instruction means an exception propagated through the delegation.
        return "RAISE_FROM"
    # CPython 3.13+ can report the RESUME immediately after a yield.
    if name == "RESUME" and offset >= 2:
        previous = dis.opname[bytecode[offset - 2]]
        if previous in ("YIELD_VALUE", "YIELD_FROM"):
            return previous
    return name


def resumed_before_observation(frame):
    offset = frame.f_lasti
    if offset < 0:  # first entry on older CPython
        return False
    code = frame.f_code.co_code
    return not (dis.opname[code[offset]] == "RESUME" and code[offset + 1] == 0)

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
    if record.get("kind"):
        return "\n    note: resumable functions need an explicit strategy for caching completed values; no size is estimated."
    if record.get("custom_inputs") or record.get("custom_results"):
        return "\n    note: custom object identity and visible state require review; no size is estimated."
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
    if record.get("concurrent"):
        return "\n    note: concurrent calls can compute the same input before a cache entry exists; no size is estimated."
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
    if record.get("concurrent"):
        text += "\n    note: calls overlap across threads; cache savings are not estimated."
    if record.get("kind"):
        text += (
            f"\n    note: {record['kind']}; {record['suspensions']} suspension(s),"
            " suspended time excluded; no cache savings or size is estimated."
        )
    if record.get("closed"):
        text += f"\n    note: {record['closed']} execution(s) were closed before completion."
    if record.get("partial_start"):
        text += "\n    note: some executions began before observation; their initial inputs and earlier time are unknown."
    if record.get("custom_inputs") or record.get("custom_results"):
        text += "\n    note: custom objects compared by identity and visible stored state; cache-key equivalence requires review."
    if record.get("sequence_truncated") and label != "CANDIDATE":
        text += (
            f"\n    note: call sequence truncated after {len(record['sequence'])} calls;"
            " no cache size or hit rate is estimated."
        )
    return wasted, text

class Tracer:
    def __init__(self, root=None, *, threads=False, objects=False):
        self.root = clean(root or os.getcwd())
        self.own_folder = clean(OWN_FOLDER)
        self.threads = threads
        self._objects = ObjectFingerprinter() if objects else None
        # (file, function name, first line) -> what we know about it
        self.records = {}
        # Frame id -> one logical execution, including suspended frames.
        self._open = {}
        # code object -> True/False: is this the user's code? Decided once, then remembered.
        self._decisions = {}
        self.observation_errors = 0
        self._local = threading.local()
        self._lock = threading.RLock()
        self._session = None
        self._retained_bytes = 0
        self.memory_limited = False
        self._running = False
        self._owner_thread = None
        self._previous_profile = None
        self._installed_hook = self._hook
        self._thread_hook = None
        self._previous_thread_profile = None

    def start(self):
        with _PROFILE_LOCK:
            return self._start()

    def _start(self):
        owner = threading.get_ident()
        if self._running:
            if owner != self._owner_thread:
                raise RuntimeError("this tracer is already active in another thread")
            if sys.getprofile() is not self._installed_hook:
                raise RuntimeError("this tracer's hook was replaced; stop it before restarting")
            return self
        previous = sys.getprofile()
        if tracer_for_hook(previous) is not None:
            raise RuntimeError("another Tracer is already active in this thread")
        if previous is not None and not callable(previous):
            raise RuntimeError("a native profiler is active; stop it before starting Tracer")
        previous_threads = thread_profile() if self.threads else None
        if tracer_for_hook(previous_threads) is not None:
            raise RuntimeError("another Tracer already owns worker-thread profiling")
        if previous_threads is not None and not callable(previous_threads):
            raise RuntimeError("an incompatible worker-thread profiler is active")
        self._previous_profile, self._previous_thread_profile = previous, previous_threads
        self._owner_thread, self._session = owner, object()
        session = self._session

        def worker_hook(frame, event, arg):
            if not self._running or self._session is not session:
                # Older Python versions cannot uninstall another thread's hook.
                # A live worker restores its prior hook on its next event.
                sys.setprofile(previous_threads)
                if previous_threads is not None:
                    previous_threads(frame, event, arg)
                return
            self._hook(frame, event, arg)

        worker_hook._cachescope_owner = self
        self._thread_hook = worker_hook
        self._running = True
        try:
            if self.threads:
                threading.setprofile(worker_hook)
            sys.setprofile(self._installed_hook)
        except BaseException:
            self._running = False
            if sys.getprofile() is self._installed_hook:
                sys.setprofile(previous)
            if self.threads and thread_profile() is worker_hook:
                threading.setprofile(previous_threads)
            self._owner_thread = self._previous_profile = None
            raise
        return self

    def stop(self):
        with _PROFILE_LOCK:
            self._stop()

    def _stop(self):
        if not self._running:
            return
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("stop the tracer in the thread where it was started")
        with self._lock:
            self._running = False
            if sys.getprofile() is self._installed_hook:
                sys.setprofile(self._previous_profile)
            else:
                self.observation_errors += 1
            if self.threads:
                if thread_profile() is self._thread_hook:
                    threading.setprofile(self._previous_thread_profile)
                else:
                    self.observation_errors += 1
            for entry in self._open.values():
                record, state = entry.record, entry.state
                record["incomplete"] += 1
                if not entry.suspended:
                    self._deactivate(entry)
                state.closures.clear()
            self._open.clear()
            self._owner_thread = self._previous_profile = None

    def __enter__(self):
        if self._running:
            raise RuntimeError("tracing contexts cannot be nested on the same tracer")
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()


    def _is_user_code(self, code):
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
        with self._lock:
            if not self._running:
                return
            state = getattr(self._local, "state", None)
            if state is None or state.session is not self._session:
                state = self._local.state = _ThreadState(self._session)
            state.event_time = entered - state.observer_time
            try:
                if event == "call":
                    self._on_call(frame)
                elif event == "return":
                    self._on_return(frame, arg)
                elif event in ("c_call", "c_return", "c_exception"):
                    self._on_c_event(event, arg)
            except Exception:
                # Observing a program must never introduce an ordinary exception in it.
                self.observation_errors += 1
                entry = self._open.pop(id(frame), None)
                if entry is not None and not entry.suspended:
                    self._deactivate(entry)
            finally:
                state.observer_time += time.perf_counter() - entered

    def _on_call(self, frame):
        code = frame.f_code
        decision = self._decisions.get(code)
        if decision is None:
            decision = self._is_user_code(code)
            if len(self._decisions) < MAX_DECISIONS:
                self._decisions[code] = decision
        if not decision:
            return
        entry = self._open.get(id(frame))
        if entry is not None and entry.suspended:
            entry.state = self._local.state
            entry.started = entry.state.event_time
            self._activate(entry)
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
        label = fingerprint(args + captured, objects=self._objects)
        custom_inputs = self._objects is not None and contains_objects(label)
        kind = execution_kind(code)
        partial_start = kind is not None and resumed_before_observation(frame)
        if partial_start:
            label = UNKNOWN
        cache_key = UNKNOWN
        if label is not UNKNOWN and kind is None and not custom_inputs:
            cache_key = normalized_cache_key(code, args)

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
                "custom_inputs": False,
                "custom_results": False,
                "successful": 0,
                "failed": 0,
                "failed_time": 0.0,
                "incomplete": 0,
                "closed": 0,
                "kind": kind,
                "suspensions": 0,
                "partial_start": False,
                "recursive": False,
                "concurrent": False,
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
        record["partial_start"] |= partial_start
        record["custom_inputs"] |= custom_inputs
        entry = _Call(record, label, self._local.state.event_time, cache_key, True, self._local.state)
        # A bounded frame reference prevents a closed generator's frame id from
        # being reused as if it were a resume when finalization emits no event.
        if kind is not None:
            entry.frame = frame
        self._activate(entry)
        self._open[id(frame)] = entry

    def _activate(self, entry):
        record, state = entry.record, entry.state
        local_active = state.active.get(id(record), 0)
        entry.outermost = local_active == 0
        record["recursive"] |= not entry.outermost
        record["concurrent"] |= record["active"] > local_active
        record["active"] += 1
        state.active[id(record)] = local_active + 1
        entry.suspended = False

    def _deactivate(self, entry):
        if entry.suspended:
            return
        entry.record["active"] -= 1
        entry.state.active[id(entry.record)] -= 1
        entry.suspended = True

    def _on_c_event(self, event, method):
        # Explicit close/throw can finish at a yield instruction or emit no
        # Python return event. Inspect only exact built-in resumable objects.
        if type(method) is not types.BuiltinMethodType or method.__name__ not in ("close", "throw", "send"):
            return
        owner = method.__self__
        attribute = {types.GeneratorType: "gi_frame", types.CoroutineType: "cr_frame"}.get(type(owner))
        if attribute is None:
            return
        state = self._local.state
        if event == "c_call":
            frame = getattr(owner, attribute)
            entry = self._open.get(id(frame))
            if entry is not None:
                state.closures[id(method)] = (entry, entry.suspensions)
                entry.closing = method.__name__ == "close"
        else:
            pending = state.closures.pop(id(method), None)
            if pending is None:
                return
            entry, previous_suspensions = pending
            if getattr(owner, attribute) is None:
                if self._open.pop(id(entry.frame), None) is entry:
                    # An exception at a yield can initially resemble a pause.
                    entry.record["suspensions"] -= entry.suspensions - previous_suspensions
                    if not entry.suspended:
                        self._deactivate(entry)
                    if method.__name__ == "close":
                        entry.record["closed"] += 1
                    else:
                        entry.record["failed"] += 1
                        entry.record["time"] += entry.accounted
                        entry.record["failed_time"] += entry.accounted
            else:
                entry.closing = False  # close may fail if the generator yields again

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
        entry = self._open.get(id(frame))
        if entry is None:  # a function we chose not to record
            return
        record, label, cache_key = entry.record, entry.label, entry.cache_key
        elapsed = max(0.0, entry.state.event_time - entry.started)
        entry.elapsed += elapsed
        if entry.outermost:
            entry.accounted += elapsed
        self._deactivate(entry)
        instruction = exit_instruction(frame)
        if record["kind"] and instruction in ("YIELD_VALUE", "YIELD_FROM"):
            entry.suspended = True
            record["suspensions"] += 1
            entry.suspensions += 1
            return
        self._open.pop(id(frame))
        if entry.closing:
            record["closed"] += 1
            return
        record["time"] += entry.accounted
        elapsed = entry.elapsed

        # Exceptional exits also report arg=None. An actual return instruction
        # establishes that None was returned successfully.
        if returned_value is None and not instruction.startswith("RETURN_"):
            record["failed"] += 1
            record["failed_time"] += entry.accounted
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
        if record["kind"] is None and cache_key is not UNKNOWN:
            if not is_hashable(cache_key):
                record["unhashable"] += 1
            elif not record["contextual"]:
                first_id = record["cache_keys"].setdefault(cache_key, input_id)
                if first_id != input_id:
                    record["cache_conflict"] = True
        if record["kind"] in ("generator", "async generator"):
            # A generator's final return is not the stream it yielded. send()
            # and throw() can also affect that stream without being arguments.
            record["unknown_results"] += 1
            return
        result_label = fingerprint(returned_value, objects=self._objects)
        if result_label is UNKNOWN:
            record["unknown_results"] += 1
            return
        if not immutable_label(result_label):
            record["mutable_results"] += 1
        if self._objects is not None:
            record["custom_results"] |= contains_objects(result_label)
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
        with self._lock:
            self._report(min_calls)

    def _report(self, min_calls):
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
        print("Times are elapsed under profiling, with per-thread callback work and recursive overlap excluded.")
        print("Function times include callees; times and repeated work across rows are not additive.")
        if self.threads:
            print("Worker threads started during tracing are included; thread times can overlap.")
        print("Based on this one run. Advice, not a guarantee: review before caching.")
