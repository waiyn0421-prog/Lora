"""OCR runtime verification and confidence normalization helpers."""

from functools import lru_cache
import os
import shutil
import subprocess


def confidence_percent(value):
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return confidence if 0.0 <= confidence <= 100.0 else None


@lru_cache(maxsize=1)
def verify_tesseract():
    """Verify the configured OCR executable without claiming OCR from the wrapper alone."""
    try:
        import pytesseract
    except ImportError:
        return {"status": "python_dependency_missing", "verified": False,
                "executable": None, "version": None}
    configured = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    configured = configured if isinstance(configured, str) and configured else "tesseract"
    executable = configured if os.path.isfile(configured) else shutil.which(configured)
    if not executable:
        return {"status": "executable_not_found", "verified": False,
                "executable": None, "version": None}
    try:
        result = subprocess.run([executable, "--version"], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=3, check=False)
        first_line = (result.stdout or "").splitlines()
        if result.returncode != 0 or not first_line:
            return {"status": "verification_failed", "verified": False,
                    "executable": executable, "version": None,
                    "return_code": result.returncode}
        return {"status": "verified", "verified": True, "executable": executable,
                "version": first_line[0].strip()}
    except Exception as exc:
        return {"status": "verification_failed", "verified": False,
                "executable": executable, "version": None, "error_type": type(exc).__name__}
