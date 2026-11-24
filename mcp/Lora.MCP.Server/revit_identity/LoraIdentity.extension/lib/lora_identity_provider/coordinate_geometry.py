# -*- coding: utf-8 -*-
"""Read-only Revit coordinate and bounded geometry diagnostics.

Kept Python 2.7 compatible for the pyRevit host. This module never opens a
transaction and never writes to the document.
"""

import logging
import math


_LOGGER = logging.getLogger(__name__)
_MM_PER_FOOT = 304.8
_MAX_PAGE_SIZE = 100
_MAX_SCAN = 100000


def coordinate_diagnostic(runtime, uiapp, doc):
    """Return coordinate landmarks and a cross-checked active-location transform."""
    try:
        identity = runtime.identity_response(uiapp, doc)
        if identity.get("status") != "success":
            return {"status": "error", "error": "document_identity_unavailable"}
        from Autodesk.Revit.DB import BasePoint, InternalOrigin, XYZ
        result = {"status": "success", "document_identity": _identity_summary(identity.get("identity") or {}),
                  "units": {"internal": "feet and radians", "display": "millimetres and degrees",
                            "millimetres_per_foot": _MM_PER_FOOT},
                  "warnings": []}

        origin = None
        try:
            origin = InternalOrigin.Get(doc)
            result["internal_origin"] = {"element_id": _id_text(origin.Id),
                "position_internal": _point(origin.Position, "Revit internal model coordinates"),
                "shared_position_active_project_location": _point(origin.SharedPosition, "active ProjectLocation shared coordinates"),
                "coordinate_reference": "Revit internal origin; internal Position is expected at XYZ.Zero"}
        except Exception as exc:
            result["internal_origin"] = None
            result["warnings"].append(_warning("internal_origin", exc))

        result["project_base_point"] = _base_point_record(BasePoint.GetProjectBasePoint, doc, "project_base_point", result["warnings"])
        result["survey_point"] = _base_point_record(BasePoint.GetSurveyPoint, doc, "survey_point", result["warnings"])

        location = None
        try:
            location = doc.ActiveProjectLocation
            if location is None:
                raise ValueError("ActiveProjectLocation returned null")
            location_record = {"element_id": _id_text(location.Id), "name": _safe_get(location, "Name"),
                               "transform": None, "origin_project_position": None,
                               "transform_cross_check": {"status": "unverified", "direction": None,
                                   "method": "ProjectLocation transform compared to GetProjectPosition(XYZ.Zero) and point SharedPosition"}}
            position = location.GetProjectPosition(XYZ.Zero)
            location_record["origin_project_position"] = _project_position(position)
            transform = location.GetTransform()
            transform_record = _transform(transform)
            location_record["transform"] = transform_record
            # GetProjectPosition is an independent Revit API coordinate report. Probe
            # two asymmetric, non-origin internal points so a rotation about XYZ.Zero
            # cannot make forward and inverse transforms appear equivalent.
            reference_checks = []
            for raw in ((1.25, -2.5, 3.75), (-4.5, 6.25, 9.125)):
                point = XYZ(raw[0], raw[1], raw[2])
                reported = location.GetProjectPosition(point)
                expected = XYZ(reported.EastWest, reported.NorthSouth, reported.Elevation)
                forward = transform.OfPoint(point)
                reverse = transform.Inverse.OfPoint(point)
                reference_checks.append({
                    "input_internal_feet": {"x": raw[0], "y": raw[1], "z": raw[2]},
                    "project_position": _project_position(reported),
                    "forward_matches_project_position": _near_xyz(forward, expected),
                    "inverse_matches_project_position": _near_xyz(reverse, expected),
                })

            base_point_checks = []
            for key in ("project_base_point", "survey_point"):
                point_record = result.get(key)
                if not point_record or not point_record.get("position_internal") or not point_record.get("shared_position"):
                    continue
                raw = point_record["position_internal"]["raw_feet"]
                shared_raw = point_record["shared_position"]["raw_feet"]
                point = XYZ(raw["x"], raw["y"], raw["z"])
                expected = XYZ(shared_raw["x"], shared_raw["y"], shared_raw["z"])
                base_point_checks.append({
                    "base_point": key,
                    "forward_matches_shared_position": _near_xyz(transform.OfPoint(point), expected),
                    "inverse_matches_shared_position": _near_xyz(transform.Inverse.OfPoint(point), expected),
                })

            forward_refs = [item["forward_matches_project_position"] for item in reference_checks]
            inverse_refs = [item["inverse_matches_project_position"] for item in reference_checks]
            forward_base = any(item["forward_matches_shared_position"] for item in base_point_checks)
            inverse_base = any(item["inverse_matches_shared_position"] for item in base_point_checks)
            forward_verified = (len(reference_checks) >= 2 and all(forward_refs) and not all(inverse_refs) and forward_base)
            inverse_verified = (len(reference_checks) >= 2 and all(inverse_refs) and not all(forward_refs) and inverse_base)
            direction = "internal_to_shared" if forward_verified else ("shared_to_internal" if inverse_verified else "unverified")
            location_record["transform"] = _transform(transform, direction)
            location_record["transform_cross_check"] = {
                "status": "verified" if direction != "unverified" else "unverified",
                "direction": direction,
                "method": "Two asymmetric non-origin points checked against ProjectLocation.GetProjectPosition; candidate direction also checked against BasePoint.Position and BasePoint.SharedPosition.",
                "independent_project_position_checks": reference_checks,
                "base_point_checks": base_point_checks,
            }
            if direction == "unverified":
                result["warnings"].append({"field": "project_location.transform", "code": "transform_cross_check_failed",
                    "message": "Forward and inverse transforms were not uniquely confirmed by two non-origin ProjectPosition references and BasePoint data; direction is unverified."})
            # Report the independent ProjectPosition result at each base point too.
            for key in ("project_base_point", "survey_point"):
                point_record = result.get(key)
                if point_record and point_record.get("position_internal"):
                    try:
                        raw = point_record["position_internal"]["raw_feet"]
                        point = XYZ(raw["x"], raw["y"], raw["z"])
                        calculated = location.GetProjectPosition(point)
                        point_record["project_location_cross_check"] = _project_position(calculated)
                        point_record["project_location_cross_check_status"] = (
                            "matches_shared_position" if point_record.get("shared_position") and
                            _near_xyz(XYZ(calculated.EastWest, calculated.NorthSouth, calculated.Elevation),
                                      XYZ(point_record["shared_position"]["raw_feet"]["x"],
                                          point_record["shared_position"]["raw_feet"]["y"],
                                          point_record["shared_position"]["raw_feet"]["z"]))
                            else "does_not_match_or_shared_position_unavailable")
                    except Exception as exc:
                        point_record["project_location_cross_check_status"] = "failed"
                        point_record.setdefault("warnings", []).append(_warning("project_location_cross_check", exc))
            result["active_project_location"] = location_record
        except Exception as exc:
            result["active_project_location"] = None
            result["warnings"].append(_warning("active_project_location", exc))
        return result
    except Exception as exc:
        _LOGGER.exception("Coordinate diagnostic failed")
        return {"status": "error", "error": "coordinate_diagnostic_failed",
                "diagnostic": {"exception_type": type(exc).__name__}}


