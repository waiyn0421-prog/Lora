"""PDF metadata, text, vector summaries, bounded raster rendering and OCR preparation."""

import io
import time

from .coordinate_system import pdf_coordinate_metadata, pixels_to_pdf_points
from .exceptions import DrawingError
from .models import ProcessingLimits
from .ocr import confidence_percent
from .pdf_geometry import extract_pdf_geometry


def process_pdf(path, file_name, dpi=100, run_ocr=False, limits=None, render_handler=None,
                source_bytes=None):
    limits = limits or ProcessingLimits()
    _validate_dpi(dpi)
    if not isinstance(run_ocr, bool):
        raise DrawingError("invalid_input", "run_ocr must be a boolean")
    fitz = _dependency("pymupdf", "PDF processing requires PyMuPDF; install requirements-drawing.txt")
    started = time.monotonic()
    try:
        document = (fitz.open(stream=source_bytes, filetype="pdf") if source_bytes is not None
                    else fitz.open(str(path)))
    except Exception as exc:
        raise DrawingError("corrupt_pdf", "PDF could not be opened", {"reason": type(exc).__name__})
    try:
        if document.needs_pass:
            raise DrawingError("password_protected_pdf", "Password-protected PDFs are not processed")
        page_count = document.page_count
        if page_count <= 0:
            raise DrawingError("empty_pdf", "PDF contains no pages")
        if page_count > limits.max_pdf_pages:
            raise DrawingError("page_limit_exceeded", "PDF exceeds the configured page limit",
                               {"page_count": page_count, "max_pdf_pages": limits.max_pdf_pages})
        pages = []
        render_pixels_total = 0
        vector_items_total = 0
        geometry_entities_total = 0
        embedded_images_total = 0
        ocr_blocks_total = 0
        text_remaining = limits.max_text_chars
        complete = True
        warnings = []
        metadata = dict(document.metadata or {})
        for page_index in range(page_count):
            _check_time(started, limits)
            page = document.load_page(page_index)
            rect = page.rect
            width_points, height_points = float(rect.width), float(rect.height)
            scale = dpi / 72.0
            estimate = int(width_points * scale + 0.999) * int(height_points * scale + 0.999)
            if estimate > limits.max_render_pixels_per_page:
                raise DrawingError("render_limit_exceeded", "A PDF page would exceed the configured render pixel limit",
                                   {"page_number": page_index + 1, "estimated_pixels": estimate,
                                    "max_pixels_per_page": limits.max_render_pixels_per_page})
            render_pixels_total += estimate
            if render_pixels_total > limits.max_render_pixels_total:
                raise DrawingError("render_limit_exceeded", "PDF exceeds the configured total render pixel budget",
                                   {"estimated_pixels": render_pixels_total,
                                    "max_total_pixels": limits.max_render_pixels_total})
            text, text_blocks, text_truncated = _extract_text(page, text_remaining)
            text_remaining -= len(text)
            if text_truncated:
                complete = False
                warnings.append("extracted text exceeded configured output limit")
            if text_remaining < 0:
                text_remaining = 0
            vector_limit = min(limits.max_vector_items_per_page,
                               max(0, limits.max_vector_items_total - vector_items_total))
            drawings, drawing_summary, vector_truncated = _extract_drawings(page, vector_limit)
            vector_items_total += len(drawings)
            geometry_limit = min(limits.max_vector_items_per_page,
                                 max(0, limits.max_vector_items_total - geometry_entities_total))
            geometry_extraction = extract_pdf_geometry(page, page_index + 1, geometry_limit)
            geometry_entities_total += geometry_extraction["entity_count"]
            if not geometry_extraction["complete"]:
                complete = False
                warnings.extend(geometry_extraction["warnings"])
            embedded_images, embedded_image_summary, embedded_images_truncated = _extract_images(
                page, min(limits.max_vector_items_per_page,
                          max(0, limits.max_embedded_images_total - embedded_images_total)))
            embedded_images_total += len(embedded_images)
            if embedded_images_truncated:
                complete = False
                warnings.append("embedded raster image records were truncated by configured per-page limit")
            if vector_truncated:
                complete = False
                warnings.append("vector geometry output was truncated by configured per-page limit")
            scanned_candidate = len(text.strip()) < 20
            rendered, render_info = _render(page, dpi, fitz)
            render_info["retrieval_available"] = bool(render_handler(page_index + 1, rendered, render_info)) \
                if render_handler else False
            ocr = {"status": "not_requested"}
            if scanned_candidate and run_ocr:
                ocr_limit = min(limits.max_ocr_blocks_per_page,
                                max(0, limits.max_ocr_blocks_total - ocr_blocks_total))
                ocr = _run_ocr(rendered, dpi, page_index + 1, warnings, ocr_limit)
                ocr["render"] = render_info
                ocr_blocks_total += ocr.get("block_count", 0)
                if ocr.get("truncated"):
                    complete = False
                    warnings.append("OCR text block output was truncated by configured per-page limit")
                elif ocr.get("status") != "completed":
                    complete = False
            pages.append({
                "page_number": page_index + 1,
                "width": width_points,
                "height": height_points,
                "units": "PDF points (1/72 inch)",
                "rotation_degrees_clockwise": int(page.rotation),
                "coordinate_system": pdf_coordinate_metadata(width_points, height_points, int(page.rotation)),
                "transformation_history": [
                    {"operation": "pdf_page_rotation", "degrees_clockwise": int(page.rotation)},
                    {"operation": "render", "dpi": dpi, "scale_pixels_per_pdf_point": dpi / 72.0}],
                "extracted_text": text,
                "text_blocks": text_blocks,
                "text_character_count": len(text),
                "scanned_page_candidate": scanned_candidate,
                "ocr": ocr,
                "vector_geometry": drawing_summary,
                "vector_items": drawings,
                "geometry_extraction": geometry_extraction,
                "embedded_raster_images": embedded_images,
                "embedded_raster_image_summary": embedded_image_summary,
                "render": {"status": "rendered_in_memory", "dpi": dpi,
                           "pixel_width": render_info.get("pixel_width"),
                           "pixel_height": render_info.get("pixel_height"),
                           "mime_type": "image/png",
                           "retrieval_available": render_info.get("retrieval_available", False)},
                "extraction_complete": (not text_truncated and not vector_truncated and
                                        not embedded_images_truncated and geometry_extraction["complete"]),
            })
            _check_time(started, limits)
        if any(page["scanned_page_candidate"] for page in pages) and not run_ocr:
            warnings.append("one or more pages have little embedded text and may require OCR")
        if time.monotonic() - started > limits.max_processing_seconds:
            complete = False
            warnings.append("processing time limit exceeded after bounded extraction completed")
        return {
            "file_name": file_name, "file_type": "pdf",
            "file_size_bytes": len(source_bytes) if source_bytes is not None else path.stat().st_size,
            "metadata": metadata, "page_count": page_count, "pages": pages,
            "coordinate_system": "Each page uses top-left page coordinates in PDF points; rotation is recorded per page.",
            "extraction_complete": complete and all(p["extraction_complete"] for p in pages),
            "warnings": _unique(warnings),
        }
    finally:
        document.close()


