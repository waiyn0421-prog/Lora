"""Deterministic plan builder and validator. It never calls a Revit write operation."""

import copy
import math
import uuid

from .errors import GenerationPlanError
from .repository import GenerationPlanRepository


_FEET_PER_UNIT = {"mm": 1.0 / 304.8, "millimeters": 1.0 / 304.8,
                  "cm": 1.0 / 30.48, "m": 1.0 / 0.3048,
                  "in": 1.0 / 12.0, "ft": 1.0}
_OPERATIONS = {"Walls": "create_wall", "Doors": "create_hosted_door",
               "Windows": "create_hosted_window", "Columns": "create_column",
               "Rooms": "place_room", "Levels": "create_level", "Grids": "create_grid"}


class GenerationPlanner:
    def __init__(self, review_manager, repository=None):
        self.review_manager = review_manager
        self.repository = repository or GenerationPlanRepository()

    def create(self, review_id, inventory, mappings=None, coordinate_mapping=None):
        session = self.review_manager.session(review_id)["session"]
        all_items = _all_review_items(self.review_manager, review_id)
        approved = [item for item in all_items if item.get("status") == "approved"]
        explicit = session.get("source_kind") == "explicit_geometry"
        eligible = lambda item: _review_geometry_is_current(item, explicit)
        excluded = [{"review_item_id": item["item_id"], "page_number": item.get("page_number"),
                     "status": item.get("status"), "reason": _excluded_reason(item, explicit)}
                    for item in all_items if item.get("status") != "approved" or not eligible(item)]
        map_data = _validate_mappings(mappings or {}, {item["item_id"] for item in approved})
        if explicit and (session.get("coordinate_system") or {}).get("name") == "revit_internal":
            coordinate = {"status": "user_confirmed_project_mapping", "value": {
                "user_confirmed": True, "origin_internal_feet": [0.0, 0.0, 0.0],
                "rotation_degrees": 0.0, "basis": "explicitly user-confirmed Revit internal coordinate system"}}
        else:
            coordinate = _normalize_coordinate_mapping(coordinate_mapping)
        plan_id = str(uuid.uuid4())
        items = [_build_item(item, map_data.get(item["item_id"], {}), inventory, coordinate)
                 for item in approved if eligible(item)]
        plan = {"plan_schema_version": 1, "source_review_id": review_id,
                "source_review_title": session.get("title"), "plan_id": plan_id,
                "source_kind": session.get("source_kind", "drawing"),
                "source_drawing": session.get("source_file"),
                "source_processing_id": session.get("processing_id"),
                "source_pages": sorted({item.get("page_number") for item in all_items
                                         if isinstance(item.get("page_number"), int) and item.get("page_number") > 0}),
                "target_revit_document_identity": copy.deepcopy(inventory.get("document_identity")) if inventory.get("status") == "success" else None,
                "target_revit_document_title": inventory.get("document_title"),
                "target_revit_document_path": inventory.get("document_path"),
                "revit_version": inventory.get("revit_version"),
                "revit_version_availability": inventory.get("application_version_availability", "unavailable"),
                "source_coordinate_system": ({"description": "Explicit user-supplied geometry; no drawing page or calibration is asserted",
                    "coordinate_system": session.get("coordinate_system")} if explicit else
                    {"description": "Page-specific calibrated local architectural coordinates",
                    "pages": session.get("calibrations_by_page", {})}),
                "target_coordinate_system": {"name": "Revit internal coordinates", "unit": "internal feet",
                    "mapping_status": coordinate["status"], "user_mapping": coordinate.get("value"),
                    "transformation_matrix_4x4": _coordinate_matrix(coordinate),
                    "project_base_point": copy.deepcopy(inventory.get("project_base_point")),
                    "survey_point": copy.deepcopy(inventory.get("survey_point"))},
                "unit_conversion": {"calibrated_units_to_internal_feet": copy.deepcopy(_FEET_PER_UNIT),
                    "status": "exact conversion for supported units; placement origin is separate"},
                "approved_review_item_ids": [item["item_id"] for item in approved
                                              if eligible(item)],
                "items": items, "unresolved_items": excluded,
                "validation": {"errors": [], "warnings": []},
                "inventory_snapshot": _inventory_snapshot(inventory),
                "plan_status": "blocked", "creation_timestamp": None,
                "execution_supported": False,
                "restriction": "Planning only. This plan cannot create, modify, delete, or transact Revit elements."}
        self._validate(plan, inventory)
        return self.repository.create(plan)

    def get(self, plan_id):
        plan = self.repository.get(plan_id)
        plan["history"] = self.repository.history(plan_id, limit=200)
        return plan

    def items(self, plan_id, category=None, status=None, offset=0, limit=100):
        _pagination(offset, limit)
        plan = self.repository.get(plan_id)
        records = plan["items"]
        if category:
            records = [item for item in records if item["planned_category"].casefold() == category.casefold()]
        if status:
            records = [item for item in records if item["plan_status"] == status]
        return {"total": len(records), "offset": offset, "limit": limit,
                "items": records[offset:offset + limit],
                "truncated": offset + limit < len(records),
                "unresolved_items": plan["unresolved_items"]}

    def update_mapping(self, plan_id, review_item_id, mapping, inventory):
        if not isinstance(mapping, dict):
            raise GenerationPlanError("invalid_mapping", "mapping must be an object")
        plan = self.repository.get(plan_id)
        target = next((item for item in plan["items"] if item["review_item_id"] == review_item_id), None)
        if target is None:
            raise GenerationPlanError("plan_item_not_found", "Review item is not included in this plan")
        validated = _validate_mapping(mapping)
        old = copy.deepcopy(target.get("mapping", {}))

        def mutate(data):
            item = next(x for x in data["items"] if x["review_item_id"] == review_item_id)
            item["mapping"] = copy.deepcopy(validated)
            item["parameter_assignments"] = copy.deepcopy(validated.get("parameter_assignments", {}))
            _apply_dimension_mapping(item.get("geometry") or {}, validated.get("dimensions", {}), None)
            item["dependencies"] = _dependencies(item["planned_category"], validated)
            self._validate(data, inventory)
        return self.repository.mutate(plan_id, mutate, {"action": "mapping_updated",
            "review_item_id": review_item_id, "before": old, "after": validated,
            "target_document_identity": inventory.get("document_identity")})

    def validate(self, plan_id, inventory):
        def mutate(plan):
            self._validate(plan, inventory)
        return self.repository.mutate(plan_id, mutate,
            {"action": "plan_revalidated", "target_document_identity": inventory.get("document_identity")})

    def validate_snapshot(self, plan, inventory):
        """Run the existing validator on an in-memory copy without changing the stored plan."""
        snapshot = copy.deepcopy(plan)
        self._validate(snapshot, inventory)
        return snapshot

    def summary(self, plan_id):
        plan = self.repository.get(plan_id)
        counts = {}
        errors = warnings = 0
        for item in plan["items"]:
            status = item["plan_status"]
            counts[status] = counts.get(status, 0) + 1
            errors += len(item.get("validation", {}).get("errors", []))
            warnings += len(item.get("validation", {}).get("warnings", []))
        return {"plan_id": plan_id, "plan_status": plan["plan_status"],
                "source_review_id": plan["source_review_id"],
                "target_document_title": plan.get("target_revit_document_title"),
                "approved_source_item_count": len(plan["approved_review_item_ids"]),
                "planned_item_count": len(plan["items"]), "unresolved_item_count": len(plan["unresolved_items"]),
                "counts_by_plan_status": counts,
                "error_count": len(plan["validation"]["errors"]) + errors,
                "warning_count": len(plan["validation"]["warnings"]) + warnings,
                "execution_supported": False}

    def _validate(self, plan, inventory):
        errors, warnings = [], []
        current_review_items = {item["item_id"]: item for item in
                                _all_review_items(self.review_manager, plan["source_review_id"])}
        if inventory.get("status") != "success":
            errors.append(_issue("target_document_unavailable", "Read-only Revit inventory is unavailable."))
        else:
            old = plan.get("target_revit_document_identity")
            current = inventory.get("document_identity")
            if old and not _same_document_identity(old, current):
                errors.append(_issue("target_document_changed", "Active Revit document identity differs from the plan snapshot."))
        if plan["target_coordinate_system"]["mapping_status"] != "user_confirmed_project_mapping":
            warnings.append(_issue("coordinate_origin_unmapped", "Calibrated geometry is local; no user-confirmed mapping to Revit internal coordinates was supplied."))
        if not inventory.get("revit_version"):
            warnings.append(_issue("revit_version_unavailable", "Revit application version was not reported by confirmed read-only routes."))
        if inventory.get("warnings"):
            warnings.extend(_issue("inventory_limitation", warning) for warning in inventory["warnings"])
        for unresolved in plan.get("unresolved_items", []):
            code = ("approval_missing" if unresolved.get("status") != "approved" else
                    "explicit_geometry_approval_stale" if unresolved.get("reason", "").startswith("explicit_geometry") else
                    "calibration_stale")
            errors.append(dict(_issue(code, "Source review item {} was excluded: {}.".format(
                unresolved.get("review_item_id"), unresolved.get("reason"))),
                review_item_id=unresolved.get("review_item_id"),
                review_status=unresolved.get("status")))

        for item in plan["items"]:
            item.pop("revit_level_mapping", None)
            item_errors, item_warnings = _validate_item(item, inventory, plan["target_coordinate_system"])
            source_item = current_review_items.get(item["review_item_id"])
            if source_item is None:
                item_errors.append(_issue("review_item_missing", "Source review item no longer exists."))
            else:
                if source_item.get("status") != "approved":
                    item_errors.append(_issue("approval_missing", "Source review item is no longer approved."))
                if item.get("geometry_source") == "explicit_user_geometry":
                    from lora_review.manager import _explicit_geometry_fingerprint
                    current_geometry = (source_item.get("current_geometry") or {}).get("explicit_user_geometry")
                    fingerprint = _explicit_geometry_fingerprint(current_geometry)
                    approval = source_item.get("explicit_geometry_approval") or {}
                    if (source_item.get("explicit_geometry_status") != "confirmed" or
                            fingerprint != item.get("explicit_geometry_fingerprint") or
                            approval.get("fingerprint") != fingerprint):
                        item_errors.append(_issue("explicit_geometry_approval_stale", "Explicit geometry approval is missing or no longer matches current geometry."))
                else:
                    if source_item.get("calibration_state") != "current":
                        item_errors.append(_issue("calibration_stale", "Source calibration is no longer current."))
                    if source_item.get("calibration_fingerprint") != item.get("calibration_fingerprint"):
                        item_errors.append(_issue("calibration_changed", "Source calibration fingerprint differs from the planned snapshot."))
            item["validation"] = {"errors": item_errors, "warnings": item_warnings}
            item["plan_status"] = "blocked" if item_errors else ("planned_with_warnings" if item_warnings else "planned")
            errors.extend(dict(issue, review_item_id=item["review_item_id"]) for issue in item_errors)
            warnings.extend(dict(issue, review_item_id=item["review_item_id"]) for issue in item_warnings)

        duplicates = _duplicate_pairs(plan["items"])
        for first, second in duplicates:
            for item in (first, second):
                issue = _issue("duplicate_geometry", "Plan geometry duplicates review item {}.".format(
                    second["review_item_id"] if item is first else first["review_item_id"]))
                item["validation"]["errors"].append(issue)
                item["plan_status"] = "blocked"
                errors.append(dict(issue, review_item_id=item["review_item_id"]))

        item_by_review_id = {item["review_item_id"]: item for item in plan["items"]}
        for item in plan["items"]:
            if item["planned_category"] not in ("Doors", "Windows"):
                continue
            host_id = item.get("mapping", {}).get("host_review_item_id")
            host = item_by_review_id.get(host_id)
            if host_id and (host is None or host.get("planned_category") != "Walls"):
                issue = _issue("host_dependency_invalid", "Mapped host must be an included planned Wall item.")
                item["validation"]["errors"].append(issue)
                item["plan_status"] = "blocked"
                errors.append(dict(issue, review_item_id=item["review_item_id"]))
            elif host is not None:
                dependency = next((dep for dep in item["dependencies"] if dep["kind"] == "host_wall"), None)
                if dependency:
                    dependency["status"] = "mapped"
                    dependency["plan_item_id"] = host["plan_item_id"]
        order = {"Levels": 0, "Grids": 1, "Walls": 2, "Columns": 3,
                 "Doors": 4, "Windows": 4, "Rooms": 5}
        plan["dependency_order"] = [item["plan_item_id"] for item in sorted(
            plan["items"], key=lambda value: (order.get(value["planned_category"], 99), value["plan_item_id"]))]

        plan["validation"] = {"errors": errors, "warnings": warnings,
            "validated_against_document_identity": inventory.get("document_identity"),
            "inventory_status": inventory.get("status"), "validated_item_count": len(plan["items"])}
        plan["inventory_snapshot"] = _inventory_snapshot(inventory)
        plan["target_revit_document_identity"] = copy.deepcopy(inventory.get("document_identity"))
        plan["target_revit_document_title"] = inventory.get("document_title")
        plan["target_revit_document_path"] = inventory.get("document_path")
        plan["revit_version"] = inventory.get("revit_version")
        plan["revit_version_availability"] = inventory.get("application_version_availability", "unavailable")
        plan["plan_status"] = "blocked" if errors or plan["unresolved_items"] else "ready_for_review"
        plan["creation_timestamp"] = plan.get("creation_timestamp")


