from pydantic import BaseModel, Field


class ValidateCpfRequest(BaseModel):
    cpf: str = Field(..., min_length=1, pattern=r'^[\d.\-()\s]+$')


class ValidateCpfResponse(BaseModel):
    valid: bool
