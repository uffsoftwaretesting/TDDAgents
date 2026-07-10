from pydantic import BaseModel, Field
from typing import Optional


class ResponseModel(BaseModel):
    html: str = Field(..., strict=True)
    message: Optional[str] = Field(None, strict=True)
