import pytest
import numpy as np
from diferencas_finitas_bvp.validation import _validate_inputs

# Definição de uma função válida vetorizada
def valid_f(x: np.ndarray) -> np.ndarray:
    return x * 0.0

# Parâmetros válidos padrão
a_valid = 0.0
b_valid = 1.0
bc_valid = {"u_a": 0.0, "u_b": 1.0}
N_valid = 5
x_alvo_valid = 0.5


def test_validate_inputs_with_valid_parameters():
    """
    Caso positivo: parâmetros corretos não devem levantar exceção.
    """
    # Nenhuma exceção esperada
    _validate_inputs(valid_f, a_valid, b_valid, bc_valid, N_valid, x_alvo_valid)


def test_validate_inputs_a_not_float_or_b_not_float():
    """
    a ou b não são float devem levantar ValueError.
    """
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, '0.0', b_valid, bc_valid, N_valid, x_alvo_valid)
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, a_valid, None, bc_valid, N_valid, x_alvo_valid)


def test_validate_inputs_a_ge_b():
    """
    a >= b deve levantar ValueError.
    """
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, 1.0, 0.0, bc_valid, N_valid, x_alvo_valid)
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, 0.5, 0.5, bc_valid, N_valid, x_alvo_valid)


def test_validate_inputs_bc_not_dict_or_missing_keys():
    """
    bc não é dict ou falta chave deve levantar ValueError.
    """
    # bc não é dict
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, a_valid, b_valid, ['u_a', 'u_b'], N_valid, x_alvo_valid)
    # falta chave u_b
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, a_valid, b_valid, {'u_a': 0.0}, N_valid, x_alvo_valid)
    # valor não float
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, a_valid, b_valid, {'u_a': '0.0', 'u_b': 1.0}, N_valid, x_alvo_valid)


def test_validate_inputs_N_not_int_or_less_than_one():
    """
    N não inteiro ou < 1 deve levantar ValueError.
    """
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, a_valid, b_valid, bc_valid, 0, x_alvo_valid)
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, a_valid, b_valid, bc_valid, 1.5, x_alvo_valid)


def test_validate_inputs_x_alvo_not_float_or_out_of_bounds():
    """
    x_alvo não float ou fora do intervalo [a, b] deve levantar ValueError.
    """
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, a_valid, b_valid, bc_valid, N_valid, '0.5')
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, a_valid, b_valid, bc_valid, N_valid, -0.1)
    with pytest.raises(ValueError):
        _validate_inputs(valid_f, a_valid, b_valid, bc_valid, N_valid, 1.1)


def test_validate_inputs_f_not_callable_or_returns_wrong_shape():
    """
    f não callable ou que retorna array de forma incorreta deve levantar ValueError.
    """
    # f não é callable
    with pytest.raises(ValueError):
        _validate_inputs(None, a_valid, b_valid, bc_valid, N_valid, x_alvo_valid)
    # f retorna escalar em vez de vetor
    def bad_f(x: np.ndarray):
        return x[0]
    with pytest.raises(ValueError):
        _validate_inputs(bad_f, a_valid, b_valid, bc_valid, N_valid, x_alvo_valid)
