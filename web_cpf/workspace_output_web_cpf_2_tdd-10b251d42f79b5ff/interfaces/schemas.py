from pydantic import BaseModel


class CpfInput(BaseModel):
    cpf: str


class CpfOutput(BaseModel):
    valid: bool
