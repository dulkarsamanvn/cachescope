"""The command line: cachescope run script.py"""

import os
import runpy
import sys
import traceback

from .tracer import Tracer


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)

    if len(args) < 2 or args[0] != "run":
        print("usage: cachescope run script.py [script arguments]", file=sys.stderr)
        return 2

    script = args[1]
    script_args = args[2:]

    if not os.path.isfile(script):
        print(f"cachescope: cannot find script: {script}", file=sys.stderr)
        return 2

    script_path = os.path.abspath(script)
    script_folder = os.path.dirname(script_path)

    # Make the script believe it was started normally.
    sys.argv = [script, *script_args]
    sys.path.insert(0, script_folder)

    tracer = Tracer(root=script_folder)
    exit_code = 0

    tracer.start()
    try:
        runpy.run_path(script_path, run_name="__main__")
    except SystemExit as exc:  # the script called exit() or sys.exit()
        code = exc.code
        exit_code = code if isinstance(code, int) else (0 if code is None else 1)
    except Exception:  # the script crashed: show the error, but still give the report
        traceback.print_exc()
        exit_code = 1
    finally:
        tracer.stop()  # the camera must ALWAYS be switched off

    print()
    tracer.report()
    return exit_code