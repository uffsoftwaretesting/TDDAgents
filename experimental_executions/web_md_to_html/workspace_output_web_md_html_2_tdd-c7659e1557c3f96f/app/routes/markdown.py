from fastapi import APIRouter
from app.schemas.markdown import RequestModel, ResponseModel
from app.services.converter import convert_markdown_to_html

router = APIRouter()

@router.post("/convert", response_model=ResponseModel)
async def convert_markdown(request: RequestModel):
    html = convert_markdown_to_html(request.markdown)
    return {"data": {"html": html}, "error": None}