def _build_item(review_item, mapping, inventory, coordinate):
    category = review_item.get("category")
    operation = _OPERATIONS.get(category)
    explicit_geometry = (review_item.get("current_geometry") or {}).get("explicit_user_geometry")
    calibrated = review_item.get("current_geometry", {}).get("calibrated")
    units = calibrated.get("coordinate_units") if calibrated else None
    scale = _FEET_PER_UNIT.get(str(units).lower()) if units else None
    geometry = (_explicit_geometry_for(explicit_geometry, coordinate) if explicit_geometry else
                _geometry_for(review_item, calibrated, scale, coordinate))
    _apply_dimension_mapping(geometry, mapping.get("dimensions", {}), scale)
    dependencies = _dependencies(category, mapping)
    return {"plan_item_id": str(uuid.uuid4()), "review_item_id": review_item["item_id"],
            "source_drawing": review_item.get("source_file"), "source_page": review_item.get("page_number")
                if isinstance(review_item.get("page_number"), int) and review_item.get("page_number") > 0 else None,
            "source_geometry_references": copy.deepcopy(review_item.get("original_geometry_references", [])),
            "calibration_id": review_item.get("calibration_id"),
            "calibration_fingerprint": review_item.get("calibration_fingerprint"),
            "recognition_type": review_item.get("recognition_type"),
            "recognition_evidence": copy.deepcopy(review_item.get("recognition_evidence", [])),
            "confidence": review_item.get("confidence"), "review_status": review_item.get("status"),
            "planned_category": category or "Other", "planned_operation": operation,
            "geometry_source": "explicit_user_geometry" if explicit_geometry else "calibrated_drawing_geometry",
            "explicit_geometry_fingerprint": review_item.get("explicit_geometry_fingerprint"),
            "explicit_geometry_approval": copy.deepcopy(review_item.get("explicit_geometry_approval")),
            "geometry": geometry, "mapping": copy.deepcopy(mapping),
            "dependencies": dependencies, "parameter_assignments": copy.deepcopy(mapping.get("parameter_assignments", {})),
            "warnings": copy.deepcopy(review_item.get("warnings", [])),
            "validation": {"errors": [], "warnings": []}, "plan_status": "blocked"}


