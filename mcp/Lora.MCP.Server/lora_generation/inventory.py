"""Read-only target document inventory over the existing, identity-checked RevitMCP client."""

from datetime import datetime, timezone

from lora_mcp.client import RevitMCPError


FAMILY_CATEGORIES = ("Doors", "Windows", "Structural Columns", "Structural Framing")


def read_generation_inventory(client):
    project = client.call("GET", "/project_info")
    identity = project.get("_lora_document_identity")
    diagnostics = client.call("GET", "/diagnostics/revit_state")
    statistics = client.call("POST", "/model/statistics", {
        "include_levels": True, "include_detailed_types": True, "top_n": 200})
    family_data = client.call("POST", "/families/types", {
        "category_names": list(FAMILY_CATEGORIES), "limit": 2000})
    schema_data = client.call("GET", "/schema/context")
    snapshots = [project, diagnostics, statistics, family_data, schema_data]
    wall_type_data = None
    wall_type_discovery_error = None
    wall_type_reader = getattr(client, "get_wall_types", None)
    if callable(wall_type_reader):
        try:
            wall_type_data = wall_type_reader()
            snapshots.append(wall_type_data)
        except RevitMCPError as exc:
            wall_type_discovery_error = exc.code
    level_data = None
    level_catalog_error = None
    level_reader = getattr(client, "get_level_catalog", None)
    if callable(level_reader):
        try:
            level_data = _read_complete_level_catalog(level_reader)
            if isinstance(level_data, dict) and level_data.get("status") == "success":
                snapshots.extend(level_data.pop("_identity_snapshots", []))
                snapshots.append(level_data)
            else:
                level_catalog_error = "invalid_level_catalog_response"
                level_data = None
        except RevitMCPError as exc:
            level_catalog_error = exc.code
    for result in snapshots:
        if result.get("_lora_document_identity") != identity:
            raise RevitMCPError("document_changed", "Active Revit document changed during generation inventory reads")

    families = family_data.get("family_types") or []
    categories = statistics.get("categories") or []
    system_types = {}
    for category in categories:
        name = category.get("category_name") or category.get("name")
        if not name:
            continue
        system_types[name] = {"types": [item.get("type_name") for item in (category.get("types") or [])
                                        if item.get("type_name")],
                              "types_total": category.get("types_total", category.get("type_count")),
                              "truncated": bool(category.get("types_truncated"))}
    if wall_type_data is not None:
        records = wall_type_data.get("wall_types") or []
        system_types["Walls"] = {"types": [item.get("name") for item in records],
            "wall_types": records, "types_total": wall_type_data.get("total", len(records)),
            "truncated": not bool(wall_type_data.get("complete", False)),
            "source": wall_type_data.get("source")}

    diagnostics_version = _first_value(diagnostics, (
        "revit_version", "application_version", "product_version", "version"))
    if diagnostics_version is None:
        for parent in (diagnostics.get("application"), diagnostics.get("process"), diagnostics.get("runtime")):
            if isinstance(parent, dict):
                diagnostics_version = _first_value(parent, ("revit_version", "application_version", "product_version", "version"))
                if diagnostics_version is not None:
                    break

    schema = schema_data.get("schema") or {}
    statistics_levels = statistics.get("levels") or []
    levels = statistics_levels
    level_names = schema.get("levels") or [item.get("level_name") for item in statistics_levels]
    level_ids_available = False
    level_catalog_status = "unavailable"
    if level_data is not None:
        catalog_records = level_data.get("levels") or []
        level_catalog_complete = (not level_data.get("truncated") and
            level_data.get("has_more") is False and
            len(catalog_records) == level_data.get("total_levels"))
        usable = [item for item in catalog_records if item.get("element_id_status") == "inspected" and
                  item.get("name_status") == "inspected" and item.get("element_id") and item.get("level_name")]
        by_name = {item["level_name"]: item for item in usable}
        if level_catalog_complete:
            statistics_by_name = {item.get("level_name"): item for item in statistics_levels
                                  if item.get("level_name")}
            levels = []
            for record in catalog_records:
                level = dict(statistics_by_name.get(record.get("level_name"), {}))
                level.update({"level_name": record.get("level_name"),
                    "level_id": record.get("element_id"),
                    "level_id_status": record.get("element_id_status"),
                    "level_name_status": record.get("name_status"),
                    "elevation_internal_feet": record.get("elevation_internal_feet"),
                    "elevation_mm": record.get("elevation_mm"),
                    "elevation_status": record.get("elevation_status")})
                levels.append(level)
        else:
            levels = [dict(item, level_id=by_name[item.get("level_name")]["element_id"],
                           level_id_status="inspected",
                           level_catalog_elevation_status=by_name[item.get("level_name")].get("elevation_status"))
                      if item.get("level_name") in by_name else dict(item, level_id=None,
                           level_id_status="unavailable") for item in statistics_levels]
        if level_catalog_complete and len(usable) == len(catalog_records):
            level_names = [item.get("level_name") for item in usable]
        level_ids_available = level_catalog_complete and len(usable) == len(catalog_records)
        level_catalog_status = "complete" if level_data.get("inventory_complete") else "partial"
    return {"status": "success", "inventory_source": "live_revitmcp", "verified_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "document_identity": identity,
            "document_title": diagnostics.get("document_state", {}).get("title") or project.get("project_name"),
            "document_path": diagnostics.get("document_state", {}).get("path") or project.get("file_path"),
            "revit_version": diagnostics_version,
            "application_version_availability": "unavailable_from_confirmed_read_routes" if diagnostics_version is None else "reported_by_diagnostics",
            "levels": levels, "level_names": level_names,
            "level_ids_available": level_ids_available,
            "level_catalog": level_data,
            "level_catalog_status": level_catalog_status,
            "family_types": families, "family_types_total": family_data.get("total_count", len(families)),
            "family_types_truncated": bool(family_data.get("truncated")),
            "system_types_by_category": system_types,
            "system_type_catalog_scope": ("complete Revit WallType elements from Lora sidecar plus observed instance-used system types"
                if wall_type_data is not None else "types observed in model statistics (instance-used types only); complete system type enumeration unavailable"),
            "wall_type_discovery_status": "complete" if wall_type_data is not None else "unavailable",
            "categories": schema.get("document_categories") or [],
            "parameter_names": schema.get("parameter_names") or [],
            "project_base_point": {"status": "unavailable", "value": None,
                                   "reason": "No confirmed read-only RevitMCP route exposes Project Base Point coordinates."},
            "survey_point": {"status": "unavailable", "value": None,
                              "reason": "No confirmed read-only RevitMCP route exposes Survey Point coordinates."},
            "statistics_complete": not statistics.get("categories_truncated", False),
            "warnings": (["Loaded family symbol list is truncated."] if family_data.get("truncated") else []) +
                        (["System type list is based on placed instances and is not an exhaustive type catalog."] if wall_type_data is None else []) +
                        (["Lora Revit-side WallType discovery route is unavailable: {}.".format(wall_type_discovery_error)] if wall_type_discovery_error else []) +
                        (["Lora Revit-side Level catalog is unavailable: {}.".format(level_catalog_error)] if level_catalog_error else []) +
                        (["Lora Revit-side Level catalog is incomplete; exact Level IDs are not considered globally available."]
                         if level_data is not None and not level_ids_available else [])}


