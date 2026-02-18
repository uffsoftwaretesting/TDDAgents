import pytest
from app_code import convert_roman_to_integer

def test_convert_roman_to_integer_I():
    assert convert_roman_to_integer("I") == 1

def test_convert_roman_to_integer_IV():
    assert convert_roman_to_integer("IV") == 4

def test_convert_roman_to_integer_XIII():
    assert convert_roman_to_integer("XIII") == 13

def test_convert_roman_to_integer_MCMXCIV():
    assert convert_roman_to_integer("MCMXCIV") == 1994

def test_convert_roman_to_integer_MMMCMXCIX():
    assert convert_roman_to_integer("MMMCMXCIX") == 3999

def test_convert_roman_to_integer_empty():
    # Deve lançar uma exceção ou retornar um valor de erro específico
    with pytest.raises(ValueError):
        convert_roman_to_integer("")

def test_convert_roman_to_integer_invalid():
    # Deve lançar uma exceção ou retornar um valor de erro específico
    with pytest.raises(ValueError):
        convert_roman_to_integer("ABC")

def test_convert_roman_to_integer_IV_new():
    assert convert_roman_to_integer("IV") == 4

def test_convert_roman_to_integer_XIII_new():
    assert convert_roman_to_integer("XIII") == 13

def test_convert_roman_to_integer_MCMXCIV_new():
    assert convert_roman_to_integer("MCMXCIV") == 1994

def test_convert_roman_to_integer_MMMCMXCIX_new():
    assert convert_roman_to_integer("MMMCMXCIX") == 3999

def test_convert_roman_to_integer_empty_new():
    # Testar entrada vazia e verificar tratamento de erro
    with pytest.raises(ValueError):
        convert_roman_to_integer("")

def test_convert_roman_to_integer_invalid_characters():
    # Testar entrada inválida com caracteres não romanos
    with pytest.raises(ValueError):
        convert_roman_to_integer("12345")