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