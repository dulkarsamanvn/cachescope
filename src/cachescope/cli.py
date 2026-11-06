"""The command line: cachescope run script.py"""

import os
import runpy
import sys
import traceback

from .tracer import Tracer

USAGE = "usage: cachescope run [--root DIRECTORY] [--threads] [--objects] script.py [script arguments]"


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)

    if args in (["--help"], ["-h"], ["run", "--help"]):
        print(USAGE)
        return 0
    if not args or args[0] != "run":
        print(USAGE, file=sys.stderr)
        return 2

    remaining, root, threads, objects = args[1:], None, False, False
    while remaining and remaining[0] in ("--root", "--threads", "--objects"):
        if remaining[0] in ("--threads", "--objects"):
            if remaining[0] == "--threads":
                threads = True
            else:
                objects = True
            remaining = remaining[1:]
            continue
        if len(remaining) < 3:
            print(USAGE, file=sys.stderr)
            return 2
        root, remaining = remaining[1], remaining[2:]
        if not os.path.isdir(root):
            print(f"cachescope: cannot find root directory: {root}", file=sys.stderr)
            return 2
    if remaining[:1] == ["--"]:
        remaining = remaining[1:]
    if not remaining:
        print(USAGE, file=sys.stderr)
        return 2
    script, script_args = remaining[0], remaining[1:]

    if not os.path.isfile(script):
        print(f"cachescope: cannot find script: {script}", file=sys.stderr)
        return 2

    script_path = os.path.abspath(script)
    script_folder = os.path.dirname(script_path)

    tracer = Tracer(root=root or script_folder, threads=threads, objects=objects)
    exit_code = 0
    previous_argv, previous_path = sys.argv, sys.path
    previous_path_values = previous_path[:]

    try:
        # Make the script believe it was started normally, then restore the host.
        sys.argv = [script, *script_args]
        sys.path = [script_folder, *previous_path_values]
        tracer.start()
        runpy.run_path(script_path, run_name="__main__")
    except SystemExit as exc:  # the script called exit() or sys.exit()
        code = exc.code
        exit_code = code if isinstance(code, int) else (0 if code is None else 1)
        if code is not None and not isinstance(code, int):
            print(code, file=sys.stderr)
    except Exception:  # the script crashed: show the error, but still give the report
        traceback.print_exc()
        exit_code = 1
    finally:
        try:
            tracer.stop()
        finally:
            sys.argv, sys.path = previous_argv, previous_path
            previous_path[:] = previous_path_values

    print()
    tracer.report()
    return exit_code