def _dependency(name, message):
    try:
        module = __import__(name)
        return module
    except ImportError:
        raise DrawingError("dependency_missing", message, {"dependency": name})


def _validate_dpi(dpi):
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 36 <= dpi <= 300:
        raise DrawingError("invalid_input", "dpi must be an integer from 36 to 300")


def _extract_text(page, remaining):
    page_dict = page.get_text("dict")
    blocks, text_parts = [], []
    truncated = False
    budget = max(0, remaining)
    char_count = 0
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        spans = []
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                value = span.get("text", "")
                if not value:
                    continue
                if char_count + len(value) > budget:
                    truncated = True
                    break
                text_parts.append(value)
                char_count += len(value)
                spans.append({"text": value, "bbox": [float(v) for v in span.get("bbox", [])],
                              "font": span.get("font"), "size_points": span.get("size")})
            if truncated:
                break
        if spans:
            blocks.append({"bbox": [float(v) for v in block.get("bbox", [])], "spans": spans})
        if truncated:
            break
    return "\n".join(part for part in text_parts), blocks, truncated


def _extract_drawings(page, maximum):
    raw = page.get_drawings()
    items, counts = [], {"paths": len(raw), "lines": 0, "rectangles": 0, "quads": 0, "curves": 0,
                        "other_items": 0}
    for path in raw:
        for item in path.get("items", []):
            kind = item[0] if item else "?"
            if kind == "l":
                counts["lines"] += 1
            elif kind == "re":
                counts["rectangles"] += 1
            elif kind == "qu":
                counts["quads"] += 1
            elif kind == "c":
                counts["curves"] += 1
            else:
                counts["other_items"] += 1
            if len(items) < maximum:
                items.append({"kind": kind, "coordinates": _coordinates(item[1:]),
                              "path_bbox": [float(v) for v in path.get("rect", [])],
                              "coordinate_units": "PDF page points"})
    return items, counts, sum(counts[key] for key in ("lines", "rectangles", "quads", "curves", "other_items")) > maximum


