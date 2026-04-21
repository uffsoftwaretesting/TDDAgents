import pytest
from diferencas_finitas_bvp.validation import _validate_inputs


def test_validate_inputs_not_implemented_positional():
    """
    Chamada com parâmetros posicionais deve levantar NotImplementedError.
    """
    with pytest.raises(NotImplementedError):
        _validate_inputs(1, 'a', None)


def test_validate_inputs_not_implemented_keyword():
    """
    Chamada com parâmetros nomeados deve levantar NotImplementedError.
    """
    with pytest.raises(NotImplementedError):
        _validate_inputs(x=10, y=20)


def test_validate_inputs_not_implemented_mixed():
    """
    Chamada com parâmetros mistos deve levantar NotImplementedError.
    """
    with pytest.raises(NotImplementedError):
        _validate_inputs(100, z=200)