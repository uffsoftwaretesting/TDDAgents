from fastapi import APIRouter, Depends, HTTPException

from src.schemas.request import CPFValidationRequestSchema, PaginationParams
from src.schemas.response import CPFValidationResponseSchema, ValidationListResponseSchema
from src.usecases.validate_cpf_usecase import ValidateCPFUseCase
from src.usecases.retrieve_validation_usecase import RetrieveValidationUseCase
from src.usecases.list_validations_usecase import ListValidationsUseCase

router = APIRouter()


class _DefaultValidateRepo:
    async def save(self, validation):
        return validation


def get_validate_usecase() -> ValidateCPFUseCase:
    """
    Default ValidateCPFUseCase using a no-op repository.
    """
    return ValidateCPFUseCase(_DefaultValidateRepo())


class _DefaultRetrieveRepo:
    async def get_by_cpf(self, cpf: str):
        return None


def get_retrieve_usecase() -> RetrieveValidationUseCase:
    """
    Default RetrieveValidationUseCase using a repository that returns None.
    """
    return RetrieveValidationUseCase(_DefaultRetrieveRepo())


class _DefaultListRepo:
    async def list_all(self, page: int, size: int):
        return []


def get_list_usecase() -> ListValidationsUseCase:
    """
    Default ListValidationsUseCase using a repository that returns an empty list.
    """
    return ListValidationsUseCase(_DefaultListRepo())


@router.post(
    "/validate",
    response_model=CPFValidationResponseSchema,
    status_code=201,
)
async def validate_cpf_endpoint(
    request: CPFValidationRequestSchema,
    usecase: ValidateCPFUseCase = Depends(get_validate_usecase),
) -> CPFValidationResponseSchema:
    """Validate a CPF and persist the result."""
    result = await usecase.execute(request.cpf)
    # Convert domain entity to dict for Pydantic parsing
    return result.__dict__


@router.get(
    "/validate/{cpf}",
    response_model=CPFValidationResponseSchema,
)
async def retrieve_cpf_endpoint(
    cpf: str,
    usecase: RetrieveValidationUseCase = Depends(get_retrieve_usecase),
) -> CPFValidationResponseSchema:
    """Retrieve the most recent validation for a CPF."""
    result = await usecase.execute(cpf)
    if result is None:
        raise HTTPException(status_code=404, detail="CPF validation not found")
    # Convert domain entity to dict for Pydantic parsing
    return result.__dict__


@router.get(
    "/validations",
    response_model=ValidationListResponseSchema,
)
async def list_validations_endpoint(
    pagination: PaginationParams = Depends(),
    usecase: ListValidationsUseCase = Depends(get_list_usecase),
) -> dict:
    """List all CPF validations with pagination."""
    items = await usecase.execute(pagination.page, pagination.size)
    serialized = [
        {
            "id": str(item.id),
            "cpf": item.cpf,
            "is_valid": item.is_valid,
            "created_at": item.created_at,
        }
        for item in items
    ]
    return {"items": serialized, "page": pagination.page, "size": pagination.size}