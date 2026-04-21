import inspect
import pytest
from mathutils.derivada_diferenca_central import derivada_diferenca_central

def test_docstring_exists_and_not_empty():
    """
    Verifica que a função possua docstring e que não seja vazia.
    """
    doc = derivada_diferenca_central.__doc__
    assert doc is not None, "A função deve ter uma docstring"
    assert doc.strip() != "", "Docstring não deve ser vazia"

def test_docstring_contains_sections():
    """
    Verifica que a docstring contém as seções obrigatórias PEP8:
    Descrição, Parameters, Returns, Raises, Examples.
    """
    doc = derivada_diferenca_central.__doc__
    required_sections = [
        'Parameters',
        'Returns',
        'Raises',
        'Examples'
    ]
    missing = [sec for sec in required_sections if sec not in doc]
    assert not missing, f"Docstring está faltando as seções: {missing}"
