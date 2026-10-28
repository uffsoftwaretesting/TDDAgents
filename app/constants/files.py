"""
Binary file extensions.

Ported from `reference/claude-code/src/constants/files.ts` -> `BINARY_EXTENSIONS`,
`hasBinaryExtension`.
"""

from __future__ import annotations

#: `BINARY_EXTENSIONS`: files that can't be meaningfully read as text.
BINARY_EXTENSIONS = frozenset({
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".tif",
    # Videos
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".flv", ".m4v", ".mpeg", ".mpg",
    # Audio
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma", ".aiff", ".opus",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz", ".z", ".tgz", ".iso",
    # Executables/binaries
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".obj", ".lib", ".app", ".msi", ".deb", ".rpm",
    # Documents (PDF is here; FileReadTool excludes it at the call site)
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # Bytecode / VM artifacts
    ".pyc", ".pyo", ".class", ".jar", ".war", ".ear", ".node", ".wasm", ".rlib",
    # Database files
    ".sqlite", ".sqlite3", ".db", ".mdb", ".idx",
    # Design / 3D
    ".psd", ".ai", ".eps", ".sketch", ".fig", ".xd", ".blend", ".3ds", ".max",
    # Flash
    ".swf", ".fla",
    # Lock/profiling data
    ".lockb", ".dat", ".data",
})


def has_binary_extension(file_path: str) -> bool:
    """`hasBinaryExtension`: the text from the last `.` on, lowercased, is a binary extension."""
    dot = file_path.rfind(".")
    return dot != -1 and file_path[dot:].lower() in BINARY_EXTENSIONS


__all__ = ["BINARY_EXTENSIONS", "has_binary_extension"]
