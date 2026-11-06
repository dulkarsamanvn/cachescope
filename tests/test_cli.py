import subprocess
import sys

import pytest

SCRIPT = """
import sys

def square(n):
    return n * n

for i in range(10):
    square(i % 3)
print("args:", sys.argv[1:])
"""


def run_cli(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "cachescope", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_run_script_prints_report(tmp_path):
    (tmp_path / "app.py").write_text(SCRIPT)
    result = run_cli("run", "app.py", cwd=tmp_path)
    assert result.returncode == 0
    assert "10 calls, 3 unique, 7 repeats" in result.stdout


def test_script_gets_its_own_arguments(tmp_path):
    (tmp_path / "app.py").write_text(SCRIPT)
    result = run_cli("run", "app.py", "hello", "--flag", cwd=tmp_path)
    assert "args: ['hello', '--flag']" in result.stdout


def test_exit_code_of_script_is_kept(tmp_path):
    (tmp_path / "bad.py").write_text("import sys\nsys.exit(3)\n")
    assert run_cli("run", "bad.py", cwd=tmp_path).returncode == 3


def test_missing_script_is_a_clear_error(tmp_path):
    result = run_cli("run", "nope.py", cwd=tmp_path)
    assert result.returncode == 2
    assert "cannot find script" in result.stderr


def test_crashing_script_still_prints_report(tmp_path):
    (tmp_path / "crash.py").write_text(SCRIPT + "\nraise RuntimeError('boom')\n")
    result = run_cli("run", "crash.py", cwd=tmp_path)
    assert result.returncode == 1
    assert "boom" in result.stderr
    assert "10 calls" in result.stdout


@pytest.mark.parametrize("expression, expected", [("'explanation'", 1), ("None", 0)])
def test_text_and_none_exit_values_keep_normal_behavior(tmp_path, expression, expected):
    (tmp_path / "exit.py").write_text(f"raise SystemExit({expression})\n")
    result = run_cli("run", "exit.py", cwd=tmp_path)
    assert result.returncode == expected
    assert result.stderr == ("explanation\n" if expected else "")


def test_direct_main_restores_argv_and_path_objects_and_contents(tmp_path, capsys):
    from cachescope.cli import main

    path = tmp_path / "state.py"
    path.write_text(
        "import sys\nsys.argv.append('changed')\nsys.path.append('changed')\n"
        "sys.argv = ['rebound']\nsys.path = ['rebound']\n"
        "raise SystemExit('explanation')\n"
    )
    argv, import_path, values = sys.argv, sys.path, sys.path[:]
    assert main(["run", str(path)]) == 1
    assert sys.argv is argv
    assert sys.path is import_path
    assert sys.path == values
    assert capsys.readouterr().err == "explanation\n"


def test_startup_failure_also_restores_process_state(tmp_path, monkeypatch, capsys):
    import cachescope.cli as cli

    path = tmp_path / "unused.py"
    path.write_text("raise AssertionError('must not execute')\n")
    argv, import_path, values = sys.argv, sys.path, sys.path[:]

    def fail_start(self):
        raise RuntimeError("profile conflict")

    monkeypatch.setattr(cli.Tracer, "start", fail_start)
    assert cli.main(["run", str(path)]) == 1
    assert sys.argv is argv
    assert sys.path is import_path
    assert sys.path == values
    assert "profile conflict" in capsys.readouterr().err


def test_explicit_root_can_include_code_outside_the_script_folder(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / "helper.py").write_text(SCRIPT)
    (scripts / "main.py").write_text("import runpy\nrunpy.run_path('helper.py')\n")
    default = run_cli("run", "scripts/main.py", cwd=tmp_path)
    explicit = run_cli("run", "--root", str(tmp_path), "scripts/main.py", cwd=tmp_path)
    assert "10 calls, 3 unique, 7 repeats" not in default.stdout
    assert explicit.returncode == 0
    assert "10 calls, 3 unique, 7 repeats" in explicit.stdout


def test_root_option_after_script_is_a_script_argument(tmp_path):
    (tmp_path / "app.py").write_text(SCRIPT)
    result = run_cli("run", "app.py", "--root", "literal", cwd=tmp_path)
    assert "args: ['--root', 'literal']" in result.stdout


def test_missing_root_and_help(tmp_path):
    result = run_cli("run", "--root", "missing", "app.py", cwd=tmp_path)
    assert result.returncode == 2
    assert "root directory" in result.stderr
    help_result = run_cli("--help", cwd=tmp_path)
    assert help_result.returncode == 0
    assert "--root DIRECTORY" in help_result.stdout


@pytest.mark.parametrize("options", [("--threads", "--root", "."), ("--root", ".", "--threads")])
def test_worker_threads_option_and_root_in_either_order(tmp_path, options):
    (tmp_path / "workers.py").write_text(
        "import threading\n"
        "def square(n):\n    return n * n\n"
        "def work():\n    for _ in range(3):\n        square(2)\n"
        "worker = threading.Thread(target=work)\nworker.start()\nworker.join()\n"
    )
    result = run_cli("run", *options, "workers.py", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "3 calls, 1 unique, 2 repeats" in result.stdout


def test_threads_option_after_script_is_forwarded(tmp_path):
    (tmp_path / "app.py").write_text(SCRIPT)
    result = run_cli("run", "app.py", "--threads", cwd=tmp_path)
    assert result.returncode == 0
    assert "args: ['--threads']" in result.stdout


def test_objects_option_reports_method_inputs(tmp_path):
    (tmp_path / "objects.py").write_text(
        "class Customer:\n"
        "    def __init__(self):\n        self.balance = 10\n"
        "    def total(self, n):\n        return self.balance + n\n"
        "customer = Customer()\ncustomer.total(2)\ncustomer.total(2)\n"
    )
    result = run_cli("run", "--objects", "objects.py", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "[MAYBE] total" in result.stdout
    assert "2 calls, 1 unique, 1 repeats" in result.stdout
    assert "custom objects compared by identity" in result.stdout
