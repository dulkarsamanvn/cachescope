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


MUTATING_METHODS = {
    "append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse",
    "update", "setdefault", "popitem", "add", "discard", "difference_update",
    "intersection_update", "symmetric_difference_update", "__setitem__", "__delitem__",
}


class _Bindings(ast.NodeVisitor):
    """Names bound in this scope, without entering another function's body."""

    def __init__(self):
        self.names, self.outside, self.aliases, self.helpers = set(), set(), {}, {}

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node):
        self.names.add(node.name)
        self.helpers[node.name] = node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.names.add(node.name)

    def visit_Lambda(self, node):
        pass

    def visit_Global(self, node):
        self.outside.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_Import(self, node):
        for item in node.names:
            name = item.asname or item.name.split(".")[0]
            self.names.add(name)
            self.aliases[name] = item.name if item.asname else name

    def visit_ImportFrom(self, node):
        for item in node.names:
            name = item.asname or item.name
            self.names.add(name)
            self.aliases[name] = ".".join(part for part in (node.module, item.name) if part)


def _root_name(node):
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


class _Risks(ast.NodeVisitor):
    def __init__(self, aliases, external, helpers):
        self.aliases = dict(aliases)
        self.external = set(external)
        self.helpers = dict(helpers)
        self.module_aliases, self.module_helpers = dict(aliases), dict(helpers)
        self.module_helper_ids = {id(node) for node in helpers.values()}
        self.locals, self.origins, self.seen, self.reasons = set(), {}, set(), set()

    def scan(self, function):
        if id(function) in self.seen:
            return
        if len(self.seen) >= 64:
            self.reasons.add("helper analysis limit was reached")
            return
        self.seen.add(id(function))
        bindings = _Bindings()
        for statement in function.body:
            bindings.visit(statement)
        args = function.args
        parameters = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
        parameters.update(arg.arg for arg in (args.vararg, args.kwarg) if arg is not None)
        saved = self.aliases, self.locals, self.origins, self.helpers
        self.locals = (bindings.names | parameters) - bindings.outside
        module_scope = id(function) in self.module_helper_ids
        self.aliases = dict(self.module_aliases if module_scope else self.aliases)
        self.aliases.update(dict.fromkeys(self.locals))
        origins = {} if module_scope else self.origins
        self.origins = {key: value for key, value in origins.items() if key not in self.locals}
        self.origins.update(dict.fromkeys(parameters, "argument"))
        helpers = self.module_helpers if module_scope else self.helpers
        self.helpers = {key: value for key, value in helpers.items() if key not in self.locals}
        self.helpers.update(bindings.helpers)
        for statement in function.body:
            self.visit(statement)
        self.aliases, self.locals, self.origins, self.helpers = saved

    def resolve(self, name):
        if name is None:
            return None
        first, separator, rest = name.partition(".")
        if first in self.aliases:
            replacement = self.aliases[first]
            return replacement + separator + rest if replacement is not None else None
        return name

    def origin(self, node):
        name = _root_name(node)
        if name in self.origins:
            return self.origins[name]
        if name in self.external and name not in self.locals:
            return "outside"
        return None

    def mutation(self, node):
        origin = self.origin(node)
        if origin:
            self.reasons.add("mutates an argument" if origin == "argument" else "changes variables outside itself")

    def visit_Call(self, node):
        name = self.resolve(dotted_name(node.func))
        reason = reason_for_call(name) if name else None
        if reason:
            self.reasons.add(reason)
        if isinstance(node.func, ast.Attribute) and node.func.attr in MUTATING_METHODS:
            self.mutation(node.func.value)
        if isinstance(node.func, ast.Name) and node.func.id in self.helpers:
            self.scan(self.helpers[node.func.id])
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in self.external and node.id not in self.locals:
            self.reasons.add("reads variables outside itself")

    def visit_Global(self, node):
        self.reasons.add("changes variables outside itself")
        self.origins.update(dict.fromkeys(node.names, "outside"))

    visit_Nonlocal = visit_Global

    def visit_Import(self, node):
        bindings = _Bindings()
        bindings.visit(node)
        self.aliases.update(bindings.aliases)

    visit_ImportFrom = visit_Import

    def visit_Assign(self, node):
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.helpers.pop(target.id, None)
                origin = self.origin(node.value)
                if origin:
                    self.origins[target.id] = origin
                else:
                    self.origins.pop(target.id, None)
                self.aliases[target.id] = self.resolve(dotted_name(node.value))
            elif isinstance(target, (ast.Attribute, ast.Subscript)):
                self.mutation(target)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self.mutation(node.target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if isinstance(node.target, (ast.Attribute, ast.Subscript)):
            self.mutation(node.target)
        self.generic_visit(node)

    def visit_Delete(self, node):
        for target in node.targets:
            self.mutation(target)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        # Defining a helper evaluates its defaults/decorators, but not its body.
        for expression in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
            if expression is not None:
                self.visit(expression)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        for expression in (*node.args.defaults, *node.args.kw_defaults):
            if expression is not None:
                self.visit(expression)


def find_risks(source, aliases=None, external=(), helpers=None):
    """Find known effects in the executed function scope and directly called helpers."""
    tree = ast.parse(textwrap.dedent(source))
    bindings = _Bindings()
    bindings.visit(tree)
    imports = dict(aliases or {})
    imports.update(bindings.aliases)
    functions = dict(helpers or {})
    functions.update(bindings.helpers)
    visitor = _Risks(imports, external, functions)
    function = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if function is None:
        visitor.visit(tree)
    else:
        visitor.scan(function)
    return sorted(visitor.reasons)

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
        module = ast.parse("".join(linecache.getlines(filename)))
        bindings = _Bindings()
        bindings.visit(module)
        external = bindings.names - bindings.aliases.keys() - bindings.helpers.keys()
        return find_risks(source, bindings.aliases, external, bindings.helpers)
    except SyntaxError:
        return ["its source code could not be understood"]
