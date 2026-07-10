"""
Convert a Roman numeral string to an integer.

Case-insensitive. Supports basic subtractive pairs.
Raises ValueError for empty input, invalid characters, invalid repetitions, invalid subtractive combinations, or result out of supported range (1-3999).
"""

def roman_to_int(s: str) -> int:
    """
    Convert a Roman numeral string to an integer.

    Case-insensitive. Supports basic subtractive pairs.
    Raises ValueError for empty input, invalid characters, invalid repetitions, invalid subtractive combinations, or result out of supported range (1-3999).
    """
    # Empty string check
    if not s:
        raise ValueError("Input string is empty")

    # Normalize to uppercase
    s = s.upper()

    # Mapping of Roman symbols to integers
    values = {
        'I': 1,
        'V': 5,
        'X': 10,
        'L': 50,
        'C': 100,
        'D': 500,
        'M': 1000,
    }

    # Validate characters
    for ch in s:
        if ch not in values:
            raise ValueError(f"Invalid Roman numeral character: {ch}")

    # Validate repetitions
    prev = None
    count = 0
    for ch in s:
        if ch == prev:
            count += 1
        else:
            prev = ch
            count = 1
        # Symbols that can repeat up to 3 times
        if ch in ('I', 'X', 'C', 'M') and count > 3:
            raise ValueError(f"Too many repeats of symbol: {ch}")
        # Symbols that cannot repeat
        if ch in ('V', 'L', 'D') and count > 1:
            raise ValueError(f"Invalid repetition of symbol: {ch}")

    # Allowed subtractive combinations
    allowed_subtractive = {
        'I': {'V', 'X'},
        'X': {'L', 'C'},
        'C': {'D', 'M'},
    }

    total = 0
    i = 0
    length = len(s)
    while i < length:
        # If this symbol is less than the next, it's a subtractive pair
        if i + 1 < length and values[s[i]] < values[s[i + 1]]:
            curr = s[i]
            nxt = s[i + 1]
            # Validate allowed subtractive
            if curr not in allowed_subtractive or nxt not in allowed_subtractive[curr]:
                raise ValueError(f"Invalid subtractive combination: {curr}{nxt}")
            total += values[nxt] - values[curr]
            i += 2
        else:
            total += values[s[i]]
            i += 1

    # Validate result range
    if total < 1 or total > 3999:
        raise ValueError(f"Result out of range: {total}")

    return total
