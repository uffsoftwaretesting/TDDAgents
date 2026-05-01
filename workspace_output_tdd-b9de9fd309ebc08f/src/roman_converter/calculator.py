"""
Calculator functions for Roman numeral converter.

This module computes the integer value of a well-formed Roman
numeral string using additive and subtractive rules.
"""
from typing import Dict, Set

def compute_value(
    roman: str,
    values_map: Dict[str, int],
    valid_subtractions: Set[str]
) -> int:
    """
    Compute the integer value of a well-formed Roman numeral string.

    Args:
        roman (str): The Roman numeral string to convert.
        values_map (Dict[str, int]): Mapping from Roman symbols to values.
        valid_subtractions (Set[str]): Valid subtractive pairs.

    Returns:
        int: The integer value of the Roman numeral.
    """
    total = 0
    i = 0
    length = len(roman)
    while i < length:
        # Check for valid subtractive pair
        if i + 1 < length and roman[i:i+2] in valid_subtractions:
            total += (
                values_map[roman[i+1]]
                - values_map[roman[i]]
            )
            i += 2
        else:
            total += values_map[roman[i]]
            i += 1
    return total