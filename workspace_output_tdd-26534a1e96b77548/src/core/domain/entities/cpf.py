from core.domain.errors import DomainError


class CPF:
    def __init__(self, raw: str):
        # Trim whitespace
        trimmed = raw.strip()
        self.raw = trimmed

        # Normalize: keep only digits
        normalized = ''.join(filter(str.isdigit, trimmed))

        # Must be exactly 11 digits
        if len(normalized) != 11:
            raise DomainError(f"Invalid CPF length: {len(normalized)} digits found, expected 11.")

        # Cannot be all digits equal
        if normalized == normalized[0] * len(normalized):
            raise DomainError("Invalid CPF: all digits are equal.")

        self.normalized = normalized
