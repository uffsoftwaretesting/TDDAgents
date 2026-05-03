import pytest
import inspect
import src.solve

def test_solve_function_exists():
    """
    Verifica se a função `solve` está definida em `src.solve`.
    """
    solve = getattr(src.solve, 'solve', None)
    assert solve is not None, "A função 'solve' deve ser definida em src.solve"

def test_solve_is_callable_and_signature():
    """
    Verifica se `solve` é chamável e possui a assinatura correta.
    """
    solve = getattr(src.solve, 'solve', None)
    assert callable(solve), "'solve' deve ser chamável"
    sig = inspect.signature(solve)
    assert list(sig.parameters.keys()) == ['f', 't0', 'tf', 'y0', 'n'], \
        f"Assinatura incorreta: esperada parâmetros ['f', 't0', 'tf', 'y0', 'n'], got {list(sig.parameters.keys())}"

# Placeholder para que pytest descubra ao menos um teste com falha esperada
def test_placeholder_failure():
    """
    Teste placeholder que deverá falhar inicialmente.
    """
    pytest.skip("Placeholder: testes de comportamento serão implementados após definir 'solve'")