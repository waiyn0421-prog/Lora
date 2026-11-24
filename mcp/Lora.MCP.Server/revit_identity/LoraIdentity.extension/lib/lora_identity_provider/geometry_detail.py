# -*- coding: utf-8 -*-
"""Bounded read-only geometry extraction for explicitly requested element IDs."""

from . import coordinate_geometry

_MAX_IDS = 25
_MAX_ENTITIES = 200
_MAX_VISITS = 5000
_MAX_DEPTH = 8
_MM_PER_FOOT = 304.8


def geometry_detail(runtime, uiapp, doc, element_ids, entity_offset=0, entity_limit=100):
    """Extract bounded geometry evidence for a caller-selected element ID list."""
    if not isinstance(element_ids, (list, tuple)) or not 1 <= len(element_ids) <= _MAX_IDS:
        return {"status": "error", "error": "invalid_element_ids", "maximum_elements": _MAX_IDS}
    if (isinstance(entity_offset, bool) or not isinstance(entity_offset, int) or entity_offset < 0 or
            entity_offset > 10000000):
        return {"status": "error", "error": "invalid_entity_offset"}
    if (isinstance(entity_limit, bool) or not isinstance(entity_limit, int) or
            entity_limit < 1 or entity_limit > _MAX_ENTITIES):
        return {"status": "error", "error": "invalid_entity_limit", "maximum": _MAX_ENTITIES}
    normalized_ids = []
    for value in element_ids:
        if isinstance(value, bool) or not str(value).isdigit():
            return {"status": "error", "error": "invalid_element_id"}
        normalized_ids.append(str(value))
    if len(set(normalized_ids)) != len(normalized_ids):
        return {"status": "error", "error": "duplicate_element_ids"}
    try:
        identity = runtime.identity_response(uiapp, doc)
        if identity.get("status") != "success":
            return {"status": "error", "error": "document_identity_unavailable"}
        from Autodesk.Revit.DB import (Curve, ElementId, GeometryInstance, LocationCurve,
            LocationPoint, Mesh, Options, Solid, ViewDetailLevel)
        options = Options()
        options.IncludeNonVisibleObjects = False
        options.DetailLevel = ViewDetailLevel.Fine
        items = []
        failures = []
        all_entities = []
        request_visits = [0]
        request_truncated = False
        for element_id_text in normalized_ids:
            element_warnings = []
            try:
                element = doc.GetElement(ElementId(int(element_id_text)))
            except Exception as exc:
                failures.append(_failure(element_id_text, "element_resolution", exc))
                continue
            if element is None:
                failures.append({"element_id": element_id_text, "field": "element_resolution",
                    "error": {"code": "element_not_found", "message": "Requested element ID is not present in the active document."}})
                continue
            record = {"element_id": coordinate_geometry._id_text(element.Id),
                "unique_id": coordinate_geometry._safe_get(element, "UniqueId"),
                "category": coordinate_geometry._safe_category_name(element),
                "coordinate_reference": "active host document internal model coordinates",
                "geometry_status": "not_inspected", "location_status": "not_inspected",
                "bounding_box_status": "not_inspected", "geometry_complete": False, "geometry_counts": {
                    "solids": 0, "meshes": 0, "faces": 0, "edges": 0, "curves": 0,
                    "geometry_instances": 0, "other_objects": 0}, "warnings": element_warnings}
            try:
                location = element.Location
                if location is None:
                    record["location_status"] = "unavailable"
                else:
                    location_info = _location_summary(location, element_warnings, LocationCurve, LocationPoint)
                    record["location"] = location_info
                    record["location_status"] = location_info.get("status", "failed")
            except Exception as exc:
                record["location_status"] = coordinate_geometry._status_for_access_exception(
                    element, "Location", exc, "location", element_warnings)
            try:
                box = element.get_BoundingBox(None)
                if box is None:
                    record["bounding_box_status"] = "unavailable"
                else:
                    record["bounding_box"] = {"min": coordinate_geometry._point(box.Min),
                        "max": coordinate_geometry._point(box.Max),
                        "coordinate_reference": "active host document internal model coordinates"}
                    record["bounding_box_status"] = "inspected"
            except Exception as exc:
                record["bounding_box_status"] = coordinate_geometry._status_for_access_exception(
                    element, "get_BoundingBox", exc, "bounding_box", element_warnings)
            try:
                geometry_warning_start = len(element_warnings)
                geometry = element.get_Geometry(options)
                if geometry is None:
                    record["geometry_status"] = "unavailable"
                else:
                    _walk(geometry, [], 0, element_warnings, record, all_entities,
                          request_visits, Curve, GeometryInstance, Mesh, Solid,
                          request_limit=_MAX_ENTITIES)
                    record["geometry_status"] = ("failed" if any(
                        warning.get("code") == "api_access_failed"
                        for warning in element_warnings[geometry_warning_start:]) else "inspected")
                    record["geometry_complete"] = record["geometry_status"] == "inspected" and not any(
                        warning.get("code") in ("geometry_scan_cap_reached", "geometry_entity_cap_reached",
                            "maximum_geometry_depth") for warning in element_warnings[geometry_warning_start:])
            except Exception as exc:
                record["geometry_status"] = "failed"
                element_warnings.append(coordinate_geometry._warning("geometry", exc))
            if record["geometry_status"] == "not_inspected":
                record["geometry_status"] = "failed"
            if any(warning.get("code") in ("geometry_scan_cap_reached", "geometry_entity_cap_reached",
                    "maximum_geometry_depth") for warning in element_warnings):
                request_truncated = True
            items.append(record)
        entity_page = all_entities[entity_offset:entity_offset + entity_limit]
        entity_has_more = entity_offset + len(entity_page) < len(all_entities)
        return {"status": "success", "document_identity": coordinate_geometry._identity_summary(
                identity.get("identity") or {}),
            "requested_element_ids": normalized_ids, "items": items,
            "entity_offset": entity_offset, "entity_limit": entity_limit,
            "total_entities_observed": len(all_entities), "geometry_entities": entity_page,
            "entity_has_more": entity_has_more,
            "entity_page_has_more_status": "unknown_due_to_scan_cap" if request_truncated else "known",
            "failures": failures, "failure_count": len(failures),
            "truncated": request_truncated, "visit_count": request_visits[0],
            "coordinate_reference": "active host document internal model coordinates; nested symbol geometry is local with instance transform chain",
            "linked_geometry": "not requested; linked element IDs and link transforms are not inferred",
            "clearance_status": "unverified",
            "clearance_reason": "Geometry extraction is evidence only; no clash or clearance computation is performed."}
    except Exception as exc:
        return {"status": "error", "error": "geometry_detail_failed",
                "diagnostic": {"exception_type": type(exc).__name__, "message": str(exc)[:200]}}


