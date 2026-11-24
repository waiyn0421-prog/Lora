# -*- coding: utf-8 -*-
"""Read-only, identity-guarded Revit Level catalog for planning and diagnostics."""

from . import coordinate_geometry

_MAX_PAGE_SIZE = 100
_MAX_OFFSET = 10000000
_MAX_SCAN = 100000
_MM_PER_FOOT = 304.8


def level_catalog(runtime, uiapp, doc, offset=0, limit=100,
                  failure_offset=0, failure_limit=50):
    """Return exact Level IDs, names, and elevations with independent failure paging."""
    if not _valid_page(offset, limit) or not _valid_page(failure_offset, failure_limit):
        return {"status": "error", "error": "invalid_pagination",
                "maximum_page_size": _MAX_PAGE_SIZE}
    try:
        identity = runtime.identity_response(uiapp, doc)
        if identity.get("status") != "success":
            return {"status": "error", "error": "document_identity_unavailable"}
        from Autodesk.Revit.DB import FilteredElementCollector, Level

        collector = (FilteredElementCollector(doc).OfClass(Level)
                     .WhereElementIsNotElementType())
        reported_total = None
        try:
            reported_total = int(collector.GetElementCount())
        except Exception:
            pass

        records = []
        failures = []
        failure_total = 0
        enumeration_failure = None
        truncated = False
        for level in collector:
            if len(records) >= _MAX_SCAN:
                truncated = True
                break
            record = {"element_id": None, "level_name": None,
                      "elevation_internal_feet": None, "elevation_mm": None,
                      "element_id_status": "not_inspected", "name_status": "not_inspected",
                      "elevation_status": "not_inspected", "warnings": []}
            for member, field, value_key, status_key in (
                    ("Id", "element_id", "element_id", "element_id_status"),
                    ("Name", "level_name", "level_name", "name_status")):
                try:
                    value = getattr(level, member)
                    if member == "Id":
                        value = _element_id_text(value)
                        if value in (None, "-1", "-1000000"):
                            record[status_key] = "unavailable"
                        else:
                            record[value_key] = value
                            record[status_key] = "inspected"
                    elif value in (None, ""):
                        record[status_key] = "unavailable"
                    else:
                        record[value_key] = str(value)
                        record[status_key] = "inspected"
                except Exception as exc:
                    _record_field_failure(record, failures, failure_total,
                                          field, status_key, exc,
                                          failure_offset, failure_limit)
                    failure_total += 1
            try:
                elevation = float(level.Elevation)
                if _finite(elevation):
                    record["elevation_internal_feet"] = elevation
                    record["elevation_mm"] = elevation * _MM_PER_FOOT
                    record["elevation_status"] = "inspected"
                else:
                    record["elevation_status"] = "unavailable"
            except Exception as exc:
                _record_field_failure(record, failures, failure_total,
                                      "elevation", "elevation_status", exc,
                                      failure_offset, failure_limit)
                failure_total += 1
            records.append(record)

        total = len(records)
        items = records[offset:offset + limit]
        if reported_total is not None and reported_total != total and not truncated:
            enumeration_failure = {"failure_index": failure_total,
                "element_id": None, "field": "level_enumeration",
                "error": {"exception_type": "CountMismatch",
                          "message": "Collector count differed from enumerated Level records."}}
            failure_total += 1
            if failure_offset <= enumeration_failure["failure_index"] < failure_offset + failure_limit:
                failures.append(enumeration_failure)
        values_complete = all(record["element_id_status"] == "inspected" and
                              record["name_status"] == "inspected" and
                              record["elevation_status"] == "inspected" for record in records)
        complete = (not truncated and enumeration_failure is None and failure_total == 0 and
                    values_complete and (reported_total is None or reported_total == total))
        warnings = []
        for record in records:
            for warning in record["warnings"]:
                warning_record = {"element_id": record.get("element_id")}
                warning_record.update(warning)
                warnings.append(warning_record)
        page_known = not truncated and enumeration_failure is None
        return {"status": "success",
            "document_identity": coordinate_geometry._identity_summary(identity.get("identity") or {}),
            "levels": items, "offset": offset, "limit": limit,
            "total_levels": total, "reported_total_levels": reported_total,
            "has_more": (None if not page_known else offset + len(items) < total),
            "inventory_complete": complete, "truncated": truncated,
            "failure_offset": failure_offset, "failure_limit": failure_limit,
            "total_failures": failure_total, "failures": failures,
            "failure_has_more": failure_offset + len(failures) < failure_total,
            "warnings": warnings,
            "units": {"elevation_internal": "feet", "elevation_display": "millimetres",
                      "conversion": "internal feet × 304.8 = millimetres"},
            "clearance_status": "unverified"}
    except Exception as exc:
        return {"status": "error", "error": "level_catalog_failed",
                "diagnostic": {"exception_type": type(exc).__name__,
                               "message": str(exc)[:200]}}


def _record_field_failure(record, failures, failure_total, field,
                          status_key, exc, offset, limit):
    record[status_key] = "failed"
    warning = coordinate_geometry._warning(field, exc)
    record["warnings"].append(warning)
    if offset <= failure_total < offset + limit:
        failures.append({"failure_index": failure_total,
            "element_id": record.get("element_id"), "field": field,
            "error": {"exception_type": type(exc).__name__,
                      "message": str(exc)[:200]}})


def _element_id_text(element_id):
    try:
        return str(element_id.Value)
    except Exception:
        return str(element_id.IntegerValue)


def _valid_page(offset, limit):
    return (not isinstance(offset, bool) and isinstance(offset, int) and
            0 <= offset <= _MAX_OFFSET and not isinstance(limit, bool) and
            isinstance(limit, int) and 1 <= limit <= _MAX_PAGE_SIZE)


def _finite(value):
    return value == value and value not in (float("inf"), float("-inf"))
