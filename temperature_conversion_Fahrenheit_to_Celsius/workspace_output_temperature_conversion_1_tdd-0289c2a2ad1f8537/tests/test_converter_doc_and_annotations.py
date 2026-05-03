import math
import typing
import pytest
from temperature_converter.converter import fahrenheit_to_celsius

def test_docstring_exists_and_describes_function():
    # Docstring must exist and mention converting Fahrenheit to Celsius
    doc = fahrenheit_to_celsius.__doc__
    assert isinstance(doc, str) and len(doc) > 0, "Docstring should be a non-empty string"
    assert "Converts a temperature" in doc or "Converts Fahrenheit" in doc, \
        "Docstring should describe conversion from Fahrenheit to Celsius"


def test_type_annotations_for_parameter_and_return():
    # The function must be annotated to accept int or float and return float
    annotations = fahrenheit_to_celsius.__annotations__
    # Check parameter annotation
    assert 'temperature' in annotations, "Missing annotation for 'temperature' parameter"
    temp_ann = annotations['temperature']
    # temp_ann should be typing.Union[int, float]
    assert typing.get_origin(temp_ann) is typing.Union, \
        f"Expected temperature annotation to be Union[int, float], got {temp_ann}"
    assert set(typing.get_args(temp_ann)) == {int, float}, \
        f"Expected Union[int, float] for temperature, got {typing.get_args(temp_ann)}"
    # Check return annotation
    assert 'return' in annotations, "Missing return type annotation"
    assert annotations['return'] is float, \
        f"Expected return annotation to be float, got {annotations['return']}"
