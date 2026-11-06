"""Turn values into labels so we can tell if two calls are the same."""

import sys
import types
import weakref


class _Unknown:
    """A marker that means: we are not sure about this value."""

    def __repr__(self):
        return "UNKNOWN"


UNKNOWN = _Unknown()


def immutable_label(label):
    """Whether a known fingerprint describes a value safe to share by value."""
    kind, value = label
    if kind == "tuple":
        return all(immutable_label(part) for part in value)
    return kind not in ("list", "dict", "object")


def contains_objects(label):
    if label is UNKNOWN:
        return False
    kind, value = label
    if kind == "object":
        return True
    if kind in ("tuple", "list"):
        return any(contains_objects(part) for part in value)
    if kind == "dict":
        return any(contains_objects(key) or contains_objects(val) for key, val in value)
    return False

# The simple kinds of values we trust.
SAFE_TYPES = (int, float, str, bytes, bool, type(None))
TYPE_NAMES = {kind: kind.__name__ for kind in (*SAFE_TYPES, list, tuple)}

# How many levels of "list inside a list inside..." we are willing to look into.
MAX_DEPTH = 4
MAX_ITEMS = 1024
MAX_VALUE_BYTES = 64 * 1024
MAX_OBJECTS = 4096

# Direct built-in descriptors bypass user-defined metaclass attribute access.
_TYPE_MRO = vars(type)["__mro__"]
_TYPE_NAMESPACE = vars(type)["__dict__"]


class ObjectFingerprinter:
    """Opt-in identity and visible-state snapshots, without user comparison code.

    Only weak-referenceable instances with ordinary instance dictionaries or
    slots are supported. A snapshot is evidence, not a declaration of cache-key
    equivalence or immutability.
    """

    def __init__(self):
        self._identities = {}
        self._next_identity = 0

    def snapshot(self, value, depth, budget):
        cls = type(value)
        attributes, slots, has_state = None, [], False
        for base in _TYPE_MRO.__get__(cls):
            namespace = _TYPE_NAMESPACE.__get__(base)
            for name, descriptor in namespace.items():
                budget[0] -= 1
                if budget[0] < 0:
                    return UNKNOWN
                if name == "__dict__":
                    if type(descriptor) is not types.GetSetDescriptorType:
                        return UNKNOWN
                    if attributes is None:
                        attributes = descriptor.__get__(value, cls)
                        if type(attributes) is not dict:
                            return UNKNOWN
                        has_state = True
                elif type(descriptor) is types.MemberDescriptorType:
                    has_state = True
                    try:
                        item = descriptor.__get__(value, cls)
                    except AttributeError:
                        continue  # an unset slot
                    budget[1] -= 64 + len(name) * 4
                    label = _fingerprint(item, depth + 1, budget)
                    if label is UNKNOWN:
                        return UNKNOWN
                    slots.append((id(base), name, label))
        if not has_state:
            return UNKNOWN
        state = _fingerprint(attributes, depth + 1, budget)
        if state is UNKNOWN:
            return UNKNOWN
        identity = id(value)
        entry = self._identities.get(identity)
        if entry is None or entry[0]() is not value:
            if len(self._identities) >= MAX_OBJECTS:
                return UNKNOWN
            token = self._next_identity
            self._next_identity += 1

            def discard(reference):
                current = self._identities.get(identity)
                if current is not None and current[0] is reference:
                    self._identities.pop(identity, None)

            reference = weakref.ref(value, discard)
            self._identities[identity] = (reference, token)
        else:
            token = entry[1]
        return ("object", (token, state, tuple(slots)))


def fingerprint(value, depth=0, *, objects=None):
    """Best-effort observation: a changing container must not break its caller."""
    try:
        return _fingerprint(value, depth, [MAX_ITEMS, MAX_VALUE_BYTES, objects])
    except Exception:
        return UNKNOWN


def _fingerprint(value, depth, budget):
    t = type(value)
    budget[0] -= 1
    budget[1] -= 64
    if t is str:
        budget[1] -= len(value) * 4
    elif t is bytes:
        budget[1] -= len(value)
    elif t is int:
        budget[1] -= value.bit_length() // 8
    if budget[0] < 0 or budget[1] < 0:
        return UNKNOWN

    if any(t is kind for kind in SAFE_TYPES):
        return (TYPE_NAMES[t], value)

    # Too deep (or a value that contains itself): give up instead of crashing.
    if depth >= MAX_DEPTH:
        return UNKNOWN

    if t is list or t is tuple:
        parts = []
        for item in value:
            item_label = _fingerprint(item, depth + 1, budget)
            if item_label is UNKNOWN:
                return UNKNOWN
            parts.append(item_label)
        return (TYPE_NAMES[t], tuple(parts))

    if t is dict:
        pairs = []
        for key, val in value.items():
            key_label = _fingerprint(key, depth + 1, budget)
            val_label = _fingerprint(val, depth + 1, budget)
            if key_label is UNKNOWN or val_label is UNKNOWN:
                return UNKNOWN
            pairs.append((key_label, val_label))
        return ("dict", tuple(pairs))

    if budget[2] is not None:
        return budget[2].snapshot(value, depth, budget)

    return UNKNOWN


def fingerprint_size(label):
    """Conservative retention estimate for an already bounded, known label."""
    pending, seen, size = [label], set(), 0
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        size += sys.getsizeof(value)
        if type(value) is tuple:
            pending.extend(value)
    return size
