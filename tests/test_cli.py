import subprocess
import sys

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