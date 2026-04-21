import math
import pytest
from src.rk2_heun import rk2_heun

@ pytest.mark.parametrize(
    "t0, t_final, h, expected_n_full, expected_remainder",
    [
        # exact division: interval=1.0, h=0.5 -> n_full=2, remainder=0.0
        (0.0, 1.0, 0.5, 2, 0.0),
        # non-exact: interval=1.0, h=0.3 -> n_full=3, remainder=0.1
        (0.0, 1.0, 0.3, 3, 0.1),
        # h > interval: interval=1.5, h=2.0 -> n_full=0, remainder=1.5
        (1.0, 2.5, 2.0, 0, 1.5),
        # larger interval exact: interval=10.0, h=2.0 -> n_full=5, remainder=0.0
        (0.5, 10.5, 2.0, 5, 0.0),
    ]
)
def test_step_count_and_remainder(t0, t_final, h, expected_n_full, expected_remainder):
    """
    For given parameters, verify that:
      - computed n_full and remainder match expectations,
      - number of function calls equals 2 * number_of_steps,
      - y remains unchanged when f returns zero.
    """
    calls = []
    def f(t, y):
        # spy that records every invocation
        calls.append(t)
        return 0.0

    y0 = 7.77
    # call rk2_heun
    y_final = rk2_heun(f, t0, y0, t_final, h)

    # y should remain unchanged because derivative is zero
    assert y_final == pytest.approx(y0)

    # compute from spec
    interval = t_final - t0
    n_full = int(math.floor(interval / h))
    remainder = interval - n_full * h
    # compare against expected
    assert n_full == expected_n_full
    assert math.isclose(remainder, expected_remainder, rel_tol=1e-9)

    # expected number of steps = n_full + (1 if remainder > 0 else 0)
    expected_steps = expected_n_full + (1 if expected_remainder > 0 else 0)
    # each step invokes f twice
    assert len(calls) == 2 * expected_steps


def test_floating_point_remainder_extra_step():
    """Test floating imprecision leads to a tiny remainder > 0 and an extra step."""
    calls = []
    def f(t, y):
        calls.append(t)
        return 0.0

    h = 0.3
    t0 = 0.0
    # t_final chosen so that t_final/h is very close to integer but slightly less
    t_final = 0.9  # mathematically 3*0.3 but float gives ~2.9999999999
    y0 = 5.0
    y_final = rk2_heun(f, t0, y0, t_final, h)
    assert y_final == pytest.approx(y0)

    interval = t_final - t0
    n_full = int(math.floor(interval / h))
    remainder = interval - n_full * h
    # due to floating imprecision, remainder should be > 0
    assert remainder > 0
    expected_steps = n_full + 1
    # f is called twice per step
    assert len(calls) == 2 * expected_steps


def test_small_remainder_from_h3():
    """Test when remainder is extremely small due to float arithmetic, it's still >0 and results in extra step."""
    calls = []
    def f(t, y):
        calls.append(t)
        return 0.0

    h = 0.1
    t0 = 0.0
    # explicit literal forcing the tiny rounding error
    t_final = 0.30000000000000004
    y0 = -1.0
    y_final = rk2_heun(f, t0, y0, t_final, h)
    assert y_final == pytest.approx(y0)

    # compute remainder via raw_steps to capture tiny floating residue
    interval = t_final - t0
    raw_steps = interval / h
    n_full = int(math.floor(raw_steps))
    remainder = (raw_steps - n_full) * h
    # mathematically remainder is zero but float literal is slightly >0
    assert 0 < remainder < 1e-12
    expected_steps = n_full + 1
    assert len(calls) == 2 * expected_steps
