"""Evidence-based scale calibration and source-preserving reconstruction."""

import math
import statistics

from lora_drawing.coordinate_system import apply_affine

from .errors import CalibrationError
from .store import CalibrationStore


_MM_PER_UNIT = {"mm": 1.0, "millimeter": 1.0, "millimeters": 1.0,
                "cm": 10.0, "centimeter": 10.0, "centimeters": 10.0,
                "m": 1000.0, "meter": 1000.0, "meters": 1000.0,
                "in": 25.4, "inch": 25.4, "inches": 25.4,
                "ft": 304.8, "foot": 304.8, "feet": 304.8}
_TARGET_UNITS_PER_MM = {"mm": 1.0, "cm": 0.1, "m": 0.001,
                        "in": 1.0 / 25.4, "ft": 1.0 / 304.8}


class CalibrationEngine:
    def __init__(self, store=None):
        self.store = store or CalibrationStore()

    def calibrate(self, drawing, recognition, page_number, explicit_scale_mm_per_source_unit=None,
                  calibration_references=None, independent_xy=False, origin=None,
                  rotation_degrees=0.0, target_unit="mm", consistency_tolerance=0.025,
                  alignment_references=None):
        page, rec_page = _page_pair(drawing, recognition, page_number)
        units = _source_units(page, drawing)
        target = str(target_unit).lower()
        if target not in _TARGET_UNITS_PER_MM:
            raise CalibrationError("invalid_target_unit", "target_unit must be mm, cm, m, in, or ft")
        if isinstance(independent_xy, bool) is False:
            raise CalibrationError("invalid_input", "independent_xy must be a boolean")
        rotation = _finite(rotation_degrees, "rotation_degrees")
        tolerance = _finite(consistency_tolerance, "consistency_tolerance")
        if tolerance < 0 or tolerance > 0.25:
            raise CalibrationError("invalid_input", "consistency_tolerance must be between 0 and 0.25")
        source_origin = [0.0, 0.0] if origin is None else _point(origin, "origin")

        references, rejected = _reference_scales(calibration_references or [])
        x_refs = [item for item in references if item["axis"] == "x"]
        y_refs = [item for item in references if item["axis"] == "y"]
        all_verified = [item for item in references]
        warnings = []
        method = None
        if explicit_scale_mm_per_source_unit is not None:
            scale_mm = _positive(explicit_scale_mm_per_source_unit, "explicit_scale_mm_per_source_unit")
            sx_mm = sy_mm = scale_mm
            method = "explicit_user_scale"
            if all_verified:
                for ref in all_verified:
                    implied = ref["scale_mm_per_source_unit"]
                    if _relative_error(implied, scale_mm) > tolerance:
                        warnings.append("A verified dimension reference conflicts with the explicit user scale.")
                        break
        elif independent_xy:
            if not x_refs or not y_refs:
                raise CalibrationError("insufficient_axis_references",
                                      "Independent X/Y calibration requires at least one verified reference for each axis")
            sx_mm = statistics.median(item["scale_mm_per_source_unit"] for item in x_refs)
            sy_mm = statistics.median(item["scale_mm_per_source_unit"] for item in y_refs)
            scale_mm = None
            method = "independent_verified_x_y_references"
        elif all_verified:
            scale_mm = statistics.median(item["scale_mm_per_source_unit"] for item in all_verified)
            sx_mm = sy_mm = scale_mm
            method = "median_verified_dimension_references"
        else:
            sx_mm = sy_mm = scale_mm = None
            method = "uncalibrated"

        if rejected:
            warnings.append("Unconfirmed, OCR-only, or inferred references were not used to compute scale.")
        consistency = _consistency(references, sx_mm, sy_mm, tolerance)
        if not consistency["consistent"]:
            warnings.append("Verified dimension references are inconsistent beyond the configured tolerance.")
        if not references and explicit_scale_mm_per_source_unit is None:
            warnings.append("No explicit scale or verified dimension references were provided; coordinates remain uncalibrated.")

        target_factor = _TARGET_UNITS_PER_MM[target]
        sx = sx_mm * target_factor if sx_mm is not None else None
        sy = sy_mm * target_factor if sy_mm is not None else None
        matrix = _matrix(sx, sy, source_origin, rotation) if sx is not None and sy is not None else None
        alignment = {"status": "not_aligned", "scope": "page_local",
                     "reference_count": 0, "max_residual": None,
                     "warning": "Cross-page alignment requires at least two user-confirmed shared point correspondences."}
        if matrix and alignment_references:
            matrix, alignment = _align_matrix(matrix, alignment_references, target)
            if alignment["status"] != "aligned":
                warnings.append("Shared page-alignment references are insufficient or inconsistent; page remains page-local.")
        complete = (matrix is not None and consistency["consistent"] and
                    (not alignment_references or alignment["status"] == "aligned"))
        if not complete and matrix is not None:
            warnings.append("A transform is available, but conflicting references make calibration incomplete.")
        if drawing.get("file_type") == "pdf" and len(drawing.get("pages", [])) > 1 and alignment["status"] != "aligned":
            warnings.append("Calibration is page-local. Other pages are not aligned without shared verified references.")
        transformation_history = list(rec_page.get("transformation_history") or page.get("transformation_history") or [])
        if matrix:
            transformation_history.append({"operation": "source_to_calibrated_local_coordinates",
                "matrix_3x3": matrix, "source_origin": source_origin,
                "rotation_degrees_counterclockwise": rotation, "target_unit": target,
                "axis_orientation": "x right, y up"})

        reconstruction = _reconstruct(page, rec_page, matrix, target) if matrix else {
            "elements": [], "spatial_relationships": [], "grid_intersection_candidates": []}
        result = {"source_file": drawing["file_name"], "processing_id": drawing.get("processing_id"),
                  "recognition_id": recognition.get("recognition_id"), "page_number": page_number,
                  "calibration_method": method, "calibration_complete": complete,
                  "source_units": units, "target_units": target,
                  "scale_factors_target_units_per_source_unit": {"x": sx, "y": sy,
                      "uniform": sx if sx == sy else None},
                  "scale_factors_mm_per_source_unit": {"x": sx_mm, "y": sy_mm,
                      "uniform": scale_mm},
                  "origin_source_coordinates": source_origin,
                  "rotation_degrees_counterclockwise": rotation,
                  "axis_orientation": {"source_x": "right", "source_y": "down",
                                       "target_x": "right", "target_y": "up"},
                  "transformation_matrix_3x3": matrix,
                  "transformation_history": transformation_history,
                  "calibration_references": [item["record"] for item in references] + rejected,
                  "reference_consistency": consistency,
                  "page_alignment": alignment,
                  "spatial_geometry": reconstruction["elements"],
                  "spatial_geometry_count": len(reconstruction["elements"]),
                  "spatial_relationships": reconstruction["spatial_relationships"],
                  "grid_intersection_candidates": reconstruction["grid_intersection_candidates"],
                  "confidence": _confidence(method, complete, consistency, references),
                  "warnings": list(dict.fromkeys(warnings)),
                  "calibration_status": "calibrated" if complete else ("conflicted" if matrix else "uncalibrated")}
        calibration_id = self.store.put(result)
        return result

    def validate_references(self, drawing, recognition, page_number, calibration_references):
        page, rec_page = _page_pair(drawing, recognition, page_number)
        entities = page.get("geometry_extraction", {}).get("entities", [])
        dimension_text = {item.get("text_id"): item for item in rec_page.get("elements", [])
                          if item.get("proposed_category") == "Dimension" and item.get("text_id")}
        output = []
        for index, raw in enumerate(calibration_references or []):
            item = dict(raw) if isinstance(raw, dict) else {}
            status, messages = "valid", []
            try:
                normalized = _normalize_reference(item)
                if not _source_segment_exists(normalized["start"], normalized["end"], entities):
                    messages.append("Reference endpoints do not match an extracted source geometry segment.")
                    status = "unmatched_geometry"
                text_id = normalized["record"].get("dimension_text_id")
                if text_id:
                    text_element = dimension_text.get(text_id)
                    if text_element is None:
                        messages.append("dimension_text_id does not identify a recognized dimension text on this page.")
                        status = "unmatched_text"
                    elif text_element.get("dimensions", {}).get("text_source") != normalized["evidence_source"] and normalized["evidence_source"] != "user_confirmed":
                        messages.append("Declared evidence source differs from the recognition text source.")
                        if status == "valid":
                            status = "evidence_source_mismatch"
                    elif text_element.get("dimensions", {}).get("association_status") == "ambiguous":
                        messages.append("Recognized dimension text has an ambiguous geometry association.")
                        status = ("valid_with_user_confirmed_association" if normalized["user_confirmed"]
                                  else "ambiguous_dimension_association")
                if not normalized["user_confirmed"]:
                    messages.append("Reference is not user-confirmed and cannot determine scale.")
                    if status == "valid":
                        status = "unconfirmed"
                if normalized["evidence_source"] == "ocr" and not normalized["user_confirmed"]:
                    messages.append("OCR-derived value requires explicit user confirmation before scale use.")
                    if status in ("valid", "unconfirmed"):
                        status = "unconfirmed_ocr"
                output.append({"index": index, "reference_id": normalized["record"]["reference_id"],
                               "status": status, "messages": messages,
                               "source_length": normalized["source_length"],
                               "unit": normalized["record"]["unit"], "axis": normalized["record"]["axis"]})
            except CalibrationError as exc:
                output.append({"index": index, "status": "invalid", "messages": [str(exc)],
                               "error_code": exc.code})
        return {"page_number": page_number, "validated_count": len(output),
                "accepted_count": sum(item["status"] in ("valid", "valid_with_user_confirmed_association") for item in output),
                "references": output,
                "warning": "Validation checks input and geometry correspondence; it does not itself verify a dimension's real-world truth."}


