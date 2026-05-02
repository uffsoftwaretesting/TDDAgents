import pytest
from src.adams import adams_bashforth_3

@ pytest.mark.parametrize("delta_factor", [2.0, 2.5])
def test_adams_initialization_rk4_steps_for_delta_ge_2h(monkeypatch, delta_factor):
    """
    For Δ ≥ 2h, adams_bashforth_3 should perform two RK4 steps of size h before proceeding to multistep.
    """
    # Dummy f; the real work is in _rk4_step calls
    f = lambda t, y: 0.0
    t0 = 1.0
    y0 = 2.0
    h = 0.3
    t_final = t0 + delta_factor * h
    calls = []

    def fake_rk4_step(f_arg, t_arg, y_arg, dt_arg):
        # record each RK4 invocation
        calls.append((t_arg, y_arg, dt_arg))
        # return a predictable y: y + dt
        return y_arg + dt_arg

    # Patch the internal RK4 step
    monkeypatch.setattr("src.adams._rk4_step", fake_rk4_step)

    # Expect NotImplementedError after initialization
    with pytest.raises(NotImplementedError):
        adams_bashforth_3(f, t0, y0, t_final, h)

    # Verify that exactly two RK4 steps were invoked
    assert len(calls) == 2
    # First call: from (t0, y0) with dt=h
    assert calls[0] == (t0, y0, h)
    # Second call: from (t0+h, y0+h) with dt=h
    assert calls[1] == (t0 + h, y0 + h, h)
