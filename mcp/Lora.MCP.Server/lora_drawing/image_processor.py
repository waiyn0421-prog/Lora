"""Bounded raster preparation, optional OCR, and basic line/edge detection."""

import time
from io import BytesIO

from .coordinate_system import invert_affine_bbox, invert_affine_point, pixel_coordinate_metadata
from .exceptions import DrawingError
from .geometry import bounding_box, line_length, line_orientation_degrees
from .models import ProcessingLimits
from .ocr import confidence_percent


def process_image(path, file_name, max_dimension=3000, run_ocr=False, limits=None, render_handler=None,
                  source_bytes=None):
    limits = limits or ProcessingLimits()
    if isinstance(max_dimension, bool) or not isinstance(max_dimension, int) or not 256 <= max_dimension <= 5000:
        raise DrawingError("invalid_input", "max_dimension must be an integer from 256 to 5000")
    if not isinstance(run_ocr, bool):
        raise DrawingError("invalid_input", "run_ocr must be a boolean")
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise DrawingError("dependency_missing", "Image processing requires Pillow; install requirements-drawing.txt",
                           {"dependency": "Pillow"})
    started = time.monotonic()
    try:
        from io import BytesIO
        check_source = BytesIO(source_bytes) if source_bytes is not None else str(path)
        with Image.open(check_source) as check:
            check.verify()
        image_source = BytesIO(source_bytes) if source_bytes is not None else str(path)
        image = Image.open(image_source)
    except Exception as exc:
        raise DrawingError("corrupt_image", "Image could not be decoded", {"reason": type(exc).__name__})
    try:
        original_width, original_height = image.size
        if original_width <= 0 or original_height <= 0:
            raise DrawingError("invalid_image_dimensions", "Image dimensions must be positive")
        source_pixels = original_width * original_height
        if source_pixels > limits.max_image_pixels:
            raise DrawingError("image_too_large", "Image exceeds the configured pixel limit",
                               {"width": original_width, "height": original_height,
                                "pixels": source_pixels, "max_pixels": limits.max_image_pixels})
        color_mode = image.mode
        try:
            orientation = int(image.getexif().get(274, 1))
        except Exception:
            orientation = 1
        orientation = orientation if orientation in range(1, 9) else 1
        oriented = ImageOps.exif_transpose(image)
        oriented_width, oriented_height = oriented.size
        target_scale = min(1.0, float(max_dimension) / max(oriented_width, oriented_height))
        processed_width = max(1, int(round(oriented_width * target_scale)))
        processed_height = max(1, int(round(oriented_height * target_scale)))
        scale_x = float(processed_width) / oriented_width
        scale_y = float(processed_height) / oriented_height
        if (processed_width, processed_height) != oriented.size:
            prepared = oriented.resize((processed_width, processed_height), Image.Resampling.LANCZOS)
        else:
            prepared = oriented.copy()
        affine = _orientation_affine(orientation, original_width, original_height)
        affine = [
            [affine[0][0] * scale_x, affine[0][1] * scale_x,
             affine[0][2] * scale_x + 0.5 * scale_x - 0.5],
            [affine[1][0] * scale_y, affine[1][1] * scale_y,
             affine[1][2] * scale_y + 0.5 * scale_y - 0.5],
            [0.0, 0.0, 1.0],
        ]
        edge_result = _detect_edges_and_lines(prepared)
        _attach_source_coordinates(edge_result, affine)
        geometry_entities = _raster_geometry_entities(edge_result, affine)
        render_available = False
        if render_handler:
            output = BytesIO()
            prepared.save(output, format="PNG", optimize=True)
            render_available = bool(render_handler(1, output.getvalue(),
                                                   {"mime_type": "image/png", "width": processed_width,
                                                    "height": processed_height}))
        ocr_result = _run_ocr(prepared) if run_ocr else {"status": "not_requested", "text_blocks": []}
        elapsed = time.monotonic() - started
        warnings = []
        complete = (edge_result["status"] == "completed" and
                    not edge_result.get("segments_truncated", False) and
                    not edge_result.get("components_truncated", False) and
                    not edge_result.get("contours_truncated", False))
        if edge_result["status"] != "completed":
            warnings.append(edge_result.get("message", "line and edge extraction was unavailable"))
        elif edge_result.get("segments_truncated"):
            warnings.append("detected line segment output was truncated to the configured limit")
        if edge_result.get("components_truncated") or edge_result.get("contours_truncated"):
            warnings.append("connected-component or contour output was truncated")
        if ocr_result["status"] in ("dependency_missing", "engine_unavailable"):
            warnings.append(ocr_result.get("message", "OCR could not be completed"))
            if run_ocr:
                complete = False
        if ocr_result.get("truncated"):
            complete = False
            warnings.append("OCR text block output was truncated by configured per-image limit")
        if elapsed > limits.max_processing_seconds:
            complete = False
            warnings.append("processing time limit exceeded after bounded image processing completed")
        return {
            "file_name": file_name, "file_type": "jpeg" if image.format == "JPEG" else "png",
            "file_size_bytes": len(source_bytes) if source_bytes is not None else path.stat().st_size,
            "image": {"width_pixels": original_width, "height_pixels": original_height,
                      "color_mode": color_mode, "format": image.format,
                      "exif_orientation": orientation, "orientation_applied": orientation != 1,
                      "oriented_width_pixels": oriented_width, "oriented_height_pixels": oriented_height,
                      "coordinate_system": pixel_coordinate_metadata(original_width, original_height),
                      "source_to_processed_pixel_transform": affine,
                      "transform_description": "3x3 affine matrix maps original pixel coordinates to processed pixel coordinates; includes EXIF orientation and resize",
                      "transformation_history": [
                          {"operation": "exif_orientation", "orientation": orientation,
                           "applied": orientation != 1, "source": "EXIF tag 274"},
                          {"operation": "resize", "scale": target_scale,
                           "scale_x": scale_x, "scale_y": scale_y,
                           "resampling": "LANCZOS" if target_scale < 1.0 else "none"},
                          {"operation": "geometry_preprocessing", "steps": [
                              "processed RGB conversion", "grayscale conversion",
                              "Canny edge detection (thresholds 50 and 150)",
                              "Hough probabilistic line detection"]}]},
            "preparation": {"status": "completed", "width_pixels": processed_width,
                            "height_pixels": processed_height, "resize_scale": target_scale,
                            "resize_scale_x": scale_x, "resize_scale_y": scale_y,
                            "resampling": "LANCZOS" if target_scale < 1.0 else "none",
                            "render_retrieval_available": render_available},
            "ocr": ocr_result,
            "geometry": edge_result,
            "geometry_entities": geometry_entities,
            "geometry_coordinate_system": "original image pixels; processed-pixel coordinates retained on each entity",
            "extraction_complete": complete,
            "warnings": warnings,
        }
    finally:
        image.close()


