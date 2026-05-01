class CpfInvalidError(Exception):
    """Exception raised when a CPF is invalid according to domain rules."""
    pass

class CPF:
    """
    Domain entity representing a Brazilian CPF with validation.
    """
    def __setattr__(self, name, value):
        # raw_value is immutable once set
        if name == 'raw_value' and 'raw_value' in self.__dict__:
            raise AttributeError("raw_value attribute is immutable")
        super().__setattr__(name, value)

    def __init__(self, raw_value: str):
        # initial assignment of raw_value allowed
        self.raw_value = raw_value
        # normalize (remove mask) and set normalized_value
        self.normalized_value = self._remove_mask()
        # run validations
        self._validate_length()
        self._validate_sequence()
        self._validate_check_digits()

    def _remove_mask(self) -> str:
        """
        Remove mask characters (dots and dashes) from the raw CPF value.
        """
        return self.raw_value.replace('.', '').replace('-', '')

    def _validate_length(self) -> None:
        """
        Validate that the normalized CPF has exactly 11 characters.
        """
        length = len(self.normalized_value)
        if length != 11:
            raise CpfInvalidError(f"CPF must have 11 digits, got {length}")

    def _validate_sequence(self) -> None:
        """
        Reject CPFs where all digits are identical.
        """
        if len(set(self.normalized_value)) == 1:
            raise CpfInvalidError("CPF cannot be a sequence of identical digits")

    def _validate_check_digits(self) -> None:
        """
        Perform the standard modulus-11 check digit validation for CPF.
        """
        # Convert characters to integers, invalid chars cause error
        try:
            digits = [int(char) for char in self.normalized_value]
        except ValueError:
            raise CpfInvalidError("CPF contains invalid characters")

        # First check digit
        sum1 = sum(digits[i] * (10 - i) for i in range(9))
        remainder1 = sum1 % 11
        if remainder1 < 2:
            check1 = 0
        else:
            check1 = 11 - remainder1
        if digits[9] != check1:
            raise CpfInvalidError("Invalid CPF check digits")

        # Second check digit
        sum2 = sum(digits[i] * (11 - i) for i in range(10))
        remainder2 = sum2 % 11
        if remainder2 < 2:
            check2 = 0
        else:
            check2 = 11 - remainder2
        if digits[10] != check2:
            raise CpfInvalidError("Invalid CPF check digits")