def _dependencies(category, mapping):
    dependencies = []
    if category in ("Walls", "Doors", "Windows", "Columns", "Rooms"):
        if mapping.get("level_name"):
            dependencies.append({"kind": "level", "name": mapping["level_name"], "status": "mapped"})
        else:
            dependencies.append({"kind": "level", "name": None, "status": "unresolved"})
    if category in ("Walls", "Doors", "Windows", "Columns"):
        dependencies.append({"kind": "family_or_type", "status": "mapped" if mapping.get("type_name") else "unresolved"})
    if category in ("Doors", "Windows"):
        dependencies.append({"kind": "host_wall", "review_item_id": mapping.get("host_review_item_id"),
                             "status": "mapped" if mapping.get("host_review_item_id") else "unresolved"})
    return dependencies


def _validate_item(item, inventory, coordinate_system):
    errors, warnings = [], []
    category = item["planned_category"]
    if item.get("review_status") != "approved":
        errors.append(_issue("approval_missing", "Source review item is not approved."))
    explicit = item.get("geometry_source") == "explicit_user_geometry"
    if explicit:
        if not item.get("explicit_geometry_fingerprint") or not item.get("explicit_geometry_approval"):
            errors.append(_issue("explicit_geometry_approval_missing", "Explicit geometry requires persistent user confirmation and approval."))
    elif not item.get("calibration_id") or not item.get("calibration_fingerprint"):
        errors.append(_issue("calibration_missing", "A current calibration reference is required."))
    if item.get("planned_operation") is None:
        errors.append(_issue("unsupported_category", "No generation planning operation is implemented for this review category."))
        return errors, warnings
    if category in ("Walls", "Doors", "Windows", "Columns", "Rooms"):
        level_name = item.get("mapping", {}).get("level_name")
        available = inventory.get("level_names") or []
        if not level_name:
            errors.append(_issue("level_mapping_missing", "Explicit level mapping is required."))
        elif level_name not in available:
            errors.append(_issue("level_unavailable", "Mapped level is not present in the active Revit document."))
        else:
            level_id = item.get("mapping", {}).get("level_id")
            if inventory.get("level_ids_available"):
                matching_levels = [level for level in (inventory.get("levels") or [])
                                   if level.get("level_name") == level_name]
                exact_level = next((level for level in matching_levels
                                    if str(level.get("level_id")) == str(level_id)), None)
                if level_id and exact_level is None:
                    errors.append(_issue("level_id_mapping_mismatch",
                        "Mapped Level ID does not identify the selected Level name in the active document."))
                elif exact_level is not None:
                    item["revit_level_mapping"] = {"element_id": str(exact_level.get("level_id")),
                        "level_name": exact_level.get("level_name"),
                        "elevation_internal_feet": exact_level.get("elevation_internal_feet"),
                        "verification": "matched_by_level_catalog_id_and_name"}
                elif category != "Walls":
                    warnings.append(_issue("level_id_mapping_missing",
                        "The Level ID is available from the live catalog but was not selected in the mapping."))
            else:
                warnings.append(_issue("level_id_unavailable", "The read-only Level catalog is unavailable or incomplete; RevitMCP exposes level names/elevations but not IDs."))
        if category == "Walls" and not item.get("mapping", {}).get("level_id"):
            errors.append(_issue("wall_level_id_mapping_missing", "Wall execution requires an explicit Revit Level element ID mapping."))
    if category in ("Walls", "Doors", "Windows", "Columns"):
        _validate_type_mapping(item, inventory, errors, warnings)
    _validate_geometry(category, item.get("geometry") or {}, errors, warnings)
    if category in ("Doors", "Windows"):
        host_id = item.get("mapping", {}).get("host_review_item_id")
        if not host_id:
            errors.append(_issue("host_dependency_missing", "Door/window requires an explicit approved host wall mapping."))
        width = item["geometry"].get("dimensions", {}).get("width_internal_feet")
        height = item["geometry"].get("dimensions", {}).get("height_internal_feet")
        if not _positive(width):
            errors.append(_issue("opening_width_invalid", "Door/window opening width is unavailable or invalid."))
        if not _positive(height):
            errors.append(_issue("opening_height_invalid", "Door/window height is unavailable or invalid."))
    if category == "Grids" and not item.get("mapping", {}).get("grid_name"):
        errors.append(_issue("grid_label_missing", "Grid candidate needs a user-confirmed Revit grid name."))
    assignments = item.get("parameter_assignments") or {}
    available_parameters = set(inventory.get("parameter_names") or [])
    for parameter_name in assignments:
        if parameter_name not in available_parameters:
            warnings.append(_issue("parameter_not_in_schema_snapshot",
                                   "Assigned parameter is not present in the returned schema parameter-name snapshot."))
    if assignments:
        warnings.append(_issue("parameter_scope_unverified",
                               "Read-only schema data does not establish target type/instance scope or writability."))
    if inventory.get("status") != "success":
        errors.append(_issue("target_inventory_unavailable", "Target types and levels cannot be validated without a live read-only inventory."))
    if coordinate_system.get("mapping_status") != "user_confirmed_project_mapping":
        warnings.append(_issue("placement_coordinates_local_only", "Internal-foot geometry is local to the calibrated drawing origin; project placement remains unresolved."))
    return errors, warnings


