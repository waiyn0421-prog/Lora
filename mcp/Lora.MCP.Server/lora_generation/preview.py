"""Stable, non-native 2D representations and validation for generation plans."""

import copy
import math
import uuid
from datetime import datetime, timezone

from .errors import GenerationPlanError


class GenerationPreviewEngine:
    def __init__(self, planner, repository):
        self.planner = planner
        self.plan_repository = planner.repository
        self.repository = repository

    def create(self, plan_id, inventory, revalidation_of_preview_id=None):
        plan = self.plan_repository.get(plan_id)
        checked = self.planner.validate_snapshot(plan, inventory)
        previous = self.repository.previous(plan_id)
        items = [_preview_item(item, inventory) for item in checked["items"]]
        findings = _findings(checked, inventory)
        preview = {"preview_schema_version": 1, "preview_id": str(uuid.uuid4()),
                   "source_plan_id": plan_id, "source_plan_version": int(plan.get("plan_version", 1)),
                   "source_review_id": plan.get("source_review_id"),
                   "document_identity": copy.deepcopy(inventory.get("document_identity")),
                   "inventory_source": inventory.get("inventory_source", "unavailable" if inventory.get("status") != "success" else "unspecified_fixture_or_manual"),
                   "inventory_status": inventory.get("status"),
                   "inventory_verified_at": inventory.get("verified_at") if inventory.get("inventory_source") == "live_revitmcp" else None,
                   "inventory_verification": "live_verified" if inventory.get("status") == "success" and inventory.get("inventory_source") == "live_revitmcp" else "not_live_verified",
                   "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                   "validation": {"status": checked["plan_status"], "errors": findings["errors"],
                                  "warnings": findings["warnings"], "information": findings["information"],
                                  "error_count": len(findings["errors"]), "warning_count": len(findings["warnings"]),
                                  "information_count": len(findings["information"])},
                   "readiness": "blocked" if findings["errors"] else "review_required",
                   "ready_for_execution": False, "execution_supported": False,
                   "items": items, "visual_preview": _visual_preview(items, plan),
                   "differences_from_previous": None,
                   "source_render_reference": {"tool": "lora_get_drawing_render",
                       "processing_id": plan.get("source_processing_id"),
                       "availability": "not_checked", "page_numbers": plan.get("source_pages", [])},
                   "previous_preview_id": previous.get("preview_id") if previous else None,
                   "revalidation_of_preview_id": revalidation_of_preview_id,
                   "stale": False,
                   "restriction": "Preview only. No Revit element creation, modification, deletion, selection or transaction was performed."}
        if previous:
            delta = _compare_snapshot_items(previous, items)
            delta["changed_snapshot_fields"] = [field for field in ("document_identity", "inventory_source",
                "inventory_status", "inventory_verification", "validation", "readiness")
                if previous.get(field) != preview.get(field)]
            preview["differences_from_previous"] = delta
        return self.repository.create(preview)

    def get(self, preview_id):
        return self.repository.get(preview_id)

    def items(self, preview_id, category=None, status=None, offset=0, limit=100):
        _pagination(offset, limit)
        preview = self.get(preview_id)
        items = preview["items"]
        if category:
            items = [item for item in items if item["revit_category"].casefold() == category.casefold()]
        if status:
            items = [item for item in items if item["validation"]["status"] == status]
        return {"total": len(items), "offset": offset, "limit": limit,
                "items": items[offset:offset + limit], "truncated": offset + limit < len(items),
                "preview_stale": preview["stale"]}

    def summary(self, preview_id):
        preview = self.get(preview_id)
        by_category, by_status = {}, {}
        for item in preview["items"]:
            by_category[item["revit_category"]] = by_category.get(item["revit_category"], 0) + 1
            state = item["validation"]["status"]
            by_status[state] = by_status.get(state, 0) + 1
        return {"preview_id": preview_id, "source_plan_id": preview["source_plan_id"],
                "source_plan_version": preview["source_plan_version"], "stale": preview["stale"],
                "inventory_verification": preview["inventory_verification"],
                "item_count": len(preview["items"]), "items_by_category": by_category,
                "items_by_status": by_status, "validation": preview["validation"],
                "readiness": preview["readiness"], "ready_for_execution": False}

    def validate(self, preview_id, inventory):
        old = self.get(preview_id)
        return self.create(old["source_plan_id"], inventory, revalidation_of_preview_id=preview_id)

    def compare(self, first_id, second_id):
        from .preview_repository import compare_previews
        return compare_previews(self.get(first_id), self.get(second_id))


