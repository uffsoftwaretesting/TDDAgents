"""
Reading PDFs whole, counting their pages and rendering page ranges to images.

Ported from `reference/claude-code/src/utils/pdf.ts`: `readPDF`, `getPDFPageCount`,
`isPdftoppmAvailable` / `resetPdftoppmCache` and `extractPDFPages`. `pdfinfo` and `pdftoppm` come
from poppler-utils and run on the host, as upstream's `execFileNoThrow` runs them.
"""

from __future__ import annotations

import base64
import math
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.constants.api_limits import PDF_MAX_EXTRACT_SIZE, PDF_TARGET_RAW_SIZE
from app.utils.tool_result_storage import format_file_size

PDFErrorReason = Literal["empty", "too_large", "password_protected", "corrupted", "unknown", "unavailable"]


@dataclass(frozen=True, slots=True)
class PDFError:
    reason: PDFErrorReason
    message: str


@dataclass(frozen=True, slots=True)
class PDFData:
    """`readPDF`'s `{type: 'pdf', file}`."""

    file_path: str
    base64: str
    original_size: int


@dataclass(frozen=True, slots=True)
class PDFPages:
    """`PDFExtractPagesResult` (`type: 'parts'`)."""

    file_path: str
    original_size: int
    count: int
    output_dir: Path


def _run(args: list[str], timeout: float) -> tuple[int, str, str]:
    """`execFileNoThrow`: never raises; a missing binary or a timeout is a non-zero code."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except OSError:  # the binary is missing: nothing ran, so nothing was printed
        return 127, "", ""
    except subprocess.TimeoutExpired:
        return 1, "", f"timed out after {timeout:g}s"
    return done.returncode, done.stdout, done.stderr


def read_pdf(file_path: Path) -> PDFData | PDFError:
    """`readPDF`: the whole file, base64-encoded, if it is a non-empty PDF under the size limit."""
    try:
        original_size = file_path.stat().st_size
        if original_size == 0:
            return PDFError("empty", f"PDF file is empty: {file_path}")
        if original_size > PDF_TARGET_RAW_SIZE:
            return PDFError("too_large",
                            f"PDF file exceeds maximum allowed size of {format_file_size(PDF_TARGET_RAW_SIZE)}.")
        data = file_path.read_bytes()
        # An invalid PDF document block in the history fails every later request.
        if not data[:5].decode("ascii", "replace").startswith("%PDF-"):
            return PDFError("corrupted", f"File is not a valid PDF (missing %PDF- header): {file_path}")
        return PDFData(str(file_path), base64.b64encode(data).decode("ascii"), original_size)
    except Exception as exc:  # noqa: BLE001 - upstream's catch: an unknown error, reported
        return PDFError("unknown", str(exc))


_PAGES_LINE = re.compile(r"^Pages:\s+(\d+)", re.MULTILINE)


def get_pdf_page_count(file_path: Path) -> int | None:
    """`getPDFPageCount` (`pdfinfo`): the page count, or `None` when it cannot be determined."""
    code, stdout, _ = _run(["pdfinfo", str(file_path)], timeout=10)
    if code != 0:
        return None
    match = _PAGES_LINE.search(stdout)
    return int(match.group(1)) if match else None


_pdftoppm_available: bool | None = None


def reset_pdftoppm_cache() -> None:
    """`resetPdftoppmCache`."""
    global _pdftoppm_available
    _pdftoppm_available = None


def is_pdftoppm_available() -> bool:
    """`isPdftoppmAvailable`: cached for the process; `-v` prints to stderr and may exit 99."""
    global _pdftoppm_available
    if _pdftoppm_available is None:
        code, _, stderr = _run(["pdftoppm", "-v"], timeout=5)
        _pdftoppm_available = code == 0 or len(stderr) > 0
    return _pdftoppm_available


def extract_pdf_pages(file_path: Path, output_root: Path,
                      page_range: tuple[int, float] | None = None) -> PDFPages | PDFError:
    """`extractPDFPages`: the pages (or a range) as `page-NN.jpg` under `output_root/pdf-<uuid>`."""
    try:
        original_size = file_path.stat().st_size
        if original_size == 0:
            return PDFError("empty", f"PDF file is empty: {file_path}")
        if original_size > PDF_MAX_EXTRACT_SIZE:
            return PDFError("too_large", "PDF file exceeds maximum allowed size for text extraction "
                                         f"({format_file_size(PDF_MAX_EXTRACT_SIZE)}).")
        if not is_pdftoppm_available():
            return PDFError("unavailable", "pdftoppm is not installed. Install poppler-utils (e.g. `brew install "
                                           "poppler` or `apt-get install poppler-utils`) to enable PDF page rendering.")
        output_dir = output_root / f"pdf-{uuid.uuid4()}"
        output_dir.mkdir(parents=True, exist_ok=True)
        args = ["pdftoppm", "-jpeg", "-r", "100"]
        if page_range is not None:
            first, last = page_range
            args += ["-f", str(first)]
            if last != math.inf:
                args += ["-l", str(int(last))]
        args += [str(file_path), str(output_dir / "page")]
        code, _, stderr = _run(args, timeout=120)
        if code != 0:
            if re.search(r"password", stderr, re.IGNORECASE):
                return PDFError("password_protected",
                                "PDF is password-protected. Please provide an unprotected version.")
            if re.search(r"damaged|corrupt|invalid", stderr, re.IGNORECASE):
                return PDFError("corrupted", "PDF file is corrupted or invalid.")
            return PDFError("unknown", f"pdftoppm failed: {stderr}")
        count = len([f for f in output_dir.iterdir() if f.name.endswith(".jpg")])
        if count == 0:
            return PDFError("corrupted", "pdftoppm produced no output pages. The PDF may be invalid.")
        return PDFPages(str(file_path), original_size, count, output_dir)
    except Exception as exc:  # noqa: BLE001 - upstream's catch: an unknown error, reported
        return PDFError("unknown", str(exc))


__all__ = [
    "PDFData",
    "PDFError",
    "PDFPages",
    "extract_pdf_pages",
    "get_pdf_page_count",
    "is_pdftoppm_available",
    "read_pdf",
    "reset_pdftoppm_cache",
]
