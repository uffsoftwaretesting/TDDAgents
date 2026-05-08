import pytest

from src.solve import solve


def test_import_solve():
    # Verifica se a função solve está disponível e é chamável
    assert callable(solve), "solve deve ser uma função chamável"


def test_return_initial_y_when_t0_equals_tf():
    # Quando t0 == tf, o resultado deve ser y0 convertido para float, independentemente de n
    y0 = 3.14
    result = solve(lambda t, y: t + y, 1.0, 1.0, y0, 10)
    assert isinstance(result, float), "Resultado deve ser float"
    assert result == y0, f"Esperado retornar o valor inicial y0 ({y0}), mas obteve {result}"
