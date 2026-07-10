import pytest
from cep_formatter.formatter import format_cep


def test_docstring_exists_and_not_empty():
    """
    A função deve ter uma docstring definida e não vazia.
    """
    doc = format_cep.__doc__
    assert doc is not None, "Docstring não deve ser None"
    assert doc.strip() != "", "Docstring não deve ser vazia"


def test_docstring_includes_param_return_and_raises():
    """
    A docstring deve seguir o padrão Sphinx incluindo ':param cep:', ':return:' e ':raises TypeError'/'ValueError'.
    """
    doc = format_cep.__doc__
    # Verifica se existe seção de parâmetros
    assert ":param cep" in doc, "Docstring deve incluir ':param cep'"
    # Verifica se existe seção de return
    assert ":return" in doc, "Docstring deve incluir ':return'"
    # Verifica se existem seções de raises para cada exceção possível
    assert ":raises TypeError" in doc, "Docstring deve incluir ':raises TypeError'"
    assert ":raises ValueError" in doc, "Docstring deve incluir ':raises ValueError'"