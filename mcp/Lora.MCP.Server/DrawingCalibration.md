# Drawing Calibration and Spatial Reconstruction

Milestone 3D adds a page-local calibration layer over the existing processed-drawing and recognition caches. It does not call Revit or modify a model. Calibration is deterministic and evidence-driven.

## Scale evidence

`lora_calibrate_drawing` requires an existing `processing_id` and `recognition_id` for the same drawing. Scale is established only by either:

- `explicit_scale_mm_per_source_unit`: a positive user-supplied scale.
- One or more `calibration_references`: source endpoints, a positive real dimension and unit, axis (`x`, `y`, or `both`), evidence source, and `user_confirmed=true`.

References not marked user-confirmed remain in the result but are excluded from calibration. This applies to OCR, extracted PDF text and inferred values. For OCR or embedded text, a caller should set user confirmation only after an actual user verifies the displayed value and its geometry association. `dimension_text_id` may be provided and is checked against recognized dimensions. Unconfirmed ambiguous associations remain flagged. References are checked against extracted geometry segments; this confirms correspondence to linework, not the truth of the stated real-world dimension.

Multiple verified references use a median scale and relative deviation check (default tolerance 2.5%). Inconsistent references leave a transform available for inspection but set `calibration_complete=false` and `calibration_status=conflicted`. Independent X/Y factors require at least one verified reference on both axes and explicit `independent_xy=true`; otherwise a common median scale is used. Axis labels are supplied by the caller and are not inferred from sheet labels.

## Transform mathematics

Source image/PDF coordinates use x-right/y-down. The local architectural frame uses x-right/y-up. With source point `p`, origin `o`, X/Y scales `sx, sy`, and counterclockwise rotation `θ`, the page-local transform is:

```text
x' = cos(θ) sx (x - ox) + sin(θ) sy (y - oy)
y' = sin(θ) sx (x - ox) - cos(θ) sy (y - oy)
```

The corresponding 3×3 affine matrix is returned. `sx` and `sy` are converted from mm per source unit to target mm, cm, m, inches, or feet. Source coordinates and the processor's PDF rotation or image EXIF/resize transform history remain in the result. The calibration transform is appended; no source geometry is overwritten.

Optional `alignment_references` each provide a source point, a point in an already established common coordinate frame, and `user_confirmed=true`. At least two non-degenerate shared correspondences are required. A rigid rotation/translation fit aligns the page after its scale is applied and reports residuals. Without sufficient references, each page stays independent. Different pages can be calibrated with different scales; the engine does not combine them automatically.

## Reconstructed output

Recognized element bounding boxes, positions, source geometry, and spatial relationship intersections are transformed into target coordinates. Wall thickness is recalculated from transformed paired source lines. Room area is calculated from transformed closed boundaries. Candidate status and warnings are preserved. An intersection is only a linework intersection candidate; grid identity is not established without grid evidence. Opening dimensions are transformed only when scale/direction is supportable; otherwise they are marked unavailable.

## MCP tools

| Tool | Purpose |
|---|---|
| `lora_calibrate_drawing` | Calibrate one recognized page and optionally align it from shared verified points. |
| `lora_get_calibration_result` | Retrieve the bounded stored calibration record. |
| `lora_get_reconstructed_spatial_geometry` | Retrieve a bounded/paged list of transformed architectural hypotheses. |
| `lora_validate_calibration_references` | Check input, extracted segment match, recognized text association, and confirmation state. |

Results are held in a 20-record process-local cache and expire after restart/eviction. All geometry is read-only. No calibration value is applied to Revit.

## Example

For a verified 5000 mm span over 250 PDF points, user confirmation yields `20 mm/PDF point`. With a 90° rotation and target metres, the matrix maps the page into a rotated local y-up metre frame. This example describes the calculation only; it is not a result from the user's Revit model or a real drawing.

## Limits

- A user-provided scale/dimension is only as reliable as its input and confirmation.
- Pixel/DPI metadata does not establish drawing scale; explicit calibration is still required.
- OCR availability is reported from the recognition result. OCR is never treated as verified on its own.
- The implementation does not identify grid labels, infer datum meaning, or match separate pages automatically. Shared point correspondences must be provided and confirmed.
- Vector/raster extraction and recognition may be incomplete; callers must review completeness and warnings before using counts or geometry.
