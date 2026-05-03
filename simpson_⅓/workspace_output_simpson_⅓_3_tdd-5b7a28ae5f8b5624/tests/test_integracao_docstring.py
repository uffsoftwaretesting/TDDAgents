from src.integracao import integracao_simpson_1_3


def test_docstring_not_empty():
    # Verifica que a função possui docstring
    assert hasattr(integracao_simpson_1_3, '__doc__'), \
        'integracao_simpson_1_3 deve ter atributo __doc__'
    doc = integracao_simpson_1_3.__doc__
    # Docstring deve ser string não vazia
    assert isinstance(doc, str), '__doc__ deve ser uma string'
    assert doc.strip() != '', '__doc__ não deve estar vazia'
