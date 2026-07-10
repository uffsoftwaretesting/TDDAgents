

class InvalidCPFFormatError(Exception):
    """Raised when the CPF format is invalid (wrong length or invalid characters)."""
    pass


class InvalidCPFSequenceError(Exception):
    """Raised when the CPF is a sequence of the same digit."""
    pass


class CPF:
    """
    Domain entity representing a Brazilian CPF number.

    Validates that the CPF has exactly 11 digits and is not a sequence of identical
    digits.
    Supports input with or without mask (dots and hyphens).
    """
    def __init__(self, cpf: str) -> None:
        if not isinstance(cpf, str):
            raise InvalidCPFFormatError("CPF must be a string")

        # Allowed characters: digits, dot, hyphen
        allowed = set("0123456789.-")
        for ch in cpf:
            if ch not in allowed:
                raise InvalidCPFFormatError(
                    f"Invalid character '{ch}' in CPF"
                )

        # Remove mask characters
        digits = ''.join(ch for ch in cpf if ch.isdigit())

        # Must have exactly 11 digits
        if len(digits) != 11:
            raise InvalidCPFFormatError(
                "CPF must contain exactly 11 digits"
            )

        # Cannot be a sequence of the same digit
        if digits == digits[0] * 11:
            raise InvalidCPFSequenceError(
                "CPF cannot be a sequence of the same digit"
            )

        self.value = digits
