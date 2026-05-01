from fastapi import APIRouter, Depends
from src.interfaces.api.models.validate_cpf import ValidateCpfRequest, ValidateCpfResponse
from src.config.app import get_cpf_validator, get_validate_usecase
from src.domain.ports.cpf_validator import CPFValidator
from src.application.ports.validate_cpf_usecase import ValidateCpfUseCase

router = APIRouter()

@router.post(
    '/validate-cpf',
    response_model=ValidateCpfResponse,
    tags=["CPF"]
)
def validate_cpf_endpoint(
    request: ValidateCpfRequest,
    validator: CPFValidator = Depends(get_cpf_validator),
    usecase: ValidateCpfUseCase = Depends(get_validate_usecase)
) -> ValidateCpfResponse:
    """
    Endpoint to validate CPF. Delegates to the use case.
    """
    valid = usecase.execute(request.cpf)
    return ValidateCpfResponse(valid=valid)