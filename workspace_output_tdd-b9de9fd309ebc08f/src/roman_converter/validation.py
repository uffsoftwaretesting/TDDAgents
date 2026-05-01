"""
Validation functions for the Roman numeral converter.

This module provides routines to check that a Roman numeral string:
- Is not empty.
- Contains only valid Roman symbols.
- Does not exceed repetition rules and uses only
  valid subtractive pairs.
"""
from typing import Dict, Set

def validate_non_empty(roman: str) -> None:
    """
    Verifies that the input string is not empty.

    Args:
        roman (str): The Roman numeral string to validate.

    Raises:
        ValueError: If the input string is empty.
    """
    if not roman:
        raise ValueError("Input string is empty")

def validate_characters(roman: str, symbols: Set[str]) -> None:
    """
    Verifies that all characters in the Roman numeral string are valid.

    Args:
        roman (str): The Roman numeral string to validate.
        symbols (Set[str]): Allowed Roman numeral symbols.

    Raises:
        ValueError: If any character is not in the allowed symbols.
    """
    for char in roman:
        if char not in symbols:
            raise ValueError(f"Invalid character: {char}")

def validate_repetitions_and_subtractions(
    roman: str,
    max_repeats: Dict[str, int],
    valid_subtractions: Set[str]
) -> None:
    """
    Verifies that the Roman numeral string does not contain excessive
    repetitions and only uses valid subtractive pairs.

    Args:
        roman (str): The Roman numeral string to validate.
        max_repeats (Dict[str, int]): Maximum allowed consecutive repeats.
        valid_subtractions (Set[str]): Valid subtractive symbol pairs.

    Raises:
        ValueError: If there are too many repetitions or invalid subtractive
            pairs.
    """
    # Check for excessive repetitions
    count = 1
    for i in range(1, len(roman)):
        if roman[i] == roman[i - 1]:
            count += 1
            if count > max_repeats.get(roman[i], 0):
                raise ValueError(f"Too many repetitions: {roman[i]}")
        else:
            count = 1

    # Check for invalid subtractive pairs
    values_map = {
        'I': 1,
        'V': 5,
        'X': 10,
        'L': 50,
        'C': 100,
        'D': 500,
        'M': 1000
    }
    for i in range(len(roman) - 1):
        current = roman[i]
        nxt = roman[i + 1]
        if values_map[current] < values_map[nxt]:
            pair = current + nxt
            if pair not in valid_subtractions:
                raise ValueError(f"Invalid subtractive pair: {pair}")