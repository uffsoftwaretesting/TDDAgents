from fastapi import FastAPI
from config.config import Settings
from pydantic import ValidationError
from routes.markdown import router as markdown_router
import logging

# Attempt to load settings, but don't fail import if vars are missing
try:
    settings = Settings()
except ValidationError:
    settings = None

# Metadata for OpenAPI tags
tags_metadata = [
    {
        "name": "markdown",
        "description": "Endpoints para conversão de Markdown em HTML"
    }
]

# Create FastAPI app with OpenAPI tags and optional title/debug
app = FastAPI(
    title=settings.APP_NAME if settings else "MarkdownAPI",
    debug=settings.DEBUG if settings else False,
    openapi_tags=tags_metadata
)

# Include the markdown conversion router, tagged for OpenAPI
app.include_router(markdown_router, tags=["markdown"])

# Basic logging configuration
if settings and settings.DEBUG:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)