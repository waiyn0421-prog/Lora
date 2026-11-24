"""Map existing geometry candidates to cautious architectural recognition records."""

import math
import uuid

from lora_drawing.geometry import (bounding_box, geometry_points, line_length,
                                   line_orientation_degrees, polygon_area)
from .spatial import bbox_center, point_in_polygon


def classify_geometry(file_name, page_number, width, height, coordinate_units,
                      entities, candidates, text_records, levels, dimensions, relationships, paths=None):
    by_id = {entity.get("id"): entity for entity in entities}
    output = []
    diagonal = max(math.hypot(float(width or 1), float(height or 1)), 1.0)
    wall_line_ids = set()
    room_records = []

    for candidate in candidates:
        kind = candidate.get("candidate_type")
        refs = candidate.get("geometry_references", [])
        if kind == "wall_like_parallel_line_group":
            wall_line_ids.update(refs)
            feature = _wall_features(refs, by_id, coordinate_units)
            output.append(_element(file_name, page_number, "Wall", candidate, coordinate_units,
                                   feature.get("centerline") or candidate.get("source_coordinates"),
                                   feature=feature,
                                   warnings=candidate.get("warnings", []) +
                                   ["Wall thickness is a source-coordinate estimate, not a calibrated length."]))
        elif kind == "door_like_opening_candidate":
            output.append(_element(file_name, page_number, "Door", candidate, coordinate_units,
                                   candidate.get("source_coordinates"),
                                   feature={"opening_width_candidate": _measure_candidate(candidate),
                                            "door_leaf_or_swing_evidence": candidate.get("evidence", []),
                                            "curve_geometry_references": [ref for ref in refs
                                                                          if by_id.get(ref, {}).get("kind") == "curve"]},
                                   warnings=candidate.get("warnings", []) +
                                   ["Door identity and opening width require review."]))
        elif kind == "window_like_opening_candidate":
            output.append(_element(file_name, page_number, "Window", candidate, coordinate_units,
                                   candidate.get("source_coordinates"),
                                   feature={"opening_width_candidate": _measure_candidate(candidate)},
                                   warnings=candidate.get("warnings", []) +
                                   ["Window identity and opening width require review."]))
        elif kind == "opening_candidate":
            output.append(_element(file_name, page_number, "Door or window opening", candidate,
                                   coordinate_units, candidate.get("source_coordinates"),
                                   feature={"opening_width_candidate": _measure_candidate(candidate),
                                            "possible_types": ["Door", "Window"]},
                                   status="requires_review",
                                   warnings=candidate.get("warnings", []) +
                                   ["Available geometry does not distinguish a door from a window."]))
        elif kind == "room_boundary_candidate":
            entity = by_id.get(refs[0]) if refs else None
            points = geometry_points(entity or {})
            labels = _labels_in_boundary(points, candidate.get("bounding_box"), text_records)
            area = polygon_area(points) if points else None
            room = _element(file_name, page_number, "Room boundary", candidate, coordinate_units,
                            points or candidate.get("source_coordinates"),
                            feature={"closed_boundary_candidate": bool(entity and entity.get("closed")),
                                     "label_candidates": labels,
                                     "area_source_units_squared": area,
                                     "real_world_area": None,
                                     "area_status": "unavailable_without_coordinate_calibration"},
                            warnings=candidate.get("warnings", []) +
                            ["No real-world room area is reported without coordinate calibration."])
            output.append(room)
            room_records.append(room)
        elif kind == "possible_wall_junction_candidate":
            output.append(_element(file_name, page_number, "Wall junction", candidate,
                                   coordinate_units, candidate.get("source_coordinates"),
                                   warnings=candidate.get("warnings", []) +
                                   ["Junction linework may be annotation or unrelated drafting geometry."]))
        elif kind == "dimension_line_candidate":
            output.append(_element(file_name, page_number, "Dimension line", candidate,
                                   coordinate_units, candidate.get("source_coordinates"),
                                   feature={"dimension_text_associations": []},
                                   warnings=candidate.get("warnings", []) +
                                   ["Dimension value and unit require an associated text annotation."]))

    output.extend(_columns(file_name, page_number, width, height, coordinate_units,
                           entities, candidates, diagonal, paths or []))
    output.extend(_single_line_wall_candidates(file_name, page_number, width, height,
                                               coordinate_units, entities, relationships,
                                               wall_line_ids, diagonal))

    # Add text-derived dimension and level records with their evidence source retained.
    for dimension in dimensions:
        refs = dimension.get("source_geometry_references", [])
        associated_candidate = next((candidate for candidate in candidates
                                     if candidate.get("candidate_id") in refs), None)
        element = _element(file_name, page_number, "Dimension", associated_candidate or {},
                           coordinate_units, dimension.get("bounding_box"),
                           geometry_refs=(associated_candidate or {}).get("geometry_references", []),
                           feature=dimension, status=dimension.get("recognition_status", "requires_review"),
                           method="text_and_spatial_association",
                           score=dimension.get("evidence_score", 0.35),
                           warnings=dimension.get("warnings", []))
        element["text_id"] = dimension.get("text_id")
        output.append(element)
    for level in levels:
        element = _element(file_name, page_number, "Level annotation", {}, coordinate_units,
                           level.get("bbox"), geometry_refs=[level.get("text_id")] if level.get("text_id") else [],
                           feature=level, status=level.get("recognition_status", "requires_review"),
                           method="text_pattern", score=level.get("evidence_score", 0.35),
                           warnings=level.get("warnings", []))
        element["text_id"] = level.get("text_id")
        output.append(element)

    spatial = {"relationships": relationships.get("relationships", []),
               "counts": relationships.get("counts", {}),
               "complete": relationships.get("complete", False),
               "groups": relationships.get("geometry_groups", [])}
    return {"elements": output, "element_count": len(output),
            "spatial_relationships": spatial, "room_count": len(room_records)}


