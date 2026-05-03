from src.core.exceptions import InvalidCPFError


class CPF:
    """
    Domain entity representing a Brazilian CPF number.
    """
    def __init__(self, value: str):
        # Input must be a string
        if not isinstance(value, str):
            raise InvalidCPFError("CPF must be a string")

        # Remove common mask characters
        sanitized = value.replace('.', '').replace('-', '')

        # After sanitization, must be only digits
        if not sanitized.isdigit():
            raise InvalidCPFError("CPF must contain only digits after sanitization")

        # Must contain exactly 11 digits
        if len(sanitized) != 11:
            raise InvalidCPFError("CPF must have 11 digits")

        # Cannot be a sequence of the same digit
        if all(d == sanitized[0] for d in sanitized):
            raise InvalidCPFError("CPF cannot have all digits equal")

        # Convert to list of integers
        digits = [int(d) for d in sanitized]

        # Calculate first verifying digit
        first_sum = sum(d * w for d, w in zip(digits[:9], range(10, 1, -1)))
        first_mod = first_sum % 11
        first_check = 0 if first_mod < 2 else 11 - first_mod
        if digits[9] != first_check:
            raise InvalidCPFError("Invalid CPF check digits")

        # Calculate second verifying digit
        second_sum = sum(d * w for d, w in zip(digits[:10], range(11, 1, -1)))
        second_mod = second_sum % 11
        second_check = 0 if second_mod < 2 else 11 - second_mod
        if digits[10] != second_check:
            raise InvalidCPFError("Invalid CPF check digits")

        # All checks passed; set the sanitized value
        self.value = sanitized
