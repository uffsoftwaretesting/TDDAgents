from fastapi import FastAPI, Depends
from pydantic import BaseModel

from application.use_cases.validate_cpf_use_case import ValidateCpfUseCase
from application.ports.icpf_validator import ICpfValidator
from infrastructure.cpf_validator_adapter import CpfValidatorAdapter


class CpfInput(BaseModel):
    cpf: str


class CpfOutput(BaseModel):
    valid: bool


def get_validator() -> ICpfValidator:
    """Dependency that provides an ICpfValidator implementation."""
    return CpfValidatorAdapter()


def get_use_case(
    validator: ICpfValidator = Depends(get_validator)
) -> ValidateCpfUseCase:
    """Dependency that provides the ValidateCpfUseCase."""
    return ValidateCpfUseCase(validator)


app = FastAPI()


@app.post("/validate-cpf", response_model=CpfOutput)
def validate_cpf_route(
    input: CpfInput,
    use_case: ValidateCpfUseCase = Depends(get_use_case)
) -> CpfOutput:
    """Route to validate CPF strings."""
    valid = use_case.execute(input.cpf)
    return CpfOutput(valid=valid)