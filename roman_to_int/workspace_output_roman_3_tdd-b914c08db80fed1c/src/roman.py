# src/roman.py
# Módulo para conversão de numerais romanos (implementação mínima para o teste atual)

ROMAN_VALUES = {
    'I': 1,
    'V': 5,
    'X': 10,
    'L': 50,
    'C': 100,
    'D': 500,
    'M': 1000,
}

# Pares de subtração válidos
VALID_SUBTRACTIVE_PAIRS = {'IV', 'IX', 'XL', 'XC', 'CD', 'CM'}

# Limites do domínio
MIN_VALUE = 1
MAX_VALUE = 3999


def roman_to_int(roman: str) -> int:
    # Validação de tipo: apenas str é permitido
    if not isinstance(roman, str):
        raise TypeError("Input must be a string")

    # Normalização para uppercase
    roman = roman.upper()

    # String vazia não é válida
    if len(roman) == 0:
        raise ValueError("Input cannot be empty")

    total = 0
    length = len(roman)

    prev_char = None
    repeat_count = 0

    for i, ch in enumerate(roman):
        # Validação de caractere
        if ch not in ROMAN_VALUES:
            raise ValueError(f"Invalid Roman numeral character: {ch}")

        # Verificação de repetição
        if ch == prev_char:
            repeat_count += 1
        else:
            prev_char = ch
            repeat_count = 1

        # Regras de repetição:
        # V, L, D não podem repetir consecutivamente
        if ch in ('V', 'L', 'D') and repeat_count > 1:
            raise ValueError(f"Invalid repetition of roman numeral: {ch}")
        # I, X, C, M no máximo 3 vezes consecutivas
        if ch in ('I', 'X', 'C', 'M') and repeat_count > 3:
            raise ValueError(f"Invalid repetition of roman numeral: {ch}")

        value = ROMAN_VALUES[ch]

        # Se próximo existe e é maior, possivelmente subtrai; caso contrário, soma
        if i + 1 < length:
            next_ch = roman[i + 1]
            if next_ch not in ROMAN_VALUES:
                raise ValueError(f"Invalid Roman numeral character: {next_ch}")
            next_value = ROMAN_VALUES[next_ch]
            if value < next_value:
                pair = ch + next_ch
                # Verifica par subtrativo válido e sem repetição antes da subtração
                if pair not in VALID_SUBTRACTIVE_PAIRS or repeat_count > 1:
                    raise ValueError(f"Invalid subtractive pair: {pair}")
                total -= value
                continue
        # Soma normal
        total += value

    # Validação de faixa de resultado
    if total < MIN_VALUE or total > MAX_VALUE:
        raise ValueError(f"Result out of range: {total}")

    return total
