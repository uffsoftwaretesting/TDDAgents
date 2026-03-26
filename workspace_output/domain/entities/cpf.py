import re

class CPF:
    def __init__(self, value: str):
        # Ensure it's a string of exactly 11 digits
        if not (isinstance(value, str) and re.fullmatch(r"\d{11}", value)):
            raise ValueError("Invalid CPF format: must be 11 digits")
        self.value = value

    def is_valid(self) -> bool:
        # Import validator dynamically so monkeypatch will work
        import validate_docbr
        validator = validate_docbr.CPF()
        return bool(validator.validate(self.value))
