class InvalidCPFError(Exception):
    """
    Custom exception raised when a CPF is invalid due to format, length or check-digit errors.
    """
    def __init__(self, message: str):
        super().__init__(message)