def _page_pair(drawing, recognition, page_number):
    if not isinstance(drawing, dict) or not isinstance(recognition, dict):
        raise CalibrationError("invalid_input", "Processed drawing and recognition results are required")
    if drawing.get("processing_id") != recognition.get("processing_id"):
        raise CalibrationError("document_mismatch", "Drawing and recognition results must share a processing_id")
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        raise CalibrationError("invalid_page_number", "page_number must be a positive integer")
    page = next((item for item in drawing.get("pages", []) if item.get("page_number") == page_number), None)
    if drawing.get("file_type") != "pdf" and page_number == 1:
        image = drawing.get("image", {})
        page = {"page_number": 1, "coordinate_system": image.get("coordinate_system"),
                "geometry_extraction": {"entities": drawing.get("geometry_entities", [])},
                "transformation_history": image.get("transformation_history", [])}
    rec_page = next((item for item in recognition.get("pages", []) if item.get("page_number") == page_number), None)
    if page is None or rec_page is None:
        raise CalibrationError("page_not_found", "The requested page must exist in both source and recognition results")
    return page, rec_page


def _source_units(page, drawing):
    coords = page.get("coordinate_system")
    if isinstance(coords, dict):
        return coords.get("units") or coords.get("source_space") or "unknown source units"
    return "pixels" if drawing.get("file_type") != "pdf" else "PDF points"


