import inspect
import pytest
from src.solver import adams_bashforth_3, ODEFunction

def test_docstring_exists_and_not_empty():
    """A função deve ter docstring não vazia."""
    doc = adams_bashforth_3.__doc__
    assert isinstance(doc, str) and doc.strip(), "Docstring não deve ser vazia"


def test_docstring_contains_signature():
    """Verifica se a docstring contém a assinatura completa da função."""
    doc = adams_bashforth_3.__doc__
    expected_sig = (
        "def adams_bashforth_3(f: ODEFunction, t0: float, y0: float, t_final: float, h: float) -> float"
    )
    assert expected_sig in doc, f"Docstring deve conter assinatura: {expected_sig}"


def test_docstring_mentions_rk4_initialization():
    """Garante que a docstring menciona Runge–Kutta de 4ª ordem (RK4)."""
    doc = adams_bashforth_3.__doc__
    assert "Runge–Kutta de 4ª ordem" in doc or "Runge-Kutta de 4ª ordem" in doc, \
        "Docstring deve mencionar Runge–Kutta de 4ª ordem"
    assert "RK4" in doc, "Docstring deve mencionar RK4"


def test_docstring_contains_ab3_formula():
    """Verifica se a docstring inclui a fórmula de Adams–Bashforth 3ª ordem."""
    doc = adams_bashforth_3.__doc__
    assert "Adams–Bashforth de 3ª ordem" in doc, \
        "Docstring deve mencionar Adams–Bashforth de 3ª ordem"
    formula = "y_next = y_n + dt/12 * (23 * f_n - 16 * f_n1 + 5 * f_n2)"
    assert formula in doc, f"Docstring deve conter a fórmula AB3: {formula}"