def _validate_type_mapping(item, inventory, errors, warnings):
    category = item["planned_category"]
    mapping = item.get("mapping", {})
    family, name, symbol_id = mapping.get("family_name"), mapping.get("type_name"), mapping.get("symbol_id")
    if not name:
        errors.append(_issue("type_mapping_missing", "An explicit existing Revit type mapping is required."))
        return
    if category == "Walls":
        catalog = inventory.get("system_types_by_category", {}).get("Walls")
        if not catalog:
            errors.append(_issue("wall_type_catalog_unavailable", "No wall system type inventory was returned."))
        elif name not in catalog.get("types", []):
            errors.append(_issue("revit_type_unavailable", "Mapped wall type is not in the observed system type inventory."))
        elif catalog.get("truncated"):
            errors.append(_issue("wall_type_inventory_truncated", "Wall type inventory is truncated; exact availability is not verified."))
        else:
            actual = [value for value in catalog.get("wall_types", []) if value.get("name") == name]
            requested_id = mapping.get("wall_type_id")
            matches = [value for value in actual if not requested_id or str(value.get("element_id")) == str(requested_id)]
            if actual and len(matches) != 1:
                errors.append(_issue("wall_type_mapping_ambiguous", "Choose one exact WallType element ID from the live catalog."))
            elif actual:
                exact = matches[0]
                item["revit_type_mapping"] = {"category": "Walls", "type_name": exact.get("name"),
                    "type_id": str(exact.get("element_id")), "wall_kind": exact.get("wall_kind"),
                    "width_internal_feet": exact.get("width_internal_feet"),
                    "function": exact.get("function"), "type_id_availability": "verified_by_walltype_route"}
                if not item.get("geometry", {}).get("dimensions", {}).get("thickness_internal_feet"):
                    item["geometry"].setdefault("dimensions", {})["thickness_internal_feet"] = exact.get("width_internal_feet")
                    item["geometry"]["local_geometry_internal_feet"].setdefault("dimensions", {})["thickness_internal_feet"] = exact.get("width_internal_feet")
            else:
                item["revit_type_mapping"] = {"category": "Walls", "type_name": name,
                    "type_id": None, "type_id_availability": "not_reported_by_read_route"}
        return
    symbols = [symbol for symbol in (inventory.get("family_types") or [])
               if symbol.get("category") == {"Doors": "Doors", "Windows": "Windows",
                                              "Columns": "Structural Columns"}.get(category)]
    matches = [symbol for symbol in symbols if symbol.get("type_name") == name and
               (not family or symbol.get("family_name") == family) and
               (not symbol_id or str(symbol.get("symbol_id")) == str(symbol_id))]
    if not symbol_id and len(matches) != 1:
        errors.append(_issue("type_mapping_ambiguous", "Select a unique family symbol ID; family/type names alone do not identify one symbol."))
    elif not matches:
        errors.append(_issue("revit_type_unavailable", "Mapped family/type/symbol is not in the active document inventory."))
    else:
        exact = matches[0]
        item["revit_type_mapping"] = {"category": exact.get("category"),
            "family_name": exact.get("family_name"), "type_name": exact.get("type_name"),
            "symbol_id": exact.get("symbol_id"), "is_active": exact.get("is_active")}
        if exact.get("is_active") is False:
            warnings.append(_issue("family_symbol_inactive", "Matched family symbol is inactive and may require activation before future placement."))
    if inventory.get("family_types_truncated"):
        errors.append(_issue("family_type_inventory_truncated", "Loaded family symbol list is truncated; type availability is uncertain."))