def _normalize_reference(item):
    start, end = _point(item.get("source_start"), "source_start"), _point(item.get("source_end"), "source_end")
    source_length = math.dist(start, end)
    if source_length <= 1e-9:
        raise CalibrationError("invalid_reference", "Reference endpoints must be distinct")
    value = _positive(item.get("value"), "value")
    unit = str(item.get("unit", "")).lower()
    if unit not in _MM_PER_UNIT:
        raise CalibrationError("invalid_reference_unit", "Reference unit must be mm, cm, m, in, or ft")
    axis = str(item.get("axis", "")).lower()
    if axis not in ("x", "y", "both"):
        raise CalibrationError("invalid_reference_axis", "Reference axis must be x, y, or both")
    confirmed = item.get("user_confirmed", False)
    if not isinstance(confirmed, bool):
        raise CalibrationError("invalid_reference", "user_confirmed must be a boolean")
    source = str(item.get("evidence_source", "inferred")).lower()
    if source not in ("embedded_pdf", "ocr", "user_confirmed", "inferred"):
        raise CalibrationError("invalid_reference", "evidence_source is unsupported")
    record = {"reference_id": str(item.get("reference_id") or "reference-{}".format(item.get("dimension_text_id", "unnamed"))),
              "dimension_text_id": item.get("dimension_text_id"), "source_start": start,
              "source_end": end, "source_length": source_length, "value": value,
              "unit": unit, "axis": axis, "evidence_source": source,
              "user_confirmed": confirmed, "used_for_scale": confirmed,
              "verification_status": "user_confirmed" if confirmed else "unverified"}
    scale = value * _MM_PER_UNIT[unit] / source_length
    return {"record": record, "source_length": source_length,
            "scale_mm_per_source_unit": scale, "axis": axis,
            "user_confirmed": confirmed, "evidence_source": source,
            "start": start, "end": end}


