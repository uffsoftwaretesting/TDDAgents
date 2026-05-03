from fastapi import FastAPI, Depends, HTTPException, status

from src.interfaces.schemas import ValidateCpfRequest, ValidateCpfResponse
from src.application.use_cases.validate_cpf_use_case import ValidateCpfUseCase

app = FastAPI()


@app.post("/validate-cpf", response_model=ValidateCpfResponse)
def validate_cpf(
    request: ValidateCpfRequest,
    use_case: ValidateCpfUseCase = Depends(ValidateCpfUseCase),
) -> ValidateCpfResponse:
    try:
        valid = use_case.execute(request.cpf)
        return ValidateCpfResponse(valid=valid)
    except HTTPException:
        # Re-raise HTTPExceptions (not expected here but just in case)
        raise
    except Exception:
        # Unexpected errors
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error",
        )
