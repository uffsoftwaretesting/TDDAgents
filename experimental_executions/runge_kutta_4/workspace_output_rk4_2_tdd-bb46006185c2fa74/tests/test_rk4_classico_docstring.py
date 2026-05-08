import inspect
import pytest

from src.rk4_classico import rk4_classico


def test_docstring_exists():
    """
    Confirma que rk4_classico possui uma docstring não vazia.
    """
    doc = inspect.getdoc(rk4_classico)
    assert doc, "Docstring para rk4_classico está ausente ou vazia"


def test_docstring_parameters_section():
    """
    Confirma presença da seção 'Parâmetros' na docstring.
    """
    doc = inspect.getdoc(rk4_classico)
    assert "Parâmetros" in doc, "Seção 'Parâmetros' ausente na docstring"


def test_docstring_algorithm_section():
    """
    Confirma presença da descrição do algoritmo com k1, k2, k3 e k4 na docstring.
    """
    doc = inspect.getdoc(rk4_classico)
    for step in ["k1", "k2", "k3", "k4"]:
        assert step in doc, f"Descrição do algoritmo ausente: '{step}' não encontrado na docstring"


def test_docstring_return_section():
    """
    Confirma presença da seção 'Retorna' na docstring.
    """
    doc = inspect.getdoc(rk4_classico)
    assert "Retorna" in doc, "Seção 'Retorna' ausente na docstring"


def test_docstring_exceptions_section():
    """
    Confirma presença da seção de exceções ('Lança' ou 'Exceções') na docstring.
    """
    doc = inspect.getdoc(rk4_classico)
    assert ("Lança" in doc) or ("Exceções" in doc), "Seção de exceções ausente na docstring"