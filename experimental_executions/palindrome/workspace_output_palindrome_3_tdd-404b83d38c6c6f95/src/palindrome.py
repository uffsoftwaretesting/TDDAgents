import unicodedata

def is_palindrome(s):
    if not isinstance(s, str):
        raise TypeError("O argumento 's' deve ser do tipo str")
    # Decompose Unicode and strip diacritics
    normalized_chars = []
    for char in unicodedata.normalize('NFD', s):
        if unicodedata.category(char) == 'Mn':
            continue
        if char.isalnum():
            normalized_chars.append(char.lower())
    normalized = ''.join(normalized_chars)
    # Compare with reverse
    return normalized == normalized[::-1]