# Drawing Formats

## PDF

Implemented with PyMuPDF. Reads metadata, page count, page bounds, rotation, embedded text and text bboxes. Reports vector path counts, a bounded set of line, rectangle, quad and curve records, and embedded raster image dimensions and page placements. Each page is rendered in memory to PNG at configurable DPI (36–300). Multi-page files are supported within the page/render pixel limits. Pages with less than 20 embedded text characters are flagged as scanned-page candidates. OCR is optional and requires both `pytesseract` and a local Tesseract executable.

Encrypted PDFs requiring a password, corrupt documents, empty documents, oversized page counts and excessive render pixel budgets return explicit errors. Text, geometry and OCR output are bounded and warnings mark truncation.

## JPG / JPEG / PNG

Implemented with Pillow and OpenCV. Reports actual decoded format, original pixel dimensions, color mode and EXIF orientation. EXIF orientation is applied for processing and its affine transform is returned with the resize factor. The image is resized to a configurable maximum edge while preserving aspect ratio. OCR preparation is separate from Canny edge detection and Hough line detection. Segment bboxes are returned in processed-image pixel coordinates. OCR is opt-in and requires Tesseract.

Corrupt files, extension/signature mismatches, unsupported formats, files above 50 MiB and decoded images above 40 million pixels are rejected. Pillow may reject extreme image headers before application-level dimensions are checked.

## DWG

Only filename and DWG version signature recognition are provided. `cad_adapter.py` defines a future adapter contract, but the installed adapter always reports `cad_adapter_unavailable`. There is no native DWG parsing, geometry extraction, external CAD dependency, PDF conversion or assertion that CAD geometry is preserved.

## Sketches

Raster sketches use the JPG/PNG path. Processing prepares and optionally resizes the raster and reports basic lines/edges and OCR. No wall, door, room, dimension or other semantic recognition is implemented. Pixel measurements are not claimed to be dimensionally accurate.

## Optional OCR environment

Install the Python packages from `requirements.txt`, then install Tesseract OCR separately and make its executable available on `PATH`. The code detects wrapper/executable failures and returns `dependency_missing` or `engine_unavailable`; OCR can be omitted with `run_ocr=false`.

The current development environment did not have a discoverable Tesseract executable, so OCR has not been verified here.
