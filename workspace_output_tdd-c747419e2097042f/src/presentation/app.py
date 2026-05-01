from fastapi import FastAPI, Depends

from src.presentation.schemas import ValidateCpfRequest, ValidateCpfResponse
from src.application.usecases.validate_cpf_usecase import ValidateCpfUseCaseImpl, ValidateCpfUseCase
from src.infrastructure.adapters.validate_docbr_adapter import ValidateDocbrAdapter

app = FastAPI()

def get_validate_cpf_usecase() -> ValidateCpfUseCase:
    adapter = ValidateDocbrAdapter()
    return ValidateCpfUseCaseImpl(adapter)

@app.post("/validate-cpf", response_model=ValidateCpfResponse)
def validate_cpf(
    request: ValidateCpfRequest,
    use_case: ValidateCpfUseCase = Depends(get_validate_cpf_usecase)
) -> ValidateCpfResponse:
    result = use_case.execute(request.cpf)
    return ValidateCpfResponse(valid=result)