def _preview_item(item, inventory):
    geometry = copy.deepcopy(item.get("geometry") or {})
    local = geometry.get("local_geometry_internal_feet") or {}
    revit = geometry.get("revit_geometry_internal_feet")
    verified_type = item.get("revit_type_mapping")
    mapping = item.get("mapping") or {}
    level_name = mapping.get("level_name")
    level_verified = bool(level_name and level_name in (inventory.get("level_names") or []) and inventory.get("status") == "success")
    type_verified = bool(verified_type and inventory.get("status") == "success")
    status = "error" if item["validation"].get("errors") else ("warning" if item["validation"].get("warnings") else "information")
    return {"planned_element_id": item["plan_item_id"], "source_review_item_id": item["review_item_id"],
            "revit_category": item["planned_category"], "planned_operation": item.get("planned_operation"),
            "proposed_family": mapping.get("family_name"), "proposed_type": mapping.get("type_name"),
            "proposed_symbol_id": mapping.get("symbol_id"), "verified_revit_type": verified_type,
            "type_verification": _verification_label(type_verified, inventory),
            "proposed_level": level_name, "level_verification": _verification_label(level_verified, inventory),
            "geometry": geometry,
            "location_internal_feet": revit.get("position_internal_feet") if revit else local.get("position_internal_feet"),
            "orientation_degrees": _orientation_degrees(local),
            "dimensions_internal_feet": copy.deepcopy(local.get("dimensions", {})),
            "parameter_assignments": copy.deepcopy(mapping.get("parameter_assignments", {})),
            "parameter_verification": "proposed_only_scope_and_compatibility_unverified",
            "dependencies": copy.deepcopy(item.get("dependencies", [])),
            "validation": {"status": status, "errors": copy.deepcopy(item["validation"].get("errors", [])),
                           "warnings": copy.deepcopy(item["validation"].get("warnings", [])), "information": []},
            "warnings": copy.deepcopy(item.get("warnings", [])),
            "errors": copy.deepcopy(item["validation"].get("errors", []))}


def _verification_label(verified, inventory):
    if not verified:
        return "unverified"
    return "verified_by_live_inventory" if inventory.get("inventory_source") == "live_revitmcp" else "matched_to_fixture_or_manual_inventory"


def _findings(plan, inventory):
    errors, warnings, information = [], [], []
    for issue in plan["validation"].get("errors", []):
        errors.append(dict(issue, classification="error"))
    for issue in plan["validation"].get("warnings", []):
        target = errors if issue.get("code") == "coordinate_origin_unmapped" else warnings
        target.append(dict(issue, classification="error" if target is errors else "warning"))
    if plan["target_coordinate_system"].get("mapping_status") != "user_confirmed_project_mapping":
        errors.append({"code": "placement_coordinates_unresolved", "message": "No complete user-confirmed drawing-to-Revit coordinate mapping is available.", "classification": "error"})
    if inventory.get("status") == "success" and inventory.get("inventory_source") == "live_revitmcp":
        information.append({"code": "live_inventory_verified", "message": "Type, level and document checks used live RevitMCP read-only results.", "classification": "information"})
    elif inventory.get("status") == "success":
        warnings.append({"code": "inventory_not_live_verified", "message": "Inventory is fixture/manual data and is not live-verified.", "classification": "warning"})
    else:
        warnings.append({"code": "revitmcp_unavailable", "message": "RevitMCP inventory is unavailable; family/type/level checks could not be live-verified.", "classification": "warning"})
    for item in plan.get("items", []):
        if not item.get("planned_operation"):
            errors.append({"code": "unsupported_element_type", "message": "No preview operation exists for the source category.", "review_item_id": item["review_item_id"], "classification": "error"})
        for issue in item.get("validation", {}).get("errors", []):
            errors.append(dict(issue, review_item_id=item["review_item_id"], classification="error"))
        for issue in item.get("validation", {}).get("warnings", []):
            warnings.append(dict(issue, review_item_id=item["review_item_id"], classification="warning"))
        for name in item.get("parameter_assignments", {}):
            if name not in (inventory.get("parameter_names") or []):
                warnings.append({"code": "parameter_compatibility_unverified", "review_item_id": item["review_item_id"],
                                 "parameter": name, "message": "Parameter is not confirmed in the schema-name snapshot.", "classification": "warning"})
        if item.get("parameter_assignments"):
            information.append({"code": "parameter_scope_unavailable", "review_item_id": item["review_item_id"],
                                "message": "Type/instance scope and writability are unavailable.", "classification": "information"})
    for item in plan.get("unresolved_items", []):
        errors.append({"code": "review_item_unresolved", "review_item_id": item.get("review_item_id"),
                       "message": item.get("reason"), "classification": "error"})
    warnings.extend(_possible_column_overlaps(plan.get("items", [])))
    return {"errors": _deduplicate(errors), "warnings": _deduplicate(warnings), "information": _deduplicate(information)}


