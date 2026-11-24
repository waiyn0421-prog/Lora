# Dependency and License Manifest

The core server dependencies are in `requirements.txt`; optional PDF/raster processing dependencies are in `requirements-drawing.txt`, and the optional OCR wrapper is in `requirements-ocr.txt`. The project does not vendor dependency implementation sources. Versions and license metadata below were read from the existing development `.venv` on 2026-11-22. This is an inventory, not legal advice or a final release licensing decision.

| Package in current environment | Version | Metadata license | Release note |
|---|---:|---|---|
| `mcp` | 1.30.0 | MIT | Include its upstream notice as required. |
| `requests` | 2.34.2 | Apache-2.0 | Include applicable license/notice. |
| `PyMuPDF` | 1.28.2 | AGPL-3.0 or Artifex commercial license | Optional drawing dependency. Official [PyMuPDF licensing documentation](https://pymupdf.readthedocs.io/en/latest/about.html) states these licensing options. The server reports missing PyMuPDF as a structured dependency error when PDF processing is requested. Do not bundle/install it in a distributed application until the selected licensing obligations are reviewed. |
| `Pillow` | 12.3.0 | MIT-CMU | Optional drawing dependency. Pillow's [official license documentation](https://pillow.readthedocs.io/en/stable/about.html) identifies MIT-CMU. |
| `opencv-python-headless` | 4.14.0.94 | Apache-2.0 for OpenCV; MIT for the wheel build scripts | Optional drawing dependency. Upstream documents additional wheel-bundled third-party components; review `LICENSE-3RD-PARTY.txt` for the exact wheel before redistribution. |
| `numpy` | 2.5.5 | Modified BSD | Optional dependency used by edge/line extraction; see [NumPy license](https://numpy.org/doc/stable/license.html). |
| `pytesseract` | 0.3.13 | Apache-2.0 | Optional OCR Python wrapper. A Tesseract executable is a separate external dependency and is not included. |

Core installs no longer install PyMuPDF. The code imports it only when a PDF is processed and returns `dependency_missing` if absent. Pillow is likewise needed only for raster processing; OpenCV/NumPy are used for optional edge/line extraction; OCR is optional and reports when its wrapper or executable is unavailable. This makes PDF/raster/OCR features optional, not license-cleared. The `requirements-test.txt` includes all optional sets so the complete test suite can run.

Dependency ranges are not reproducible release locks. Before a release, generate a complete lock with hashes, inventory all transitive packages, retrieve exact license texts/NOTICE files, review the chosen PyMuPDF distribution path and exact OpenCV wheel notices, and package notices without copying unverified license statements. Until that review is complete, the release candidate remains source-only and does not include wheels or installed dependency binaries.
