import pytest
from temperature_converter import converter

def test_converter_module_importable():
    # O módulo deve ser importável
    assert converter.__name__ == "temperature_converter.converter"

def test_has_fahrenheit_to_celsius_function():
    # O módulo deve definir a função a ser implementada
    assert hasattr(converter, 'fahrenheit_to_celsius')

@pytest.mark.parametrize("invalid_input", [
    "100",
    None,
    [],
    {},
    object(),
])
def test_invalid_temperature_type_raises_type_error(invalid_input):
    # Entradas não int/float devem levar a TypeError
    with pytest.raises(TypeError, match="temperature must be an int or float"):
        converter.fahrenheit_to_celsius(invalid_input)
