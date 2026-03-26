from datetime import datetime
from typing import List

from pydantic import BaseModel, Field, ConfigDict


class CPFValidateRequestSchema(BaseModel):
    cpf: str = Field(..., pattern=r"^\d{11}$")


class CPFValidateResponseSchema(BaseModel):
    cpf: str
    valid: bool


class ValidationEntrySchema(BaseModel):
    model_config = ConfigDict(strict=True)

    timestamp: datetime
    valid: bool


class CPFHistoryResponseSchema(BaseModel):
    cpf: str
    results: List[ValidationEntrySchema]


class ValidationRecordSchema(BaseModel):
    model_config = ConfigDict(strict=True)

    id: int
    cpf: str
    valid: bool
    timestamp: datetime


class PaginatedValidationsSchema(BaseModel):
    items: List[ValidationRecordSchema]
    page: int = Field(..., ge=1)
    size: int = Field(..., ge=1)
    total: int