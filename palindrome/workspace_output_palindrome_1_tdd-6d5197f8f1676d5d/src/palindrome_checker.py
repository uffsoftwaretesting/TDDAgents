def normalize_string(s) -> str:
    """Normalize the input string by removing non-alphanumeric characters, converting to lowercase, and removing spaces."""
    return ''.join(ch.lower() for ch in s if ch.isalnum())


def is_palindrome(s) -> bool:
    """Check if the given string is a palindrome after normalization."""
    # Validate input type
    if not isinstance(s, str):
        raise TypeError(f"Input must be a string, got {type(s)}")

    # Normalize: keep only alphanumeric, lowercase
    normalized = normalize_string(s)

    # Two-pointer palindrome check
    i, j = 0, len(normalized) - 1
    while i < j:
        if normalized[i] != normalized[j]:
            return False
        i += 1
        j -= 1
    return True