def _possible_column_overlaps(items):
    columns = [item for item in items if item.get("planned_category") == "Columns"]
    warnings = []
    for index, first in enumerate(columns):
        box_a = ((first.get("geometry") or {}).get("local_geometry_internal_feet") or {}).get("bounding_box_internal_feet")
        if not box_a:
            continue
        for second in columns[index + 1:]:
            box_b = ((second.get("geometry") or {}).get("local_geometry_internal_feet") or {}).get("bounding_box_internal_feet")
            if box_b and max(box_a[0], box_b[0]) < min(box_a[2], box_b[2]) and max(box_a[1], box_b[1]) < min(box_a[3], box_b[3]):
                warnings.append({"code": "potential_overlap", "review_item_ids": [first["review_item_id"], second["review_item_id"]],
                                 "message": "Column footprints overlap in drawing-local coordinates.", "classification": "warning"})
    return warnings


def _visual_preview(items, plan):
    features = []
    for item in items:
        local = (item.get("geometry") or {}).get("local_geometry_internal_feet") or {}
        revit = (item.get("geometry") or {}).get("revit_geometry_internal_feet") or {}
        display = revit or local
        category, kind, coordinates = item["revit_category"], None, None
        if category == "Walls" and display.get("centerline_internal_feet"):
            kind, coordinates = "wall_centerline", display["centerline_internal_feet"]
        elif category in ("Doors", "Windows") and display.get("position_internal_feet"):
            kind, coordinates = "opening_location", display["position_internal_feet"]
        elif category == "Columns" and display.get("bounding_box_internal_feet"):
            x0, y0, x1, y1 = display["bounding_box_internal_feet"]
            kind, coordinates = "column_footprint", [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
        elif category == "Rooms" and display.get("boundary_internal_feet"):
            kind, coordinates = "room_boundary", display["boundary_internal_feet"]
        elif category == "Levels" and display.get("elevation_internal_feet") is not None:
            kind, coordinates = "level_annotation", display["elevation_internal_feet"]
        if kind:
            features.append({"feature_id": item["planned_element_id"], "source_review_item_id": item["source_review_item_id"],
                             "geometry_type": kind, "coordinates": copy.deepcopy(coordinates),
                             "coordinate_space": "revit_internal_feet" if (item.get("geometry") or {}).get("revit_geometry_internal_feet") else "drawing_local_internal_feet",
                             "category": category, "label": "{} {}".format(category, item["planned_element_id"][:8]),
                             "level": item.get("proposed_level"), "thickness_internal_feet": item.get("dimensions_internal_feet", {}).get("thickness_internal_feet"),
                             "validation": copy.deepcopy(item["validation"]),
                             "selected_properties": {"family": item.get("proposed_family"), "type": item.get("proposed_type"),
                                                     "level": item.get("proposed_level"), "dimensions_internal_feet": item.get("dimensions_internal_feet")}})
    return {"format": "lora_2d_preview_v1", "native_revit_3d": False,
            "coordinate_system": copy.deepcopy(plan.get("target_coordinate_system")),
            "features": features, "feature_count": len(features), "omitted_geometry_count": len(items) - len(features),
            "notes": ["2D structured geometry only; no native Revit 3D view or elements are produced."]}


def _orientation_degrees(local):
    line = local.get("centerline_internal_feet") or local.get("line_internal_feet")
    if not line or len(line) < 2:
        return None
    return math.degrees(math.atan2(line[-1][1] - line[0][1], line[-1][0] - line[0][0]))


def _compare_snapshot_items(previous, current):
    before = {item["source_review_item_id"]: item for item in previous.get("items", [])}
    after = {item["source_review_item_id"]: item for item in current}
    return {"added_review_item_ids": sorted(after.keys() - before.keys()),
            "removed_review_item_ids": sorted(before.keys() - after.keys()),
            "changed_review_item_ids": sorted(key for key in before.keys() & after.keys() if before[key] != after[key])}


def _deduplicate(items):
    result, seen = [], set()
    for item in items:
        key = (item.get("code"), item.get("review_item_id"), tuple(item.get("review_item_ids", [])), item.get("parameter"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _pagination(offset, limit):
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise GenerationPlanError("invalid_pagination", "offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise GenerationPlanError("invalid_pagination", "limit must be an integer from 1 to 500")
