"""Turn values into labels so we can tell if two calls are the same."""


class _Unknown:
    """A marker that means: we are not sure about this value."""

    def __repr__(self):
        return "UNKNOWN"


UNKNOWN = _Unknown()

# The simple kinds of values we trust.
SAFE_TYPES = (int, float, str, bool, type(None))

# How many levels of "list inside a list inside..." we are willing to look into.
MAX_DEPTH = 4


def fingerprint(value, depth=0):
    t = type(value)

    if t in SAFE_TYPES:
        return (t.__name__, value)

    # Too deep (or a value that contains itself): give up instead of crashing.
    if depth >= MAX_DEPTH:
        return UNKNOWN

    if t is list or t is tuple:
        parts = []
        for item in value:
            item_label = fingerprint(item, depth + 1)
            if item_label is UNKNOWN:
                return UNKNOWN
            parts.append(item_label)
        return (t.__name__, tuple(parts))

    if t is dict:
        pairs = []
        for key, val in value.items():
            key_label = fingerprint(key, depth + 1)
            val_label = fingerprint(val, depth + 1)
            if key_label is UNKNOWN or val_label is UNKNOWN:
                return UNKNOWN
            pairs.append((key_label, val_label))
        return ("dict", frozenset(pairs))

    return UNKNOWN