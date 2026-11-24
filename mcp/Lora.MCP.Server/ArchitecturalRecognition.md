# Architectural Drawing Recognition

Milestone 3C adds a deterministic recognition layer over the existing PDF/raster extraction pipeline. It does not call an AI service, modify Revit, or confirm BIM objects. Its outputs are hypotheses for review.

## Flow

`processed drawing ID -> existing drawing cache -> existing geometry candidates/relationships -> text and dimension interpretation -> category hypotheses -> bounded process-local recognition cache`

`RecognitionEngine` reuses the processor's coordinate data, candidate detector, relationship analyzer, and grouping logic. It adds text-to-boundary and dimension-to-line proximity associations, explicit-unit parsing, level annotation patterns, and conservative shape-based column symbols. The provider interface is a future extension point; `NoProviderConfigured` makes no network call.

## Results and status

Each result includes source file/page, candidate category, geometry references, source-coordinate bounding box, coordinate units, method, evidence score, warnings, and `review_status` (`candidate` or `requires_review`). No result is labeled as confirmed. Page and overall completeness reflect extraction and bounded geometry analysis. `text_extraction_complete` and OCR status separately indicate whether OCR text was available. The API reports text evidence sources separately (`embedded_pdf` and `ocr`) and retains OCR confidence when supplied.

Dimensions retain source text and explicit units. Imperial feet/inches remain components. Bare numbers are marked as unit-missing. Geometry lengths and areas remain in source coordinate units. Calibration is `not_provided`; no conversion to millimetres, Revit units, or real-world room areas is inferred. Candidate associations use configurable geometry limits from the existing extraction/analysis functions and a page-relative proximity heuristic for text.

## OCR status

OCR is used only if requested during drawing processing and the Tesseract executable is present. `verify_tesseract()` probes the configured executable's version. In the current development runtime no Tesseract executable was found; therefore scanned-page OCR has not been verified and must not be represented as operational. Embedded PDF text and geometry analysis remain available. OCR absence is reported as a warning and separate text completeness state.

## MCP tools

| Tool | Purpose |
|---|---|
| `lora_recognize_architecture(processing_id, page_number?)` | Run deterministic hypotheses on a previously processed drawing. |
| `lora_get_recognized_elements(recognition_id, category?, page_number?, offset=0, limit=100)` | Retrieve a bounded element page (max 500). |
| `lora_analyze_recognition(recognition_id)` | Summarize counts by category and review status. |
| `lora_get_recognition_warnings(recognition_id)` | Return global/per-page warnings and completeness. |

The cache is process-local and bounded to 20 recognition records. Results expire on server restart or cache eviction. Tools accept IDs, not filesystem paths; drawing access remains restricted to the existing configured inbox. No Revit routes are called by recognition tools.

## Known limits

- Geometry classification is heuristic and can misclassify annotation, furniture, detail symbols, and noisy edges.
- Door/window distinction is uncertain unless the source geometry has relevant evidence; candidate records always retain warnings.
- Level names/elevations are extracted only from explicit annotation text patterns; they are not tied to Revit levels.
- Raster OCR output is usable only when Tesseract was available during initial processing. Recognition will not persist, render, or access arbitrary files.
- DXF/DWG are outside this milestone and remain unsupported by the drawing pipeline.
- No external multimodal provider is configured or called; there are no API credentials or remote dependencies.
- Geometry candidate and relationship caps can mark results incomplete. Consumers must check completeness and warnings before treating counts as exhaustive.

## Synthetic verification

Tests use synthetic linework, a room contour, column-like closed shape, embedded annotation text, dimension text with/without units, and affine transform round trips. These tests validate logic only and are not evidence of recognition accuracy on production plans.
