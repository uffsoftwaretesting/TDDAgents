"""
Fitting an image to the API's size and dimension limits.

Ported from `reference/claude-code/src/utils/imageResizer.ts`:
`maybeResizeAndDownsampleImageBuffer`, `compressImageBuffer`, `compressImageBufferWithTokenLimit`
(with `tryProgressiveResizing`, `applyFormatOptimizations`, `tryPalettePNG`, `tryJPEGConversion`,
`createUltraCompressedJPEG`), `detectImageFormatFromBuffer` and `createImageMetadataText`.

Upstream uses sharp; here Pillow does the same operations (`fit: inside` + `withoutEnlargement`
is `thumbnail`, PNG `palette` is an adaptive palette, `jpeg({quality})` is a JPEG save). The
analytics events and error classification have no equivalent and are not ported.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass

from PIL import Image

from app.constants.api_limits import (
    API_IMAGE_MAX_BASE64_SIZE,
    IMAGE_MAX_HEIGHT,
    IMAGE_MAX_WIDTH,
    IMAGE_TARGET_RAW_SIZE,
)
from app.utils.tool_result_storage import format_file_size

logger = logging.getLogger(__name__)


class ImageResizeError(Exception):
    """`ImageResizeError`: the image cannot be fitted to the API limits."""


@dataclass(frozen=True, slots=True)
class ImageDimensions:
    original_width: int | None = None
    original_height: int | None = None
    display_width: int | None = None
    display_height: int | None = None


@dataclass(frozen=True, slots=True)
class ResizeResult:
    buffer: bytes
    media_type: str
    dimensions: ImageDimensions | None = None


@dataclass(frozen=True, slots=True)
class CompressedImageResult:
    base64: str
    media_type: str
    original_size: int


_PIL_FORMATS = {"PNG": "png", "JPEG": "jpeg", "GIF": "gif", "WEBP": "webp"}


def _open(buffer: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(buffer))
    image.load()
    return image


def _fit_inside(image: Image.Image, width: int, height: int) -> Image.Image:
    """sharp `resize(w, h, {fit: 'inside', withoutEnlargement: true})`."""
    copy = image.copy()
    copy.thumbnail((max(width, 1), max(height, 1)))
    return copy


def _encode(image: Image.Image, fmt: str, **options: object) -> bytes:
    out = io.BytesIO()
    if fmt == "jpeg":
        image = image.convert("RGB")
        image.save(out, format="JPEG", quality=options.get("quality", 80))
    elif fmt == "png":
        if options.get("palette"):
            colors = int(options.get("colors", 256))  # type: ignore[call-overload]
            image = image.convert("RGB").quantize(colors=colors)
        image.save(out, format="PNG", optimize=True, compress_level=9)
    elif fmt == "webp":
        image.save(out, format="WEBP", quality=options.get("quality", 80))
    else:
        image.save(out, format=image.format or "PNG")
    return out.getvalue()


def _dims(ow: int, oh: int, dw: int, dh: int) -> ImageDimensions:
    return ImageDimensions(original_width=ow, original_height=oh, display_width=dw, display_height=dh)


def maybe_resize_and_downsample_image_buffer(image_buffer: bytes, original_size: int, ext: str) -> ResizeResult:
    """`maybeResizeAndDownsampleImageBuffer`: resize to the dimension limits and compress to the size limit."""
    if len(image_buffer) == 0:
        raise ImageResizeError("Image file is empty (0 bytes)")
    try:
        image = _open(image_buffer)
        media_type = _PIL_FORMATS.get(image.format or "", ext)
        normalized = "jpeg" if media_type == "jpg" else media_type
        original_width, original_height = image.size
        if not original_width or not original_height:
            if original_size > IMAGE_TARGET_RAW_SIZE:
                return ResizeResult(_encode(image, "jpeg", quality=80), "jpeg")
            return ResizeResult(image_buffer, normalized)

        width, height = original_width, original_height
        if original_size <= IMAGE_TARGET_RAW_SIZE and width <= IMAGE_MAX_WIDTH and height <= IMAGE_MAX_HEIGHT:
            return ResizeResult(image_buffer, normalized, _dims(original_width, original_height, width, height))

        needs_dimension_resize = width > IMAGE_MAX_WIDTH or height > IMAGE_MAX_HEIGHT
        is_png = normalized == "png"
        if not needs_dimension_resize and original_size > IMAGE_TARGET_RAW_SIZE:
            if is_png:
                png = _encode(image, "png", palette=True)
                if len(png) <= IMAGE_TARGET_RAW_SIZE:
                    return ResizeResult(png, "png", _dims(original_width, original_height, width, height))
            for quality in (80, 60, 40, 20):
                jpeg = _encode(image, "jpeg", quality=quality)
                if len(jpeg) <= IMAGE_TARGET_RAW_SIZE:
                    return ResizeResult(jpeg, "jpeg", _dims(original_width, original_height, width, height))

        if width > IMAGE_MAX_WIDTH:
            height = round(height * IMAGE_MAX_WIDTH / width)
            width = IMAGE_MAX_WIDTH
        if height > IMAGE_MAX_HEIGHT:
            width = round(width * IMAGE_MAX_HEIGHT / height)
            height = IMAGE_MAX_HEIGHT

        resized = _fit_inside(image, width, height)
        resized_buffer = _encode(resized, normalized)
        if len(resized_buffer) > IMAGE_TARGET_RAW_SIZE:
            if is_png:
                png = _encode(resized, "png", palette=True)
                if len(png) <= IMAGE_TARGET_RAW_SIZE:
                    return ResizeResult(png, "png", _dims(original_width, original_height, width, height))
            for quality in (80, 60, 40, 20):
                jpeg = _encode(resized, "jpeg", quality=quality)
                if len(jpeg) <= IMAGE_TARGET_RAW_SIZE:
                    return ResizeResult(jpeg, "jpeg", _dims(original_width, original_height, width, height))
            smaller_width = min(width, 1000)
            smaller_height = round(height * smaller_width / max(width, 1))
            smallest = _encode(_fit_inside(image, smaller_width, smaller_height), "jpeg", quality=20)
            return ResizeResult(smallest, "jpeg",
                                _dims(original_width, original_height, smaller_width, smaller_height))
        return ResizeResult(resized_buffer, normalized, _dims(original_width, original_height, width, height))
    except ImageResizeError:
        raise
    except Exception as exc:
        logger.warning("Image resize failed: %s", exc)
        detected = detect_image_format_from_buffer(image_buffer)[len("image/"):]
        base64_size = -(-original_size * 4 // 3)
        over_dimensions = (
            len(image_buffer) >= 24 and image_buffer[:4] == b"\x89PNG"
            and (int.from_bytes(image_buffer[16:20], "big") > IMAGE_MAX_WIDTH
                 or int.from_bytes(image_buffer[20:24], "big") > IMAGE_MAX_HEIGHT)
        )
        if base64_size <= API_IMAGE_MAX_BASE64_SIZE and not over_dimensions:
            return ResizeResult(image_buffer, detected)
        if over_dimensions:
            raise ImageResizeError(
                f"Unable to resize image — dimensions exceed the {IMAGE_MAX_WIDTH}x{IMAGE_MAX_HEIGHT}px limit and "
                "image processing failed. Please resize the image to reduce its pixel dimensions."
            ) from exc
        raise ImageResizeError(
            f"Unable to resize image ({format_file_size(original_size)} raw, {format_file_size(base64_size)} base64). "
            "The image exceeds the 5MB API limit and compression failed. "
            "Please resize the image manually or use a smaller image."
        ) from exc


#: `applyFormatOptimizations`.
_FORMAT_OPTIMIZATIONS: dict[str, dict[str, object]] = {
    "png": {"palette": True}, "jpeg": {"quality": 80}, "webp": {"quality": 80},
}


def _compressed(buffer: bytes, media_type: str, original_size: int) -> CompressedImageResult:
    normalized = "jpeg" if media_type == "jpg" else media_type
    return CompressedImageResult(base64.b64encode(buffer).decode("ascii"), f"image/{normalized}", original_size)


def compress_image_buffer(image_buffer: bytes, max_bytes: int = IMAGE_TARGET_RAW_SIZE,
                          original_media_type: str | None = None) -> CompressedImageResult:
    """`compressImageBuffer`: progressively smaller renderings until one fits `max_bytes`."""
    fallback = (original_media_type or "").split("/")[-1] or "jpeg"
    fallback = "jpeg" if fallback == "jpg" else fallback
    original_size = len(image_buffer)
    try:
        image = _open(image_buffer)
        fmt = _PIL_FORMATS.get(image.format or "", fallback)
        if original_size <= max_bytes:
            return _compressed(image_buffer, fmt, original_size)
        width, height = image.size
        for factor in (1.0, 0.75, 0.5, 0.25):  # `tryProgressiveResizing`
            resized = _fit_inside(image, round((width or 2000) * factor), round((height or 2000) * factor))
            options: dict[str, object] = dict(_FORMAT_OPTIMIZATIONS.get(fmt, {}))
            buffer = _encode(resized, fmt, **options)
            if len(buffer) <= max_bytes:
                return _compressed(buffer, fmt, original_size)
        if fmt == "png":  # `tryPalettePNG`
            palette = _encode(_fit_inside(image, 800, 800), "png", palette=True, colors=64)
            if len(palette) <= max_bytes:
                return _compressed(palette, "png", original_size)
        jpeg = _encode(_fit_inside(image, 600, 600), "jpeg", quality=50)  # `tryJPEGConversion`
        if len(jpeg) <= max_bytes:
            return _compressed(jpeg, "jpeg", original_size)
        return _compressed(_encode(_fit_inside(image, 400, 400), "jpeg", quality=20), "jpeg", original_size)
    except Exception as exc:
        logger.warning("Image compression failed: %s", exc)
        if original_size <= max_bytes:
            return CompressedImageResult(base64.b64encode(image_buffer).decode("ascii"),
                                         detect_image_format_from_buffer(image_buffer), original_size)
        raise ImageResizeError(
            f"Unable to compress image ({format_file_size(original_size)}) to fit within "
            f"{format_file_size(max_bytes)}. Please use a smaller image."
        ) from exc


def compress_image_buffer_with_token_limit(image_buffer: bytes, max_tokens: int,
                                           original_media_type: str | None = None) -> CompressedImageResult:
    """`compressImageBufferWithTokenLimit`: `maxBytes = (maxTokens / 0.125) * 0.75`."""
    max_base64_chars = int(max_tokens / 0.125)
    return compress_image_buffer(image_buffer, int(max_base64_chars * 0.75), original_media_type)


def detect_image_format_from_buffer(buffer: bytes) -> str:
    """`detectImageFormatFromBuffer`: the media type from the magic bytes, PNG by default."""
    if len(buffer) < 4:
        return "image/png"
    if buffer[:4] == b"\x89PNG":
        return "image/png"
    if buffer[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if buffer[:3] == b"GIF":
        return "image/gif"
    if buffer[:4] == b"RIFF" and len(buffer) >= 12 and buffer[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def create_image_metadata_text(dims: ImageDimensions, source_path: str | None = None) -> str | None:
    """`createImageMetadataText`: a note when the image was resized (or has a source path)."""
    ow, oh, dw, dh = dims.original_width, dims.original_height, dims.display_width, dims.display_height
    if not ow or not oh or not dw or not dh or dw <= 0 or dh <= 0:
        return f"[Image source: {source_path}]" if source_path else None
    was_resized = ow != dw or oh != dh
    if not was_resized and not source_path:
        return None
    parts = []
    if source_path:
        parts.append(f"source: {source_path}")
    if was_resized:
        parts.append(f"original {ow}x{oh}, displayed at {dw}x{dh}. "
                     f"Multiply coordinates by {ow / dw:.2f} to map to original image.")
    return f"[Image: {', '.join(parts)}]"


__all__ = [
    "CompressedImageResult",
    "ImageDimensions",
    "ImageResizeError",
    "ResizeResult",
    "compress_image_buffer",
    "compress_image_buffer_with_token_limit",
    "create_image_metadata_text",
    "detect_image_format_from_buffer",
    "maybe_resize_and_downsample_image_buffer",
]
