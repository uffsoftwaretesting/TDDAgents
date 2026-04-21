import pytest
import numpy as np
import importlib


def test_solve_system_exists_and_callable():
    """
    Verifica se o stub _solve_system existe no módulo solver e é chamável.
    """
    module = importlib.import_module("diferencas_finitas_bvp.solver")
    assert hasattr(module, "_solve_system"), "Módulo 'solver' deve expor a função _solve_system"
    func = getattr(module, "_solve_system")
    assert callable(func), "_solve_system deve ser uma função"


def test_solve_system_not_implemented():
    """
    Chamada ao stub _solve_system deve levantar NotImplementedError.
    """
    from diferencas_finitas_bvp.solver import _solve_system
    # Dados dummy para A e RHS
    A = np.eye(2)
    RHS = np.array([1.0, 2.0])
    with pytest.raises(NotImplementedError):
        _solve_system(A, RHS)