def _validate_geometry(category, geometry, errors, warnings):
    local = geometry.get("local_geometry_internal_feet") or {}
    if category == "Walls":
        line = local.get("centerline_internal_feet")
        if not isinstance(line, list) or len(line) < 2 or _distance(line[0], line[-1]) <= 1e-9:
            errors.append(_issue("wall_geometry_invalid", "Wall requires a non-zero calibrated centreline."))
        declared_length = geometry.get("dimensions", {}).get("length_internal_feet")
        if declared_length is not None and (not _positive(declared_length) or
                abs(_distance(line[0], line[-1]) - float(declared_length)) > max(0.5 / 304.8, float(declared_length) * 1e-6)):
            errors.append(_issue("wall_length_mismatch", "Explicit length does not match the reviewed straight line."))
        thickness = geometry.get("dimensions", {}).get("thickness_internal_feet")
        if not _positive(thickness):
            errors.append(_issue("wall_thickness_invalid", "Wall thickness must be a positive calibrated value."))
    elif category in ("Doors", "Windows", "Columns"):
        if not local.get("position_internal_feet"):
            errors.append(_issue("placement_geometry_missing", "Calibrated placement point is unavailable."))
    elif category == "Rooms":
        polygon = local.get("boundary_internal_feet")
        if not isinstance(polygon, list) or len(polygon) < 4 or polygon[0] != polygon[-1]:
            errors.append(_issue("room_boundary_invalid", "Room planning requires a calibrated, explicitly closed boundary polygon."))
    elif category == "Levels":
        elevation = local.get("elevation_internal_feet")
        if not _finite_number(elevation):
            errors.append(_issue("level_elevation_invalid", "Level planning requires an explicit elevation converted to internal feet."))
    elif category == "Grids":
        line = local.get("line_internal_feet")
        if not isinstance(line, list) or len(line) != 2 or _distance(line[0], line[1]) <= 1e-9:
            errors.append(_issue("grid_geometry_invalid", "Grid planning requires one non-zero calibrated line."))
        warnings.append(_issue("grid_source_not_confirmed", "Grid recognition is not implemented; geometry must have been explicitly reviewed."))


