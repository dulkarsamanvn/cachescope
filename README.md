# cachescope

Find which Python functions repeat slow work.

Install the alpha release from PyPI:

```console
python -m pip install --pre cachescope
```

The worker-thread and object options, plus resumable-function analysis, are
unreleased additions. Install this checkout to try them:

```console
python -m pip install .
cachescope run app.py [script arguments]
python -m cachescope run --root . scripts/app.py [script arguments]
python -m cachescope run --threads app.py [script arguments]
python -m cachescope run --threads --objects examples/capabilities.py
```

`--root` selects the directory whose functions are eligible for analysis. It
defaults to the script's directory and does not change Python's import paths.
Place this option before the script name; options after the script are passed
to the script. Use `cachescope --help` for the command syntax.

The report sorts functions by observed repeated work:

- **CANDIDATE**: repeated successful calls were slow enough to investigate,
  their comparable results were stable, and the checks found no known risk.
- **MAYBE**: evidence is incomplete or caching needs a human check, such as
  unknown/mutable results, factory-created functions, captured state, detected
  effects, or observation limits.
- **UNSAFE**: comparable calls with the same observed input returned different
  results, without ambiguous closure context.
- **SKIP**: too few useful repeats or too little repeated work was observed.

“Repeat work” is the measured time of repeated successful evaluations, not a
promise of elapsed time saved. Times subtract the current thread's profiling-callback work
and recursive overlap, but interpreter profiling overhead remains. Function
times include callees, so rows are not additive. Recursion receives no independent
cache savings estimate. Call-heavy programs can slow down substantially.

Size estimates model normalized argument values. Python has already bound
defaults and positional/keyword arguments when these values are observed.
Consequently, `f()`, `f(1)`, and `f(n=1)` can look identical here while occupying
different real cache entries. Estimates assume consistent call spelling; verify
the decorated function with `cache_info()` on a representative workload.
The report does not prescribe an `@lru_cache` decorator. Observed numeric-type
key collisions block size advice, and `typed=True` does not distinguish types
inside tuple arguments.

The starting thread is observed by default. `--threads` (or
`Tracer(threads=True)`) also observes workers started through `threading` after
tracing begins, including newly created thread-pool workers. Existing workers
and child processes are outside its scope. Join workers before tracing stops
for complete observations. Concurrent calls are distinguished from recursion;
overlapping calls receive no independent cache savings or capacity estimate.
Worker hooks restore their previous profiler on their next event after stop.
Generators, coroutines, and async generators are recorded as logical executions:
resuming after `yield` or `await` does not count as a new call. Their times sum
active execution segments and exclude suspended time. Coroutines compare their
completed return values; generator streams are not compared as reusable results.
These functions receive a review verdict and no cache savings/capacity estimate:
coroutines require an async-aware result cache, and generators require an explicit
strategy for materializing/replaying values. Cancellation, early close, and
unfinished executions are excluded from successful-input estimates. Some implicit
finalization paths emit no completion event and remain incomplete until stop.
Suspended frames count toward the open-frame limit and may retain local objects.

Comparisons support exact built-in int,
float, bool, str, bytes, None, list, tuple, and dict values, preserving dictionary
order. `--objects` (or `Tracer(objects=True)`) additionally snapshots
weak-referenceable instances with ordinary instance dictionaries or slots,
including dataclasses and method `self` arguments. Observations include instance
identity and visible stored attributes; different instances remain distinct and
changes to stored state produce different inputs. Properties, user attribute
lookup, `__eq__`, `__hash__`, and `__repr__` are not invoked. Object observations
always require review and receive no cache-capacity estimate: visible state does
not establish real cache-key equivalence, immutability, or all external state.
Instances without weak-reference support, opaque values, unsupported attributes,
cycles, and snapshots exceeding the value/identity budgets remain unknown.
Mutable return values require review because caching would share them. Failed
and unfinished calls are excluded from cache-hit and repeated-work estimates.

Observation is bounded: values have depth, item, and byte budgets; each function
retains at most 4,096 distinct inputs and 100,000 sequence entries. Retention is
budgeted at approximately 4 MiB per function and 32 MiB across records, with a
function-count limit as well. These are accounting budgets, not a process RSS
limit; active snapshots and interpreter overhead also use memory. Reaching a
retention limit is reported. Truncated sequences receive no size/hit-rate estimate.
Object identity tracking uses weak references and retains at most 4,096 live
identities. Unsupported or over-budget snapshots are recorded as unknown.

Source checks recognize common risky calls, import aliases, direct helper calls,
external-variable reads, and argument mutations. They are a heuristic: dynamic
dispatch, imported helper implementations, and changes in external state can
escape detection. A CANDIDATE is evidence from one run, not a proof of purity.

For programmatic use, `Tracer` supports a context manager and restores a prior
Python profile hook when stopped. That prior hook is suspended during tracing.
Nested tracers and incompatible native hooks are rejected; independent
`sys.monitoring` tools can coexist. Start and stop a tracer in the same thread.

To develop and check distributions:

```console
python -m pip install -e ".[dev]"
python -m pytest
python -m build
python -m twine check dist/*
```

CI runs the suite on Python 3.9 through 3.14 on Linux and Windows, and checks
the built wheel and source distribution. The Linux Python 3.9 job uses Ubuntu
22.04, which has a supported `setup-python` binary for that version.

Maintainers: see the [release guide](https://github.com/dulkarsamanvn/cachescope/blob/main/RELEASING.md)
for the TestPyPI rehearsal and Trusted Publishing setup.
