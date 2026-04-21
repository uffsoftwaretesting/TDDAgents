import pytest
from src.euler_impl.exceptions import ConvergenceError

def test_convergence_error_is_exception():
    """ConvergenceError should inherit from Exception"""
    err = ConvergenceError(1, 0.0, 0.1, 1e-8)
    assert isinstance(err, Exception)


def test_convergence_error_message_and_attributes():
    """ConvergenceError should store step, t, h_i, criterion and include them in its message"""
    step = 5
    t_val = 2.5
    h_i = 0.05
    crit = 1e-6
    err = ConvergenceError(step, t_val, h_i, crit)
    # Attributes exist
    assert hasattr(err, 'step')
    assert hasattr(err, 't')
    assert hasattr(err, 'h_i')
    assert hasattr(err, 'criterion')
    # Attributes have correct values
    assert err.step == step
    assert err.t == t_val
    assert err.h_i == h_i
    assert err.criterion == crit
    # Message contains all values
    msg = str(err)
    assert str(step) in msg
    assert str(t_val) in msg
    assert str(h_i) in msg
    assert str(crit) in msg
