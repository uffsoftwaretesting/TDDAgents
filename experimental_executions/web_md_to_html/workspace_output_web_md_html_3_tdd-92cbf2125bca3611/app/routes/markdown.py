from fastapi import APIRouter
from starlette.responses import JSONResponse

from app.schemas.markdown import MarkdownInput, HTMLResponse, ErrorResponse
import app.services.markdown_converter as markdown_converter

router = APIRouter(
    prefix="/convert",
    tags=["markdown"],
)

@router.post(
    "/",
    response_model=HTMLResponse,
    responses={
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Convert Markdown to HTML",
    description="Recebe um conteúdo em Markdown e retorna seu equivalente em HTML."
)
async def convert_markdown_endpoint(payload: MarkdownInput):
    try:
        html = markdown_converter.convert_markdown_to_html(payload.content)
        return HTMLResponse(data={"html": html})
    except markdown_converter.MarkdownConversionError as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": {"code": 500, "message": str(e)}}
        )