def _reference_scales(references):
    if not isinstance(references, list) or len(references) > 100:
        raise CalibrationError("invalid_references", "calibration_references must be a list of at most 100 items")
    accepted, rejected = [], []
    for index, item in enumerate(references):
        try:
            normalized = _normalize_reference(item)
            if normalized["user_confirmed"]:
                accepted.append(normalized)
            else:
                record = normalized["record"]
                record["used_for_scale"] = False
                rejected.append(record)
        except CalibrationError as exc:
            raise CalibrationError(exc.code, "Reference {}: {}".format(index, str(exc)))
    return accepted, rejected


def _consistency(references, sx, sy, tolerance):
    if not references:
        return {"consistent": True, "reference_count": 0, "max_relative_deviation": None,
                "tolerance": tolerance, "deviations": []}
    estimates = []
    for item in references:
        target = sx if item["axis"] == "x" else sy if item["axis"] == "y" else (sx + sy) / 2.0
        deviation = _relative_error(item["scale_mm_per_source_unit"], target) if target else None
        estimates.append({"reference_id": item["record"]["reference_id"],
                          "axis": item["axis"],
                          "scale_mm_per_source_unit": item["scale_mm_per_source_unit"],
                          "relative_deviation": deviation})
    max_dev = max((item["relative_deviation"] for item in estimates if item["relative_deviation"] is not None), default=0.0)
    return {"consistent": max_dev <= tolerance, "reference_count": len(estimates),
            "max_relative_deviation": max_dev, "tolerance": tolerance, "deviations": estimates}


def _matrix(sx, sy, origin, rotation_degrees):
    angle = math.radians(rotation_degrees)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    # Source images generally have y down; flip to a local y-up architectural frame.
    a, b = cos_a * sx, sin_a * sy
    c, d = sin_a * sx, -cos_a * sy
    tx = -(a * origin[0] + b * origin[1])
    ty = -(c * origin[0] + d * origin[1])
    return [[a, b, tx], [c, d, ty], [0.0, 0.0, 1.0]]


def _align_matrix(matrix, references, target_unit):
    if not isinstance(references, list) or len(references) > 100:
        raise CalibrationError("invalid_alignment_references", "alignment_references must be a list of at most 100 items")
    pairs = []
    for index, item in enumerate(references):
        if not isinstance(item, dict) or item.get("user_confirmed") is not True:
            continue
        source = _point(item.get("source_point"), "alignment source_point")
        common = _point(item.get("common_point"), "alignment common_point")
        pairs.append((apply_affine(source, matrix), common))
    if len(pairs) < 2:
        return matrix, {"status": "insufficient_shared_references", "scope": "page_local",
                        "reference_count": len(pairs), "max_residual": None,
                        "required_reference_count": 2}
    source_center = [sum(pair[0][axis] for pair in pairs) / len(pairs) for axis in (0, 1)]
    target_center = [sum(pair[1][axis] for pair in pairs) / len(pairs) for axis in (0, 1)]
    dot = cross = 0.0
    for source, target in pairs:
        sx, sy = source[0] - source_center[0], source[1] - source_center[1]
        tx, ty = target[0] - target_center[0], target[1] - target_center[1]
        dot += sx * tx + sy * ty
        cross += sx * ty - sy * tx
    if abs(dot) + abs(cross) < 1e-12:
        return matrix, {"status": "degenerate_shared_references", "scope": "page_local",
                        "reference_count": len(pairs), "max_residual": None}
    angle = math.atan2(cross, dot)
    ca, sa = math.cos(angle), math.sin(angle)
    tx = target_center[0] - ca * source_center[0] + sa * source_center[1]
    ty = target_center[1] - sa * source_center[0] - ca * source_center[1]
    aligned = [[ca * matrix[0][0] - sa * matrix[1][0], ca * matrix[0][1] - sa * matrix[1][1],
                ca * matrix[0][2] - sa * matrix[1][2] + tx],
               [sa * matrix[0][0] + ca * matrix[1][0], sa * matrix[0][1] + ca * matrix[1][1],
                sa * matrix[0][2] + ca * matrix[1][2] + ty],
               [0.0, 0.0, 1.0]]
    residuals = [math.dist([ca * source[0] - sa * source[1] + tx,
                            sa * source[0] + ca * source[1] + ty], target)
                 for source, target in pairs]
    max_residual = max(residuals)
    tolerance = 1.0 if target_unit == "mm" else 0.01
    status = "aligned" if max_residual <= tolerance else "inconsistent_shared_references"
    return (aligned if status == "aligned" else matrix), {
        "status": status, "scope": "shared_reference_coordinate_system" if status == "aligned" else "page_local",
        "reference_count": len(pairs), "rotation_degrees": math.degrees(angle),
        "translation": [tx, ty], "max_residual": max_residual,
        "residual_tolerance": tolerance, "target_units": target_unit,
        "warning": "Alignment references are user-confirmed point correspondences; pages without these remain independent."}