def _read_complete_level_catalog(reader, page_size=100, max_pages=1000):
    """Read all bounded Level pages, refusing unstable totals or identity changes."""
    records, failures, warnings, identity_snapshots = [], [], [], []
    offset = 0
    expected_total = None
    expected_failures = None
    page_count = 0
    while True:
        page = reader(offset=offset, limit=page_size, failure_offset=0,
                      failure_limit=100)
        if not isinstance(page, dict) or page.get("status") != "success":
            return page
        page_count += 1
        if page_count > max_pages:
            raise RevitMCPError("level_catalog_page_limit", "Level catalog exceeded the safe page limit")
        if expected_total is None:
            expected_total = page.get("total_levels")
            expected_failures = page.get("total_failures")
        elif (page.get("total_levels") != expected_total or
              page.get("total_failures") != expected_failures):
            raise RevitMCPError("level_catalog_changed", "Level catalog totals changed during pagination")
        identity_snapshots.append(page)
        records.extend(page.get("levels") or [])
        warnings.extend(page.get("warnings") or [])
        if page.get("has_more") is not True:
            if page.get("has_more") is None:
                raise RevitMCPError("level_catalog_incomplete", "Level catalog pagination completeness is unknown")
            break
        offset += len(page.get("levels") or [])
        if not page.get("levels"):
            raise RevitMCPError("level_catalog_pagination_stalled", "Level catalog returned has_more without records")
    if len(records) != expected_total:
        raise RevitMCPError("level_catalog_incomplete", "Level catalog page count does not match its total")
    failure_offset = 0
    failure_page_count = 0
    while failure_offset < (expected_failures or 0):
        page = reader(offset=0, limit=1, failure_offset=failure_offset,
                      failure_limit=100)
        if not isinstance(page, dict) or page.get("status") != "success":
            return page
        failure_page_count += 1
        if failure_page_count > max_pages:
            raise RevitMCPError("level_catalog_failure_page_limit",
                                "Level catalog exceeded the safe failure-page limit")
        if page.get("total_levels") != expected_total or page.get("total_failures") != expected_failures:
            raise RevitMCPError("level_catalog_changed", "Level catalog totals changed during failure pagination")
        identity_snapshots.append(page)
        next_failures = page.get("failures") or []
        if not next_failures:
            raise RevitMCPError("level_catalog_pagination_stalled", "Failure pagination returned no records before its total")
        failures.extend(next_failures)
        failure_offset += len(next_failures)
    first = identity_snapshots[0]
    first.update({"levels": records, "offset": 0, "limit": len(records),
                  "has_more": False, "failures": failures, "failure_offset": 0,
                  "failure_limit": len(failures), "failure_has_more": False,
                  "warnings": warnings,
                  "_identity_snapshots": identity_snapshots})
    return first


def _first_value(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None
