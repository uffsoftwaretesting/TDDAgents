from fastapi import APIRouter, Depends
from interfaces.schemas import CpfInput, CpfOutput
from application.ports.icpf_validator import ICpfValidator
from infrastructure.cpf_validator_adapter import CpfValidatorAdapter
from application.use_cases.validate_cpf_use_case import ValidateCpfUseCase


def get_validator() -> ICpfValidator:
    return CpfValidatorAdapter()


def get_use_case(
    validator: ICpfValidator = Depends(get_validator)
) -> ValidateCpfUseCase:
    return ValidateCpfUseCase(validator)


router = APIRouter()


@router.post("/validate-cpf", response_model=CpfOutput)
def validate_cpf_route(
    input: CpfInput,
    use_case: ValidateCpfUseCase = Depends(get_use_case)
) -> CpfOutput:
    valid = use_case.execute(input.cpf)
    return CpfOutput(valid=valid)
