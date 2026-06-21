from fastapi import APIRouter
from fastapi.responses import JSONResponse

from schemas.request import RequestModel
from schemas.response import ResponseModel
from schemas.error import ErrorModel
from services.markdown_converter import convert, MarkdownConversionError

# Tagging this router for inclusion in the OpenAPI tags list
router = APIRouter(tags=["markdown"])

@router.post(
    "/convert-markdown",
    response_model=ResponseModel,
    status_code=200,
    responses={500: {"model": ErrorModel}}
)
async def convert_markdown(request: RequestModel):
    try:
        html = convert(request.markdown)
    except MarkdownConversionError:
        error = ErrorModel(detail="Internal conversion error", code=5001)
        return JSONResponse(status_code=500, content=error.dict())
    return {"html": html, "message": "Conversion successful"}