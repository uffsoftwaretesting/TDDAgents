import pytest
from taylor_2.taylor_2 import taylor_2

def test_n_steps_without_last_step():
    t0 = 0.0
    y0 = 0.0
    t_final = 1.0
    h = 0.25
    calls = {"f": 0, "df": 0}

    def f(t, y):
        calls["f"] += 1
        return 1.0

    def df(t, y):
        calls["df"] += 1
        return 0.0

    result = taylor_2(f, df, t0, y0, t_final, h)
    # Since (1.0 - 0.0) / 0.25 = 4 steps, no remainder
    assert calls["f"] == 4, f"Expected 4 calls to f, got {calls['f']}"
    assert calls["df"] == 4, f"Expected 4 calls to df, got {calls['df']}"
    # result = y0 + n*h*1 = 4*0.25 = 1.0
    assert pytest.approx(result, rel=1e-12) == 1.0

def test_n_steps_with_last_step():
    t0 = 1.0
    y0 = 2.0
    t_final = 2.1
    h = 0.5
    # (2.1 - 1.0) / 0.5 = 2 steps + remainder 0.1 => 3 calls total
    calls = {"f": 0, "df": 0}

    def f(t, y):
        calls["f"] += 1
        return 1.0

    def df(t, y):
        calls["df"] += 1
        return 0.0

    result = taylor_2(f, df, t0, y0, t_final, h)
    assert calls["f"] == 3, f"Expected 3 calls to f, got {calls['f']}"
    assert calls["df"] == 3, f"Expected 3 calls to df, got {calls['df']}"
    # result = y0 + 2*0.5 + 0.1 = 2.0 + 1.0 + 0.1 = 3.1
    assert pytest.approx(result, rel=1e-12) == 3.1

def test_constant_f_and_zero_df_exact_multiple():
    """
    Test the uniform-step loop when f(t, y) = c (constant) and df = 0,
    with (t_final - t0) exactly a multiple of h.
    Expect y = y0 + c * (t_final - t0) and correct number of calls.
    """
    t0 = 0.0
    y0 = 1.5
    t_final = 3.0
    h = 0.5
    c = 2.0
    calls = {"f": 0, "df": 0}

    def f(t, y):
        calls["f"] += 1
        return c

    def df(t, y):
        calls["df"] += 1
        return 0.0

    # Execute
    result = taylor_2(f, df, t0, y0, t_final, h)
    # Number of full steps
    n = int((t_final - t0) / h)
    # Check call counts
    assert calls["f"] == n, f"Expected {n} calls to f, got {calls['f']}"
    assert calls["df"] == n, f"Expected {n} calls to df, got {calls['df']}"
    # Analytical solution
    expected = y0 + c * (t_final - t0)
    assert pytest.approx(result, rel=1e-12) == expected