def _walk(geometry, transform_chain, depth, warnings, record, entities,
          visits, Curve, GeometryInstance, Mesh, Solid, request_limit):
    if depth > _MAX_DEPTH:
        warnings.append({"field": "geometry.nesting", "code": "maximum_geometry_depth",
                         "maximum_depth": _MAX_DEPTH})
        return
    try:
        for obj in geometry:
            if visits[0] >= _MAX_VISITS or len(entities) >= request_limit:
                warnings.append({"field": "geometry", "code": "geometry_scan_cap_reached"})
                return
            visits[0] += 1
            type_name = coordinate_geometry._class_name(obj) or "unknown"
            if isinstance(obj, GeometryInstance):
                record["geometry_counts"]["geometry_instances"] += 1
                try:
                    transform = obj.Transform
                    transform_record = _transform_record(transform)
                    _append_entity(entities, record, {"kind": "geometry_instance",
                        "geometry_class": type_name, "transform": transform_record,
                        "transform_provenance": "GeometryInstance.Transform maps symbol-local coordinates to the owning instance coordinate system",
                        "geometry_source": "GetSymbolGeometry()", "coordinate_reference": "symbol-local; transform chain included"})
                    nested = obj.GetSymbolGeometry()
                    _walk(nested, transform_chain + [transform], depth + 1, warnings, record,
                          entities, visits, Curve, GeometryInstance, Mesh, Solid, request_limit)
                except Exception as exc:
                    warnings.append(coordinate_geometry._warning("geometry.instance", exc))
                continue
            if isinstance(obj, Solid):
                record["geometry_counts"]["solids"] += 1
                faces = list(obj.Faces)
                edges = list(obj.Edges)
                record["geometry_counts"]["faces"] += len(faces)
                record["geometry_counts"]["edges"] += len(edges)
                _append_entity(entities, record, {"kind": "solid", "geometry_class": type_name,
                    "face_count": len(faces), "edge_count": len(edges),
                    "volume_internal_cubic_feet": _optional_number(obj, "Volume", warnings, "geometry.solid.volume"),
                    "surface_area_internal_square_feet": _optional_number(obj, "SurfaceArea", warnings, "geometry.solid.surface_area"),
                    "coordinate_reference": "active host document internal model coordinates",
                    "instance_transform_chain": [_transform_record(item) for item in transform_chain]})
                for face in faces:
                    if len(entities) >= request_limit:
                        warnings.append({"field": "geometry.faces", "code": "geometry_entity_cap_reached"})
                        break
                    _append_entity(entities, record, {"kind": "face", "geometry_class": coordinate_geometry._class_name(face),
                        "area_internal_square_feet": _optional_number(face, "Area", warnings, "geometry.face.area"),
                        "coordinate_reference": "solid-local; parent transform chain included",
                        "instance_transform_chain": [_transform_record(item) for item in transform_chain]})
                for edge in edges:
                    if len(entities) >= request_limit:
                        warnings.append({"field": "geometry.edges", "code": "geometry_entity_cap_reached"})
                        break
                    try:
                        curve = edge.AsCurve()
                        desc = _curve_summary(curve, transform_chain, warnings)
                        desc.update({"kind": "edge", "geometry_class": coordinate_geometry._class_name(edge)})
                        _append_entity(entities, record, desc)
                    except Exception as exc:
                        warnings.append(coordinate_geometry._warning("geometry.edge", exc))
                continue
            if isinstance(obj, Mesh):
                record["geometry_counts"]["meshes"] += 1
                triangle_count = int(obj.NumTriangles)
                sample_count = min(triangle_count, 3)
                samples = []
                for index in range(sample_count):
                    tri = obj.get_Triangle(index)
                    samples.append([_transformed_point(tri.get_Vertex(vertex), transform_chain)
                                    for vertex in range(3)])
                _append_entity(entities, record, {"kind": "mesh", "geometry_class": type_name,
                    "triangle_count": triangle_count, "sample_triangles": samples,
                    "sample_limit": 3, "coordinate_reference": "active host document internal model coordinates",
                    "instance_transform_chain": [_transform_record(item) for item in transform_chain]})
                continue
            if isinstance(obj, Curve):
                record["geometry_counts"]["curves"] += 1
                curve_record = _curve_summary(obj, transform_chain, warnings)
                curve_record.update({"kind": "curve", "geometry_class": type_name})
                _append_entity(entities, record, curve_record)
                continue
            record["geometry_counts"]["other_objects"] += 1
            _append_entity(entities, record, {"kind": "other", "geometry_class": type_name,
                "coordinate_reference": "not interpreted"})
    except Exception as exc:
        warnings.append(coordinate_geometry._warning("geometry.enumeration", exc))