def geometry_inventory(runtime, uiapp, doc, offset=0, limit=50, failure_offset=0, failure_limit=50):
    """Inspect bounded pages of host-document instances and report global scan completeness."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > 10000000:
        return {"status": "error", "error": "invalid_offset"}
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > _MAX_PAGE_SIZE:
        return {"status": "error", "error": "invalid_limit", "maximum": _MAX_PAGE_SIZE}
    if isinstance(failure_offset, bool) or not isinstance(failure_offset, int) or failure_offset < 0 or failure_offset > 10000000:
        return {"status": "error", "error": "invalid_failure_offset"}
    if isinstance(failure_limit, bool) or not isinstance(failure_limit, int) or failure_limit < 1 or failure_limit > _MAX_PAGE_SIZE:
        return {"status": "error", "error": "invalid_failure_limit", "maximum": _MAX_PAGE_SIZE}
    try:
        identity = runtime.identity_response(uiapp, doc)
        if identity.get("status") != "success":
            return {"status": "error", "error": "document_identity_unavailable"}
        from Autodesk.Revit.DB import (FilteredElementCollector, LocationCurve, LocationPoint,
            RevitLinkInstance)
        collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
        try:
            upstream_total = int(collector.GetElementCount())
        except Exception:
            upstream_total = None
        counts = {"total_elements_examined": 0, "physical_model_instances": 0,
            "physical_geometry_candidates": 0, "model_curve_or_reference_geometry": 0,
            "unsupported_or_unclassified": 0,
            "successfully_inspected": 0, "unsupported": 0, "failed": 0,
            "view_specific_elements": 0, "annotation_detail_elements": 0,
            "coordinate_reference_elements": 0, "other_nonphysical_elements": 0,
            "revit_link_instances": 0, "linked_documents_available": 0,
            "linked_documents_successfully_inspected": 0, "uninspected_links": 0}
        items = []
        element_failures = []
        linked_summaries = []
        link_scan_cache = {}
        linked_documents_truncated = False
        physical_index = 0
        truncated = False
        iterator = iter(collector)
        for element in iterator:
            if counts["total_elements_examined"] >= _MAX_SCAN:
                truncated = True
                break
            counts["total_elements_examined"] += 1
            try:
                category = getattr(element, "Category", None)
                category_value = category.Name if category is not None else None
                category_name = str(category_value) if category_value is not None else None
                class_name = element.GetType().Name
                view_specific = bool(getattr(element, "ViewSpecific", False))
                cat_type = category.CategoryType if category is not None else None
                cat_type_text = str(cat_type) if cat_type is not None else ""
                is_link = isinstance(element, RevitLinkInstance)
                if is_link:
                    counts["revit_link_instances"] += 1
                    try:
                        linked_doc = element.GetLinkDocument()
                    except Exception:
                        linked_doc = None
                    link_load_status = _link_load_status(element, doc)
                    if linked_doc is not None:
                        counts["linked_documents_available"] += 1
                        cache_key = _link_document_key(linked_doc)
                        if cache_key not in link_scan_cache and len(link_scan_cache) < 200:
                            link_scan_cache[cache_key] = _inspect_link_document(
                                linked_doc, LocationCurve, LocationPoint, failure_offset, failure_limit)
                            if len(linked_summaries) < 100:
                                linked_summaries.append(link_scan_cache[cache_key])
                            else:
                                linked_documents_truncated = True
                            if link_scan_cache[cache_key].get("geometry_complete") is True:
                                counts["linked_documents_successfully_inspected"] += 1
                        elif cache_key not in link_scan_cache:
                            linked_documents_truncated = True
                        link_summary = link_scan_cache.get(cache_key)
                        fully_inspected = bool(link_summary and link_summary.get("geometry_complete") is True)
                        if not fully_inspected:
                            counts["uninspected_links"] += 1
                    else:
                        counts["uninspected_links"] += 1
                        link_summary = None
                    record = _base_element_record(element, category_name, class_name)
                    record.update({"classification": "link_instance", "linked_document_available": linked_doc is not None,
                        "linked_document_load_state": ("loaded" if linked_doc is not None else link_load_status.get("status")),
                        "linked_file_status": link_load_status.get("raw"),
                        "linked_file_status_warning": link_load_status.get("warning"),
                        "linked_geometry_inspected": bool(link_summary and link_summary.get("geometry_complete")),
                        "linked_document_inventory": link_summary,
                        "link_instance_transform": _safe_link_transform(element),
                        "warning": "Linked geometry is reported in linked-document coordinates and has not undergone extent-specific host-coordinate clash analysis; linked clearance is unverified."})
                    record["classification_status"] = "inspected"
                    record["geometry_inspection_status"] = "not_applicable"
                    record["inspection_status"] = "not_applicable"
                    _append_page(items, record, physical_index, offset, limit)
                    physical_index += 1
                    continue
                if view_specific:
                    counts["view_specific_elements"] += 1
                    record = _classification_record(element, category_name, class_name, "view_specific", "Element is view-specific; excluded from model geometry candidates.")
                    _append_page(items, record, physical_index, offset, limit)
                    physical_index += 1
                    continue
                if "annotation" in cat_type_text.lower() or _is_detail_or_annotation(class_name):
                    counts["annotation_detail_elements"] += 1
                    record = _classification_record(element, category_name, class_name, "annotation_or_detail", "Annotation or detail element; excluded from model geometry candidates.")
                    _append_page(items, record, physical_index, offset, limit)
                    physical_index += 1
                    continue
                if _is_coordinate_reference(class_name, category_name):
                    counts["coordinate_reference_elements"] += 1
                    record = _classification_record(element, category_name, class_name, "coordinate_reference", "Coordinate or datum reference element; not treated as physical geometry.")
                    _append_page(items, record, physical_index, offset, limit)
                    physical_index += 1
                    continue
                if _is_model_curve_or_reference(class_name):
                    counts["model_curve_or_reference_geometry"] += 1
                    record = _classification_record(element, category_name, class_name, "model_curve_or_reference_geometry", "Model curve or reference geometry; not treated as a physical obstruction.")
                    _append_page(items, record, physical_index, offset, limit)
                    physical_index += 1
                    continue
                if category is None or "model" not in cat_type_text.lower():
                    counts["other_nonphysical_elements"] += 1
                    counts["unsupported_or_unclassified"] += 1
                    record = _classification_record(element, category_name, class_name, "unsupported_or_unclassified", "Element category/type did not establish a model geometry candidate.")
                    _append_page(items, record, physical_index, offset, limit)
                    physical_index += 1
                    continue
                record = _inspect_physical(element, category_name, class_name,
                                           LocationCurve, LocationPoint, doc)
                if record["geometry_inspection_status"] == "inspected":
                    record["classification"] = "physical_geometry_candidate"
                    counts["physical_model_instances"] += 1
                    counts["physical_geometry_candidates"] += 1
                else:
                    record["classification"] = "unsupported_or_unclassified"
                    counts["unsupported_or_unclassified"] += 1
                if record["geometry_inspection_status"] == "inspected":
                    counts["successfully_inspected"] += 1
                elif record["geometry_inspection_status"] == "unavailable":
                    counts["unsupported"] += 1
                else:
                    failure_index = counts["failed"]
                    counts["failed"] += 1
                    failure = dict(record)
                    failure.update({"failure_index": failure_index,
                        "error": {"code": "geometry_inspection_failed",
                                  "message": "No geometry evidence was available because one or more API reads failed."}})
                    if failure_offset <= failure_index < failure_offset + failure_limit:
                        element_failures.append(failure)
                _append_page(items, record, physical_index, offset, limit)
                physical_index += 1
            except Exception as exc:
                counts["failed"] += 1
                _LOGGER.warning("Geometry inventory element inspection failed: %s", type(exc).__name__)
                failure_index = counts["failed"] - 1
                failure = _base_element_record(element, _safe_category_name(element), _class_name(element))
                failure.update({"failure_index": failure_index,
                    "exception_type": type(exc).__name__,
                    "error": {"code": "element_inspection_failed", "message": str(exc)[:200]},
                    "classification": "unsupported_or_unclassified", "inspection_status": "not_inspected",
                    "classification_status": "failed",
                    "classification_warnings": [_warning("classification", exc)],
                    "geometry_inspection_status": "not_inspected"})
                if failure_offset <= failure_index < failure_offset + failure_limit:
                    element_failures.append(failure)
                if offset <= physical_index < offset + limit:
                    items.append(dict(failure))
                physical_index += 1
                counts["unsupported_or_unclassified"] += 1
        element_scan_complete = not truncated and (upstream_total is None or counts["total_elements_examined"] >= upstream_total)
        has_more = physical_index > offset + limit
        linked_has_unsupported = any(item.get("unsupported", 0) > 0 for item in linked_summaries)
        linked_has_failed = any(item.get("failed", 0) > 0 for item in linked_summaries)
        has_unsupported = counts["unsupported"] > 0 or linked_has_unsupported
        has_failed = counts["failed"] > 0 or linked_has_failed
        has_uninspected_links = counts["uninspected_links"] > 0
        is_complete = (element_scan_complete and not linked_documents_truncated and not has_unsupported and
                       not has_failed and not has_uninspected_links)
        return {"status": "success", "document_identity": _identity_summary(identity.get("identity") or {}),
            "items": items, "offset": offset, "limit": limit, "has_more": has_more,
            "element_failures": element_failures, "total_failed_elements": counts["failed"],
            "failure_offset": failure_offset, "failure_limit": failure_limit,
            "failure_has_more": counts["failed"] > failure_offset + failure_limit,
            "inventory_item_count": physical_index,
            "linked_documents": linked_summaries,
            "linked_documents_truncated": linked_documents_truncated,
            "counts": counts, "reported_total_elements": upstream_total,
            "is_complete": is_complete, "element_scan_complete": element_scan_complete,
            "truncated": truncated or linked_documents_truncated,
            "has_unsupported_elements": has_unsupported, "has_failed_elements": has_failed,
            "has_uninspected_links": has_uninspected_links,
            "clearance_status": "unverified",
            "clearance_reason": "This inventory does not perform an extent-specific clash test; incomplete, unsupported, failed, or linked geometry prevents a clearance claim.",
            "coordinate_reference": "Host-document Revit internal model coordinates; positions and bounding boxes include internal feet and converted millimetres.",
            "source": "FilteredElementCollector(document).WhereElementIsNotElementType()"}
    except Exception as exc:
        _LOGGER.exception("Geometry inventory failed")
        return {"status": "error", "error": "geometry_inventory_failed",
                "diagnostic": {"exception_type": type(exc).__name__}}


def _inspect_physical(element, category_name, class_name, LocationCurve, LocationPoint, doc,
                      coordinate_reference="host-document internal model coordinates"):
    warnings = []
    record = _base_element_record(element, category_name, class_name)
    record["classification"] = "physical_model_instance"
    record["inspection_status"] = "not_inspected"
    record["classification_status"] = "inspected"
    record["type_id_status"] = "not_inspected"
    record["type_resolution_status"] = "not_inspected"
    record["type_name_status"] = "not_inspected"
    record["family_name_status"] = "not_inspected"
    record["level_name_status"] = "not_inspected"
    record["location_status"] = "unavailable"
    record["bounding_box_status"] = "unavailable"
    record["geometry_inspection_status"] = "unavailable"
    record["geometry_extraction_method"] = []

    # Resolve type ID, type name and family name independently so one failing
    # getter cannot hide a successful value or falsely mark it inspected.
    try:
        get_type_id = element.GetTypeId
        type_id = get_type_id()
        record["type_id"] = _id_text(type_id)
        record["type_id_status"] = "inspected" if record["type_id"] not in (None, "-1", "-1000000") else "unavailable"
    except Exception as exc:
        record["type_id_status"] = _status_for_access_exception(element, "GetTypeId", exc, "type_id", warnings)

    type_element = None
    if record["type_id_status"] == "inspected":
        try:
            type_element = doc.GetElement(type_id)
            record["type_resolution_status"] = "inspected" if type_element is not None else "unavailable"
        except Exception as exc:
            record["type_resolution_status"] = "failed"
            warnings.append(_warning("type_resolution", exc))
    elif record["type_id_status"] == "unavailable":
        record["type_resolution_status"] = "not_applicable"
        record["type_name_status"] = "not_applicable"
        record["family_name_status"] = "not_applicable"
    if type_element is not None:
        name_status, name = _read_text_member(type_element, "Name", "type_name", warnings)
        record["type_name"] = name
        record["type_name_status"] = name_status
        family_status, family_name = _read_text_member(type_element, "FamilyName", "family_name", warnings)
        if family_status == "unavailable":
            family_status, family_name = _read_family_fallback(type_element, warnings)
        record["family_name"] = family_name
        record["family_name_status"] = family_status
    elif record["type_name_status"] == "not_inspected":
        if record["type_resolution_status"] == "unavailable":
            record["type_name_status"] = "unavailable"
            record["family_name_status"] = "unavailable"
    record["type_info_status"] = _aggregate_status((record["type_id_status"],
        record["type_resolution_status"], record["type_name_status"], record["family_name_status"]))

    # Level ID and level name are separate reads; a Name getter failure leaves
    # other geometry fields and the resolved LevelId intact.
    level_id_status, level_id = _read_member(element, "LevelId", "level_id", warnings)
    record["level_id_status"] = level_id_status
    if level_id_status == "inspected":
        if level_id is None or _id_text(level_id) in (None, "-1", "-1000000"):
            record["level_id_status"] = "unavailable"
            record["level_resolution_status"] = "not_applicable"
            record["level_name_status"] = "not_applicable"
        else:
            try:
                level = doc.GetElement(level_id)
                if level is None:
                    record["level_resolution_status"] = "unavailable"
                    record["level_name_status"] = "unavailable"
                else:
                    record["level_resolution_status"] = "inspected"
                    record["level"] = {"element_id": _id_text(level_id), "name": None}
                    name_status, name = _read_text_member(level, "Name", "level_name", warnings)
                    record["level"]["name"] = name
                    record["level_name_status"] = name_status
            except Exception as exc:
                record["level_resolution_status"] = "failed"
                record["level_name_status"] = "not_inspected"
                warnings.append(_warning("level_resolution", exc))
    elif level_id_status == "unavailable":
        record["level_resolution_status"] = "not_applicable"
        record["level_name_status"] = "not_applicable"
    record["level_info_status"] = _aggregate_status((record["level_id_status"],
        record["level_resolution_status"], record["level_name_status"]))

    # AttributeError is only "unavailable" when reflection confirms that the
    # member is not declared. Otherwise it is an access failure.
    try:
        location = element.Location
    except Exception as exc:
        record["location_status"] = _status_for_access_exception(
            element, "Location", exc, "location", warnings)
        location = None
    if location is not None:
        if isinstance(location, LocationCurve):
            curve_record = {"start": None, "end": None}
            record["location_curve"] = curve_record
            try:
                curve = location.Curve
                start = curve.GetEndPoint(0)
                curve_record["start"] = _point(start, coordinate_reference)
                end = curve.GetEndPoint(1)
                curve_record["end"] = _point(end, coordinate_reference)
                curve_record["is_bound"] = bool(curve.IsBound)
                curve_record["length_internal_feet"] = float(curve.Length)
                curve_record["length_mm"] = float(curve.Length) * _MM_PER_FOOT
                record["geometry_extraction_method"].append("LocationCurve")
                record["location_status"] = "inspected"
            except Exception as exc:
                record["location_status"] = "failed"
                warnings.append(_warning("location", exc))
        elif isinstance(location, LocationPoint):
            try:
                record["location_point"] = _point(location.Point, coordinate_reference)
                record["geometry_extraction_method"].append("LocationPoint")
                record["location_status"] = "inspected"
            except Exception as exc:
                record["location_status"] = "failed"
                warnings.append(_warning("location", exc))
        else:
            record["location_status"] = "unavailable"
            warnings.append({"field": "location", "code": "unsupported_location_class", "class_name": _class_name(location)})
    try:
        box = element.get_BoundingBox(None)
        if box is not None:
            record["bounding_box"] = {"min": _point(box.Min, coordinate_reference),
                "max": _point(box.Max, coordinate_reference), "coordinate_reference": coordinate_reference}
            record["geometry_extraction_method"].append("BoundingBox(null view)")
            record["bounding_box_status"] = "inspected"
    except Exception as exc:
        record["bounding_box_status"] = _status_for_access_exception(
            element, "get_BoundingBox", exc, "bounding_box", warnings)
    if record["location_status"] == "inspected" or record["bounding_box_status"] == "inspected":
        record["inspection_status"] = "inspected"
        record["geometry_inspection_status"] = "inspected"
    elif "failed" in (record["location_status"], record["bounding_box_status"]):
        record["inspection_status"] = "failed"
        record["geometry_inspection_status"] = "failed"
        record["warning"] = "No geometry evidence was available because one or more API reads failed."
    else:
        record["inspection_status"] = "unavailable"
        record["geometry_inspection_status"] = "unavailable"
        record["warning"] = "No supported LocationCurve, LocationPoint, or model bounding box was available."
    record["warnings"] = warnings
    return record


def _inspect_link_document(linked_doc, LocationCurve, LocationPoint, offset=0, limit=50):
    """Summarize linked-document geometry in its own coordinate space, without editing or reloading it."""
    summary = {"document_title": _safe_get(linked_doc, "Title"),
        "document_path": _safe_get(linked_doc, "PathName"), "coordinate_reference": "linked document internal coordinates",
        "elements_examined": 0, "physical_model_instances": 0, "physical_geometry_candidates": 0,
        "successfully_inspected": 0,
        "unsupported": 0, "failed": 0, "failed_element_records": [],
        "failure_offset": offset, "failure_limit": limit, "failure_has_more": False,
        "truncated": False, "is_complete": False,
        "geometry_complete": False, "warnings": []}
    try:
        from Autodesk.Revit.DB import FilteredElementCollector
        collector = FilteredElementCollector(linked_doc).WhereElementIsNotElementType()
        try:
            total = int(collector.GetElementCount())
        except Exception:
            total = None
        for element in collector:
            if summary["elements_examined"] >= _MAX_SCAN:
                summary["truncated"] = True
                break
            summary["elements_examined"] += 1
            try:
                category = getattr(element, "Category", None)
                category_type = str(category.CategoryType or "") if category is not None else ""
                class_name = _class_name(element)
                if bool(getattr(element, "ViewSpecific", False)) or "annotation" in category_type.lower():
                    continue
                category_value = category.Name if category is not None else None
                category_name = str(category_value) if category_value is not None else None
                if _is_coordinate_reference(class_name, category_name):
                    continue
                if category is None or "model" not in category_type.lower():
                    continue
                inspected = _inspect_physical(element, category_name, class_name,
                    LocationCurve, LocationPoint, linked_doc, "linked document internal coordinates")
                if inspected.get("geometry_inspection_status") == "inspected":
                    summary["physical_model_instances"] += 1
                    summary["physical_geometry_candidates"] += 1
                    summary["successfully_inspected"] += 1
                elif inspected.get("geometry_inspection_status") == "unavailable":
                    summary["unsupported"] += 1
                else:
                    failure_index = summary["failed"]
                    summary["failed"] += 1
                    if offset <= failure_index < offset + limit:
                        summary["failed_element_records"].append({
                            "failure_index": failure_index,
                            "element_id": inspected.get("element_id"),
                            "category": inspected.get("category"),
                            "class_name": inspected.get("class_name"),
                            "error": {"code": "linked_geometry_inspection_failed",
                                      "message": "No geometry evidence was available because one or more API reads failed."},
                            "warnings": inspected.get("warnings", [])})
            except Exception as exc:
                failure_index = summary["failed"]
                summary["failed"] += 1
                if offset <= failure_index < offset + limit:
                    summary["failed_element_records"].append({
                        "failure_index": failure_index,
                        "element_id": _id_text(getattr(element, "Id", None)),
                        "category": _safe_category_name(element),
                        "class_name": _class_name(element),
                        "error": {"code": "linked_element_inspection_failed",
                                  "exception_type": type(exc).__name__, "message": str(exc)[:200]}})
        summary["reported_total_elements"] = total
        summary["failure_has_more"] = summary["failed"] > offset + limit
        summary["is_complete"] = not summary["truncated"] and (total is None or summary["elements_examined"] >= total)
        summary["geometry_complete"] = (summary["is_complete"] and summary["unsupported"] == 0 and
                                          summary["failed"] == 0)
        if not summary["geometry_complete"]:
            summary["warnings"].append({"code": "linked_geometry_incomplete",
                "message": "Linked document enumeration or geometry extraction was incomplete."})
    except Exception as exc:
        summary["warnings"].append({"code": "linked_document_inspection_failed",
            "exception_type": type(exc).__name__})
    return summary


def _base_point_record(getter, doc, name, top_warnings):
    try:
        point = getter(doc)
        if point is None:
            raise ValueError("Revit API returned null")
        warnings = []
        record = {"element_id": _id_text(point.Id), "name": name,
            "position_internal": _point(point.Position, "Revit internal model coordinates"),
            "shared_position": _point(point.SharedPosition, "active ProjectLocation shared coordinates"),
            "clipped": _optional_bool(point, "Clipped", warnings), "warnings": warnings}
        return record
    except Exception as exc:
        top_warnings.append(_warning(name, exc))
        return None


def _project_position(position):
    east = float(position.EastWest)
    north = float(position.NorthSouth)
    elevation = float(position.Elevation)
    angle = float(position.Angle)
    return {"east_west_feet": east, "north_south_feet": north,
        "elevation_feet": elevation, "angle_radians": angle,
        "east_west_mm": east * _MM_PER_FOOT, "north_south_mm": north * _MM_PER_FOOT,
        "elevation_mm": elevation * _MM_PER_FOOT, "angle_degrees": angle * 180.0 / math.pi,
        "coordinate_reference": "active ProjectLocation coordinate report for the specified input point"}


def _transform(transform, direction="unverified"):
    frame = ("active ProjectLocation shared coordinates" if direction == "internal_to_shared" else
             ("Revit internal model coordinates" if direction == "shared_to_internal" else
              "unverified transform output coordinate frame"))
    return {"origin": _point(transform.Origin, frame), "basis_x": _vector(transform.BasisX),
        "basis_y": _vector(transform.BasisY), "basis_z": _vector(transform.BasisZ),
        "direction": direction}


def _point(point, coordinate_reference="Revit internal model coordinates"):
    if point is None:
        return None
    x, y, z = float(point.X), float(point.Y), float(point.Z)
    return {"raw_feet": {"x": x, "y": y, "z": z},
        "millimetres": {"x": x * _MM_PER_FOOT, "y": y * _MM_PER_FOOT, "z": z * _MM_PER_FOOT},
        "coordinate_reference": coordinate_reference}


def _vector(vector):
    if vector is None:
        return None
    return {"raw": {"x": float(vector.X), "y": float(vector.Y), "z": float(vector.Z)},
        "coordinate_reference": "unitless transform basis vector"}


def _near_xyz(left, right):
    try:
        return (abs(float(left.X) - float(right.X)) < 1e-7 and
                abs(float(left.Y) - float(right.Y)) < 1e-7 and
                abs(float(left.Z) - float(right.Z)) < 1e-7)
    except Exception:
        return False


def _base_element_record(element, category_name, class_name):
    return {"element_id": _id_text(element.Id), "unique_id": _safe_get(element, "UniqueId"),
        "type_id": None, "type_name": None, "family_name": None,
        "category": category_name, "class_name": class_name, "level": None,
        "classification_status": "not_inspected", "classification_warnings": [],
        "type_id_status": "not_inspected", "type_resolution_status": "not_inspected",
        "type_name_status": "not_inspected",
        "family_name_status": "not_inspected", "level_id_status": "not_inspected",
        "level_resolution_status": "not_inspected", "level_name_status": "not_inspected",
        "type_info_status": "not_inspected", "level_info_status": "not_inspected",
        "location_status": "not_inspected", "bounding_box_status": "not_inspected",
        "geometry_inspection_status": "not_inspected", "inspection_status": "not_inspected",
        "warnings": []}


def _safe_category_name(element):
    try:
        return _safe_get(element.Category, "Name")
    except Exception:
        return None


def _link_document_key(document):
    try:
        return str(document.GetHashCode())
    except Exception:
        return "{}|{}".format(_safe_get(document, "PathName"), _safe_get(document, "Title"))


def _link_load_status(element, host_doc):
    try:
        link_type = host_doc.GetElement(element.GetTypeId())
        if link_type is None:
            return {"status": "unavailable", "raw": None, "warning": "Revit link type was unavailable."}
        raw = link_type.GetLinkedFileStatus()
        value = str(raw)
        normalized = value.split(".")[-1].lower().replace("_", "").replace(" ", "")
        if normalized == "loaded":
            status = "loaded"
        elif normalized == "unloaded":
            status = "unloaded"
        elif normalized == "notfound":
            status = "not_found"
        elif normalized == "invalid":
            status = "invalid"
        elif normalized == "imported":
            status = "imported"
        else:
            status = "unknown"
        return {"status": status, "raw": value, "warning": None}
    except Exception as exc:
        return {"status": "unavailable", "raw": None,
            "warning": "{} while reading link file status".format(type(exc).__name__)}


def _safe_link_transform(element):
    try:
        result = _transform(element.GetTransform())
        result["direction"] = "unverified link-instance transform; not applied to linked geometry"
        result["origin"]["coordinate_reference"] = "link-instance transform origin; coordinate frame not independently verified"
        return result
    except Exception as exc:
        return {"direction": "unverified", "warning": _warning("link_instance_transform", exc)}


def _class_name(element):
    try:
        return str(element.GetType().Name)
    except Exception:
        try:
            return type(element).__name__
        except Exception:
            return None


def _append_page(items, record, index, offset, limit):
    if offset <= index < offset + limit:
        items.append(record)


def _is_detail_or_annotation(class_name):
    text = (class_name or "").lower()
    return any(token in text for token in ("detailcurve", "dimension", "tag", "textnote", "annotation", "independenttag"))


def _is_coordinate_reference(class_name, category_name):
    class_key = (class_name or "").lower()
    return class_key in ("internalorigin", "basepoint", "level", "grid", "referenceplane", "projectlocation")


def _is_model_curve_or_reference(class_name):
    name = (class_name or "").lower()
    return any(token in name for token in ("modelcurve", "curvebypoints", "sketchcurve", "sketchplane", "referenceplane", "grid"))


def _classification_record(element, category_name, class_name, classification, warning):
    record = _base_element_record(element, category_name, class_name)
    record.update({"classification": classification, "classification_status": "inspected",
                   "warning": warning})
    if classification in ("view_specific", "annotation_or_detail", "coordinate_reference",
                          "model_curve_or_reference_geometry", "link_instance"):
        record["geometry_inspection_status"] = "not_applicable"
        record["inspection_status"] = "not_applicable"
    return record


def _read_member(obj, name, field, warnings):
    """Read one member, distinguishing a missing member from a throwing getter."""
    try:
        return "inspected", getattr(obj, name)
    except AttributeError as exc:
        if _member_is_declared(obj, name) is False:
            return "unavailable", None
        warnings.append(_warning(field, exc))
        return "failed", None
    except Exception as exc:
        warnings.append(_warning(field, exc))
        return "failed", None


def _read_text_member(obj, name, field, warnings):
    status, value = _read_member(obj, name, field, warnings)
    if status != "inspected":
        return status, None
    if value is None:
        return "unavailable", None
    try:
        value = str(value)
    except Exception as exc:
        warnings.append(_warning(field, exc))
        return "failed", None
    return ("inspected", value) if value else ("unavailable", None)


def _read_family_fallback(type_element, warnings):
    family_status, family = _read_member(type_element, "Family", "family_name", warnings)
    if family_status != "inspected":
        return family_status, None
    if family is None:
        return "unavailable", None
    return _read_text_member(family, "Name", "family_name", warnings)


def _member_is_declared(obj, name):
    """Return True/False when Python or CLR metadata can establish member presence."""
    py_type = type(obj)
    try:
        instance_dict = getattr(obj, "__dict__", None)
        if instance_dict is not None and name in instance_dict:
            return True
    except Exception:
        pass
    try:
        for cls in py_type.__mro__:
            if name in getattr(cls, "__dict__", {}):
                return True
    except Exception:
        pass
    try:
        clr_type = obj.GetType()
        get_property = getattr(clr_type, "GetProperty", None)
        get_method = getattr(clr_type, "GetMethod", None)
        if get_property is not None or get_method is not None:
            if (get_property is not None and get_property(name) is not None) or (
                    get_method is not None and get_method(name) is not None):
                return True
            return False
    except Exception:
        return False if hasattr(py_type, "__mro__") else None
    # Pure Python fakes and wrappers without CLR reflection are fully described
    # by their class dictionaries; absence there is a genuine missing member.
    if hasattr(py_type, "__mro__"):
        return False
    return None


def _status_for_access_exception(obj, member, exc, field, warnings):
    if isinstance(exc, AttributeError) and _member_is_declared(obj, member) is False:
        return "unavailable"
    warnings.append(_warning(field, exc))
    return "failed"


def _aggregate_status(statuses):
    """Aggregate Type/Level fields using this shared precedence:

    failed; unavailable; inspected when every applicable field was inspected;
    not_applicable when all fields are not applicable; otherwise not_inspected.
    The last case also covers a mixed inspected/not-inspected aggregate because
    an applicable field remains unattempted. Granular statuses retain that detail.
    """
    if "failed" in statuses:
        return "failed"
    if "unavailable" in statuses:
        return "unavailable"
    applicable = [status for status in statuses if status != "not_applicable"]
    if applicable and all(status == "inspected" for status in applicable):
        return "inspected"
    if statuses and all(status == "not_applicable" for status in statuses):
        return "not_applicable"
    return "not_inspected"


def _optional_bool(obj, name, warnings):
    try:
        value = getattr(obj, name)
        return bool(value) if value is not None else None
    except Exception as exc:
        warnings.append(_warning(name, exc))
        return None


def _safe_get(obj, name):
    try:
        value = getattr(obj, name)
        return str(value) if value is not None else None
    except Exception:
        return None


def _id_text(element_id):
    try:
        try:
            return str(element_id.Value)
        except Exception:
            return str(element_id.IntegerValue)
    except Exception:
        return None


def _warning(field, exc):
    return {"field": field, "code": "api_access_failed", "exception_type": type(exc).__name__,
            "message": str(exc)[:200]}


def _identity_summary(identity):
    process_id = identity.get("process_id", identity.get("revit_process_id"))
    return {"document_title": identity.get("document_title"), "document_path": identity.get("document_path"),
        "revit_version": identity.get("revit_version"), "process_id": process_id,
        "revit_process_id": process_id, "process_session_id": identity.get("process_session_id"),
        "application_session_id": identity.get("application_session_id"),
        "document_session_id": identity.get("document_session_id"),
        "document_lifecycle_sequence": identity.get("document_lifecycle_sequence"),
        "document_lifecycle_generation": identity.get("document_lifecycle_generation"),
        "active_document_switch_counter": identity.get("active_document_switch_counter")}
