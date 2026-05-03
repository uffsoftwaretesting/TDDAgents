import pytest
import inspect
from calculate_discount import calculate_discount


def test_return_structure_and_types():
    """
    Verifica se o resultado é um dict com as chaves corretas e valores do tipo float.
    """
    result = calculate_discount(123.45, 10)
    assert isinstance(result, dict), "O retorno deve ser um dicionário"
    assert set(result.keys()) == {'discount', 'final_price'}, "As chaves devem ser 'discount' e 'final_price'"
    assert isinstance(result['discount'], float), "'discount' deve ser float"
    assert isinstance(result['final_price'], float), "'final_price' deve ser float"


def test_exception_messages_type_error_price():
    """
    Verifica mensagem exata de TypeError para price inválido.
    """
    with pytest.raises(TypeError) as excinfo:
        calculate_discount('100', 10)
    assert str(excinfo.value) == "price must be an int or float"


def test_exception_messages_type_error_discount_percent():
    """
    Verifica mensagem exata de TypeError para discount_percent inválido.
    """
    with pytest.raises(TypeError) as excinfo:
        calculate_discount(100, None)
    assert str(excinfo.value) == "discount_percent must be an int or float"


def test_exception_messages_value_error_price_negative():
    """
    Verifica mensagem exata de ValueError quando price < 0.
    """
    with pytest.raises(ValueError) as excinfo:
        calculate_discount(-0.01, 10)
    assert str(excinfo.value) == "price must be non-negative"


def test_exception_messages_value_error_discount_out_of_range():
    """
    Verifica mensagem exata de ValueError quando discount_percent > 100.
    """
    with pytest.raises(ValueError) as excinfo:
        calculate_discount(100, 150)
    assert str(excinfo.value) == "discount_percent must be between 0 and 100"


def test_docstring_presence_and_content():
    """
    Garante que a docstring existe e menciona o cálculo de desconto.
    """
    doc = inspect.getdoc(calculate_discount)
    assert doc is not None, "A função deve ter docstring"
    assert 'desconto' in doc.lower(), "Docstring deve descrever 'desconto'"
    assert 'price' in doc.lower() and 'discount' in doc.lower(), "Docstring deve mencionar price e discount_percent"


def test_rounding_half_up():
    """
    Cenário de arredondamento half-up: price=2.675, desconto=100%.
    """
    result = calculate_discount(2.675, 100)
    # raw_discount = 2.675, arredonda para 2.68
    assert result == {'discount': 2.68, 'final_price': 0.00}


def test_small_values_precision():
    """
    Valores muito pequenos devem resultar em zero quando arredondados.
    """
    result = calculate_discount(0.005, 50)
    # raw_discount = 0.0025 -> 0.00, final_price = 0.0025 -> 0.00
    assert result == {'discount': 0.00, 'final_price': 0.00}


def test_large_values_precision():
    """
    Valores grandes devem manter precisão a duas casas.
    """
    price = 1e8
    percent = 12.3456
    result = calculate_discount(price, percent)
    # raw_discount = 100000000 * 0.123456 = 12345600.00
    expected = {'discount': 12345600.00, 'final_price': 87654400.00}
    assert result == expected
