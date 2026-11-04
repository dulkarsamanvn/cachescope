"""Pretend to run an LRU cache over recorded calls, to find a good size."""

from collections import OrderedDict


def hit_rate(sequence, size):
    """Fraction of calls a cache of this size would have answered from memory."""
    if not sequence:
        return 0.0
    cache = OrderedDict()  # remembers order: oldest first, newest last
    hits = 0
    for key in sequence:
        if key in cache:
            hits += 1
            cache.move_to_end(key)  # just used, so it is now the newest
        else:
            cache[key] = True
            if len(cache) > size:
                cache.popitem(last=False)  # drawer full: throw out the oldest
    return hits / len(sequence)


def candidate_sizes(unique):
    """Sizes worth trying: 1, 2, 4, 8, ... up to the first one that can hold every input."""
    sizes = []
    size = 1
    while True:
        sizes.append(size)
        if size >= unique:
            return sizes
        size *= 2


def suggest_maxsize(sequence, target=0.95):
    """Return (size, hit_rate) for the smallest size that gets within 95% of the best.

    Returns None if a cache would never help.
    """
    if not sequence:
        return None
    unique = len(set(sequence))
    results = [(size, hit_rate(sequence, size)) for size in candidate_sizes(unique)]
    best = max(rate for _, rate in results)
    if best == 0:
        return None
    for size, rate in results:
        if rate >= target * best:
            return size, rate