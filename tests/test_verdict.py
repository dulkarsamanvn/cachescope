from cachescope.verdict import judge


def make_record(calls=100, unknown=0, unique=10, seconds=1.0, varied=False):
    """Build a fake record, like the tracer would produce.

    The defaults describe a clearly good case: 100 calls, only 10 different
    inputs, 1 second in total (so 10 ms per call), stable results.
    """
    return {
        "calls": calls,
        "unknown": unknown,
        "inputs": {i: 1 for i in range(unique)},
        "results": {},
        "time": seconds,
        "varied": varied,
    }


def test_good_case_is_candidate():
    label, _ = judge(make_record())
    assert label == "CANDIDATE"


def test_changing_result_is_unsafe_even_if_everything_else_is_good():
    label, _ = judge(make_record(varied=True))
    assert label == "UNSAFE"


def test_no_repeats_is_skip():
    label, _ = judge(make_record(calls=10, unique=10))
    assert label == "SKIP"


def test_too_fast_is_skip():
    label, reason = judge(make_record(seconds=0.001))  # 0.01 ms per call
    assert label == "SKIP"
    assert "too fast" in reason


def test_little_time_to_save_is_skip():
    label, _ = judge(make_record(unique=95, seconds=0.05))  # only 5 repeats
    assert label == "SKIP"


def test_unknown_arguments_make_it_maybe():
    label, _ = judge(make_record(unknown=10))
    assert label == "MAYBE"


def test_mostly_new_inputs_is_maybe():
    label, _ = judge(make_record(unique=60))  # only 40 of 100 calls repeated
    assert label == "MAYBE"


def test_all_unknown_arguments_is_skip():
    label, reason = judge(make_record(calls=5, unknown=5, unique=0))
    assert label == "SKIP"
    assert "could not be compared" in reason

def test_danger_signs_downgrade_candidate_to_maybe():
    label, reason = judge(make_record(), risks=["reads the clock"])
    assert label == "MAYBE"
    assert "reads the clock" in reason


def test_danger_signs_do_not_hide_unsafe():
    label, _ = judge(make_record(varied=True), risks=["reads the clock"])
    assert label == "UNSAFE"


def test_danger_signs_on_a_skipped_function_stay_skip():
    label, _ = judge(make_record(calls=10, unique=10), risks=["reads the clock"])
    assert label == "SKIP"