import pytest
from src.euler_impl.euler_implicit import euler_implicito


def test_func_not_callable_raises_value_error():
    with pytest.raises(ValueError):
        euler_implicito(func=123, t0=0.0, y0=1.0, t_final=1.0, h=0.1)

@pytest.mark.parametrize("param, value", [
    ("t0", "0.0"),
    ("y0", None),
    ("t_final", 1),
    ("h", []),
    ("tol", {}),
])
def test_params_type_invalid_raise_value_error(param, value):
    # Prepare default valid args
    args = {
        "func": lambda t, y: y,
        "t0": 0.0,
        "y0": 1.0,
        "t_final": 1.0,
        "h": 0.1,
        "tol": 1e-8,
        "max_iter": 10,
    }
    args[param] = value
    with pytest.raises(ValueError):
        euler_implicito(**args)

def test_max_iter_not_int_raises_value_error():
    with pytest.raises(ValueError):
        euler_implicito(func=lambda t, y: y, t0=0.0, y0=1.0, t_final=1.0, h=0.1, tol=1e-8, max_iter=5.5)

@pytest.mark.parametrize("invalid_h", [0.0, -0.1])
def test_h_less_equal_zero_raises_value_error(invalid_h):
    with pytest.raises(ValueError):
        euler_implicito(func=lambda t, y: y, t0=0.0, y0=1.0, t_final=1.0, h=invalid_h)

@pytest.mark.parametrize("invalid_tol", [0.0, -1e-5])
def test_tol_less_equal_zero_raises_value_error(invalid_tol):
    with pytest.raises(ValueError):
        euler_implicito(func=lambda t, y: y, t0=0.0, y0=1.0, t_final=1.0, h=0.1, tol=invalid_tol)

def test_max_iter_less_than_one_raises_value_error():
    with pytest.raises(ValueError):
        euler_implicito(func=lambda t, y: y, t0=0.0, y0=1.0, t_final=1.0, h=0.1, tol=1e-8, max_iter=0)

@pytest.mark.parametrize("t0, t_final", [(1.0, 1.0), (2.0, 1.0)])
def test_t_final_not_greater_than_t0_raises_value_error(t0, t_final):
    with pytest.raises(ValueError):
        euler_implicito(func=lambda t, y: y, t0=t0, y0=1.0, t_final=t_final, h=0.1)
