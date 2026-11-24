# Drawing Geometry Extraction and Analysis

Milestone 3B adds geometric descriptions and conservative candidate hypotheses to the existing PDF/raster pipeline. Geometry is not semantic Revit content. No candidate is confirmed as a wall, door, window, room or dimension.

## Geometry sources

### PDF vectors

`pdf_geometry.py` reads PyMuPDF drawing paths and emits bounded entities for lines, rectangles, quadrilaterals, cubic Bezier curves, reconstructed line polylines, and source text spans. Bezier curves are sampled into 12-segment approximations by default; original control points are retained. Path records carry closed/open state, bbox, stroke width, stroke/fill color, path type, line caps/joins, dashes and close-path evidence when available. Entity IDs retain page number. Geometry and text bboxes remain in PDF page points.

PDF path primitives may overlap (for example a rectangle and its path edges). Entity counts are extraction records, not unique design objects. Every page reports bounds and completeness.

### Raster linework

`image_processor.py` runs Canny edges then probabilistic Hough line detection, connected-component extraction and contour simplification. It reports horizontal/vertical/other orientation classes, line endpoints and lengths, component/contour counts, bboxes, areas and candidate closure state. Up to 500 line segments and contours and 1,000 components are returned. OpenCV contour tracing closes edge loops internally; `candidate_open_or_boundary_clipped` therefore remains a hypothesis, not mathematical proof that the source stroke is open.

Geometry detection uses the resized, EXIF-corrected image. The full source-to-processed affine matrix and each transformation step are returned. Line endpoints and simplified contours also include original-image pixel coordinates; processed coordinates remain attached for reproducibility. Edge pixels, connected components and contour statistics use processed pixels.

## Reusable operations

`geometry.py` provides line length/orientation, parallel and perpendicular tests, finite-segment intersection, endpoint proximity, polygon closure/area, bounding boxes, line-like grouping and bounded relationship analysis. Distance tolerances use the source geometry's coordinate units; they are never assumed to be millimetres. Defaults are 3 degrees for parallel/perpendicular comparisons, 0.25 source units for intersection extension and 2 source units for endpoint proximity. MCP analysis accepts bounded overrides.

Analysis reports pair-check/relationship truncation. Relationship checks are bounded to 100,000 line-segment pairs and 10,000 returned relationships. Geometry group IDs describe endpoint-connected linework only; they do not mean BIM systems or rooms.

## Candidate hypotheses

`candidates.py` emits unique IDs, source file/page, coordinates, bbox, source geometry references, method, evidence score, evidence, warnings and a review status. Implemented patterns include:

- Near-parallel runs with overlap and bounded separation → wall-like line group.
- Collinear separated runs → unresolved opening candidate; a nearby curve may increase a door-like candidate score; repeated short perpendicular connectors across paired runs may increase a window-like candidate score.
- Closed polygons above a page-relative area threshold → room-boundary candidate.
- Line intersections or close endpoints → possible wall-junction candidate.
- Long lines with short perpendicular endpoint marks → dimension-line candidate.

Every candidate warns that it is not a confirmed element. Opening labels preserve unresolved door/window alternatives; symbols, title blocks, furniture, hatches and annotation can trigger false positives. A lower evidence score sets `recognition_status` to `requires_review`; stronger geometric evidence uses `candidate`. Nothing is labeled `confirmed`.

## MCP tools

| Tool | Inputs | Purpose |
|---|---|---|
| `lora_extract_drawing_geometry` | `file_name`, optional PDF `dpi`, raster `max_dimension` | Processes an inbox drawing and returns bounded geometry with a `processing_id`. |
| `lora_analyze_drawing_geometry` | `processing_id`, optional `page_number`, angle/distance tolerances, `max_pairs` | Returns line relationships, intersections, endpoint proximity, groups and completeness. |
| `lora_get_architectural_candidates` | `processing_id`, optional `page_number` | Returns conservative geometry candidates and evidence. |
| `lora_get_drawing_render` | `processing_id`, `page_number=1` | Returns processed PDF page or raster preview as MCP image content. |

## Safe render retrieval

Page renders are not written to a drawing folder, temp directory or other user path. The pipeline holds PNG bytes in a process-local cache, with an 8 MiB per-page and 32 MiB total cache bound. Old entries are evicted when needed; MCP image content is returned only while present. The `get_drawing_summary` result marks which renders remain retrievable. This avoids opening a filesystem read endpoint and limits temporary persistence to the local MCP process memory. Large pages may render successfully but be unavailable for image retrieval if they exceed the cache limit.

The drawings remain inside the configured inbox path. The render tool accepts only a prior `processing_id` and page number, never a file path.

## Recognition and OCR limits

These methods are geometric heuristics, not plan understanding. They do not classify walls, door swings, window families, room boundaries, dimensions or scale reliably. OCR is not claimed operational in this environment: the Tesseract executable was not found during Milestone 3B verification. If OCR is requested elsewhere, `pytesseract` must successfully invoke a verified local Tesseract executable; failure is reported as unavailable and makes requested extraction incomplete.
