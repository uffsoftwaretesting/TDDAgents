import pytest
from src.solver import adams_bashforth_3


def valid_func(t, y):
    return 0.0


test_cases_non_callable = [None, 123, "foo", [], {}]

@pytest.mark.parametrize("f", test_cases_non_callable)
def test_f_not_callable(f):
    """
    Deve lançar TypeError se f não for chamável.
    """
    with pytest.raises(TypeError):
        adams_bashforth_3(f, 0.0, 1.0, 1.0, 0.1)

@pytest.mark.parametrize("param_name, invalid_value", [
    ("t0", "0.0"),
    ("y0", []),
    ("t_final", {}),
    ("h", None),
])
def test_params_not_float(param_name, invalid_value):
    """
    Deve lançar TypeError se qualquer parâmetro t0, y0, t_final ou h não for float.
    """
    kwargs = {
        "f": valid_func,
        "t0": 0.0,
        "y0": 1.0,
        "t_final": 1.0,
        "h": 0.1,
    }
    kwargs[param_name] = invalid_value
    with pytest.raises(TypeError):
        adams_bashforth_3(**kwargs)

@pytest.mark.parametrize("h", [0.0, -0.1])
def test_h_non_positive(h):
    """
    Deve lançar ValueError se h <= 0 com mensagem adequada.
    """
    with pytest.raises(ValueError) as exc:
        adams_bashforth_3(valid_func, 0.0, 1.0, 1.0, h)
    assert str(exc.value) == "Passo h deve ser > 0"

def test_t_final_less_than_t0():
    """
    Deve lançar ValueError se t_final < t0 com mensagem adequada.
    """
    with pytest.raises(ValueError) as exc:
        adams_bashforth_3(valid_func, 1.0, 1.0, 0.5, 0.1)
    assert str(exc.value) == "t_final deve ser ≥ t0"