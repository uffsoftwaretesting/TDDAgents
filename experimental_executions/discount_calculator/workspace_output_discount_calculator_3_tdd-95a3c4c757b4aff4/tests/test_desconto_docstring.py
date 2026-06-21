import inspect
import pytest

from desconto import calcular_desconto


def test_docstring_exists_and_format():
    """
    Verifica que a função calcular_desconto possui uma docstring
    com descrição de parâmetros, retorno e exemplos.
    """
    # Obtém a docstring formatada
    doc = inspect.getdoc(calcular_desconto)
    # Deve existir docstring
    assert doc is not None, "Função calcular_desconto deve ter docstring"
    # Verifica se contém termos-chave da especificação
    assert "preco" in doc.lower(), "Docstring deve mencionar 'preco'"
    assert "percentual" in doc.lower(), "Docstring deve mencionar 'percentual'"
    assert "desconto" in doc.lower(), "Docstring deve mencionar 'desconto'"
    assert "preco_final" in doc.lower(), "Docstring deve mencionar 'preco_final'"
    # Deve conter seção de exemplos
    assert "exemplos" in doc.lower() or "examples" in doc.lower(), \
        "Docstring deve conter seção de exemplos ('Exemplos' ou 'Examples')"