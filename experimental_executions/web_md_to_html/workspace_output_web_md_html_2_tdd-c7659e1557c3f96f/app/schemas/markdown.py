from typing import Optional
from pydantic import BaseModel, validator


class RequestModel(BaseModel):
    markdown: str

    @validator('markdown')
    def validate_markdown(cls, v: str) -> str:
        if v == "":
            raise ValueError("Markdown content must not be empty.")
        if len(v) > 10000:
            raise ValueError("Markdown content too large (max 10000 chars).")
        return v


class ResponseModel(BaseModel):
    class Data(BaseModel):
        html: str

        @validator('html')
        def html_must_be_string(cls, v):
            if not isinstance(v, str):
                raise ValueError("Input should be a valid string")
            return v

    data: Data
    error: Optional[str] = None

    @validator('error')
    def error_must_be_string(cls, v):
        if v is not None and not isinstance(v, str):
            raise ValueError("Input should be a valid string")
        return v