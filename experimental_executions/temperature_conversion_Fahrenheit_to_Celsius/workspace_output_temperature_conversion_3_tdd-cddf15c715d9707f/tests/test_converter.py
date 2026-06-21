import pytest
import inspect
import math
import src.converter


def test_module_importable():
    try:
        import src.converter
    except ImportError:
        pytest.fail("Module src.converter should be importable")


def test_function_fahrenheit_to_celsius_exists():
    from src.converter import fahrenheit_to_celsius
    # A função deve estar disponível e ser chamável
    assert callable(fahrenheit_to_celsius)


def test_signature_of_fahrenheit_to_celsius():
    from src.converter import fahrenheit_to_celsius
    # Verifica se o nome do parâmetro está correto
    sig = inspect.signature(fahrenheit_to_celsius)
    params = list(sig.parameters.keys())
    assert params == ['fahrenheit']

@ pytest.mark.parametrize("invalid_input", [None, "123", [], {}])
def test_invalid_type_raises_typeerror(invalid_input):
    from src.converter import fahrenheit_to_celsius
    # Valores de tipos inválidos devem lançar TypeError com mensagem exata
    with pytest.raises(TypeError) as excinfo:
        fahrenheit_to_celsius(invalid_input)
    assert str(excinfo.value) == "fahrenheit must be int or float"

@ pytest.mark.parametrize("fahrenheit, expected", [
    (math.inf, math.inf),
    (-math.inf, -math.inf)
])
def test_infinite_values_propagation(fahrenheit, expected):
    from src.converter import fahrenheit_to_celsius
    # Propagar infinitos sem alteração
    result = fahrenheit_to_celsius(fahrenheit)
    assert result == expected


def test_nan_value_propagation():
    from src.converter import fahrenheit_to_celsius
    # Propagar NaN sem alteração
    result = fahrenheit_to_celsius(math.nan)
    assert math.isnan(result)

@ pytest.mark.parametrize("fahrenheit, expected", [
    # Casos típicos
    (32, 0.0),
    (212, 100.0),
    # Valores negativos
    (-40, -40.0),
    (-58, -50.0),
    # Valores fracionários
    (98.6, 37.0),
    (0.5, -17.5),
])
def test_numeric_conversion_cases(fahrenheit, expected):
    from src.converter import fahrenheit_to_celsius
    # Verifica o cálculo numérico para diversos casos
    result = fahrenheit_to_celsius(fahrenheit)
    assert result == expected

@ pytest.mark.parametrize("fahrenheit", [32, 212, -40, -58])
def test_return_type_int_inputs(fahrenheit):
    from src.converter import fahrenheit_to_celsius
    # Mesmo entrada int deve retornar float
    result = fahrenheit_to_celsius(fahrenheit)
    assert isinstance(result, float)


def test_docstring_exists_and_contains_summary():
    from src.converter import fahrenheit_to_celsius
    # A docstring da função deve existir e descrever o propósito
    doc = fahrenheit_to_celsius.__doc__
    assert isinstance(doc, str) and len(doc.strip()) > 0
    assert "Convert a temperature from Fahrenheit to Celsius." in doc


def test_return_type_for_float_input():
    from src.converter import fahrenheit_to_celsius
    # Mesmo entrada float deve resultar em float
    result = fahrenheit_to_celsius(0.0)
    assert isinstance(result, float)


def test_boolean_input():
    from src.converter import fahrenheit_to_celsius
    # bool é subclasse de int e deve ser processado
    result = fahrenheit_to_celsius(True)
    expected = (True - 32) * 5.0 / 9.0
    assert result == expected
    assert isinstance(result, float)