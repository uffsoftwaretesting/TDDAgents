from pydantic import BaseModel, Field


class ErrorModel(BaseModel):
    detail: str = Field(..., strict=True)
    code: int
