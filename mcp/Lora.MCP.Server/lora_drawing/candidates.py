"""Conservative geometric candidate heuristics; never confirms BIM semantics."""

import math
import uuid

from .geometry import (are_perpendicular, bounding_box, geometry_points,
                       line_length, line_orientation_degrees, orientation_difference_degrees,
                       polygon_area)


def detect_architectural_candidates(file_name, page_number, width, height, entities,
                                    relationships=None, max_pairs=50_000, max_candidates=2_000):
    diagonal = max(math.hypot(float(width), float(height)), 1.0)
    all_line_entities = [item for item in entities if item.get("kind") == "line"]
    line_entities = all_line_entities[:500]
    candidates = []
    pair_checks = [0]
    truncated = [len(all_line_entities) > len(line_entities)]

    def candidate(kind, ids, points, score, evidence, warnings=None, interpretations=None):
        if len(candidates) >= max_candidates:
            truncated[0] = True
            return
        candidates.append({
            "candidate_id": "cand-" + uuid.uuid4().hex,
            "candidate_type": kind,
            "source_file": file_name,
            "page_number": page_number,
            "source_coordinates": points,
            "coordinate_units": _coordinate_units(entities),
            "bounding_box": bounding_box(points),
            "geometry_references": list(dict.fromkeys(ids)),
            "detection_method": "heuristic_geometry_v1",
            "evidence_score": round(max(0.0, min(1.0, score)), 3),
            "evidence": list(evidence),
            "warnings": list(warnings or []) + ["candidate is not a confirmed architectural element"],
            "recognition_status": "candidate" if score >= 0.5 else "requires_review",
            "potential_interpretations": interpretations or [],
        })

    # Paired parallel runs with meaningful overlap are wall-like line groups only.
    for index, first in enumerate(line_entities):
        for second in line_entities[index + 1:]:
            pair_checks[0] += 1
            if pair_checks[0] > max_pairs:
                truncated[0] = True
                break
            metrics = _parallel_metrics(first, second)
            if not metrics:
                continue
            separation = metrics["separation"]
            if (metrics["overlap_ratio"] >= 0.55 and
                    diagonal * 0.001 <= separation <= diagonal * 0.035 and
                    min(metrics["length_a"], metrics["length_b"]) >= diagonal * 0.035):
                points = [first["start"], first["end"], second["start"], second["end"]]
                score = (0.42 + 0.35 * metrics["overlap_ratio"] +
                         0.2 * (1.0 - min(1.0, metrics["angle_difference"] / 3.0)))
                candidate("wall_like_parallel_line_group", [first["id"], second["id"]], points,
                          score, ["near-parallel segments", "longitudinal overlap", "bounded separation"],
                          ["This line pattern may represent non-wall drafting geometry."])
                connectors = _window_connectors(first, second, line_entities, diagonal)
                if len(connectors) >= 2:
                    candidate("window_like_opening_candidate",
                              [first["id"], second["id"]] + [item["id"] for item in connectors[:4]],
                              points + [point for item in connectors[:4]
                                        for point in (item["start"], item["end"])],
                              0.46, ["paired long parallel runs", "repeated short perpendicular cross-lines"],
                              ["This framing pattern can also be a drafting symbol or closed detail outline."])
        if truncated[0]:
            break

    # Line pairs separated by a short collinear gap are unresolved opening candidates.
    opening_pairs = []
    for index, first in enumerate(line_entities):
        for second in line_entities[index + 1:]:
            pair_checks[0] += 1
            if pair_checks[0] > max_pairs:
                truncated[0] = True
                break
            gap = _collinear_gap(first, second, diagonal)
            if gap:
                opening_pairs.append((first, second, gap))
                labels = ["door_like_opening_candidate", "window_like_opening_candidate"]
                kind, score, evidence = "opening_candidate", 0.34, ["collinear line segments with a bounded gap"]
                curve = _nearby_curve(gap["bbox"], entities, diagonal * 0.015)
                if curve:
                    kind, score = "door_like_opening_candidate", 0.55
                    evidence.append("nearby curve may be a swing or unrelated arc")
                    ids = [first["id"], second["id"], curve["id"]]
                else:
                    ids = [first["id"], second["id"]]
                candidate(kind, ids, gap["points"], score, evidence,
                          ["Door and window intent cannot be distinguished from this geometry alone."],
                          labels if kind == "opening_candidate" else [])
        if truncated[0]:
            break

    # Weak wall-contour candidate for closed paths with nontrivial area.
    for entity in entities:
        if entity.get("kind") not in ("rectangle", "polyline") or not entity.get("closed"):
            continue
        points = geometry_points(entity)
        area = entity.get("area")
        if area is None:
            area = polygon_area(points)
        if area >= diagonal * diagonal * 0.0005:
            candidate("room_boundary_candidate", [entity["id"]], points, 0.35,
                      ["closed polygon-like geometry", "area exceeds page-relative minimum"],
                      ["Closed drafting symbols and detail outlines can produce the same pattern."])

    # Intersections and endpoint closeness suggest junctions, not confirmed wall joins.
    for relation in (relationships or {}).get("relationships", []):
        if relation.get("type") not in ("intersection", "endpoint_proximity"):
            continue
        point = relation.get("point") or relation.get("midpoint")
        if point is None:
            continue
        candidate("possible_wall_junction_candidate", relation["geometry_ids"], [point], 0.4,
                  [relation["type"] + " between linework"],
                  ["Intersecting drafting lines may be unrelated annotations or symbols."])

    # A long line with endpoint ticks and nearby text is a dimension-line candidate.
    for main in line_entities:
        length = line_length(main["start"], main["end"])
        if length < diagonal * 0.08:
            continue
        angle = line_orientation_degrees(main["start"], main["end"])
        ticks_at_start = []
        ticks_at_end = []
        for other in line_entities:
            if other["id"] == main["id"]:
                continue
            other_length = line_length(other["start"], other["end"])
            if other_length > length * 0.18:
                continue
            other_angle = line_orientation_degrees(other["start"], other["end"])
            if not are_perpendicular(angle, other_angle, 8.0):
                continue
            start_distance = min(line_length(main["start"], other["start"]),
                                 line_length(main["start"], other["end"]))
            end_distance = min(line_length(main["end"], other["start"]),
                               line_length(main["end"], other["end"]))
            if min(start_distance, end_distance) <= diagonal * 0.012:
                if start_distance <= diagonal * 0.012:
                    ticks_at_start.append(other)
                if end_distance <= diagonal * 0.012:
                    ticks_at_end.append(other)
        if ticks_at_start and ticks_at_end:
            selected_ticks = list(dict((item["id"], item) for item in ticks_at_start + ticks_at_end).values())
            candidate("dimension_line_candidate", [main["id"]] + [item["id"] for item in selected_ticks[:4]],
                      [main["start"], main["end"]], 0.45,
                      ["long line", "at least two short perpendicular endpoint marks"],
                      ["Ticks and long lines can occur in other drafting symbols."])

    return {"candidates": candidates, "candidate_count": len(candidates),
            "complete": not truncated[0], "truncated": truncated[0],
            "pair_checks": pair_checks[0],
            "warnings": ["Candidates are geometric hypotheses only; no element recognition is performed."] +
                        (["Candidate search reached a configured output or pair-check limit."] if truncated[0] else [])}


