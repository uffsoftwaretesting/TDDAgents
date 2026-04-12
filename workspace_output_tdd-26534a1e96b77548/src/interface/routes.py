from fastapi import FastAPI
from pydantic import BaseModel
from application.use_cases.validate_cpf import ValidateCPFUseCase

app = FastAPI()

class CPFRequestSchema(BaseModel):
    cpf: str

class CPFResponseSchema(BaseModel):
    cpf: str
    valid: bool

@app.post("/validate-cpf", response_model=CPFResponseSchema)
def validate_cpf_endpoint(request: CPFRequestSchema):
    result = ValidateCPFUseCase().execute(request.cpf)
    return {"cpf": result.cpf_original, "valid": result.valid}