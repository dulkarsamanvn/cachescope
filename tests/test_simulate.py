from cachescope.simulate import candidate_sizes, hit_rate, suggest_maxsize


def test_empty_sequence_has_zero_hit_rate():
    assert hit_rate([], 4) == 0.0


def test_repeating_one_input_hits_after_the_first_call():
    assert hit_rate(["a"] * 10, 1) == 0.9


def test_cache_one_short_of_a_cycle_never_hits():
    cycle = list(range(20)) * 5
    assert hit_rate(cycle, 19) == 0.0


def test_cache_big_enough_for_a_cycle_hits_almost_always():
    cycle = list(range(20)) * 5
    assert hit_rate(cycle, 20) == 80 / 100  # only the first 20 calls miss


def test_candidate_sizes_end_at_or_above_the_unique_count():
    assert candidate_sizes(20) == [1, 2, 4, 8, 16, 32]
    assert candidate_sizes(1) == [1]


def test_suggest_picks_the_smallest_good_size():
    cycle = list(range(20)) * 15
    size, rate = suggest_maxsize(cycle)
    assert size == 32
    assert rate > 0.9


def test_a_hot_key_needs_only_a_tiny_cache():
    # input 0 is asked for again and again; every other input appears only once
    sequence = [x for k in range(1, 51) for x in (0, k)]
    size, rate = suggest_maxsize(sequence)
    assert size == 2
    assert rate == 49 / 100


def test_no_repeats_means_no_suggestion():
    assert suggest_maxsize(list(range(50))) is None


def test_empty_sequence_means_no_suggestion():
    assert suggest_maxsize([]) is None