def _parallel_metrics(first, second):
    a, b, c, d = first["start"], first["end"], second["start"], second["end"]
    angle_a, angle_b = line_orientation_degrees(a, b), line_orientation_degrees(c, d)
    difference = orientation_difference_degrees(angle_a, angle_b)
    if difference > 3.0:
        return None
    length_a, length_b = line_length(a, b), line_length(c, d)
    if min(length_a, length_b) <= 1e-9:
        return None
    dx, dy = (b[0] - a[0]) / length_a, (b[1] - a[1]) / length_a
    nx, ny = -dy, dx
    separation = abs((c[0] - a[0]) * nx + (c[1] - a[1]) * ny)
    interval_a = sorted((a[0] * dx + a[1] * dy, b[0] * dx + b[1] * dy))
    interval_b = sorted((c[0] * dx + c[1] * dy, d[0] * dx + d[1] * dy))
    overlap = max(0.0, min(interval_a[1], interval_b[1]) - max(interval_a[0], interval_b[0]))
    return {"separation": separation, "overlap_ratio": overlap / min(length_a, length_b),
            "length_a": length_a, "length_b": length_b, "angle_difference": difference}


def _window_connectors(first, second, lines, diagonal):
    metrics = _parallel_metrics(first, second)
    if not metrics:
        return []
    angle = line_orientation_degrees(first["start"], first["end"])
    separation = metrics["separation"]
    connectors = []
    for line in lines:
        if line["id"] in (first["id"], second["id"]):
            continue
        line_angle = line_orientation_degrees(line["start"], line["end"])
        length = line_length(line["start"], line["end"])
        if not are_perpendicular(angle, line_angle, 5.0):
            continue
        if abs(length - separation) > max(separation * 0.35, diagonal * 0.002):
            continue
        if _connector_reaches_pair(line, first, second, diagonal * 0.003):
            connectors.append(line)
    return connectors


