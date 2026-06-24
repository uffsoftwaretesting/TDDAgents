"""
Custom exceptions for the Markdown to HTML Conversion service.
"""


class ConversionError(Exception):
    """Exception raised when markdown conversion fails."""
    def __init__(self, message: str):
        super().__init__(message)