def _element(file_name, page_number, category, candidate, coordinate_units, coordinates,
             feature=None, status=None, method=None, score=None, geometry_refs=None, warnings=None):
    candidate = candidate or {}
    points = coordinates if isinstance(coordinates, list) else []
    if points and points and isinstance(points[0], (int, float)):
        if len(points) == 4:
            bbox = list(points)
        else:
            bbox = [points[0], points[1], points[0], points[1]] if len(points) == 2 else None
    else:
        bbox = bounding_box(points) if points and isinstance(points[0], list) else candidate.get("bounding_box")
    score = candidate.get("evidence_score", 0.35) if score is None else score
    if status is None:
        status = candidate.get("recognition_status", "requires_review")
    return {"recognized_element_id": "rec-" + uuid.uuid4().hex,
            "proposed_category": category,
            "source_file": file_name,
            "page_number": page_number,
            "source_geometry_references": list(geometry_refs if geometry_refs is not None else
                                                 candidate.get("geometry_references", [])),
            "bounding_box": bbox or candidate.get("bounding_box"),
            "position": bbox_center(bbox or candidate.get("bounding_box")),
            "orientation_degrees": _orientation(candidate, coordinates),
            "dimensions": feature or {},
            "coordinate_units": coordinate_units,
            "recognition_method": method or candidate.get("detection_method", "deterministic_geometry_heuristics"),
            "evidence_score": float(score),
            "evidence": candidate.get("evidence", []),
            "warnings": list(warnings or []),
            "review_status": status}


def _wall_features(refs, by_id, units):
    lines = [by_id[ref] for ref in refs if ref in by_id and by_id[ref].get("kind") == "line"]
    if len(lines) < 2:
        return {"centerline": None, "wall_thickness_candidate": None}
    first, second = lines[:2]
    a, b, c, d = first["start"], first["end"], second["start"], second["end"]
    direct = line_length(a, c) + line_length(b, d)
    reverse = line_length(a, d) + line_length(b, c)
    if reverse < direct:
        c, d = d, c
    mid_a = [(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0]
    mid_b = [(c[0] + d[0]) / 2.0, (c[1] + d[1]) / 2.0]
    normal_length = line_length(mid_a, mid_b)
    centerline = [[(a[0] + c[0]) / 2.0, (a[1] + c[1]) / 2.0],
                  [(b[0] + d[0]) / 2.0, (b[1] + d[1]) / 2.0]]
    return {"centerline": centerline,
            "wall_thickness_candidate": {"value": normal_length, "unit": units,
                                         "status": "source_coordinate_distance_not_calibrated"},
            "wall_endpoints": [centerline[0], centerline[1]],
            "source_line_lengths": [line_length(a, b), line_length(c, d)]}


def _measure_candidate(candidate):
    points = candidate.get("source_coordinates", [])
    if len(points) >= 2 and isinstance(points[0], list):
        value = line_length(points[0], points[1])
        return {"value": value, "unit": candidate.get("coordinate_units"),
                "status": "opening_span_in_source_coordinates_not_calibrated"}
    return {"value": None, "unit": None, "status": "unavailable"}


def _columns(file_name, page_number, width, height, units, entities, candidates, diagonal, paths):
    shapes = []
    for entity in entities:
        kind = entity.get("kind")
        if kind not in ("rectangle", "polyline") or not entity.get("closed"):
            continue
        bbox = entity.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        box_width, box_height = abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1])
        size_ratio = min(box_width, box_height) / max(box_width, box_height, 1e-9)
        if 0.72 <= size_ratio <= 1.0 and diagonal * 0.003 <= max(box_width, box_height) <= diagonal * 0.08:
            shapes.append((entity, "rectangular column symbol"))
    # Closed PDF paths composed of cubic curves can represent circles; the symbol remains uncertain.
    for path in _closed_curve_paths(paths, entities):
        bbox = path["bbox"]
        box_width, box_height = abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1])
        ratio = min(box_width, box_height) / max(box_width, box_height, 1e-9)
        if 0.8 <= ratio <= 1.0 and diagonal * 0.003 <= max(box_width, box_height) <= diagonal * 0.08:
            shapes.append((path, "circular column symbol"))
    output = []
    for entity, symbol_type in shapes:
        bbox = entity.get("bbox")
        refs = entity.get("geometry_ids") or [entity.get("id")]
        same_size = [other for other, _ in shapes if other.get("id") != entity.get("id") and
                     _similar_bbox_size(bbox, other.get("bbox"))]
        score = 0.48 if same_size else 0.38
        candidate = {"candidate_id": "column-symbol-" + uuid.uuid4().hex,
                     "bounding_box": bbox, "geometry_references": refs,
                     "evidence_score": score,
                     "evidence": [symbol_type] + (["repeated similar symbol size"] if same_size else []),
                     "recognition_status": "requires_review",
                     "detection_method": "closed_shape_aspect_ratio_and_page_relative_size",
                     "source_coordinates": _box_points(bbox),
                     "warnings": ["Column-like symbols can be furniture, tags or other drafting symbols."]}
        output.append(_element(file_name, page_number, "Column", candidate, units,
                               _box_points(bbox), feature={"symbol_shape_candidate": symbol_type,
                                                           "repetition_count": len(same_size) + 1},
                               status="requires_review"))
    return output


