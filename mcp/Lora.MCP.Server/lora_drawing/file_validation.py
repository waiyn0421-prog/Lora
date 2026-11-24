"""Resolve only named uploads inside a dedicated local drawing inbox."""

import os
import stat
from pathlib import Path

from .exceptions import DrawingError
from .models import ProcessingLimits


SUPPORTED = {".pdf": "pdf", ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".dwg": "dwg"}


def inbox_path():
    configured = os.environ.get("LORA_DRAWING_INBOX")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[1] / "drawing_inbox"
    return root


def resolve_input(file_name, limits=None):
    limits = limits or ProcessingLimits()
    if not isinstance(file_name, str) or not file_name or len(file_name) > 255:
        raise DrawingError("invalid_file_name", "file_name must be a non-empty file name of at most 255 characters")
    candidate_name = Path(file_name)
    if (candidate_name.name != file_name or file_name in (".", "..") or "/" in file_name or
            "\\" in file_name or ":" in file_name or any(ord(char) < 32 for char in file_name)):
        raise DrawingError("invalid_file_name", "Only a file name is accepted; directory paths are not allowed")
    suffix = candidate_name.suffix.lower()
    if suffix not in SUPPORTED:
        raise DrawingError("unsupported_format", "Supported file extensions are .pdf, .jpg, .jpeg, and .png")
    root = inbox_path()
    unresolved = root / file_name
    if unresolved.is_symlink():
        raise DrawingError("path_not_allowed", "Symbolic links are not accepted as drawing inputs")
    try:
        path = unresolved.resolve(strict=True)
    except (OSError, RuntimeError):
        raise DrawingError("file_not_found", "The named drawing was not found in the configured drawing inbox")
    if path.parent != root or path.is_symlink() or not path.is_file():
        raise DrawingError("path_not_allowed", "Drawing inputs must be regular files directly inside the drawing inbox")
    try:
        with unresolved.open("rb") as source:
            opened_stat = os.fstat(source.fileno())
            entry_stat = unresolved.lstat()
            if (stat.S_ISLNK(entry_stat.st_mode) or
                    opened_stat.st_dev != entry_stat.st_dev or
                    opened_stat.st_ino != entry_stat.st_ino):
                raise DrawingError("path_not_allowed", "Drawing path changed during secure file opening")
            size = opened_stat.st_size
            if size <= 0:
                raise DrawingError("empty_file", "The drawing file is empty")
            if size > limits.max_file_bytes:
                raise DrawingError("file_too_large", "Drawing exceeds the configured file size limit",
                                   {"size_bytes": size, "max_file_bytes": limits.max_file_bytes})
            if suffix == ".dwg":
                data = None
                header = source.read(16)
            else:
                data = source.read(limits.max_file_bytes + 1)
                if len(data) > limits.max_file_bytes:
                    raise DrawingError("file_too_large", "Drawing grew beyond the configured file size limit",
                                       {"max_file_bytes": limits.max_file_bytes})
                size = len(data)
                header = data[:16]
    except DrawingError:
        raise
    except OSError:
        raise DrawingError("file_unavailable", "The drawing file could not be opened")
    actual_type = _detect_type(header)
    expected_type = SUPPORTED[suffix]
    if suffix == ".dwg" and actual_type == "dwg":
        return {"path": path, "file_name": file_name, "file_type": "dwg", "size_bytes": size,
                "data": None}
    if actual_type is None:
        raise DrawingError("invalid_file", "The file signature is not a supported PDF, JPEG, or PNG")
    if actual_type != expected_type:
        raise DrawingError("format_mismatch", "File extension does not match the detected file format",
                           {"extension_type": expected_type, "detected_type": actual_type})
    return {"path": path, "file_name": file_name, "file_type": actual_type, "size_bytes": size,
            "data": data}


def _detect_type(header):
    if header.startswith(b"%PDF-"):
        return "pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header[:6] in (b"AC1009", b"AC1012", b"AC1014", b"AC1015", b"AC1018", b"AC1021",
                      b"AC1024", b"AC1027", b"AC1032"):
        return "dwg"
    return None
