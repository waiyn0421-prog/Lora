# Drawing Processing Engine

Milestone 3A adds a local, isolated drawing-input processor under `lora_drawing/`. It reads files from a dedicated inbox, returns bounded JSON summaries, and keeps the latest 20 processing results in memory for `lora_get_drawing_summary`. It does not call RevitMCP, import the Revit API, write Revit elements, or persist extracted content.

## Local input workflow

Place a drawing in `mcp/Lora.MCP.Server/drawing_inbox/` and call an MCP tool with its **file name only**. Absolute paths, relative paths, directory traversal, symbolic links, unsupported extensions, and extension/signature mismatches are rejected. The inbox contents are ignored by Git. An administrator may choose another inbox directory with `LORA_DRAWING_INBOX`; tool callers still supply only a file name.

The default processing bounds are 50 MiB per file, 100 PDF pages, 40 million source pixels per image, 25 million rendered pixels per PDF page, 80 million total rendered PDF pixels, 100,000 extracted PDF text characters, 1,000 vector items per page and 5,000 vector items overall. Embedded raster image records and OCR blocks also have per-page and document-wide bounds. PDF time is checked between pages; image time is checked after processing. Native library calls cannot be forcibly interrupted by this cooperative deadline.

## MCP tools

| Tool | Inputs | Result |
|---|---|---|
| `lora_inspect_drawing` | `file_name`, optional `dpi`, `run_ocr`, `max_dimension` | Detects supported content and runs the appropriate processor; identifies DWG without parsing it. |
| `lora_process_pdf` | `file_name`, optional `dpi` (36–300), `run_ocr` | Metadata, page dimensions/rotation, embedded text and boxes, vector summaries, scanned-page candidates and in-memory render dimensions. |
| `lora_process_image` | `file_name`, optional `max_dimension` (256–5000), `run_ocr` | Original image metadata, EXIF orientation, resize transform, OCR status and basic edges/line segments. |
| `lora_get_drawing_summary` | `processing_id` | Retrieves a result retained in this server process. Results expire when the process exits or the 20-entry cache evicts them. |

Tool names, definitions and registration are in `lora_mcp/server.py`. The existing eight BIM tools remain registered; no model-writing route or listener was added.

## Dependencies

Install the server dependencies from the MCP server directory:

```powershell
python -m pip install -r requirements.txt
```

PyMuPDF extracts PDF text/vectors and renders pages. Pillow validates, reads, rotates, resizes and prepares raster images. OpenCV headless and NumPy perform Canny edge and Hough line detection. `pytesseract` is a Python wrapper only: OCR also requires a separately installed Tesseract executable available to the local process. If the executable or wrapper is missing, requested OCR is reported unavailable; no text is fabricated. OCR is opt-in (`run_ocr=true`). For PDFs it runs only on pages with fewer than 20 extracted characters.

The Tesseract executable was not found during Milestone 3B verification. OCR is therefore not claimed as operational in this environment; only the unavailable-engine handling was tested.

## Extraction and completeness

- PDF embedded text is preferred. Bboxes, vector coordinates and embedded raster image placements are in source page points. Geometry returns path counts and a bounded list of primitive records; output limits set `extraction_complete=false` and warnings.
- A page with fewer than 20 embedded text characters is marked `scanned_page_candidate`, not definitively classified as a scan. It may also be a blank or vector-only page.
- PDF pages are rendered to PNG in memory at the requested DPI. The response reports dimensions and render status; cached page images can be returned separately as MCP image content, with bounds documented in `DrawingGeometry.md`.
- Image OCR/edge/line coordinates are in processed pixels. `source_to_processed_pixel_transform` maps original pixels through EXIF orientation and resize. The original dimensions and color mode remain in the result.
- Processing results state unavailable dependencies, warnings, OCR status, truncation and extraction completeness explicitly.
- No drawing scale is inferred from page or pixel dimensions. Image DPI may define physical scan size but does not establish the drawing's real-world scale.
- DWG is signature-checked and reported as unsupported for native extraction. No conversion to PDF or preservation of CAD geometry is claimed.

## Example summary

```json
{
  "file_name": "plan.pdf",
  "file_type": "pdf",
  "page_count": 2,
  "pages": [{
    "page_number": 1,
    "width": 841.89,
    "height": 595.28,
    "units": "PDF points (1/72 inch)",
    "rotation_degrees_clockwise": 0,
    "scanned_page_candidate": false,
    "render": {"status": "rendered_in_memory", "dpi": 100, "mime_type": "image/png"},
    "vector_geometry": {"lines": 24, "rectangles": 3, "paths": 16}
  }],
  "extraction_complete": true,
  "warnings": []
}
```

The values above illustrate response shape only; they are not values from a user drawing.
