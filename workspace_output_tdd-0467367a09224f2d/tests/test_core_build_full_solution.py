import numpy as np
import pytest
from diferencas_finitas_bvp.core import _build_full_solution

@ pytest.mark.parametrize("u_int, bc, expected", [
    ([1.0, 2.0, 3.0], {'u_a': 0.0, 'u_b': 4.0}, np.array([0.0, 1.0, 2.0, 3.0, 4.0])),
    (np.array([10.0]), {'u_a': -1.0, 'u_b': 1.0}, np.array([-1.0, 10.0, 1.0])),
    ([5.5, 6.5], {'u_a': 2.2, 'u_b': 3.3}, np.array([2.2, 5.5, 6.5, 3.3])),
])
def test_build_full_solution_various_inputs(u_int, bc, expected):
    """
    Verifica se _build_full_solution concatena corretamente u_a, u_int e u_b
    para diferentes tipos de u_int e valores de contorno.
    """
    result = _build_full_solution(u_int, bc)
    # Deve retornar numpy.ndarray
    assert isinstance(result, np.ndarray), "Resultado deve ser numpy.ndarray"
    # Verificar forma
    assert result.shape == expected.shape, f"Shape esperado {expected.shape}, obtido {result.shape}"
    # Verificar valores
    assert np.allclose(result, expected), f"Valores esperados {expected}, obtidos {result}"
