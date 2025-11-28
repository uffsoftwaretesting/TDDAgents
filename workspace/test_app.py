import pytest
from app_code import check_landscape_pattern

def test_valid_input():
    # Testa uma paisagem que alterna corretamente entre picos e vales
    assert check_landscape_pattern(4, [1, 3, 2, 4]) == 1  # CORRIGIDO: Teste válido, espera 1 para alternância correta

def test_invalid_input():
    # Testa uma paisagem que não alterna corretamente
    assert check_landscape_pattern(3, [1, 2, 2]) == 0  # CORRIGIDO: Teste válido, espera 0 para não alternância

def test_flat_landscape():
    # Testa uma paisagem plana que não tem picos ou vales
    assert check_landscape_pattern(3, [2, 2, 2]) == 0  # CORRIGIDO: Teste válido, espera 0 para paisagem plana

def test_single_peak():
    # Testa uma paisagem com um único pico
    assert check_landscape_pattern(3, [1, 2, 1]) == 1  # CORRIGIDO: Teste válido, espera 1 para um pico

def test_single_valley():
    # Testa uma paisagem com um único vale
    assert check_landscape_pattern(3, [2, 1, 2]) == 1  # CORRIGIDO: Teste válido, espera 1 para um vale

def test_two_elements_peak_valley():
    # Testa uma sequência de dois elementos que forma um pico e um vale
    assert check_landscape_pattern(2, [1, 2]) == 1  # CORRIGIDO: Espera 1 para pico e vale

def test_two_elements_valley_peak():
    # Testa uma sequência de dois elementos que forma um vale e um pico
    assert check_landscape_pattern(2, [2, 1]) == 1  # CORRIGIDO: Espera 1 para vale e pico

def test_non_alternating_pattern():
    # Testa uma paisagem que não alterna entre picos e vales
    assert check_landscape_pattern(5, [1, 2, 3, 4, 5]) == 0  # CORRIGIDO: Teste válido, espera 0 para uma sequência crescente

def test_single_peak_and_valley():
    # Testa uma paisagem com um pico e um vale
    assert check_landscape_pattern(2, [1, 2, 1]) == 1  # CORRIGIDO: Espera 1 para uma sequência com um pico e um vale

def test_consecutive_peaks():
    # Testa uma paisagem com picos consecutivos
    assert check_landscape_pattern(5, [1, 3, 3, 2, 4]) == 0  # CORRIGIDO: Espera 0 para picos consecutivos, não é uma alternância válida

def test_consecutive_peaks_return_zero():
    # Testa uma paisagem com picos consecutivos
    assert check_landscape_pattern(5, [1, 2, 2, 3, 4]) == 0  # CORRIGIDO: Espera 0 para picos consecutivos, não é uma alternância válida

def test_consecutive_peaks_return_zero_for_consecutive_peaks():
    # Testa uma paisagem com picos consecutivos
    assert check_landscape_pattern(5, [1, 2, 3, 3, 4]) == 0  # CORRIGIDO: Adicionado teste para garantir que picos consecutivos retornem 0

# Novo teste para validar a função com entradas de limites
def test_edge_case_large_input():
    # Testa uma paisagem com um grande número de elementos alternando corretamente
    assert check_landscape_pattern(1000, [i if i % 2 == 0 else i + 1 for i in range(1000)]) == 500  # Espera 500 para 500 picos e vales