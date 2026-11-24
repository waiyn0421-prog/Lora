"""Coordinate-unit-neutral geometry primitives and relationship analysis."""

import math


DEFAULT_TOLERANCES = {
    "parallel_angle_degrees": 3.0,
    "perpendicular_angle_degrees": 3.0,
    "intersection_units": 0.25,
    "endpoint_proximity_units": 2.0,
}


def line_length(start, end):
    return math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))


def line_orientation_degrees(start, end):
    angle = math.degrees(math.atan2(float(end[1]) - float(start[1]),
                                   float(end[0]) - float(start[0]))) % 180.0
    return angle


def orientation_difference_degrees(first, second):
    difference = abs(float(first) - float(second)) % 180.0
    return min(difference, 180.0 - difference)


def are_parallel(first, second, angle_tolerance=3.0):
    return orientation_difference_degrees(first, second) <= angle_tolerance


def are_perpendicular(first, second, angle_tolerance=3.0):
    return abs(90.0 - orientation_difference_degrees(first, second)) <= angle_tolerance


def line_intersection(first_start, first_end, second_start, second_end, tolerance=0.0):
    """Return finite-segment intersection point, or None for no intersection."""
    x1, y1 = map(float, first_start)
    x2, y2 = map(float, first_end)
    x3, y3 = map(float, second_start)
    x4, y4 = map(float, second_end)
    dx1, dy1, dx2, dy2 = x2 - x1, y2 - y1, x4 - x3, y4 - y3
    denominator = dx1 * dy2 - dy1 * dx2
    if abs(denominator) <= 1e-12:
        return None
    t = ((x3 - x1) * dy2 - (y3 - y1) * dx2) / denominator
    u = ((x3 - x1) * dy1 - (y3 - y1) * dx1) / denominator
    t_tolerance = tolerance / max(math.hypot(dx1, dy1), 1e-12)
    u_tolerance = tolerance / max(math.hypot(dx2, dy2), 1e-12)
    if -t_tolerance <= t <= 1.0 + t_tolerance and -u_tolerance <= u <= 1.0 + u_tolerance:
        return [x1 + t * dx1, y1 + t * dy1]
    return None


def endpoint_proximity(first_start, first_end, second_start, second_end, tolerance):
    points = ((first_start, second_start), (first_start, second_end),
              (first_end, second_start), (first_end, second_end))
    pairs = [(line_length(a, b), list(a), list(b)) for a, b in points]
    distance, point_a, point_b = min(pairs, key=lambda entry: entry[0])
    if distance <= tolerance:
        return {"distance": distance, "point_a": point_a, "point_b": point_b,
                "midpoint": [(point_a[0] + point_b[0]) / 2.0,
                             (point_a[1] + point_b[1]) / 2.0]}
    return None


def polygon_is_closed(points, tolerance=0.0):
    return len(points) >= 3 and line_length(points[0], points[-1]) <= tolerance


def polygon_area(points):
    if len(points) < 3:
        return 0.0
    vertices = list(points)
    if vertices[0] == vertices[-1]:
        vertices = vertices[:-1]
    return abs(sum(vertices[i][0] * vertices[(i + 1) % len(vertices)][1] -
                   vertices[(i + 1) % len(vertices)][0] * vertices[i][1]
                   for i in range(len(vertices))) / 2.0)


def bounding_box(points):
    if not points:
        return None
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def geometry_points(entity):
    if entity.get("kind") == "line":
        return [entity["start"], entity["end"]]
    return entity.get("points", [])


def geometry_bbox(entity):
    if entity.get("bbox") is not None:
        return list(entity["bbox"])
    return bounding_box(geometry_points(entity))


def line_segments(entity):
    points = geometry_points(entity)
    if entity.get("kind") == "line" and len(points) >= 2:
        return [(points[0], points[1])]
    return [(points[index], points[index + 1]) for index in range(len(points) - 1)]