def _orientation_affine(orientation, width, height):
    # Maps original source pixel coordinates to the EXIF-corrected orientation.
    matrices = {
        1: [[1, 0, 0], [0, 1, 0]],
        2: [[-1, 0, width - 1], [0, 1, 0]],
        3: [[-1, 0, width - 1], [0, -1, height - 1]],
        4: [[1, 0, 0], [0, -1, height - 1]],
        5: [[0, 1, 0], [1, 0, 0]],
        6: [[0, -1, height - 1], [1, 0, 0]],
        7: [[0, -1, height - 1], [-1, 0, width - 1]],
        8: [[0, 1, 0], [-1, 0, width - 1]],
    }
    return matrices[orientation]


def _detect_edges_and_lines(image):
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {"status": "dependency_missing", "edge_pixel_count": None, "line_count": None,
                "line_segments": [], "message": "OpenCV and NumPy are required for edge and line detection; install requirements-drawing.txt"}
    rgb = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    detected = cv2.HoughLinesP(edges, 1, 3.141592653589793 / 180.0, threshold=60,
                               minLineLength=max(20, min(image.size) // 100), maxLineGap=8)
    segments = []
    if detected is not None:
        for line in detected[:500]:
            x1, y1, x2, y2 = [int(value) for value in line[0]]
            segments.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                             "coordinate_units": "processed image pixels"})
    component_count, _, component_stats, _ = cv2.connectedComponentsWithStats(edges, connectivity=8)
    components = []
    for index in range(1, min(component_count, 1001)):
        x, y, width, height, area = [int(value) for value in component_stats[index]]
        components.append({"bbox": [x, y, x + width, y + height], "area_pixels": area})
    contours_raw, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = []
    for index, contour in enumerate(contours_raw[:500]):
        perimeter = float(cv2.arcLength(contour, True))
        area = float(cv2.contourArea(contour))
        simplified = cv2.approxPolyDP(contour, max(1.0, perimeter * 0.002), True)
        points = [[int(point[0][0]), int(point[0][1])] for point in simplified[:200]]
        x, y, width, height = [int(value) for value in cv2.boundingRect(contour)]
        touches_border = x == 0 or y == 0 or x + width >= image.width or y + height >= image.height
        contours.append({"contour_index": index + 1, "points": points,
                         "bbox": [x, y, x + width, y + height], "area_pixels_squared": area,
                         "perimeter_pixels": perimeter,
                         "closure_status": "candidate_open_or_boundary_clipped" if touches_border or area <= 2.0
                                           else "candidate_closed",
                         "coordinate_units": "processed image pixels"})
    return {"status": "completed", "edge_pixel_count": int(cv2.countNonZero(edges)),
            "line_count": int(0 if detected is None else len(detected)),
            "line_segments": segments,
            "segments_truncated": bool(detected is not None and len(detected) > len(segments)),
            "connected_components": components,
            "connected_component_count": max(0, int(component_count) - 1),
            "components_truncated": component_count > 1001,
            "contours": contours,
            "contour_count": len(contours_raw),
            "contours_truncated": len(contours_raw) > len(contours),
            "processing_steps": ["processed image converted to RGB", "RGB converted to grayscale",
                                 "Canny edge detector with thresholds 50 and 150",
                                 "probabilistic Hough line detection",
                                 "8-connected edge component extraction",
                                 "contour extraction and polygon simplification"],
            "coordinate_units": "processed image pixels"}


