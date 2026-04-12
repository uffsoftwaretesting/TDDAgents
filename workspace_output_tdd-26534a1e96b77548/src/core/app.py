from fastapi import FastAPI
from pydantic import BaseModel
from core.domain.errors import DomainError
from application.use_cases.validate_cpf import ValidateCPFUseCase

app = FastAPI()

class CPFRequestSchema(BaseModel):
    cpf: str

class CPFResponseSchema(BaseModel):
    cpf: str
    valid: bool

@app.post("/validate-cpf", response_model=CPFResponseSchema)
async def validate_cpf_endpoint(request: CPFRequestSchema):
    raw = request.cpf
    try:
        result = ValidateCPFUseCase().execute(raw)
        return {"cpf": result.cpf_original, "valid": result.valid}
    except DomainError:
        return {"cpf": raw, "valid": False}
