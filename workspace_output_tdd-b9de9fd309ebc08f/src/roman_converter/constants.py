"""
Constants for Roman numeral converter.

Attributes:
    symbols (Set[str]): Basic Roman numeral symbols.
    values_map (Dict[str, int]): Mapping from Roman symbols to integer values.
    max_repeats (Dict[str, int]): Maximum allowed consecutive repeats per symbol.
    valid_subtractions (Set[str]): Valid subtractive symbol pairs.
"""
from typing import Dict, Set

# Basic Roman numeral symbols
symbols: Set[str] = {"I", "V", "X", "L", "C", "D", "M"}

# Map of Roman symbols to their integer values
values_map: Dict[str, int] = {
    'I': 1,
    'V': 5,
    'X': 10,
    'L': 50,
    'C': 100,
    'D': 500,
    'M': 1000
}

# Maximum allowed consecutive repeats per symbol
max_repeats: Dict[str, int] = {
    'I': 3,
    'X': 3,
    'C': 3,
    'M': 3,
    'V': 1,
    'L': 1,
    'D': 1
}

# Valid subtractive symbol pairs
valid_subtractions: Set[str] = {"IV", "IX", "XL", "XC", "CD", "CM"}