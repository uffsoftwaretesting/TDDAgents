from fastapi import APIRouter, HTTPException, Path, Query
from api.schemas.cpf_schemas import CPFValidateRequestSchema, CPFValidateResponseSchema
from application.usecases.validate_cpf_usecase import ValidateCPFUseCase
from application.usecases.get_cpf_history_usecase import GetCPFHistoryUseCase
from application.usecases.list_validations_usecase import ListValidationsUseCase

router = APIRouter()

@router.post("/cpf/validate", response_model=CPFValidateResponseSchema)
async def validate_cpf(request: CPFValidateRequestSchema):
    try:
        usecase = ValidateCPFUseCase(None)
        result = await usecase.execute(request.cpf)
        return {"cpf": result.cpf, "valid": result.valid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/cpf/{cpf}/history")
async def get_cpf_history(
    cpf: str = Path(..., regex=r"^\d{11}$"),
):
    try:
        usecase = GetCPFHistoryUseCase(None)
        result = await usecase.execute(cpf)
        return {
            "cpf": result.cpf,
            "results": [
                {"timestamp": entry.timestamp.isoformat(), "valid": entry.valid}
                for entry in result.results
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/cpf/history")
async def list_all_history(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1),
):
    try:
        usecase = ListValidationsUseCase(None)
        result = await usecase.execute(page=page, size=size)
        return {
            "items": [
                {"id": rec.id, "cpf": rec.cpf, "valid": rec.valid, "timestamp": rec.timestamp.isoformat()}
                for rec in result.items
            ],
            "page": result.page,
            "size": result.size,
            "total": result.total,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))