import pytest
import inspect
from src.integracao_simpson_1_3 import integracao_simpson_1_3

def test_function_exists():
    """
    Verifica se a função integracao_simpson_1_3 está definida e é chamável.
    """
    assert callable(integracao_simpson_1_3), "integracao_simpson_1_3 deve ser uma função"


def test_has_docstring():
    """
    Verifica se a função possui docstring não vazia (placeholder).
    """
    doc = integracao_simpson_1_3.__doc__
    assert isinstance(doc, str) and doc.strip(), "A função deve ter uma docstring placeholder não vazia"


def test_signature_parameters():
    """
    Verifica se a assinatura da função é (f, a, b, N).
    """
    sig = inspect.signature(integracao_simpson_1_3)
    params = list(sig.parameters.keys())
    assert params == ['f', 'a', 'b', 'N'], (
        f"Assinatura incorreta. Esperado ['f', 'a', 'b', 'N'], mas obteve {params}"
    )


def test_simple_integration():
    """
    Testa cálculo da integral de f(x)=x de 0 a 1 com N=2.
    """
    result = integracao_simpson_1_3(lambda x: x, 0, 1, 2)
    assert isinstance(result, float), "Resultado deve ser float"
    # A integral de x de 0 a 1 com N=2 é 0.5
    assert abs(result - 0.5) < 1e-6, f"Esperado ~0.5, obteve {result}"