def _geometry_for(item, calibrated, scale, coordinate_mapping):
    category = item.get("planned_category") or item.get("category")
    if not calibrated:
        return {"coordinate_units": None, "local_geometry_internal_feet": None,
                "revit_geometry_internal_feet": None, "dimensions": {}}
    geometry_correction = calibrated.get("reviewer_correction")
    units = calibrated.get("coordinate_units")
    geometries = calibrated.get("geometry") or []
    dims = calibrated.get("dimensions") or {}
    position = calibrated.get("position")
    bbox = calibrated.get("bounding_box")
    if scale is None:
        return {"coordinate_units": calibrated.get("coordinate_units"),
                "local_geometry_internal_feet": None, "revit_geometry_internal_feet": None,
                "dimensions": {}, "source_calibrated_geometry": copy.deepcopy(calibrated)}

    def point(p):
        return [float(p[0]) * scale, float(p[1]) * scale]

    def points(seq):
        return [point(p) for p in seq] if isinstance(seq, list) else None

    local = {"coordinate_units": "internal feet, drawing-local origin",
             "position_internal_feet": point(position) if position else None,
             "bounding_box_internal_feet": _bbox_to_feet(bbox, scale) if bbox else None,
             "centerline_internal_feet": points(dims.get("centerline")),
             "boundary_internal_feet": None, "line_internal_feet": None,
             "dimensions": {}}
    if category == "Walls" and not local.get("centerline_internal_feet"):
        line_geometries = [geom for geom in geometries if geom.get("kind") == "line" and
                           geom.get("start") and geom.get("end")]
        if len(line_geometries) >= 2:
            first, second = line_geometries[0], line_geometries[1]
            a0, a1 = point(first["start"]), point(first["end"])
            b0, b1 = point(second["start"]), point(second["end"])
            if _distance(a0, b1) + _distance(a1, b0) < _distance(a0, b0) + _distance(a1, b1):
                b0, b1 = b1, b0
            local["centerline_internal_feet"] = [[(a0[i] + b0[i]) / 2 for i in range(2)],
                                                  [(a1[i] + b1[i]) / 2 for i in range(2)]]
    if geometry_correction:
        corr = geometry_correction
        coords = corr.get("coordinates")
        if corr.get("geometry_type") in ("polyline", "polygon"):
            converted = points(coords)
            if category == "Rooms":
                local["boundary_internal_feet"] = converted
            elif category == "Walls":
                local["centerline_internal_feet"] = converted
            elif category == "Grids":
                local["line_internal_feet"] = [converted[0], converted[-1]] if len(converted) >= 2 else None
        elif corr.get("geometry_type") == "point":
            local["position_internal_feet"] = point(coords)
        elif corr.get("geometry_type") == "bbox":
            local["bounding_box_internal_feet"] = _bbox_to_feet(coords, scale)
    elif category == "Rooms":
        poly = next((geom.get("points") for geom in geometries
                     if geom.get("kind") in ("polyline", "contour") and geom.get("points")), None)
        local["boundary_internal_feet"] = points(poly)
    elif category == "Grids":
        line = next((geom for geom in geometries if geom.get("kind") == "line" and geom.get("start") and geom.get("end")), None)
        local["line_internal_feet"] = [point(line["start"]), point(line["end"])] if line else None

    for name, source_name in (("thickness_internal_feet", "wall_thickness"),
                              ("width_internal_feet", "opening_width_candidate"),
                              ("height_internal_feet", "height"), ("width_internal_feet", "width"),
                              ("depth_internal_feet", "depth"), ("diameter_internal_feet", "diameter")):
        value = dims.get(source_name)
        unit = units
        if isinstance(value, dict):
            unit = value.get("unit") or units
            value = value.get("value")
        if _positive(value):
            dimension_scale = _FEET_PER_UNIT.get(str(unit).lower()) if unit else None
            if dimension_scale is not None:
                local["dimensions"][name] = float(value) * dimension_scale
    if item.get("planned_category") == "Columns" and local["bounding_box_internal_feet"]:
        box = local["bounding_box_internal_feet"]
        local["dimensions"].setdefault("width_internal_feet", abs(box[2] - box[0]))
        local["dimensions"].setdefault("depth_internal_feet", abs(box[3] - box[1]))
    if item.get("planned_category") == "Levels":
        elevation = _explicit_elevation(item.get("current_properties", {}).get("dimensions", {}))
        if elevation:
            local["elevation_internal_feet"] = elevation[0] * _FEET_PER_UNIT.get(elevation[1], 0)
    revit_geometry = _map_to_revit(local, coordinate_mapping)
    return {"coordinate_units": "internal feet", "local_geometry_internal_feet": local,
            "revit_geometry_internal_feet": revit_geometry,
            "dimensions": local["dimensions"],
            "source_geometry_references": copy.deepcopy(item.get("original_geometry_references", []))}


def _explicit_geometry_for(explicit, coordinate_mapping):
    if not isinstance(explicit, dict):
        return {"coordinate_units": "internal feet", "local_geometry_internal_feet": None,
                "revit_geometry_internal_feet": None, "dimensions": {}}
    raw = explicit.get("internal_feet") or {}
    start, end = raw.get("start"), raw.get("end")
    local = {"coordinate_units": "internal feet, explicit user origin",
        "centerline_internal_feet": [copy.deepcopy(start), copy.deepcopy(end)] if start and end else None,
        "position_internal_feet": None, "bounding_box_internal_feet": None,
        "boundary_internal_feet": None, "line_internal_feet": None,
        "dimensions": {"length_internal_feet": raw.get("length")}}
    if explicit.get("coordinate_system") == "revit_internal":
        revit = {"centerline_internal_feet": copy.deepcopy(local["centerline_internal_feet"])}
    else:
        revit = _map_to_revit(local, coordinate_mapping)
    return {"coordinate_units": "internal feet", "local_geometry_internal_feet": local,
        "revit_geometry_internal_feet": revit,
        "dimensions": copy.deepcopy(local["dimensions"]),
        "source_geometry_references": [],
        "explicit_geometry_reference": {key: copy.deepcopy(explicit.get(key)) for key in
            ("coordinate_system", "units", "origin_description", "start", "end", "length")}}