def _reconstruct(page, rec_page, matrix, target_unit):
    source_entities = page.get("geometry_extraction", {}).get("entities", [])
    by_id = {item.get("id"): item for item in source_entities}
    output = []
    for element in rec_page.get("elements", []):
        refs = element.get("source_geometry_references", [])
        geometry = [_transform_entity(by_id[ref], matrix, target_unit) for ref in refs if ref in by_id]
        source_bbox = element.get("bounding_box")
        bbox = _transform_bbox(source_bbox, matrix) if source_bbox else None
        position = _transform_point(element.get("position"), matrix) if element.get("position") else None
        features = element.get("dimensions", {})
        transformed_features = {}
        for key in ("centerline", "wall_endpoints", "source_coordinates"):
            value = features.get(key)
            if isinstance(value, list) and value and isinstance(value[0], list):
                transformed_features[key] = [_transform_point(point, matrix) for point in value]
        calibrated_dimensions = _calibrated_dimensions(element, geometry, matrix, target_unit)
        output.append({"recognized_element_id": element["recognized_element_id"],
                       "proposed_category": element["proposed_category"],
                       "source_geometry_references": refs,
                       "source_bounding_box": source_bbox, "bounding_box": bbox,
                       "source_position": element.get("position"), "position": position,
                       "orientation_degrees": _orientation(geometry), "geometry": geometry,
                       "source_dimensions": features,
                       "dimensions": {**transformed_features, **calibrated_dimensions},
                       "coordinate_units": target_unit, "review_status": element["review_status"],
                       "warnings": element.get("warnings", [])})
    relationships = []
    intersection_candidates = []
    relations = rec_page.get("spatial_relationships", {}).get("relationships", [])
    for relation in relations:
        item = dict(relation)
        if relation.get("point"):
            item["source_point"] = relation["point"]
            item["point"] = apply_affine(relation["point"], matrix)
        item["coordinate_units"] = target_unit
        relationships.append(item)
        if relation.get("type") == "intersection":
            intersection_candidates.append({"geometry_references": relation.get("geometry_ids", []),
                "source_position": relation.get("point"),
                "position": item.get("point"), "coordinate_units": target_unit,
                "classification": "linework_intersection_candidate",
                "review_status": "requires_review",
                "warning": "Grid-line identity was not established from available geometry."})
    return {"elements": output, "spatial_relationships": relationships,
            "grid_intersection_candidates": intersection_candidates}


