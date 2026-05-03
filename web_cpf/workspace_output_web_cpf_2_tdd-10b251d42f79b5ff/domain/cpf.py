class InvalidCpfFormat(Exception):
    """Exception raised when CPF format is invalid (non-numeric characters or wrong length)."""
    pass

class InvalidCpfCheckDigits(Exception):
    """Exception raised when CPF check digits validation fails."""
    pass

class Cpf:
    """Value Object for CPF number with normalization and validation."""
    def __init__(self, raw: str):
        # Input must be a string
        if not isinstance(raw, str):
            raise InvalidCpfFormat("CPF must be provided as a string")
        # Only digits, dots and dashes are allowed
        allowed_chars = set("0123456789.-")
        for ch in raw:
            if ch not in allowed_chars:
                raise InvalidCpfFormat(f"Invalid character in CPF: '{ch}'")
        # Normalize: remove dots and dashes
        digits = ''.join(filter(str.isdigit, raw))
        # Must have exactly 11 digits
        if len(digits) != 11:
            raise InvalidCpfFormat("CPF must contain exactly 11 digits after removing mask")
        self.value = digits
        # All digits equal is considered invalid
        if len(set(digits)) == 1:
            raise InvalidCpfCheckDigits("CPF with all identical digits is invalid")
        # Convert to list of ints
        nums = [int(d) for d in digits]
        # Validate first check digit
        first_sum = sum(nums[i] * (10 - i) for i in range(9))
        first_rest = (first_sum * 10) % 11
        if first_rest == 10:
            first_rest = 0
        if first_rest != nums[9]:
            raise InvalidCpfCheckDigits("First check digit does not match")
        # Validate second check digit
        second_sum = sum(nums[i] * (11 - i) for i in range(10))
        second_rest = (second_sum * 10) % 11
        if second_rest == 10:
            second_rest = 0
        if second_rest != nums[10]:
            raise InvalidCpfCheckDigits("Second check digit does not match")