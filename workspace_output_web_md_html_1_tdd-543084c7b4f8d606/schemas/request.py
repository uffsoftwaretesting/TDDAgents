from pydantic import BaseModel, Field


class RequestModel(BaseModel):
    markdown: str = Field(..., min_length=1, strict=True)