def _closed_curve_paths(paths, entities):
    # Restrict circular-symbol candidates to curves in explicitly closed source paths.
    curves = [entity for entity in entities if entity.get("kind") == "curve" and entity.get("path_id")]
    by_path = {}
    for curve in curves:
        by_path.setdefault(curve["path_id"], []).append(curve)
    closed_ids = {path.get("id") for path in paths if path.get("closed")}
    output = []
    for path_id, values in by_path.items():
        if path_id not in closed_ids or len(values) < 3:
            continue
        points = [point for entity in values for point in entity.get("points", [])]
        bbox = bounding_box(points)
        if bbox:
            output.append({"id": path_id, "geometry_ids": [entity["id"] for entity in values],
                           "bbox": bbox})
    return output


def _labels_in_boundary(polygon, bbox, text_records):
    labels = []
    for record in text_records:
        center = bbox_center(record.get("bbox"))
        inside = point_in_polygon(center, polygon) if center and len(polygon) >= 3 else _inside_bbox(center, bbox)
        if inside:
            labels.append({"text_id": record["text_id"], "text": record["text"],
                           "text_source": record["source"],
                           "ocr_confidence_percent": record.get("confidence_percent"),
                           "association": "text bbox center is inside boundary candidate"})
    return labels


def _inside_bbox(point, bbox):
    return bool(point and bbox and bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3])


def _single_line_wall_candidates(file_name, page_number, width, height, units, entities,
                                 relationships, already_used, diagonal):
    output = []
    intersections = relationships.get("relationships", [])
    connected = {}
    for relation in intersections:
        if relation.get("type") not in ("intersection", "endpoint_proximity"):
            continue
        for entity_id in relation.get("geometry_ids", []):
            connected[entity_id] = connected.get(entity_id, 0) + 1
    for entity in entities:
        if entity.get("kind") != "line" or entity["id"] in already_used:
            continue
        length = line_length(entity["start"], entity["end"])
        if length < diagonal * 0.16 or connected.get(entity["id"], 0) < 2:
            continue
        candidate = {"candidate_id": "single-wall-" + uuid.uuid4().hex,
                     "bounding_box": entity.get("bbox"), "geometry_references": [entity["id"]],
                     "evidence_score": 0.28, "evidence": ["long single line", "multiple linework connections"],
                     "recognition_status": "requires_review",
                     "detection_method": "single_line_length_and_connection_heuristic",
                     "source_coordinates": [entity["start"], entity["end"]],
                     "warnings": ["Single lines may be dimensions, grid lines or other annotations."]}
        output.append(_element(file_name, page_number, "Wall", candidate, units,
                               candidate["source_coordinates"],
                               feature={"wall_representation": "single_line_centreline_candidate",
                                        "wall_thickness_candidate": None},
                               status="requires_review"))
    return output


def _similar_bbox_size(first, second):
    if not first or not second:
        return False
    a = sorted((abs(first[2] - first[0]), abs(first[3] - first[1])))
    b = sorted((abs(second[2] - second[0]), abs(second[3] - second[1])))
    return all(max(x, y) <= max(min(x, y) * 1.25, 1.0) for x, y in zip(a, b))


def _box_points(bbox):
    return [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]]]


def _orientation(candidate, coordinates):
    refs = candidate.get("source_coordinates") or coordinates
    if len(refs) >= 2 and isinstance(refs[0], list) and isinstance(refs[1], list):
        return line_orientation_degrees(refs[0], refs[1])
    return None