def group_geometry(entities, proximity_tolerance=2.0, max_pair_checks=100_000, return_status=False):
    """Group line-like entities by endpoint proximity using connected components."""
    line_entities = [entity for entity in entities if entity.get("kind") in ("line", "polyline", "curve")]
    groups = []
    pair_checks = 0
    truncated = False
    processed = 0
    for entity_index, entity in enumerate(line_entities):
        found = set()
        for index, group in enumerate(groups):
            for member in group:
                pair_checks += 1
                if pair_checks > max_pair_checks:
                    truncated = True
                    break
                if _entities_near(entity, member, proximity_tolerance):
                    found.add(index)
                    break
            if truncated:
                break
        if truncated:
            processed = entity_index
            break
        if not found:
            groups.append([entity])
        else:
            first = min(found)
            groups[first].append(entity)
            for other in sorted(found - {first}, reverse=True):
                groups[first].extend(groups[other])
                groups[other] = []
        processed = entity_index + 1
    if truncated:
        groups.extend([[entity] for entity in line_entities[processed:]])
    output = [{"group_id": "group-{:04d}".format(index + 1),
             "geometry_ids": [item["id"] for item in group],
             "bbox": bounding_box([point for item in group for point in geometry_points(item)])}
            for index, group in enumerate(groups) if group]
    if return_status:
        return {"groups": output, "complete": not truncated, "truncated": truncated,
                "pair_checks": min(pair_checks, max_pair_checks)}
    return output


def analyze_relationships(entities, tolerances=None, max_pairs=100_000, max_relationships=10_000):
    tolerances = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    lines = []
    for entity in entities:
        if entity.get("kind") not in ("line", "polyline", "curve", "rectangle"):
            continue
        segments = line_segments(entity)
        for index, (start, end) in enumerate(segments):
            if line_length(start, end) > 1e-9:
                lines.append({"id": entity["id"] if entity.get("kind") == "line" else
                              "{}:seg-{}".format(entity["id"], index + 1),
                              "source_entity_id": entity["id"], "start": start, "end": end})
    relationships, counts = [], {"parallel": 0, "perpendicular": 0, "intersections": 0,
                                 "endpoint_proximity": 0}
    pair_checks = 0
    truncated = False
    for first_index in range(len(lines)):
        first = lines[first_index]
        first_source_id = first["source_entity_id"]
        a, b = first["start"], first["end"]
        first_angle = line_orientation_degrees(a, b)
        for second in lines[first_index + 1:]:
            if first_source_id == second["source_entity_id"]:
                continue
            pair_checks += 1
            if pair_checks > max_pairs:
                truncated = True
                break
            c, d = second["start"], second["end"]
            second_angle = line_orientation_degrees(c, d)
            _add_relation(relationships, counts, max_relationships, "parallel", first, second,
                          are_parallel(first_angle, second_angle, tolerances["parallel_angle_degrees"]))
            _add_relation(relationships, counts, max_relationships, "perpendicular", first, second,
                          are_perpendicular(first_angle, second_angle,
                                            tolerances["perpendicular_angle_degrees"]))
            intersection = line_intersection(a, b, c, d, tolerances["intersection_units"])
            if intersection is not None:
                counts["intersections"] += 1
                _append_bounded(relationships, max_relationships,
                                {"type": "intersection", "geometry_ids": [first_source_id, second["source_entity_id"]],
                                 "point": intersection})
            proximity = endpoint_proximity(a, b, c, d, tolerances["endpoint_proximity_units"])
            if proximity:
                counts["endpoint_proximity"] += 1
                _append_bounded(relationships, max_relationships,
                                {"type": "endpoint_proximity", "geometry_ids": [first_source_id, second["source_entity_id"]],
                                 **proximity})
        if truncated:
            break
    return {"relationships": relationships, "counts": counts, "pair_checks": min(pair_checks, max_pairs),
            "complete": not truncated and sum(counts.values()) <= max_relationships,
            "truncated": truncated or sum(counts.values()) > max_relationships,
            "tolerances": tolerances, "tolerance_units": "same as source geometry coordinates"}


def _add_relation(output, counts, maximum, name, first, second, condition):
    if condition:
        counts[name] += 1
        _append_bounded(output, maximum,
                        {"type": name, "geometry_ids": [first["source_entity_id"],
                                                            second["source_entity_id"]]})


def _append_bounded(output, maximum, value):
    if len(output) < maximum:
        output.append(value)


def _entities_near(first, second, tolerance):
    first_points, second_points = geometry_points(first), geometry_points(second)
    return any(line_length(a, b) <= tolerance for a in first_points for b in second_points)
