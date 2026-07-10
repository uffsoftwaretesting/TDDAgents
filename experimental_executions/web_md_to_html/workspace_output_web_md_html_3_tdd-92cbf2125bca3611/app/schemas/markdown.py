from pydantic import BaseModel, validator

from app.config.settings import get_settings


class MarkdownInput(BaseModel):
    content: str

    @validator('content', pre=True)
    def check_type(cls, v):
        if not isinstance(v, str):
            raise ValueError('content must be a string')
        return v

    @validator('content')
    def check_content(cls, v):
        # Must not be empty or only whitespace
        if v is None or v.strip() == '':
            raise ValueError('content must not be empty or only whitespace')
        # Must not exceed max length
        max_len = get_settings().markdown_max_length
        if len(v) > max_len:
            raise ValueError(f'content length must be less than or equal to {max_len}')
        return v


class HTMLResponseData(BaseModel):
    html: str


class HTMLResponse(BaseModel):
    success: bool = True
    data: HTMLResponseData


class ErrorResponseError(BaseModel):
    code: int
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorResponseError