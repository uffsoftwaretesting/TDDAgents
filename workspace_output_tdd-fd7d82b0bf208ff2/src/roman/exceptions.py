"""
Custom exceptions for Roman numeral conversion.
"""


class InvalidRomanNumeralError(Exception):
    """Raised when the Roman numeral format is invalid or contains invalid characters."""
    pass


class OutOfRangeError(Exception):
    """Raised when the converted integer is outside the allowed range [1, 3999]."""
    pass
