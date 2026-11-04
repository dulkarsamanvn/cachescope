from cachescope.fingerprint import UNKNOWN, fingerprint


def test_same_value_gives_same_fingerprint():
    assert fingerprint(5) == fingerprint(5)


def test_int_and_float_are_different():
    assert fingerprint(1) != fingerprint(1.0)


def test_int_and_bool_are_different():
    assert fingerprint(1) != fingerprint(True)


class Plain:
    """A custom object. Our fingerprint does not know how to compare these."""


def test_same_list_gives_same_fingerprint():
    assert fingerprint([1, 2]) == fingerprint([1, 2])


def test_order_matters():
    assert fingerprint([1, 2]) != fingerprint([2, 1])


def test_list_and_tuple_are_different():
    assert fingerprint([1, 2]) != fingerprint((1, 2))


def test_nested_lists_work():
    assert fingerprint([1, [2, 3]]) == fingerprint([1, [2, 3]])


def test_list_with_unknown_item_is_unknown():
    assert fingerprint([1, Plain()]) is UNKNOWN


def test_fingerprint_can_be_a_dictionary_key():
    counts = {fingerprint([1, [2, 3]]): 1}  # would crash if it were not hashable
    assert counts[fingerprint([1, [2, 3]])] == 1

def test_dict_order_does_not_matter():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_different_dicts_are_different():
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_dict_with_unknown_value_is_unknown():
    assert fingerprint({"a": Plain()}) is UNKNOWN


def test_dict_fingerprint_can_be_a_dictionary_key():
    counts = {fingerprint({"a": [1, 2]}): 1}
    assert counts[fingerprint({"a": [1, 2]})] == 1

def test_self_referencing_list_is_unknown_not_a_crash():
    a = []
    a.append(a)
    assert fingerprint(a) is UNKNOWN


def test_very_deep_nesting_is_unknown():
    deep = []
    inner = deep
    for _ in range(50):
        new = []
        inner.append(new)
        inner = new
    assert fingerprint(deep) is UNKNOWN


def test_moderate_nesting_still_works():
    assert fingerprint([[[1]]]) == fingerprint([[[1]]])
    assert fingerprint([[[1]]]) is not UNKNOWN