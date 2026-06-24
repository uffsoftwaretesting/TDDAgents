from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.responses import JSONResponse, HTMLResponse
from app.config.settings import get_settings
from app.routes.markdown import router as markdown_router
import uvicorn

# Load settings singleton
settings = get_settings()

# Initialize FastAPI with configured title and debug, disable default docs and redoc
app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    docs_url=None,
    redoc_url=None,
)

# Custom Swagger UI endpoint including schema names in the title
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{settings.app_name} - Swagger UI - Schemas: MarkdownInput, HTMLResponse, ErrorResponse"
    )

# Exception handler for request validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": {"code": 422, "message": str(exc)}}
    )

# Generic exception handler
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": {"code": 500, "message": str(exc)}}
    )

# Include markdown conversion routes by flattening the router
for route in markdown_router.routes:
    app.router.routes.append(route)

# Uvicorn entrypoint
if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
