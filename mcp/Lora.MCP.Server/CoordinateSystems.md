# Drawing Coordinate Systems

## PDF pages

PDF page geometry uses PDF points (`1 point = 1/72 inch`). PyMuPDF page rectangles and text/vector bboxes are returned in page-coordinate points, with page rotation recorded in degrees clockwise and a top-left, right/down axis description. The response preserves the returned page width and height and records the source coordinate space on every page.

Conversions are explicit:

- `mm = PDF points × 25.4 / 72`
- `PDF points = mm × 72 / 25.4`
- `render pixels = PDF points × DPI / 72`
- `PDF points = render pixels × 72 / DPI`

The processor records the render DPI, pixel dimensions and image MIME type. Rendered page PNGs are transient in-memory buffers used by the pipeline and optional OCR. A bounded `lora_get_drawing_render` tool returns them as MCP image content while they remain in the process cache; they are never written to a file. See [DrawingGeometry.md](DrawingGeometry.md) for cache limits.

## Raster images

The original image coordinate space starts at the top-left and uses integer pixels, with x to the right and y down. Original width, height and color mode are reported. EXIF orientation (when present) is applied to processing pixels. A 3×3 affine matrix maps original pixel coordinates through orientation and resize to processed pixels. OCR boxes and detected line segments use the processed coordinate space.

When a trustworthy DPI is present/provided, `mm = pixels × 25.4 / DPI` describes physical scan size. The engine does not infer DPI or a drawing scale from pixel dimensions, paper dimensions, or visual content. No real-world model coordinates are emitted unless future calibration supplies a known reference.

## Unit and scale guarantees

PDF points, raster pixels, image DPI, millimetres and real-world drawing scale are kept distinct. Page physical size alone does not establish the drawing scale. No architecture element dimensions or Revit coordinates are inferred in this milestone.

Conversion helpers are in `lora_drawing/coordinate_system.py`; results include coordinate-system metadata and explicit unit labels.
