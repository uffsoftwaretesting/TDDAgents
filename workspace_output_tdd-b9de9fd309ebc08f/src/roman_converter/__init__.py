"""Roman Numeral Converter package."""

from .constants import symbols, values_map, max_repeats, valid_subtractions
from .validation import validate_non_empty, validate_characters, validate_repetitions_and_subtractions
from .calculator import compute_value

def roman_to_int(roman: str) -> int:
    """
    Convert a Roman numeral string to its integer value.

    Args:
        roman (str): The Roman numeral to convert.

    Returns:
        int: The integer value of the Roman numeral.

    Raises:
        ValueError: If the input is empty, contains invalid characters,
            has invalid repetitions or subtractive pairs, or the result
            is out of the allowed range (1–3999).
    """
    # Validate input
    validate_non_empty(roman)
    validate_characters(roman, symbols)
    validate_repetitions_and_subtractions(roman, max_repeats, valid_subtractions)

    # Compute value
    value = compute_value(roman, values_map, valid_subtractions)

    # Check range
    if value < 1 or value > 3999:
        raise ValueError(f"Result out of range (1–3999): {value}")

    return value