def _calibrated_dimensions(element, geometry, matrix, target_unit):
    source = element.get("dimensions", {})
    result = {}
    if element.get("proposed_category") == "Wall":
        lines = [item for item in geometry if item.get("kind") == "line" and item.get("start") and item.get("end")]
        if len(lines) >= 2:
            first, second = lines[:2]
            a, b = first["start"], first["end"]
            c, d = second["start"], second["end"]
            normal = _distance_between_parallel_lines(a, b, c, d)
            if normal is not None:
                result["wall_thickness"] = {"value": normal, "unit": target_unit,
                    "status": "calibrated_from_transformed_source_lines"}
    if element.get("proposed_category") == "Room boundary":
        polygon = next((item.get("points") for item in geometry
                        if item.get("kind") in ("polyline", "contour") and item.get("points")), None)
        if polygon:
            area = _polygon_area(polygon)
            result["area"] = {"value": area, "unit": target_unit + "^2",
                              "status": "calibrated_from_transformed_boundary"}
    if source.get("opening_width_candidate"):
        raw = source["opening_width_candidate"]
        x_scale = math.hypot(matrix[0][0], matrix[1][0])
        y_scale = math.hypot(matrix[0][1], matrix[1][1])
        angle = element.get("orientation_degrees")
        if abs(x_scale - y_scale) <= 1e-9:
            factor = x_scale
        elif angle is not None:
            radians = math.radians(angle)
            factor = math.hypot(matrix[0][0] * math.cos(radians) + matrix[0][1] * math.sin(radians),
                                matrix[1][0] * math.cos(radians) + matrix[1][1] * math.sin(radians))
        else:
            factor = None
        result["opening_width_candidate"] = {"source_value": raw.get("value"),
            "source_unit": raw.get("unit"),
            "value": raw.get("value") * factor if factor is not None and isinstance(raw.get("value"), (int, float)) else None,
            "unit": target_unit if factor is not None else None,
            "status": "scaled_from_source_candidate" if factor is not None else "requires_geometry_direction_for_anisotropic_scale"}
    return result


def _distance_between_parallel_lines(a, b, c, d):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None
    return abs(dx * (a[1] - c[1]) - (a[0] - c[0]) * dy) / length


def _polygon_area(points):
    if len(points) < 3:
        return 0.0
    return abs(sum(points[index][0] * points[(index + 1) % len(points)][1] -
                   points[(index + 1) % len(points)][0] * points[index][1]
                   for index in range(len(points))) / 2.0)


def _transform_entity(entity, matrix, target_unit):
    output = {"source_entity_id": entity.get("id"), "kind": entity.get("kind"),
              "coordinate_units": target_unit}
    for key in ("start", "end"):
        if entity.get(key):
            output[key] = _transform_point(entity[key], matrix)
    for key in ("points", "source_points"):
        if entity.get(key):
            output["points"] = [_transform_point(point, matrix) for point in entity[key]]
    bbox = entity.get("bbox")
    if bbox:
        output["bounding_box"] = _transform_bbox(bbox, matrix)
    return output


def _transform_bbox(bbox, matrix):
    x0, y0, x1, y1 = map(float, bbox)
    corners = [_transform_point(point, matrix) for point in ([x0, y0], [x1, y0], [x1, y1], [x0, y1])]
    return [min(point[0] for point in corners), min(point[1] for point in corners),
            max(point[0] for point in corners), max(point[1] for point in corners)]


def _transform_point(point, matrix):
    return apply_affine(point, matrix)


def _orientation(geometry):
    for entity in geometry:
        if entity.get("start") and entity.get("end"):
            a, b = entity["start"], entity["end"]
            return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0
    return None


def _source_segment_exists(start, end, entities, tolerance=2.0):
    for entity in entities:
        pairs = []
        if entity.get("start") and entity.get("end"):
            pairs.append((entity["start"], entity["end"]))
        points = entity.get("points", [])
        pairs.extend(zip(points, points[1:]))
        for a, b in pairs:
            direct = math.dist(start, a) <= tolerance and math.dist(end, b) <= tolerance
            reverse = math.dist(start, b) <= tolerance and math.dist(end, a) <= tolerance
            if direct or reverse:
                return True
    return False


def _point(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise CalibrationError("invalid_input", "{} must contain two coordinates".format(name))
    return [_finite(value[0], name), _finite(value[1], name)]


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise CalibrationError("invalid_input", "{} must be a finite number".format(name))
    return float(value)


def _positive(value, name):
    number = _finite(value, name)
    if number <= 0:
        raise CalibrationError("invalid_input", "{} must be greater than zero".format(name))
    return number


def _relative_error(value, reference):
    return abs(value - reference) / max(abs(reference), 1e-12)


def _confidence(method, complete, consistency, references):
    if not complete:
        return {"score": 0.0, "label": "unavailable_or_conflicted"}
    if method == "explicit_user_scale":
        return {"score": 0.95, "label": "explicit_user_scale"}
    count = len(references)
    score = min(0.9, 0.65 + 0.05 * max(0, count - 1))
    return {"score": score, "label": "verified_dimension_references",
            "reference_count": count, "max_relative_deviation": consistency["max_relative_deviation"]}
