def preprocess_input(s: str) -> str:
    """
    Strips leading/trailing whitespace from the input string, ensures it is not empty,
    and returns it in uppercase form. Raises ValueError if the stripped string is empty.
    """
    # Remove leading and trailing whitespace
    stripped = s.strip()
    # Check for empty input after stripping
    if not stripped:
        raise ValueError("Entrada vazia")
    # Normalize to uppercase
    return stripped.upper()


def validate_characters(s: str) -> None:
    """
    Validates that each character in the string is a valid Roman numeral symbol.
    Raises ValueError on the first invalid character.
    """
    allowed = set("IVXLCDM")
    for c in s:
        if c not in allowed:
            raise ValueError(f"Caractere inválido: {c}")
    # If all characters are valid, do nothing
    return None


def validate_repetition(s: str) -> None:
    """
    Validates that the string does not contain invalid repetitions:
    - I, X, C, M not more than three times consecutively
    - V, L, D not repeated consecutively
    Raises ValueError if any invalid repetition is found.
    """
    invalid_patterns = ["IIII", "XXXX", "CCCC", "MMMM", "VV", "LL", "DD"]
    for pattern in invalid_patterns:
        if pattern in s:
            raise ValueError(f"Repetição inválida: {s}")
    return None


def validate_subtraction_pairs(s: str) -> None:
    """
    Validates that any subtraction pair in the string is one of the allowed pairs:
    IV, IX, XL, XC, CD, CM. Raises ValueError on the first invalid pair found.
    """
    # Mapping of Roman numerals to values
    values = {
        'I': 1,
        'V': 5,
        'X': 10,
        'L': 50,
        'C': 100,
        'D': 500,
        'M': 1000
    }
    valid_pairs = {'IV', 'IX', 'XL', 'XC', 'CD', 'CM'}
    length = len(s)
    for i in range(length - 1):
        first = s[i]
        second = s[i + 1]
        # If a smaller numeral precedes a larger, it's a subtraction scenario
        if values.get(first, 0) < values.get(second, 0):
            pair = first + second
            if pair not in valid_pairs:
                raise ValueError(f"Subtração inválida: {pair}")
    return None


def compute_value(s: str) -> int:
    """
    Computes the integer value of a well-formed Roman numeral string.
    Assumes input is already valid uppercase Roman numerals.
    """
    values = {
        'I': 1,
        'V': 5,
        'X': 10,
        'L': 50,
        'C': 100,
        'D': 500,
        'M': 1000
    }
    total = 0
    length = len(s)
    for i in range(length):
        current_val = values.get(s[i], 0)
        if i + 1 < length:
            next_val = values.get(s[i + 1], 0)
            if current_val < next_val:
                total -= current_val
            else:
                total += current_val
        else:
            total += current_val
    return total


def roman_to_int(s: str) -> int:
    """
    Converts a Roman numeral string to its integer value in the range [1,3999].

    Steps:
    1. Preprocess input (strip & uppercase). Invalid empties map to range error.
    2. Validate characters only.
    3. Compute numeric value.
    4. Enforce [1,3999] bounds.
    5. After range is assured, validate repetition and subtraction rules.
    """
    try:
        normalized = preprocess_input(s)
    except ValueError:
        raise ValueError("Valor fora do intervalo permitido")
    validate_characters(normalized)
    value = compute_value(normalized)
    if value < 1 or value > 3999:
        raise ValueError("Valor fora do intervalo permitido")
    # Only after passing range, enforce structural rules
    validate_repetition(normalized)
    validate_subtraction_pairs(normalized)
    return value
