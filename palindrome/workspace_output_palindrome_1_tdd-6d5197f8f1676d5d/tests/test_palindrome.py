import pytest
from src.palindrome_checker import is_palindrome, normalize_string

# Existing tests for is_palindrome

def test_empty_string():
    assert is_palindrome('') is True

def test_single_char_alpha():
    assert is_palindrome('a') is True
    assert is_palindrome('Z') is True

def test_single_char_non_alnum():
    # após normalização fica string vazia
    assert is_palindrome('$') is True

def test_only_non_alphanumeric():
    # sem caracteres alfanuméricos, normaliza para vazia
    assert is_palindrome('!!!***') is True

def test_simple_palindrome():
    assert is_palindrome('racecar') is True

def test_mixed_case_and_punctuation():
    s = 'A man, a plan, a canal: Panama'
    assert is_palindrome(s) is True

def test_palindrome_with_numbers_and_letters():
    assert is_palindrome('1A2B2A1') is True

def test_non_palindrome_string():
    assert is_palindrome('hello') is False

def test_none_input_raises_type_error():
    with pytest.raises(TypeError) as excinfo:
        is_palindrome(None)
    assert str(excinfo.value) == "Input must be a string, got <class 'NoneType'>"

def test_int_input_raises_type_error():
    with pytest.raises(TypeError) as excinfo:
        is_palindrome(123)
    assert str(excinfo.value) == "Input must be a string, got <class 'int'>"

def test_list_input_raises_type_error():
    with pytest.raises(TypeError) as excinfo:
        is_palindrome([1, 2, 3])
    assert str(excinfo.value) == "Input must be a string, got <class 'list'>"

def test_dict_input_raises_type_error():
    with pytest.raises(TypeError) as excinfo:
        is_palindrome({'a': 1})
    assert str(excinfo.value) == "Input must be a string, got <class 'dict'>"

# New simple tests for sub-requisito atual: basic, no punctuation

def test_simple_mixed_case_palindrome_level():
    # mixed case, no punctuation
    assert is_palindrome('Level') is True

def test_simple_non_palindrome_word_palindrome():
    # simple non-palindrome word
    assert is_palindrome('palindrome') is False

# New tests for normalize_string

def test_normalize_empty_string():
    assert normalize_string("") == ""

def test_normalize_only_non_alphanumeric():
    # resultados não alfanuméricos devem sumir
    assert normalize_string("!!!***   --") == ""

def test_normalize_alphanumeric_string():
    assert normalize_string("ABC123") == "abc123"

def test_normalize_mixed_alphanumeric_and_punctuation():
    s = 'A man, a plan, a canal: Panama'
    expected = 'amanaplanacanalpanama'
    assert normalize_string(s) == expected

def test_normalize_with_spaces_and_symbols():
    assert normalize_string("Hello, World!") == "helloworld"

def test_normalize_mixed_case_and_numbers():
    assert normalize_string("MixedCASE And 123") == "mixedcaseand123"

# Additional edge-case tests: strings contendo apenas espaços ou símbolos

def test_spaces_only_string():
    # somente espaços, normaliza para vazio
    assert is_palindrome('   ') is True

def test_mixed_punctuation_only():
    # apenas símbolos e pontuação, normaliza para vazio
    assert is_palindrome('!,.?!!.') is True

def test_symbols_and_spaces_only():
    # símbolos misturados com espaços
    assert is_palindrome('   !!! ???   ') is True

# Fase 5: Casos negativos e misturados (não palíndromos)

def test_non_palindrome_with_punctuation():
    # string com pontuação e mistura de caracteres, não é palíndromo
    s = "Hello, World!"
    assert is_palindrome(s) is False

def test_non_palindrome_mixed_characters_and_symbols():
    # mistura de letras, números e símbolos que não formam palíndromo
    s = "Ab3@ba1"
    assert is_palindrome(s) is False