def _extract_images(page, maximum):
    references = page.get_images(full=True)
    output = []
    for image in references[:maximum]:
        xref = int(image[0])
        placements = []
        try:
            placements = [[float(value) for value in rect] for rect in page.get_image_rects(xref)]
        except Exception:
            placements = []
        output.append({"xref": xref, "width_pixels": int(image[2]), "height_pixels": int(image[3]),
                       "bits_per_component": int(image[4]), "color_space": str(image[5]),
                       "smask_xref": int(image[1]), "placement_bboxes_pdf_points": placements})
    return output, {"count": len(references), "reported_count": len(output)}, len(references) > maximum


def _coordinates(values):
    result = []
    for value in values:
        if hasattr(value, "x") and hasattr(value, "y"):
            result.append([float(value.x), float(value.y)])
        elif hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
            result.append(_coordinates(value))
        else:
            result.append(value if isinstance(value, (int, float, str, type(None))) else str(value))
    return result


def _render(page, dpi, fitz):
    pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
    png_bytes = pixmap.tobytes("png")
    return png_bytes, {"dpi": dpi, "pixel_width": pixmap.width, "pixel_height": pixmap.height,
                       "mime_type": "image/png"}


def _run_ocr(png_bytes, dpi, page_number, warnings, max_blocks):
    try:
        from PIL import Image
        import pytesseract
        data = pytesseract.image_to_data(Image.open(io.BytesIO(png_bytes)), output_type=pytesseract.Output.DICT)
        records = []
        text_values = data.get("text", [])
        truncated = len(text_values) > max_blocks
        for index, text in enumerate(text_values[:max_blocks]):
            text = str(text).strip()
            if text:
                left, top = int(data["left"][index]), int(data["top"][index])
                width, height = int(data["width"][index]), int(data["height"][index])
                records.append({"text": text, "bbox_pixels": [left, top, left + width, top + height],
                                "confidence_percent": confidence_percent(data.get("conf", [None] * len(text_values))[index]),
                                "bbox_pdf_points": [pixels_to_pdf_points(v, dpi) for v in
                                                    (left, top, left + width, top + height)]})
        return {"status": "completed", "text_blocks": records, "block_count": len(records),
                "truncated": truncated,
                "coordinate_units": {"bbox_pixels": "rendered pixels", "bbox_pdf_points": "PDF points"}}
    except ImportError:
        warnings.append("OCR requested but Pillow or pytesseract is not installed")
        return {"status": "dependency_missing", "text_blocks": []}
    except Exception as exc:
        warnings.append("OCR requested but the OCR engine was unavailable or failed")
        return {"status": "engine_unavailable", "error_type": type(exc).__name__, "text_blocks": []}


def _check_time(started, limits):
    if time.monotonic() - started > limits.max_processing_seconds:
        raise DrawingError("processing_time_limit", "PDF processing exceeded the configured time limit")


def _unique(values):
    return list(dict.fromkeys(values))
