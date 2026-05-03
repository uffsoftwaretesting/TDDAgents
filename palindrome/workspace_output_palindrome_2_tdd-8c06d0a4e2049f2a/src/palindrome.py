def _normalize(s: str) -> str:
    """
    Remove non-alphanumeric characters from s, convert to lowercase, and return the cleaned string.
    """
    return ''.join(c.lower() for c in s if c.isalnum())


def is_palindrome(s: str) -> bool:
    # Validação de tipo: deve ser string
    if not isinstance(s, str):
        raise TypeError("Entrada deve ser uma string")

    # Normalização
    normalized = _normalize(s)

    # Verificação de palíndromo usando dois ponteiros
    left, right = 0, len(normalized) - 1
    while left < right:
        if normalized[left] != normalized[right]:
            return False
        left += 1
        right -= 1
    return True


def _is_palindrome_core(s: str) -> bool:
    # Verificação de palíndromo usando dois ponteiros em string já normalizada ou não
    left, right = 0, len(s) - 1
    while left < right:
        if s[left] != s[right]:
            return False
        left += 1
        right -= 1
    return True