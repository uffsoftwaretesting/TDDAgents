from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from typing import List


class CPFValidationResponseSchema(BaseModel):
    id: UUID
    cpf: str
    is_valid: bool
    created_at: datetime


class ValidationListResponseSchema(BaseModel):
    items: List[CPFValidationResponseSchema]
    page: int = Field(..., ge=1)
    size: int = Field(..., ge=1)