def _connector_reaches_pair(connector, first, second, tolerance):
    points = [connector["start"], connector["end"]]
    return (any(_point_segment_distance(point, first["start"], first["end"]) <= tolerance for point in points) and
            any(_point_segment_distance(point, second["start"], second["end"]) <= tolerance for point in points))


def _point_segment_distance(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return line_length(point, start)
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared))
    projection = [start[0] + t * dx, start[1] + t * dy]
    return line_length(point, projection)


def _collinear_gap(first, second, diagonal):
    a, b, c, d = first["start"], first["end"], second["start"], second["end"]
    angle_a = line_orientation_degrees(a, b)
    angle_b = line_orientation_degrees(c, d)
    if orientation_difference_degrees(angle_a, angle_b) > 2.0:
        return None
    length_a, length_b = line_length(a, b), line_length(c, d)
    if min(length_a, length_b) <= 1e-9:
        return None
    dx, dy = (b[0] - a[0]) / length_a, (b[1] - a[1]) / length_a
    nx, ny = -dy, dx
    offset = abs((c[0] - a[0]) * nx + (c[1] - a[1]) * ny)
    if offset > diagonal * 0.002:
        return None
    projections_a = sorted((a[0] * dx + a[1] * dy, b[0] * dx + b[1] * dy))
    projections_b = sorted((c[0] * dx + c[1] * dy, d[0] * dx + d[1] * dy))
    a_low, a_high = (a, b) if a[0] * dx + a[1] * dy <= b[0] * dx + b[1] * dy else (b, a)
    c_low, c_high = (c, d) if c[0] * dx + c[1] * dy <= d[0] * dx + d[1] * dy else (d, c)
    if projections_a[1] < projections_b[0]:
        gap = projections_b[0] - projections_a[1]
        before, after = a_high, c_low
    elif projections_b[1] < projections_a[0]:
        gap = projections_a[0] - projections_b[1]
        before, after = c_high, a_low
    else:
        return None
    if not diagonal * 0.003 <= gap <= diagonal * 0.08:
        return None
    return {"gap": gap, "points": [before, after], "bbox": [min(before[0], after[0]),
                                                                 min(before[1], after[1]),
                                                                 max(before[0], after[0]),
                                                                 max(before[1], after[1])]}


def _nearby_curve(bbox, entities, tolerance):
    for entity in entities:
        if entity.get("kind") != "curve":
            continue
        points = geometry_points(entity)
        if not points:
            continue
        other = [min(point[0] for point in points), min(point[1] for point in points),
                 max(point[0] for point in points), max(point[1] for point in points)]
        if not (other[2] < bbox[0] - tolerance or other[0] > bbox[2] + tolerance or
                other[3] < bbox[1] - tolerance or other[1] > bbox[3] + tolerance):
            return entity
    return None


def _coordinate_units(entities):
    return entities[0].get("coordinate_units", "source coordinate units") if entities else "source coordinate units"