def _apply_dimension_mapping(geometry, dimensions, fallback_scale):
    if not isinstance(dimensions, dict):
        return
    local = geometry.get("local_geometry_internal_feet")
    if local is None:
        return
    target_names = {"width": "width_internal_feet", "height": "height_internal_feet",
                    "thickness": "thickness_internal_feet", "depth": "depth_internal_feet",
                    "diameter": "diameter_internal_feet"}
    for source_name, target_name in target_names.items():
        value = dimensions.get(source_name)
        if isinstance(value, dict):
            unit = str(value.get("unit") or "").lower()
            raw = value.get("value")
            factor = _FEET_PER_UNIT.get(unit) if value.get("unit_explicit") is True else None
        else:
            raw, factor = value, fallback_scale
        if _positive(raw) and factor is not None:
            local["dimensions"][target_name] = float(raw) * factor
    geometry["dimensions"] = copy.deepcopy(local["dimensions"])


def _map_to_revit(local, coordinate_mapping):
    if coordinate_mapping.get("status") != "user_confirmed_project_mapping":
        return None
    value = coordinate_mapping["value"]
    origin = value["origin_internal_feet"]
    angle = math.radians(value["rotation_degrees"])
    ca, sa = math.cos(angle), math.sin(angle)

    def p(point):
        return [origin[0] + ca * point[0] - sa * point[1],
                origin[1] + sa * point[0] + ca * point[1],
                origin[2] + (point[2] if len(point) > 2 else 0.0)]

    result = {}
    if local.get("position_internal_feet"):
        result["position_internal_feet"] = p(local["position_internal_feet"])
    for key in ("centerline_internal_feet", "boundary_internal_feet", "line_internal_feet"):
        if local.get(key):
            result[key] = [p(point) for point in local[key]]
    if local.get("bounding_box_internal_feet"):
        x0, y0, x1, y1 = local["bounding_box_internal_feet"]
        corners = [p([x0, y0]), p([x1, y0]), p([x1, y1]), p([x0, y1])]
        result["bounding_box_internal_feet"] = [min(x[0] for x in corners), min(x[1] for x in corners),
                                                 max(x[0] for x in corners), max(x[1] for x in corners)]
    if local.get("elevation_internal_feet") is not None:
        result["elevation_internal_feet"] = origin[2] + local["elevation_internal_feet"]
    return result


def _coordinate_matrix(coordinate_mapping):
    if coordinate_mapping.get("status") != "user_confirmed_project_mapping":
        return None
    value = coordinate_mapping["value"]
    x, y, z = value["origin_internal_feet"]
    angle = math.radians(value["rotation_degrees"])
    ca, sa = math.cos(angle), math.sin(angle)
    return [[ca, -sa, 0.0, x], [sa, ca, 0.0, y], [0.0, 0.0, 1.0, z], [0.0, 0.0, 0.0, 1.0]]


def _validate_mappings(mappings, approved_ids):
    if not isinstance(mappings, dict):
        raise GenerationPlanError("invalid_mappings", "mappings must be an object keyed by approved review item ID")
    unknown = set(mappings) - approved_ids
    if unknown:
        raise GenerationPlanError("mapping_item_not_approved", "Mappings may only target explicitly approved review items",
                                  {"item_ids": sorted(unknown)})
    return {item_id: _validate_mapping(value) for item_id, value in mappings.items()}


def _validate_mapping(mapping):
    if not isinstance(mapping, dict):
        raise GenerationPlanError("invalid_mapping", "Each item mapping must be an object")
    allowed = {"level_name", "level_id", "top_level_name", "top_level_id", "wall_type_id",
               "family_name", "type_name", "symbol_id", "host_review_item_id",
               "grid_name", "parameter_assignments", "dimensions"}
    extra = set(mapping) - allowed
    if extra:
        raise GenerationPlanError("invalid_mapping", "Mapping contains unsupported fields", {"fields": sorted(extra)})
    output = copy.deepcopy(mapping)
    for key in ("level_name", "level_id", "top_level_name", "top_level_id", "wall_type_id",
                "family_name", "type_name", "symbol_id", "host_review_item_id", "grid_name"):
        if key in output and (not isinstance(output[key], str) or len(output[key]) > 300):
            raise GenerationPlanError("invalid_mapping", "{} must be text of at most 300 characters".format(key))
    for key in ("parameter_assignments", "dimensions"):
        if key in output and not isinstance(output[key], dict):
            raise GenerationPlanError("invalid_mapping", "{} must be an object".format(key))
    for name, value in (output.get("parameter_assignments") or {}).items():
        if not isinstance(name, str) or not name or len(name) > 120 or not isinstance(value, (str, int, float, bool)):
            raise GenerationPlanError("invalid_mapping", "Parameter assignments must be named scalar values")
        if isinstance(value, float) and not math.isfinite(value):
            raise GenerationPlanError("invalid_mapping", "Parameter assignment values must be finite")
    return output


