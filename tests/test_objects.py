"""Opt-in object snapshots must not execute user observation/comparison code."""

import gc
import os
import weakref

from cachescope.fingerprint import UNKNOWN, ObjectFingerprinter, fingerprint, immutable_label
from cachescope.tracer import Tracer, cache_advice
from cachescope.verdict import judge

HERE = os.path.dirname(__file__)


class Customer:
    def __init__(self, balance):
        self.balance = balance

    def total(self, amount):
        return self.balance + amount


class Slotted:
    __slots__ = ("value", "__weakref__")

    def __init__(self, value):
        self.value = value


def find(tracer, name):
    return next(record for key, record in tracer.records.items() if key[1] == name)


def object_value(value):
    return value.balance


def test_objects_are_unknown_by_default_and_supported_when_enabled():
    customer = Customer(10)
    assert fingerprint(customer) is UNKNOWN
    with Tracer(root=HERE, objects=True) as tracer:
        assert object_value(customer) == object_value(customer) == 10
    record = find(tracer, "object_value")
    assert record["calls"] == 2
    assert record["unknown"] == 0
    assert list(record["inputs"].values()) == [2]
    assert record["custom_inputs"]
    assert judge(record)[0] == "MAYBE"
    assert "no size is estimated" in cache_advice(record)


def test_method_self_is_compared_with_its_stored_state():
    customer = Customer(10)
    with Tracer(root=HERE, objects=True) as tracer:
        assert customer.total(2) == customer.total(2) == 12
        customer.balance = 20
        assert customer.total(2) == 22
    record = find(tracer, "total")
    assert record["unknown"] == 0
    assert sorted(record["inputs"].values()) == [1, 2]
    assert not record["varied"]
    assert judge(record)[0] == "MAYBE"


def test_distinct_instances_with_equal_state_keep_separate_identity():
    context = ObjectFingerprinter()
    first, second = Customer(10), Customer(10)
    assert fingerprint(first, objects=context) == fingerprint(first, objects=context)
    assert fingerprint(first, objects=context) != fingerprint(second, objects=context)


def test_plain_dataclasses_and_inherited_slots_are_supported():
    from dataclasses import dataclass

    @dataclass
    class Point:
        x: int
        y: int

    class Child(Slotted):
        __slots__ = ("extra",)

    context = ObjectFingerprinter()
    point = Point(1, 2)
    child = Child(3)
    child.extra = 4
    for value in (point, child):
        before = fingerprint(value, objects=context)
        assert before is not UNKNOWN
        assert before == fingerprint(value, objects=context)
        assert not immutable_label(before)
    old = fingerprint(child, objects=context)
    child.value = 5
    assert fingerprint(child, objects=context) != old


def test_properties_getattribute_hash_eq_and_repr_are_not_called():
    touched = []

    class Hostile:
        def __getattribute__(self, name):
            touched.append("getattribute")
            raise AssertionError(name)

        def __hash__(self):
            touched.append("hash")
            raise AssertionError("hash")

        def __eq__(self, other):
            touched.append("eq")
            raise AssertionError("eq")

        def __repr__(self):
            touched.append("repr")
            raise AssertionError("repr")

        @property
        def dangerous(self):
            touched.append("property")
            raise AssertionError("property")

    value = Hostile()
    object.__setattr__(value, "field", 3)
    context = ObjectFingerprinter()
    first = fingerprint(value, objects=context)
    assert first is not UNKNOWN
    assert first == fingerprint(value, objects=context)
    with Tracer(root=HERE, objects=True) as tracer:
        take_object(value)
        take_object(value)
    assert find(tracer, "take_object")["unknown"] == 0
    assert touched == []


def take_object(value):
    return 1


def test_metaclass_attribute_and_comparison_hooks_are_not_called():
    touched = []

    class Meta(type):
        def __getattribute__(self, name):
            touched.append(name)
            raise AssertionError(name)

        def __eq__(self, other):
            touched.append("eq")
            raise AssertionError("eq")

        def __hash__(self):
            touched.append("hash")
            raise AssertionError("hash")

    class Value(metaclass=Meta):
        pass

    value = Value()
    value.field = 3
    context = ObjectFingerprinter()
    assert fingerprint(value, objects=context) is not UNKNOWN
    assert touched == []


def test_a_property_named_dict_is_not_executed():
    touched = []

    class Disguised:
        @property
        def __dict__(self):
            touched.append(True)
            raise AssertionError("must not execute")

    assert fingerprint(Disguised(), objects=ObjectFingerprinter()) is UNKNOWN
    assert touched == []


def test_objects_without_weakrefs_are_unknown_without_strong_retention():
    class NoWeakref:
        __slots__ = ("value",)

    value = NoWeakref()
    value.value = 3
    context = ObjectFingerprinter()
    assert fingerprint(value, objects=context) is UNKNOWN
    assert context._identities == {}


def test_cyclic_large_and_unsupported_attributes_remain_unknown():
    context = ObjectFingerprinter()
    customer = Customer(10)
    customer.self = customer
    assert fingerprint(customer, objects=context) is UNKNOWN
    del customer.self
    customer.large = "x" * 100_000
    assert fingerprint(customer, objects=context) is UNKNOWN
    del customer.large
    customer.opaque = object()
    assert fingerprint(customer, objects=context) is UNKNOWN


def test_identity_tracking_does_not_keep_instances_alive_or_reuse_tokens():
    context = ObjectFingerprinter()
    value = Customer(10)
    reference = weakref.ref(value)
    first = fingerprint(value, objects=context)
    del value
    gc.collect()
    assert reference() is None
    assert context._identities == {}
    replacement = Customer(10)
    assert fingerprint(replacement, objects=context) != first


def test_object_identity_registry_is_bounded(monkeypatch):
    import cachescope.fingerprint as module

    monkeypatch.setattr(module, "MAX_OBJECTS", 1)
    context = ObjectFingerprinter()
    first, second = Customer(1), Customer(2)
    assert fingerprint(first, objects=context) is not UNKNOWN
    assert fingerprint(second, objects=context) is UNKNOWN
    assert len(context._identities) == 1


def fresh_customer(value):
    return Customer(value)


def test_custom_outputs_are_reviewed_instead_of_claimed_immutable_or_unsafe():
    with Tracer(root=HERE, objects=True) as tracer:
        first = fresh_customer(10)
        second = fresh_customer(10)
    assert first is not second
    record = find(tracer, "fresh_customer")
    assert record["custom_results"]
    assert record["mutable_results"] == 2
    assert record["unknown_results"] == 0
    assert judge(record)[0] == "MAYBE"
