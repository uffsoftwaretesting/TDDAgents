import pytest

from src.rk2_ponto_medio import rk2_ponto_medio

# Função válida para casos de erro de parâmetros
valid_f = lambda t, y: 0.0


def test_non_callable_f_raises_type_error():
    """
    Se f não for callable, deve levantar TypeError
    """
    with pytest.raises(TypeError):
        rk2_ponto_medio(123, 0.0, 0.0, 1.0, 0.1)


@pytest.mark.parametrize("param, value", [
    ("t0", "0.0"),
    ("y0", "1.0"),
    ("t_final", "1.0"),
    ("h", "0.1"),
])
def test_non_float_params_raise_type_error(param, value):
    """
    Se algum dos parâmetros numéricos não for float, deve levantar TypeError
    """
    kwargs = {"f": valid_f, "t0": 0.0, "y0": 1.0, "t_final": 1.0, "h": 0.1}
    kwargs[param] = value  # atribui valor inválido
    with pytest.raises(TypeError):
        rk2_ponto_medio(**kwargs)


@pytest.mark.parametrize("h", [0.0, -0.1])
def test_non_positive_h_raises_value_error(h):
    """
    Se h for menor ou igual a zero, deve levantar ValueError
    """
    with pytest.raises(ValueError):
        rk2_ponto_medio(valid_f, 0.0, 0.0, 1.0, h)


def test_t_final_less_than_t0_raises_value_error():
    """
    Se t_final < t0, deve levantar ValueError
    """
    with pytest.raises(ValueError):
        rk2_ponto_medio(valid_f, 1.0, 0.0, 0.5, 0.1)
