"""Read a function's source code and look for signs that caching it could be unsafe."""

import ast
import inspect
import linecache
import textwrap

# Specific calls. A call matches if its name is this, or ends with ".this".
RISKY_CALLS = {
    "time.time": "reads the clock",
    "time.monotonic": "reads the clock",
    "time.perf_counter": "reads the clock",
    "datetime.now": "reads the clock",
    "datetime.utcnow": "reads the clock",
    "datetime.today": "reads the clock",
    "date.today": "reads the clock",
    "uuid.uuid4": "makes random values",
    "os.getenv": "reads environment variables",
    "open": "reads or writes files",
    "input": "waits for user input",
    "print": "prints something (a cached call would not print again)",
}

# Whole modules: any call that starts with this name.
RISKY_MODULES = {
    "random": "uses randomness",
    "requests": "uses the network",
    "urllib": "uses the network",
    "socket": "uses the network",
    "sqlite3": "talks to a database",
    "subprocess": "runs other programs",
}


def dotted_name(node):
    """Turn the tree for `time.time` into the text "time.time" (or None if too complex)."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def reason_for_call(name):
    """Return why a call with this name is risky, or None."""
    for pattern, reason in RISKY_CALLS.items():
        if name == pattern or name.endswith("." + pattern):
            return reason
    first_part = name.split(".")[0]
    return RISKY_MODULES.get(first_part)


def find_risks(source):
    """Return a sorted list of reasons why caching this code might be unsafe."""
    tree = ast.parse(textwrap.dedent(source))
    reasons = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = dotted_name(node.func)
            if name:
                reason = reason_for_call(name)
                if reason:
                    reasons.add(reason)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            reasons.add("changes variables outside itself")
    return sorted(reasons)

def source_of(filename, first_line):
    """Read one function's source code back from its file. None if we cannot."""
    lines = linecache.getlines(filename)
    if not lines or first_line < 1 or first_line > len(lines):
        return None
    try:
        return "".join(inspect.getblock(lines[first_line - 1 :]))
    except Exception:  # best effort: reading source must never crash the user's program
        return None


def risks_of_function(filename, first_line):
    """Danger signs in one function. If we cannot read it, we say so (never "no risks")."""
    source = source_of(filename, first_line)
    if source is None:
        return ["its source code could not be read"]
    try:
        return find_risks(source)
    except SyntaxError:
        return ["its source code could not be understood"]