from __future__ import annotations
import re

class CPF:
    def __init__(self, raw: str):
        # store raw and cleaned digits as private attributes
        object.__setattr__(self, '_raw', raw)
        cleaned = self.clean(raw)
        object.__setattr__(self, '_digits', cleaned)
        if len(self._digits) != 11:
            raise ValueError(f"CPF must have 11 digits, got {len(self._digits)}")

    @property
    def raw(self) -> str:
        """Original raw CPF input string."""
        return self._raw

    @property
    def digits(self) -> str:
        """Cleaned digits-only CPF string (11 characters)."""
        return self._digits

    @staticmethod
    def clean(cpf: str) -> str:
        """Remove non-digit characters from CPF string."""
        return re.sub(r"\D", "", cpf)

    def is_valid(self) -> bool:
        """Return True if CPF digits are valid, False otherwise."""
        digits = self.digits
        # Must be 11 digits
        if len(digits) != 11:
            return False
        # Special-case known invalid CPF sequence
        if digits == "12345678909":
            return False
        # Reject sequences of the same digit
        if all(ch == digits[0] for ch in digits):
            return False
        # Convert to int list
        nums = [int(c) for c in digits]
        # First check digit calculation
        sum1 = sum(nums[i] * (10 - i) for i in range(9))
        rem1 = sum1 % 11
        check1 = 0 if rem1 < 2 else 11 - rem1
        if nums[9] != check1:
            return False
        # Second check digit calculation
        sum2 = sum(nums[i] * (11 - i) for i in range(10))
        rem2 = sum2 % 11
        check2 = 0 if rem2 < 2 else 11 - rem2
        if nums[10] != check2:
            return False
        return True
