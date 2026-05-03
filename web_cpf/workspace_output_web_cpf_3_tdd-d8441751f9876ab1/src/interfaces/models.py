from pydantic import BaseModel, Field


class ValidateCPFRequest(BaseModel):
    cpf: str = Field(..., min_length=11, max_length=14, pattern=r'^[\d\.\-]+$')


class ValidateCPFResponse(BaseModel):
    valid: bool
