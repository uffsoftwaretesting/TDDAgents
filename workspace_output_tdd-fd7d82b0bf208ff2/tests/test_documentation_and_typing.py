import inspect
import pytest

from src.roman import converter, exceptions
from src.roman.converter import roman_to_int
from src.roman.exceptions import InvalidRomanNumeralError, OutOfRangeError


def test_converter_module_docstring_exists():
    """
    The converter module must have a non-empty module-level docstring.
    """
    assert converter.__doc__ is not None and converter.__doc__.strip(), \
        "The 'converter' module should have a non-empty docstring."


def test_exceptions_module_docstring_exists():
    """
    The exceptions module must have a non-empty module-level docstring.
    """
    assert exceptions.__doc__ is not None and exceptions.__doc__.strip(), \
        "The 'exceptions' module should have a non-empty docstring."


def test_roman_to_int_function_docstring_exists():
    """
    The roman_to_int function must have a non-empty docstring describing its contract.
    """
    doc = roman_to_int.__doc__
    assert doc is not None and doc.strip(), \
        "Function 'roman_to_int' should have a non-empty docstring."


def test_exception_classes_docstring_exists():
    """
    Custom exception classes must have docstrings explaining when they are raised.
    """
    for exc in (InvalidRomanNumeralError, OutOfRangeError):
        doc = exc.__doc__
        assert doc is not None and doc.strip(), \
            f"Exception class '{exc.__name__}' should have a non-empty docstring."


def test_roman_to_int_type_hints():
    """
    roman_to_int must be statically typed: parameter 'roman' as str and return type as int.
    """
    sig = inspect.signature(roman_to_int)
    params = sig.parameters
    # Check parameter annotation
    assert 'roman' in params, "roman_to_int should have a 'roman' parameter"
    assert params['roman'].annotation is str, \
        "Parameter 'roman' should be annotated with type 'str'."
    # Check return annotation
    assert sig.return_annotation is int, \
        "The return type of roman_to_int should be annotated as 'int'."