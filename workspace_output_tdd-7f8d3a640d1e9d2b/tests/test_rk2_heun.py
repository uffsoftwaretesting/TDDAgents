import pytest
from src.rk2_heun import rk2_heun

def test_rk2_heun_placeholder():
    # Placeholder: apenas validando que o test runner está funcionando
    assert True

@pytest.mark.parametrize("f", [None, 123, "not callable", 3.14])
def test_f_not_callable_raises_type_error(f):
    with pytest.raises(TypeError) as excinfo:
        rk2_heun(f, 0.0, 0.0, 1.0, 0.1)
    assert str(excinfo.value) == "f must be a callable[[float,float], float]"

@pytest.mark.parametrize(
    "param_name, t0, y0, t_final, h",
    [
        ("t0", "0.0", 0.0, 1.0, 0.1),
        ("y0", 0.0, "0.0", 1.0, 0.1),
        ("t_final", 0.0, 0.0, "1.0", 0.1),
        ("h", 0.0, 0.0, 1.0, "0.1"),
    ],
)
def test_non_float_parameters_raise_type_error(param_name, t0, y0, t_final, h):
    with pytest.raises(TypeError) as excinfo:
        rk2_heun(lambda t, y: t + y, t0, y0, t_final, h)
    assert str(excinfo.value) == "t0, y0, t_final and h must be floats"

def test_h_zero_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        rk2_heun(lambda t, y: t + y, 0.0, 0.0, 1.0, 0.0)
    assert str(excinfo.value) == "h must be greater than zero"

def test_h_negative_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        rk2_heun(lambda t, y: t + y, 0.0, 0.0, 1.0, -0.1)
    assert str(excinfo.value) == "h must be greater than zero"

def test_t_final_less_than_t0_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        rk2_heun(lambda t, y: t + y, 1.0, 0.0, 0.5, 0.1)
    assert str(excinfo.value) == "t_final must be greater than or equal to t0"


def test_t_final_equal_t0_returns_y0_and_does_not_call_f():
    # Quando t_final == t0, deve retornar y0 imediatamente e não chamar f
    flag = {"called": False}
    def f(t, y):
        flag["called"] = True
        return t + y
    y0 = 42.0
    result = rk2_heun(f, 0.0, y0, 0.0, 0.1)
    assert result == y0
    assert flag["called"] is False


def test_t_final_equal_t0_with_exception_raising_f_returns_y0_without_raising():
    # Se f lança, não deve ser chamado quando t_final == t0
    def f(t, y):
        raise RuntimeError("should not be called")
    y0 = 99.0
    # espera não levantar exceção e retornar y0
    result = rk2_heun(f, 1.23, y0, 1.23, 0.5)
    assert result == y0


@pytest.mark.parametrize("t0, y0, h", [
    (0.0, 5.5, 0.1),
    (1.23, -4.56, 2.718),
    (100.0, 0.0, 10.0),
])
def test_early_return_for_various_initial_conditions(t0, y0, h):
    # Verifica que para vários t0==t_final, retorna y0 sem chamar f
    flag = {"called": False}
    def f(t, y):
        flag["called"] = True
        return t + y
    result = rk2_heun(f, t0, y0, t0, h)
    assert result == y0
    assert flag["called"] is False
