"""
The media limits images and PDFs are fitted to.

Ported from `reference/claude-code/src/constants/apiLimits.ts`.
"""

from __future__ import annotations

#: The API's per-image base64 limit.
API_IMAGE_MAX_BASE64_SIZE = 5 * 1024 * 1024
#: The raw size that stays under the base64 limit after encoding.
IMAGE_TARGET_RAW_SIZE = (API_IMAGE_MAX_BASE64_SIZE * 3) // 4
IMAGE_MAX_WIDTH = 2000
IMAGE_MAX_HEIGHT = 2000

#: A PDF sent whole must stay under this raw size (the 32 MB request limit, after base64).
PDF_TARGET_RAW_SIZE = 20 * 1024 * 1024
API_PDF_MAX_PAGES = 100
#: Above this size a PDF is also rendered to pages.
PDF_EXTRACT_SIZE_THRESHOLD = 3 * 1024 * 1024
#: The largest PDF page rendering is attempted on.
PDF_MAX_EXTRACT_SIZE = 100 * 1024 * 1024
PDF_MAX_PAGES_PER_READ = 20
#: More pages than this need the `pages` parameter.
PDF_AT_MENTION_INLINE_THRESHOLD = 10

__all__ = [
    "API_IMAGE_MAX_BASE64_SIZE",
    "API_PDF_MAX_PAGES",
    "IMAGE_MAX_HEIGHT",
    "IMAGE_MAX_WIDTH",
    "IMAGE_TARGET_RAW_SIZE",
    "PDF_AT_MENTION_INLINE_THRESHOLD",
    "PDF_EXTRACT_SIZE_THRESHOLD",
    "PDF_MAX_EXTRACT_SIZE",
    "PDF_MAX_PAGES_PER_READ",
    "PDF_TARGET_RAW_SIZE",
]
