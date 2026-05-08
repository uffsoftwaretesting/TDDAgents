import pytest
from src.solve import solve

def test_solve_is_callable():
    """
    Verifica se a função solve está disponível e é chamável
    """
    assert callable(solve), "solve deve ser uma função chamável"