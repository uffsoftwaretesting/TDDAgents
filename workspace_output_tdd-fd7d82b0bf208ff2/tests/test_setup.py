import pytest

from src.roman import exceptions, converter


def test_exceptions_module_importable():
    # Verifica que o módulo de exceções está acessível
    assert exceptions is not None


def test_converter_module_importable():
    # Verifica que o módulo de conversão está acessível
    assert converter is not None


def test_invalid_roman_numeral_error_exists():
    # Verifica que a classe de exceção exista
    assert hasattr(exceptions, 'InvalidRomanNumeralError')
    assert issubclass(exceptions.InvalidRomanNumeralError, Exception)


def test_out_of_range_error_exists():
    # Verifica que a classe de exceção exista
    assert hasattr(exceptions, 'OutOfRangeError')
    assert issubclass(exceptions.OutOfRangeError, Exception)


def test_roman_to_int_function_exists():
    # Verifica que a função 'roman_to_int' esteja declarada
    assert hasattr(converter, 'roman_to_int')
    assert callable(converter.roman_to_int)
