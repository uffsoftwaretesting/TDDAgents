import pytest
from src.solver_euler import euler_explicito

@ pytest.mark.parametrize("invalid_f", [None, 123, "not callable", []])
def test_f_not_callable_raises_type_error(invalid_f):
    """
    If f is not callable, a TypeError with a clear message should be raised.
    """
    with pytest.raises(TypeError, match="f must be callable"):
        euler_explicito(invalid_f, 0.0, 1.0, 1.0, 0.1)

@ pytest.mark.parametrize("arg_name, bad_value, match", [
    ("t0", "0.0", "t0 must be float"),
    ("y0", 1, "y0 must be float"),
    ("t_final", [1.0], "t_final must be float"),
    ("h", None, "h must be float"),
])
def test_numeric_parameters_not_float_raises_type_error(arg_name, bad_value, match):
    """
    If any of t0, y0, t_final, or h is not a float, a TypeError with a clear message should be raised.
    """
    # Construct valid default arguments
    kwargs = {
        "f": lambda t, y: t + y,
        "t0": 0.0,
        "y0": 1.0,
        "t_final": 1.0,
        "h": 0.1
    }
    # Inject the invalid value
    kwargs[arg_name] = bad_value
    with pytest.raises(TypeError, match=match):
        euler_explicito(**kwargs)
