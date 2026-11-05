from cachescope.risk import find_risks, risks_of_function, source_of

def test_clock_is_found():
    assert find_risks("def f():\n    return time.time()\n") == ["reads the clock"]


def test_datetime_now_is_found_with_and_without_module_prefix():
    assert find_risks("def f():\n    return datetime.now()\n") == ["reads the clock"]
    assert find_risks("def f():\n    return datetime.datetime.now()\n") == ["reads the clock"]


def test_random_module_is_found():
    assert find_risks("def f():\n    return random.randint(1, 6)\n") == ["uses randomness"]


def test_open_is_found():
    assert find_risks("def f(p):\n    return open(p).read()\n") == ["reads or writes files"]


def test_network_is_found():
    assert find_risks("def f(u):\n    return requests.get(u)\n") == ["uses the network"]


def test_global_is_found():
    source = "def f():\n    global n\n    n += 1\n"
    assert find_risks(source) == ["changes variables outside itself"]


def test_plain_function_has_no_risks():
    assert find_risks("def f(a, b):\n    return a + b\n") == []


def test_several_risks_are_all_listed():
    source = "def f():\n    x = time.time()\n    print(x)\n    return random.random()\n"
    assert set(find_risks(source)) == {
        "reads the clock",
        "uses randomness",
        "prints something (a cached call would not print again)",
    }


def test_indented_method_source_still_works():
    source = "    def method(self):\n        return time.time()\n"
    assert find_risks(source) == ["reads the clock"]


def test_similar_names_are_not_flagged():
    assert find_risks("def f():\n    return my_time.time()\n") == []

def test_source_of_reads_just_that_function(tmp_path):
    path = tmp_path / "m.py"
    path.write_text("x = 1\n\ndef f(a):\n    return a + 1\n\ny = 2\n")
    text = source_of(str(path), 3)
    assert text.startswith("def f(a):")
    assert "y = 2" not in text


def test_source_of_missing_file_is_none():
    assert source_of("no_such_file.py", 1) is None


def test_unreadable_source_is_reported_not_called_safe():
    assert risks_of_function("no_such_file.py", 1) == ["its source code could not be read"]


def test_import_aliases_and_imported_functions_are_resolved():
    for source in (
        "def f():\n    import time as t\n    return t.time()\n",
        "def f():\n    from time import time\n    return time()\n",
    ):
        assert "reads the clock" in find_risks(source)
    assert "uses randomness" in find_risks("def f():\n    from random import randint as r\n    return r(1, 6)\n")


def test_mutations_of_arguments_and_their_aliases_are_found():
    for body in (
        "items.append(1)", "items[0] = 1", "items.value = 1",
        "del items[0]", "alias = items\n    alias.clear()",
    ):
        assert "mutates an argument" in find_risks("def f(items):\n    " + body + "\n")
    assert find_risks("def f():\n    items = []\n    items.append(1)\n    return len(items)\n") == []


def test_unused_helper_body_and_root_definition_expressions_are_ignored():
    source = "@print('defined')\ndef f(value=time.time()):\n    def unused():\n        print('unused')\n    return value\n"
    assert find_risks(source) == []
    assert "prints something (a cached call would not print again)" in find_risks(
        "def f():\n    def helper():\n        print('called')\n    helper()\n"
    )


def test_parameters_shadow_imported_modules():
    assert find_risks("import random\ndef f(random):\n    return random.randint(1, 6)\n") == []


def test_module_imports_globals_and_called_helpers_are_examined(tmp_path):
    path = tmp_path / "risks.py"
    path.write_text(
        "import time as clock\nSETTINGS = {}\n"
        "def helper():\n    print('effect')\n"
        "def f(key):\n    helper()\n    return SETTINGS.get(key, clock.time())\n"
    )
    risks = risks_of_function(str(path), 5)
    assert "reads the clock" in risks
    assert "reads variables outside itself" in risks
    assert "prints something (a cached call would not print again)" in risks


def test_global_helper_resolution_respects_lexical_scopes(tmp_path):
    path = tmp_path / "scopes.py"
    path.write_text(
        "import time as clock\n"
        "def helper():\n    return clock.time()\n"
        "def f(clock):\n    return helper()\n"
        "def g(helper):\n    return helper()\n"
    )
    assert "reads the clock" in risks_of_function(str(path), 4)
    assert risks_of_function(str(path), 6) == []
