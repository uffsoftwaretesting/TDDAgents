from fastapi import FastAPI, Depends
from src.interfaces.models import (
    ValidateCPFRequest,
    ValidateCPFResponse,
)
from src.application.validate_cpf_usecase import ValidateCPFUseCase
from src.infrastructure.cpfvalidator_adapter import CPFValidatorAdapter


def get_validate_cpf_use_case() -> ValidateCPFUseCase:
    """Dependency provider for ValidateCPFUseCase."""
    return ValidateCPFUseCase(CPFValidatorAdapter())


app = FastAPI()


@app.post("/validate-cpf", response_model=ValidateCPFResponse)
def validate_cpf(
    request: ValidateCPFRequest,
    use_case: ValidateCPFUseCase = Depends(get_validate_cpf_use_case),
) -> ValidateCPFResponse:
    """Endpoint para validar CPF."""
    valid = use_case.execute(request.cpf)
    return ValidateCPFResponse(valid=valid)
