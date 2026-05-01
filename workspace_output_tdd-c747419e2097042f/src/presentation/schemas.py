from pydantic import BaseModel


class ValidateCpfRequest(BaseModel):
    cpf: str


class ValidateCpfResponse(BaseModel):
    valid: bool
