"""Convert PyMuPDF drawing paths into bounded, coordinate-preserving entities."""

import math

from .geometry import bounding_box, line_length, polygon_area


def extract_pdf_geometry(page, page_number, max_entities=1_000, curve_segments=12):
    raw_paths = page.get_drawings()
    entities, paths = [], []
    counts = {"paths": len(raw_paths), "lines": 0, "rectangles": 0, "curves": 0,
              "polylines": 0, "quadrilaterals": 0, "text_spans": 0}
    truncated = False
    entity_counter = 0

    def add(entity):
        nonlocal entity_counter, truncated
        entity_counter += 1
        if entity_counter > max_entities:
            truncated = True
            return None
        entity["id"] = "p{:03d}-g{:06d}".format(page_number, entity_counter)
        entity["page_number"] = page_number
        entity["coordinate_units"] = "PDF page points"
        if entity.get("bbox") is None:
            points = entity.get("points") or ([entity["start"], entity["end"]]
                                                if "start" in entity and "end" in entity else [])
            if points:
                entity["bbox"] = bounding_box(points)
        entities.append(entity)
        return entity["id"]

    for path_index, raw_path in enumerate(raw_paths):
        if len(paths) >= max_entities:
            truncated = True
            break
        path_id = "p{:03d}-path-{:05d}".format(page_number, path_index + 1)
        style = _path_style(raw_path)
        path_geometry_ids = []
        path_line_points = []
        contains_closed_item = False
        for item in raw_path.get("items", []):
            if not item:
                continue
            kind = item[0]
            if kind == "l":
                start, end = _point(item[1]), _point(item[2])
                counts["lines"] += 1
                entity_id = add({"kind": "line", "start": start, "end": end,
                                 "length": line_length(start, end), "path_id": path_id,
                                 "source_kind": "pdf_vector_line", **style})
                if entity_id:
                    path_geometry_ids.append(entity_id)
                if not path_line_points:
                    path_line_points = [start, end]
                elif _same_point(path_line_points[-1], start):
                    path_line_points.append(end)
                else:
                    _add_polyline(add, path_geometry_ids, counts, path_id, path_line_points,
                                  style, page_number)
                    path_line_points = [start, end]
            elif kind == "re":
                contains_closed_item = True
                rect = item[1]
                points = [[float(rect.x0), float(rect.y0)], [float(rect.x1), float(rect.y0)],
                          [float(rect.x1), float(rect.y1)], [float(rect.x0), float(rect.y1)],
                          [float(rect.x0), float(rect.y0)]]
                counts["rectangles"] += 1
                entity_id = add({"kind": "rectangle", "points": points, "bbox": _rect_bbox(rect),
                                 "closed": True, "area": polygon_area(points), "path_id": path_id,
                                 "source_kind": "pdf_vector_rectangle", **style})
                if entity_id:
                    path_geometry_ids.append(entity_id)
            elif kind == "qu":
                contains_closed_item = True
                quad = item[1]
                points = [_point(quad.ul), _point(quad.ur), _point(quad.lr), _point(quad.ll),
                          _point(quad.ul)]
                counts["quadrilaterals"] += 1
                counts["polylines"] += 1
                entity_id = add({"kind": "polyline", "points": points, "bbox": bounding_box(points),
                                 "closed": True, "area": polygon_area(points), "path_id": path_id,
                                 "source_kind": "pdf_vector_quad", **style})
                if entity_id:
                    path_geometry_ids.append(entity_id)
            elif kind == "c":
                controls = [_point(value) for value in item[1:5]]
                approximated = _sample_cubic(controls, curve_segments)
                counts["curves"] += 1
                entity_id = add({"kind": "curve", "control_points": controls, "points": approximated,
                                 "bbox": bounding_box(approximated), "closed": False,
                                 "approximation_segments": curve_segments, "path_id": path_id,
                                 "source_kind": "pdf_cubic_bezier", **style})
                if entity_id:
                    path_geometry_ids.append(entity_id)
            if truncated:
                break
        if len(path_line_points) >= 3:
            _add_polyline(add, path_geometry_ids, counts, path_id, path_line_points,
                          style, page_number)
        closed = bool(raw_path.get("closePath")) or contains_closed_item or _sequence_closed(path_line_points)
        paths.append({"id": path_id, "page_number": page_number, "geometry_ids": path_geometry_ids,
                      "bbox": [float(value) for value in raw_path.get("rect", [])],
                      "closed": closed, "style": style})
        if truncated:
            break
    text_annotations = []
    for block_index, block in enumerate(page.get_text("dict").get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block.get("lines", [])):
            for span_index, span in enumerate(line.get("spans", [])):
                counts["text_spans"] += 1
                if len(text_annotations) < max_entities:
                    text_annotations.append({"id": "p{:03d}-text-{:06d}".format(page_number,
                                                                                   len(text_annotations) + 1),
                                            "text": span.get("text", ""), "page_number": page_number,
                                            "bbox": [float(value) for value in span.get("bbox", [])],
                                            "font": span.get("font"), "size_points": span.get("size"),
                                            "coordinate_units": "PDF page points"})
                else:
                    truncated = True
    return {"entities": entities, "paths": paths, "text_annotations": text_annotations,
            "counts": counts, "entity_count": len(entities), "complete": not truncated,
            "truncated": truncated, "coordinate_units": "PDF page points",
            "warnings": ["PDF geometry or text records were bounded"] if truncated else []}


def _add_polyline(add, path_geometry_ids, counts, path_id, points, style, page_number):
    closed = _sequence_closed(points)
    values = list(points)
    if closed and values[0] != values[-1]:
        values.append(values[0])
    counts["polylines"] += 1
    entity_id = add({"kind": "polyline", "points": values, "bbox": bounding_box(values),
                     "closed": closed, "area": polygon_area(values) if closed else None,
                     "path_id": path_id, "source_kind": "reconstructed_pdf_line_path", **style})
    if entity_id:
        path_geometry_ids.append(entity_id)


def _path_style(path):
    return {"path_type": path.get("type"),
            "stroke_color": _color(path.get("color")), "fill_color": _color(path.get("fill")),
            "stroke_width": float(path["width"]) if path.get("width") is not None else None,
            "line_cap": path.get("lineCap"), "line_join": path.get("lineJoin"),
            "dashes": path.get("dashes"), "even_odd": path.get("even_odd"),
            "close_path": bool(path.get("closePath")),
            "stroke_opacity": path.get("stroke_opacity"),
            "fill_opacity": path.get("fill_opacity"),
            "layer": path.get("layer"), "blend_mode": path.get("blendmode"),
            "sequence_number": path.get("seqno")}


def _color(value):
    if value is None:
        return None
    try:
        return [float(channel) for channel in value]
    except TypeError:
        return str(value)


def _point(value):
    return [float(value.x), float(value.y)]


def _rect_bbox(rect):
    return [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]


def _sample_cubic(points, segment_count):
    p0, p1, p2, p3 = points
    output = []
    for index in range(segment_count + 1):
        t = float(index) / segment_count
        u = 1.0 - t
        output.append([u ** 3 * p0[0] + 3 * u * u * t * p1[0] +
                       3 * u * t * t * p2[0] + t ** 3 * p3[0],
                       u ** 3 * p0[1] + 3 * u * u * t * p1[1] +
                       3 * u * t * t * p2[1] + t ** 3 * p3[1]])
    return output


def _same_point(first, second, tolerance=1e-6):
    return math.hypot(first[0] - second[0], first[1] - second[1]) <= tolerance


def _sequence_closed(points):
    return len(points) > 2 and _same_point(points[0], points[-1])