def _normalize_coordinate_mapping(value):
    if value is None:
        return {"status": "unmapped", "value": None}
    if not isinstance(value, dict) or value.get("user_confirmed") is not True:
        raise GenerationPlanError("coordinate_mapping_unconfirmed", "Coordinate mapping requires user_confirmed=true")
    origin = value.get("origin_internal_feet")
    angle = value.get("rotation_degrees", 0.0)
    if not isinstance(origin, (list, tuple)) or len(origin) != 3 or any(not _finite_number(x) for x in origin):
        raise GenerationPlanError("invalid_coordinate_mapping", "origin_internal_feet must contain three finite numbers")
    if not _finite_number(angle):
        raise GenerationPlanError("invalid_coordinate_mapping", "rotation_degrees must be finite")
    return {"status": "user_confirmed_project_mapping", "value": {
        "user_confirmed": True, "origin_internal_feet": [float(x) for x in origin],
        "rotation_degrees": float(angle), "basis": "Revit internal XY and elevation in internal feet"}}


def _inventory_snapshot(inventory):
    return {key: copy.deepcopy(inventory.get(key)) for key in
            ("status", "document_identity", "document_title", "document_path", "revit_version",
             "application_version_availability", "levels", "level_names", "level_ids_available",
             "level_catalog", "level_catalog_status", "family_types",
             "family_types_total", "family_types_truncated", "system_types_by_category",
             "system_type_catalog_scope", "categories", "parameter_names", "project_base_point",
             "survey_point", "statistics_complete", "warnings")}


def _all_review_items(manager, review_id):
    offset, items = 0, []
    while True:
        result = manager.list_items(review_id, offset=offset, limit=500)
        items.extend(result["items"])
        offset += len(result["items"])
        if offset >= result["total"] or not result["items"]:
            return items


def _excluded_reason(item, explicit=False):
    if item.get("status") != "approved":
        return "review_status_{}".format(item.get("status", "unknown"))
    if explicit and not _review_geometry_is_current(item, True):
        return "explicit_geometry_approval_missing_or_stale"
    if item.get("calibration_state") == "stale":
        return "calibration_stale"
    if item.get("calibration_state") != "current":
        return "calibration_missing_or_incomplete"
    return "not_eligible"


def _review_geometry_is_current(item, explicit):
    if not explicit:
        return item.get("calibration_state") == "current"
    if item.get("explicit_geometry_status") != "confirmed":
        return False
    try:
        from lora_review.manager import _explicit_geometry_fingerprint
        fingerprint = _explicit_geometry_fingerprint((item.get("current_geometry") or {}).get("explicit_user_geometry"))
        approval = item.get("explicit_geometry_approval") or {}
        return fingerprint == item.get("explicit_geometry_fingerprint") == approval.get("fingerprint")
    except Exception:
        return False


def _explicit_elevation(dimensions):
    if not isinstance(dimensions, dict):
        return None
    candidates = [dimensions.get("elevation"), (dimensions.get("level") or {}).get("elevation")]
    for value in candidates:
        if isinstance(value, dict) and _finite_number(value.get("value")) and value.get("unit_explicit") is True:
            unit = str(value.get("unit") or "").lower()
            if unit in _FEET_PER_UNIT:
                return float(value["value"]), unit
    return None


def _bbox_to_feet(box, scale):
    x0, y0, x1, y1 = box
    return [x0 * scale, y0 * scale, x1 * scale, y1 * scale]


def _distance(a, b):
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _positive(value):
    return _finite_number(value) and float(value) > 0


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _issue(code, message):
    return {"code": code, "message": message}


def _pagination(offset, limit):
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise GenerationPlanError("invalid_pagination", "offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise GenerationPlanError("invalid_pagination", "limit must be an integer from 1 to 500")


def _duplicate_pairs(items, tolerance=0.001):
    pairs = []
    for index, first in enumerate(items):
        a = first.get("geometry", {}).get("local_geometry_internal_feet") or {}
        pa = a.get("position_internal_feet")
        for second in items[index + 1:]:
            if first.get("planned_category") != second.get("planned_category"):
                continue
            b = second.get("geometry", {}).get("local_geometry_internal_feet") or {}
            pb = b.get("position_internal_feet")
            line_a = a.get("centerline_internal_feet")
            line_b = b.get("centerline_internal_feet")
            duplicate_line = (line_a and line_b and len(line_a) == len(line_b) and
                              ((_points_close(line_a, line_b, tolerance)) or
                               (_points_close(line_a, list(reversed(line_b)), tolerance))))
            if (pa and pb and _distance(pa, pb) <= tolerance) or duplicate_line:
                pairs.append((first, second))
    return pairs


def _points_close(first, second, tolerance):
    return all(_distance(a, b) <= tolerance for a, b in zip(first, second))


def _same_document_identity(first, second):
    """Compare stable identity fields while ignoring observation timestamps/diagnostics."""
    if not isinstance(first, dict) or not isinstance(second, dict):
        return first == second
    fields = ("document_title", "document_path", "document_type", "revit_version",
              "revit_process_id", "document_identifier", "connection_endpoint",
              "process_session_id", "application_session_id", "document_session_id",
              "document_lifecycle_sequence", "document_lifecycle_generation",
              "active_document_switch_counter", "identity_provider_version",
              "identity_provider_status")
    return tuple(first.get(field) for field in fields) == tuple(second.get(field) for field in fields)
