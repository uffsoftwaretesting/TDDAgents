import pytest
import math
from converter import fahrenheit_to_celsius

@ pytest.mark.parametrize(
    "invalid_value, expected_type_name",
    [
        ("string", "str"),
        (None, "NoneType"),
        ([], "list"),
        ({}, "dict"),
        (object(), "object"),
    ],
)
def test_invalid_type_raises_type_error_with_message(invalid_value, expected_type_name):
    """
    Entradas não numéricas devem levantar TypeError com mensagem indicando o tipo inválido.
    """
    with pytest.raises(TypeError) as excinfo:
        fahrenheit_to_celsius(invalid_value)
    # Verifica a mensagem exata de TypeError
    assert str(excinfo.value) == f"Invalid type: {expected_type_name}"


def test_nan_input_returns_nan():
    """
    Se input é NaN, deve retornar NaN usando math.isnan.
    """
    result = fahrenheit_to_celsius(float('nan'))
    assert math.isnan(result)


def test_infinite_positive_input_returns_inf():
    """
    Se input é +inf, deve retornar +inf usando math.isinf.
    """
    result = fahrenheit_to_celsius(float('inf'))
    assert math.isinf(result) and result > 0


def test_infinite_negative_input_returns_neg_inf():
    """
    Se input é -inf, deve retornar -inf usando math.isinf.
    """
    result = fahrenheit_to_celsius(float('-inf'))
    assert math.isinf(result) and result < 0


@pytest.mark.parametrize("fahrenheit, expected_celsius", [
    (32, 0.0),
    (212, 100.0),
    (-40, -40.0),
    (100, (100 - 32) * 5.0 / 9.0),
    (0, (0 - 32) * 5.0 / 9.0),
])
def test_finite_values_conversion(fahrenheit, expected_celsius):
    """
    Valores finitos devem ser convertidos corretamente de Fahrenheit para Celsius.
    """
    result = fahrenheit_to_celsius(fahrenheit)
    assert result == pytest.approx(expected_celsius)


@ pytest.mark.parametrize("input_value", [32, 32.0, 212, 212.0, -40, -40.0, 100, 100.0, 0, 0.0])
def test_return_type_is_float(input_value):
    """
    O tipo de retorno deve ser sempre float para entradas int e float.
    """
    result = fahrenheit_to_celsius(input_value)
    assert isinstance(result, float)
