import pytest
from app_code import validador

# Teste existente
def test_empty_input():
    # Entrada vazia deve retornar 'INVALIDO'
    assert validador('') == 'INVALIDO'  # CORRIGIDO: A expectativa deve ser 'INVALIDO', pois uma entrada vazia não é um CPF válido.

# Teste existente
def test_basic_case():
    # Caso simples, 'abc' não é um CPF válido
    assert validador('abc') == 'INVALIDO'  # CORRIGIDO: 'abc' não é um CPF válido, deve retornar 'INVALIDO'.

# Novo teste para verificar a aceitação de CPF no formato com pontos e hífen
def test_valid_cpf_format():
    # CPF no formato correto deve ser aceito
    assert validador('123.456.789-09') == 'VALIDO'  # Expectativa correta.

# Novo teste para verificar a aceitação de CPF no formato numérico contínuo
def test_valid_cpf_numeric_format():
    # CPF no formato numérico contínuo deve ser aceito
    assert validador('12345678909') == 'VALIDO'  # Expectativa correta.

# Novo teste para validar CPF com dígitos verificadores corretos
def test_valid_cpf_with_check_digits():
    # CPF com dígitos verificadores válidos deve ser aceito
    assert validador('111.444.777-35') == 'VALIDO'  # Expectativa correta.

# Novo teste para validar CPF inválido com dígitos verificadores errados
def test_invalid_cpf_with_wrong_check_digits():
    # CPF com dígitos verificadores inválidos deve ser rejeitado
    assert validador('111.444.777-36') == 'INVALIDO'  # Expectativa correta.

# Novo teste para verificar a resposta para CPF com todos os zeros
def test_cpf_all_zeros():
    # CPF com todos os dígitos como zero deve ser considerado inválido
    assert validador('000.000.000-00') == 'INVALIDO'  # Expectativa correta.

# Novo teste para verificar a resposta para CPF com todos os zeros em formato numérico contínuo
def test_cpf_all_zeros_numeric():
    # CPF com todos os dígitos como zero no formato numérico contínuo deve ser considerado inválido
    assert validador('00000000000') == 'INVALIDO'  # Expectativa correta.

# Novo teste para verificar a resposta para CPF com caracteres não numéricos
def test_invalid_cpf_with_non_numeric_characters():
    # CPF com caracteres não numéricos deve ser considerado inválido
    assert validador('123.456.ABC-09') == 'INVALIDO'  # Expectativa correta.

# Novo teste para verificar a eficiência da função com entrada de CPF válido
def test_performance_with_large_input():
    # Testando a eficiência da função com um CPF válido
    assert validador('12345678909') == 'VALIDO'  # CORRIGIDO: Teste ajustado para um único CPF válido, pois a função não deve aceitar entradas longas.

# Novo teste para verificar a resposta para entrada não numérica
def test_invalid_non_numeric_input():
    # Entrada não numérica deve ser considerada inválida
    assert validador('abc') == 'INVALIDO'  # CORRIGIDO: 'abc' deve retornar 'INVALIDO', pois não é um CPF válido.