def _run_ocr(image):
    try:
        import pytesseract
    except ImportError:
        return {"status": "dependency_missing", "text_blocks": [],
                "message": "OCR requested but pytesseract is not installed"}
    try:
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        records = []
        text_values = data.get("text", [])
        truncated = len(text_values) > 2_000
        for index, text in enumerate(text_values[:2_000]):
            text = str(text).strip()
            if text:
                left, top = int(data["left"][index]), int(data["top"][index])
                width, height = int(data["width"][index]), int(data["height"][index])
                records.append({"text": text, "bbox_pixels": [left, top, left + width, top + height],
                                "confidence_percent": confidence_percent(data.get("conf", [None] * len(text_values))[index]),
                                "coordinate_units": "processed image pixels"})
        return {"status": "completed", "text_blocks": records, "block_count": len(records),
                "truncated": truncated}
    except Exception as exc:
        return {"status": "engine_unavailable", "text_blocks": [],
                "message": "OCR engine was unavailable or failed", "error_type": type(exc).__name__}


def _raster_geometry_entities(edge_result, transform):
    entities = []
    for index, segment in enumerate(edge_result.get("line_segments", [])):
        processed = [[segment["x1"], segment["y1"]], [segment["x2"], segment["y2"]]]
        source = [_inverse_transform(point, transform) for point in processed]
        length = line_length(*source)
        orientation = line_orientation_degrees(*source)
        orientation_class = "horizontal" if min(orientation, 180 - orientation) <= 10 else (
            "vertical" if abs(orientation - 90) <= 10 else "other")
        entities.append({"id": "raster-line-{:05d}".format(index + 1), "kind": "line",
                         "start": source[0], "end": source[1], "bbox": bounding_box(source),
                         "length": length, "orientation_degrees": orientation,
                         "orientation_class": orientation_class,
                         "processed_points": processed, "coordinate_units": "original image pixels"})
    for index, contour in enumerate(edge_result.get("contours", [])):
        processed = contour.get("points", [])
        if len(processed) < 2:
            continue
        source = [_inverse_transform(point, transform) for point in processed]
        entities.append({"id": "raster-contour-{:05d}".format(index + 1), "kind": "polyline",
                         "points": source, "bbox": bounding_box(source), "processed_points": processed,
                         "closed": contour.get("closure_status") == "candidate_closed",
                         "closure_status": contour.get("closure_status"),
                         "area": contour.get("area_pixels_squared"),
                         "coordinate_units": "original image pixels"})
    return entities


def _attach_source_coordinates(edge_result, transform):
    for line in edge_result.get("line_segments", []):
        line["source_points"] = [_inverse_transform([line["x1"], line["y1"]], transform),
                                 _inverse_transform([line["x2"], line["y2"]], transform)]
        line["source_coordinate_units"] = "original image pixels"
    for component in edge_result.get("connected_components", []):
        component["source_bbox"] = _transform_bbox(component["bbox"], transform)
        component["source_coordinate_units"] = "original image pixels"
    for contour in edge_result.get("contours", []):
        contour["source_points"] = [_inverse_transform(point, transform) for point in contour.get("points", [])]
        contour["source_bbox"] = _transform_bbox(contour["bbox"], transform)
        contour["source_coordinate_units"] = "original image pixels"


def _transform_bbox(bbox, matrix):
    return invert_affine_bbox(bbox, matrix)


def _inverse_transform(point, matrix):
    try:
        return invert_affine_point(point, matrix)
    except ValueError as exc:
        raise DrawingError("invalid_coordinate_transform", str(exc))
