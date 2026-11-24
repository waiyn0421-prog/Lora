"""Preserve embedded PDF and OCR text as separate evidence sources."""

import re

from lora_drawing.coordinate_system import invert_affine_bbox


_LEVEL_LABEL = re.compile(r"\b(?:LEVEL|FLOOR|STOREY|STORY|L)\s*[-#]?\s*(\d{1,3}[A-Z]?|GROUND|ROOF|MEZZANINE|BASEMENT)\b", re.I)
_ELEVATION = re.compile(r"\b(?:EL(?:EV(?:ATION)?)?\.?|RL|FFL|TOS)\s*[:=]?\s*([+-]?\d+(?:[.,]\d+)?)\s*(mm|cm|m|metres?|meters?|ft|feet|inches|in|\"|')(?![A-Za-z])", re.I)


def extract_text_records(result, page_number, page_result):
    records = []
    if result.get("file_type") == "pdf":
        geometry = page_result.get("geometry_extraction", {})
        for item in geometry.get("text_annotations", []):
            records.append({"text_id": item.get("id"), "text": item.get("text", ""),
                            "source": "embedded_pdf", "page_number": page_number,
                            "bbox": item.get("bbox"), "coordinate_units": "PDF page points",
                            "confidence_percent": None, "status": "extracted"})
        ocr = page_result.get("ocr", {})
        records.extend(_ocr_records(ocr, page_number, "PDF page points"))
    else:
        image = result.get("image", {})
        transform = image.get("source_to_processed_pixel_transform")
        records.extend(_ocr_records(result.get("ocr", {}), page_number,
                                    "processed image pixels", transform))
    return [record for record in records if str(record.get("text") or "").strip()]


def interpret_text(records):
    levels = []
    for record in records:
        text = str(record.get("text", ""))
        label = _LEVEL_LABEL.search(text)
        elevation = _ELEVATION.search(text)
        if not label and not elevation:
            continue
        explicit_elevation = None
        if elevation:
            raw = elevation.group(1).replace(",", ".")
            explicit_elevation = {"value": float(raw), "raw_value": elevation.group(1),
                                  "unit": _normalize_unit(elevation.group(2)),
                                  "unit_explicit": True, "source": "annotation_text"}
        levels.append({"text_id": record.get("text_id"),
                       "label": label.group(1).upper() if label else None,
                       "raw_text": text, "elevation": explicit_elevation,
                       "bbox": record.get("bbox"), "coordinate_units": record.get("coordinate_units"),
                       "text_source": record.get("source"),
                       "evidence_score": 0.62 if label else 0.48,
                       "recognition_status": "candidate" if label and explicit_elevation else "requires_review",
                       "warnings": ([] if explicit_elevation else ["Elevation value/unit was not explicitly present."])})
    return levels


def _ocr_records(ocr, page_number, units, source_to_processed=None):
    if not isinstance(ocr, dict):
        return []
    output = []
    for index, item in enumerate(ocr.get("text_blocks", [])):
        bbox = item.get("bbox_pdf_points") if units == "PDF page points" else item.get("bbox_pixels")
        coordinate_units = units
        if source_to_processed and bbox:
            bbox = invert_affine_bbox(bbox, source_to_processed)
            coordinate_units = "original image pixels"
        output.append({"text_id": "p{}-ocr-{:06d}".format(page_number, index + 1),
                       "text": item.get("text", ""), "source": "ocr", "page_number": page_number,
                       "bbox": bbox, "coordinate_units": coordinate_units,
                       "confidence_percent": item.get("confidence_percent"),
                       "status": ocr.get("status", "unknown")})
    return output


def _normalize_unit(value):
    unit = value.lower()
    if unit in ("meter", "meters", "metre", "metres"):
        return "m"
    if unit == "centimeter" or unit == "centimeters":
        return "cm"
    if unit in ("feet", "foot", "'"):
        return "ft"
    if unit in ("inch", "inches", '"'):
        return "in"
    return unit
