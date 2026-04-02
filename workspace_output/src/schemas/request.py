from pydantic import BaseModel, Field


class CPFValidationRequestSchema(BaseModel):
    cpf: str = Field(
        ...,  
        min_length=11,
        max_length=11,
        pattern=r"^\d{11}$"
    )


class PaginationParams(BaseModel):
    page: int = Field(..., ge=1)
    size: int = Field(..., ge=1)
