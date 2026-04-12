"""
Module for converting Roman numerals to integers.
"""

from .exceptions import InvalidRomanNumeralError, OutOfRangeError

_ROMAN_NUMERAL_MAP = {
    'I': 1,
    'V': 5,
    'X': 10,
    'L': 50,
    'C': 100,
    'D': 500,
    'M': 1000,
}


def roman_to_int(roman: str) -> int:
    """
    Convert a Roman numeral string to its integer value.

    :param roman: Roman numeral as a string.
    :raises InvalidRomanNumeralError: for invalid format or characters.
    :raises OutOfRangeError: if result is outside [1, 3999].
    :return: integer value of the Roman numeral.
    """
    if not isinstance(roman, str) or len(roman) == 0:
        raise InvalidRomanNumeralError("Input must be a non-empty string.")

    roman_upper = roman.upper()

    # Validate characters and consecutive repetitions
    prev_char = None
    repeat_count = 0
    for char in roman_upper:
        if char not in _ROMAN_NUMERAL_MAP:
            raise InvalidRomanNumeralError(f"Invalid Roman numeral character: '{char}'")
        if char == prev_char:
            repeat_count += 1
        else:
            prev_char = char
            repeat_count = 1
        # I, X, C, M can repeat at most 3 times consecutively
        if char in ('I', 'X', 'C', 'M') and repeat_count > 3:
            raise InvalidRomanNumeralError(f"'{char}' repeated too many times.")
        # V, L, D cannot repeat consecutively
        if char in ('V', 'L', 'D') and repeat_count > 1:
            raise InvalidRomanNumeralError(f"'{char}' should not repeat.")

    total = 0
    length = len(roman_upper)

    for index, char in enumerate(roman_upper):
        value = _ROMAN_NUMERAL_MAP[char]
        # Determine if this is subtractive notation
        if index + 1 < length:
            next_char = roman_upper[index + 1]
            next_value = _ROMAN_NUMERAL_MAP.get(next_char)
            if next_value is None:
                raise InvalidRomanNumeralError(f"Invalid Roman numeral character: '{next_char}'")
            if value < next_value:
                # Only these subtractive pairs are valid
                pair = f"{char}{next_char}"
                if pair not in ('IV', 'IX', 'XL', 'XC', 'CD', 'CM'):
                    raise InvalidRomanNumeralError(f"Invalid subtractive pair: '{pair}'")
                total -= value
            else:
                total += value
        else:
            total += value

    if total < 1 or total > 3999:
        raise OutOfRangeError(f"Result out of range: {total}")

    return total
