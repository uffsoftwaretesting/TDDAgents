import pytest
from app_code import validate_cpf

def test_valid_cpf():
    assert validate_cpf('123.456.789-09') == True

def test_invalid_cpf_with_letters():
    assert validate_cpf('123.456.789-0A') == False

def test_invalid_cpf_with_wrong_length():
    assert validate_cpf('123.456.789') == False

def test_invalid_cpf_with_repeated_digits():
    assert validate_cpf('111.111.111-11') == False

def test_valid_cpf_without_punctuation():
    assert validate_cpf('12345678909') == True

# Novo teste para validar a validação de comprimento do CPF
def test_invalid_cpf_with_short_length():
    assert validate_cpf('123.456.78') == False

def test_removal_of_punctuation():
    assert validate_cpf('123.456.789-09') == validate_cpf('12345678909')

# Novo teste para verificar dígitos iguais no CPF
def test_invalid_cpf_with_all_same_digits():
    assert validate_cpf('000.000.000-00') == False

# Novo teste para testar comparação dos dígitos verificadores
def test_compare_verifier_digits():
    assert validate_cpf('123.456.789-09') == True  # O CPF é válido e os dígitos verificadores devem ser calculados corretamente.

# Novo teste para validar um CPF válido
def test_another_valid_cpf():
    assert validate_cpf('987.654.321-00') == True  # Outro exemplo de CPF válido

# Novo teste para CPF inválido
def test_invalid_cpf():
    assert validate_cpf('123.456.789-10') == False  # CPF inválido com dígitos verificadores incorretos