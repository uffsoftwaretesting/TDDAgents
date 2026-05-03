from pydantic import BaseModel, field_validator


class ValidateCpfRequest(BaseModel):
    cpf: str

    @field_validator('cpf', mode='before')
    def _sanitize_cpf(cls, v):
        if not isinstance(v, str):
            raise ValueError('CPF must be a string')
        # Remove dots and dashes
        return v.replace('.', '').replace('-', '')

    @field_validator('cpf')
    def _validate_cpf(cls, v):
        # After sanitization, ensure only digits and correct length
        if not v.isdigit():
            raise ValueError('CPF must contain only digits after sanitization')
        if len(v) != 11:
            raise ValueError('CPF must have 11 digits')
        return v


class ValidateCpfResponse(BaseModel):
    valid: bool