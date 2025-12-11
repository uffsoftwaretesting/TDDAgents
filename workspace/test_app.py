import pytest
from app_code import validate_cpf

def test_valid_cpf():
    assert validate_cpf('123.456.789-09') == True

def test_invalid_cpf():
    assert validate_cpf('123.456.789-00') == False

def test_cpf_with_letters():
    assert validate_cpf('123.456.78A-09') == False

def test_cpf_with_special_characters():
    assert validate_cpf('123.456.789-09!') == False

def test_cpf_with_spaces():
    assert validate_cpf(' 123.456.789-09 ') == True

# Novo teste para CPF válido sem pontuação
def test_valid_cpf_without_punctuation():
    assert validate_cpf('12345678909') == True

# Novo teste para CPF com pontuação e inválido
def test_cpf_with_punctuation_and_invalid():
    assert validate_cpf('123.456.789-00') == False

# Novo teste para CPF com todos os dígitos iguais e inválido
def test_cpf_with_all_digits_equal():
    assert validate_cpf('111.111.111-11') == False

# Novo teste para CPF vazio
def test_empty_cpf():
    assert validate_cpf('') == False

# Novo teste para CPF com pontuação e inválido (caso adicional)
def test_cpf_with_punctuation_and_invalid_case_additional():
    assert validate_cpf('123.456.789-01') == False