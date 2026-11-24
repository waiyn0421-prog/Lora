"""Parse dimension text conservatively and link only by spatial proximity."""

import re

from .spatial import bbox_gap


_METRIC_OR_UNIT = re.compile(r"(?<![A-Za-z])([+-]?\d[\d,]*(?:\.\d+)?)\s*(mm|cm|m|meters?|metres?|ft|feet|inches|in|\"|')(?![A-Za-z])", re.I)
_IMPERIAL = re.compile(r"(?<!\d)(\d+)\s*'\s*-?\s*(\d+(?:\.\d+)?)\s*(?:\"|in)?")
_NUMBER = re.compile(r"^\s*([+-]?\d[\d,]*(?:\.\d+)?)\s*$")


def parse_dimension_text(text):
    value = str(text or "").strip()
    if re.search(r"\b(?:LEVEL|FLOOR|STOREY|STORY|ELEVATION|EL|FFL|TOS|RL)\b", value, re.I):
        return None
    imperial = _IMPERIAL.search(value)
    if imperial:
        return {"raw_text": value, "value": None, "unit": "ft_in_components",
                "explicit_unit": True, "components": {"feet": int(imperial.group(1)),
                                                       "inches": float(imperial.group(2))},
                "measurement_status": "parsed_not_converted"}
    match = _METRIC_OR_UNIT.search(value)
    if match:
        number = _number(match.group(1))
        unit = match.group(2).lower()
        if unit in ("meters", "meter", "metres", "metre"):
            unit = "m"
        elif unit in ("feet", "foot", "'"):
            unit = "ft"
        elif unit in ("inches", '"'):
            unit = "in"
        return {"raw_text": value, "value": number, "unit": unit,
                "explicit_unit": True, "components": None,
                "measurement_status": "text_value_unit_explicit_not_converted"}
    bare = _NUMBER.search(value)
    if bare:
        return {"raw_text": value, "value": _number(bare.group(1)), "unit": None,
                "explicit_unit": False, "components": None,
                "measurement_status": "unit_missing_unverified"}
    return None


def interpret_dimension_text(records, dimension_candidates, page_diagonal):
    output = []
    candidates = [candidate for candidate in dimension_candidates
                  if candidate.get("candidate_type") == "dimension_line_candidate"]
    threshold = max(1.0, float(page_diagonal) * 0.035)
    for record in records:
        parsed = parse_dimension_text(record.get("text"))
        if not parsed:
            continue
        nearby = []
        for candidate in candidates:
            distance = bbox_gap(record.get("bbox"), candidate.get("bounding_box"))
            if distance is not None and distance <= threshold:
                nearby.append((distance, candidate))
        nearby.sort(key=lambda item: item[0])
        ambiguous = len(nearby) > 1 and abs(nearby[0][0] - nearby[1][0]) <= threshold * 0.15
        selected = nearby[0][1] if nearby and not ambiguous else None
        warning = []
        if parsed["unit"] is None:
            warning.append("Dimension text has no explicit unit; no unit was inferred.")
        if ambiguous:
            warning.append("Dimension text is spatially close to multiple dimension-line candidates.")
        elif not selected:
            warning.append("No dimension-line candidate was close enough to associate this text.")
        if record.get("source") == "ocr" and record.get("confidence_percent") is None:
            warning.append("OCR confidence was unavailable.")
        output.append({"raw_text": parsed["raw_text"], "value": parsed["value"],
                       "unit": parsed["unit"], "explicit_unit": parsed["explicit_unit"],
                       "components": parsed["components"],
                       "measurement_status": parsed["measurement_status"],
                       "text_id": record.get("text_id"), "text_source": record.get("source"),
                       "ocr_confidence_percent": record.get("confidence_percent"),
                       "source_geometry_references": ([selected["candidate_id"]] if selected else []),
                       "association_status": "ambiguous" if ambiguous else ("associated_by_spatial_proximity" if selected else "unassociated"),
                       "bounding_box": record.get("bbox"),
                       "coordinate_units": record.get("coordinate_units"),
                       "evidence_score": 0.7 if selected and not ambiguous else 0.35,
                       "recognition_status": "candidate" if selected and parsed["explicit_unit"] and not ambiguous else "requires_review",
                       "warnings": warning + ["Dimension text is not a calibrated model measurement."]})
    return output


def _number(text):
    normalized = text.replace(",", "")
    return float(normalized) if "." in normalized else int(normalized)
