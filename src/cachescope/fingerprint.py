"""Turn values into labels so we can tell if two calls are the same."""

import sys


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
    return kind not in ("list", "dict")

# The simple kinds of values we trust.
SAFE_TYPES = (int, float, str, bytes, bool, type(None))
TYPE_NAMES = {kind: kind.__name__ for kind in (*SAFE_TYPES, list, tuple)}

# How many levels of "list inside a list inside..." we are willing to look into.
MAX_DEPTH = 4
MAX_ITEMS = 1024
MAX_VALUE_BYTES = 64 * 1024


def fingerprint(value, depth=0):
    """Best-effort observation: a changing container must not break its caller."""
    try:
        return _fingerprint(value, depth, [MAX_ITEMS, MAX_VALUE_BYTES])
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

    if t in SAFE_TYPES:
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
