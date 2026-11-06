"""Run outside-checkout checks on an installed release in a fresh environment."""

import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile

import cachescope


def main():
    version = os.environ["EXPECTED_VERSION"]
    assert importlib.metadata.version("cachescope") == version
    assert "site-packages" in Path(cachescope.__file__).parts, cachescope.__file__
    console = Path(sysconfig.get_path("scripts")) / ("cachescope.exe" if os.name == "nt" else "cachescope")
    with tempfile.TemporaryDirectory(prefix="cachescope-smoke-") as temporary:
        for command in ([str(console), "--help"], [sys.executable, "-m", "cachescope", "--help"]):
            result = subprocess.run(command, cwd=temporary, check=True, capture_output=True, text=True)
            assert "usage: cachescope run" in result.stdout
        script = Path(temporary) / "app.py"
        script.write_text(
            "import sys\nimport time\n"
            "def slow(value):\n    time.sleep(0.002)\n    return value * 2\n"
            "for _ in range(8):\n    slow(3)\n"
            "print('script args:', sys.argv[1:])\n", encoding="utf-8"
        )
        result = subprocess.run(
            [str(console), "run", str(script), "release-check", "--flag"],
            cwd=temporary, check=True, capture_output=True, text=True,
        )
        assert "8 calls, 1 unique, 7 repeats" in result.stdout, result.stdout
        assert "[CANDIDATE] slow" in result.stdout, result.stdout
        assert "script args: ['release-check', '--flag']" in result.stdout, result.stdout
        script.write_text(
            "import asyncio\nfrom concurrent.futures import ThreadPoolExecutor\n"
            "class Customer:\n"
            "    def __init__(self):\n        self.balance = 10\n"
            "    def total(self, n):\n        return self.balance + n\n"
            "def worker(n):\n    return n * 2\n"
            "def numbers(n):\n    for i in range(n):\n        yield i\n"
            "async def doubled(n):\n    await asyncio.sleep(0)\n    return n * 2\n"
            "async def main():\n"
            "    customer = Customer()\n"
            "    for _ in range(3):\n"
            "        assert customer.total(2) == 12\n"
            "        assert list(numbers(2)) == [0, 1]\n"
            "        assert await doubled(3) == 6\n"
            "    with ThreadPoolExecutor(max_workers=1) as pool:\n"
            "        assert list(pool.map(worker, [3] * 4)) == [6] * 4\n"
            "asyncio.run(main())\n", encoding="utf-8"
        )
        result = subprocess.run(
            [str(console), "run", "--threads", "--objects", str(script)],
            cwd=temporary, check=True, capture_output=True, text=True,
        )
        for expected in (
            "[MAYBE] total", "[MAYBE] numbers", "[MAYBE] doubled",
            "4 calls, 1 unique, 3 repeats", "generator; 6 suspension(s)",
            "coroutine; 3 suspension(s)", "custom objects compared by identity",
        ):
            assert expected in result.stdout, result.stdout
        assert "Warning:" not in result.stdout, result.stdout
    print(f"Installed release {version}: import, CLI, arguments, workers, resumables and objects passed")


if __name__ == "__main__":
    main()
