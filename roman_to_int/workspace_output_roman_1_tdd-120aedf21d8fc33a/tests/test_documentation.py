import inspect
import roman_converter.converter as converter_module
from roman_converter.converter import roman_to_int


def test_module_docstring_exists_and_contains_expected_terms():
    """
    O módulo deve ter docstring descrevendo comportamentos e exceções.
    """
    doc = inspect.getdoc(converter_module)
    assert doc is not None and doc.strip(), "Module docstring is missing or empty"
    expected_terms = [
        "Convert a Roman numeral string to an integer",
        "Case-insensitive",
        "Supports basic subtractive pairs",
        "Raises ValueError",
        "result out of supported range",
        "1-3999",
    ]
    for term in expected_terms:
        assert term in doc, f"Expected '{term}' in module docstring"


def test_function_docstring_exists_and_contains_expected_terms():
    """
    A função roman_to_int deve ter docstring descrevendo comportamentos e exceções.
    """
    doc = inspect.getdoc(roman_to_int)
    assert doc is not None and doc.strip(), "Function docstring is missing or empty"
    expected_terms = [
        "Convert a Roman numeral string to an integer",
        "Case-insensitive",
        "Supports basic subtractive pairs",
        "Raises ValueError",
        "invalid subtractive",
        "result out of supported range",
        "1-3999",
    ]
    for term in expected_terms:
        assert term in doc, f"Expected '{term}' in function docstring"