def _append_entity(entities, record, entity):
    entity["element_id"] = record["element_id"]
    entity["entity_index"] = len(entities)
    entities.append(entity)


def _curve_summary(curve, transform_chain, warnings, field_prefix="geometry.curve"):
    warning_start = len(warnings)
    result = {"length_internal_feet": _optional_number(curve, "Length", warnings, field_prefix + ".length"),
              "length_mm": None, "is_bound": None, "start": None, "end": None,
              "coordinate_reference": "active host document internal model coordinates",
              "inspection_status": "inspected"}
    if result["length_internal_feet"] is not None:
        result["length_mm"] = result["length_internal_feet"] * _MM_PER_FOOT
    try:
        result["is_bound"] = bool(curve.IsBound)
        if result["is_bound"]:
            result["start"] = _transformed_point(curve.GetEndPoint(0), transform_chain)
            result["end"] = _transformed_point(curve.GetEndPoint(1), transform_chain)
    except Exception as exc:
        result["inspection_status"] = "failed"
        warnings.append(coordinate_geometry._warning(field_prefix + ".endpoints", exc))
    if transform_chain:
        result["instance_transform_chain"] = [_transform_record(item) for item in transform_chain]
    if any(item.get("code") == "api_access_failed" for item in warnings[warning_start:]):
        result["inspection_status"] = "failed"
    return result


def _transformed_point(point, transform_chain):
    transformed = point
    for transform in reversed(transform_chain):
        transformed = transform.OfPoint(transformed)
    return coordinate_geometry._point(transformed, "active host document internal model coordinates")


def _transform_record(transform):
    return {"origin": coordinate_geometry._point(transform.Origin,
            "GeometryInstance transform origin; source frame follows the parent instance"),
        "basis_x": coordinate_geometry._vector(transform.BasisX),
        "basis_y": coordinate_geometry._vector(transform.BasisY),
        "basis_z": coordinate_geometry._vector(transform.BasisZ),
        "direction": "symbol-local to parent instance coordinates"}


def _optional_number(obj, name, warnings, field):
    try:
        value = getattr(obj, name)
        return float(value) if value is not None else None
    except Exception as exc:
        warnings.append(coordinate_geometry._warning(field, exc))
        return None


def _location_summary(location, warnings, LocationCurve, LocationPoint):
    result = {"status": "unavailable", "class_name": coordinate_geometry._class_name(location)}
    if isinstance(location, LocationPoint):
        try:
            result["point"] = coordinate_geometry._point(location.Point)
            result["status"] = "inspected"
        except Exception as exc:
            result["status"] = "failed"
            warnings.append(coordinate_geometry._warning("location", exc))
    elif isinstance(location, LocationCurve):
        try:
            curve = _curve_summary(location.Curve, [], warnings, "location.curve")
            result["curve"] = curve
            result["status"] = curve.get("inspection_status", "failed")
        except Exception as exc:
            result["status"] = "failed"
            warnings.append(coordinate_geometry._warning("location", exc))
    return result


def _failure(element_id, field, exc):
    return {"element_id": element_id, "field": field,
            "error": {"code": "api_access_failed", "exception_type": type(exc).__name__,
                      "message": str(exc)[:200]}}
