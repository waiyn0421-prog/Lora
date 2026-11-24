# -*- coding: utf-8 -*-
"""Revit-thread Basic Wall execution used only by the explicit local Lora workflow.

This module is intentionally Python 2.7-compatible for the pyRevit Revit 2023/2024
engines. Every Revit API access is made from a Routes handler with injected `doc`
and `uiapp`, which pyRevit dispatches through its Revit ExternalEvent bridge.
"""

import hashlib
import json
import logging
import math


_LOGGER = logging.getLogger(__name__)


def list_wall_types(runtime, uiapp, doc):
    """Read actual WallType elements and compound structure details; no transaction is opened."""
    operation = "identity_validation"
    try:
        identity = runtime.identity_response(uiapp, doc)
        if identity.get("status") != "success" or bool(getattr(doc, "IsFamilyDocument", False)):
            raise ValueError("project_document_required")
        from Autodesk.Revit.DB import BuiltInParameter, FilteredElementCollector, WallType
        operation = "wall_type_collection"
        records = []
        for wall_type in FilteredElementCollector(doc).OfClass(WallType).WhereElementIsElementType():
            field_warnings = []
            element_id = _wall_type_element_id(wall_type, field_warnings)
            type_name, name_source = _wall_type_name(wall_type, BuiltInParameter, field_warnings)
            unique_id = _optional_wall_type_value(wall_type, "UniqueId", str, field_warnings)
            wall_kind = _optional_wall_type_value(wall_type, "Kind", str, field_warnings)
            width = _optional_wall_type_value(wall_type, "Width", float, field_warnings)
            function = _optional_wall_type_value(wall_type, "Function", str, field_warnings)
            compound = _wall_compound_structure(wall_type, field_warnings)
            records.append({"element_id": element_id, "unique_id": unique_id,
                "name": type_name, "type_name": type_name, "name_source": name_source,
                "wall_kind": wall_kind, "width_internal_feet": width,
                "function": function, "compound_structure": compound,
                "field_warnings": field_warnings})
        operation = "wall_type_sort"
        records.sort(key=lambda item: ((item.get("name") or "").lower(),
                                       int(item["element_id"]) if item.get("element_id") else -1))
        inventory_warnings = []
        for item in records:
            for warning in item["field_warnings"]:
                value = {"element_id": item.get("element_id")}
                value.update(warning)
                inventory_warnings.append(value)
        return {"status": "success", "document_identity": _identity_summary(identity.get("identity") or {}),
                "total": len(records), "wall_types": records, "complete": True,
                "warnings": inventory_warnings,
                "source": "Autodesk.Revit.DB.FilteredElementCollector.OfClass(WallType)"}
    except Exception as exc:
        _LOGGER.exception("WallType inventory failed during %s", operation)
        return {"status": "error", "error": _safe_error(exc),
                "diagnostic": {"operation": operation, "exception_type": type(exc).__name__,
                               "message": str(exc)[:240]}}


def _wall_type_element_id(wall_type, warnings):
    try:
        return _element_id_text(wall_type.Id)
    except Exception as exc:
        warnings.append(_wall_type_warning("element_id", "Id", exc))
        return None


def _wall_type_name(wall_type, built_in_parameter, warnings):
    try:
        parameter = wall_type.get_Parameter(built_in_parameter.SYMBOL_NAME_PARAM)
        if parameter is not None and (not hasattr(parameter, "HasValue") or parameter.HasValue):
            value = parameter.AsString()
            if value:
                return str(value), "SYMBOL_NAME_PARAM"
    except Exception as exc:
        warnings.append(_wall_type_warning("type_name", "SYMBOL_NAME_PARAM", exc))
    try:
        value = wall_type.Name
        if value:
            return str(value), "Element.Name"
    except Exception as exc:
        warnings.append(_wall_type_warning("type_name", "Name", exc))
    warnings.append({"field": "type_name", "operation": "name_resolution",
                     "exception_type": "Unavailable", "message": "No type name was available."})
    return None, None


def _optional_wall_type_value(wall_type, property_name, converter, warnings):
    try:
        value = getattr(wall_type, property_name)
        return converter(value) if value is not None else None
    except Exception as exc:
        warnings.append(_wall_type_warning(property_name.lower(), property_name, exc))
        return None


def _wall_compound_structure(wall_type, warnings):
    try:
        structure = wall_type.GetCompoundStructure()
    except Exception as exc:
        warnings.append(_wall_type_warning("compound_structure", "GetCompoundStructure", exc))
        return {"status": "unavailable"}
    if structure is None:
        return None
    compound = {"layers": [], "total_width_internal_feet": None,
                "first_core_layer_index": None, "last_core_layer_index": None,
                "status": "available"}
    for member, key, converter in (("GetWidth", "total_width_internal_feet", float),
                                   ("GetFirstCoreLayerIndex", "first_core_layer_index", int),
                                   ("GetLastCoreLayerIndex", "last_core_layer_index", int)):
        try:
            compound[key] = converter(getattr(structure, member)())
        except Exception as exc:
            warnings.append(_wall_type_warning("compound_structure." + key, member, exc))
            compound["status"] = "partial"
    try:
        layers = structure.GetLayers()
    except Exception as exc:
        warnings.append(_wall_type_warning("compound_structure.layers", "GetLayers", exc))
        compound["status"] = "partial"
        return compound
    first_core = compound.get("first_core_layer_index")
    last_core = compound.get("last_core_layer_index")
    for index, layer in enumerate(layers):
        layer_record = {"index": index, "function": None, "width_internal_feet": None,
                        "material_id": None, "is_core": None}
        for member, key, converter in (("Function", "function", str), ("Width", "width_internal_feet", float)):
            try:
                value = getattr(layer, member)
                layer_record[key] = converter(value) if value is not None else None
            except Exception as exc:
                warnings.append(_wall_type_warning("compound_structure.layers.{}.{}".format(index, key), member, exc))
                compound["status"] = "partial"
        try:
            material_id = layer.MaterialId
            layer_record["material_id"] = _element_id_text(material_id) if material_id else None
        except Exception as exc:
            warnings.append(_wall_type_warning("compound_structure.layers.{}.material_id".format(index), "MaterialId", exc))
            compound["status"] = "partial"
        if first_core is not None and last_core is not None:
            layer_record["is_core"] = first_core <= index <= last_core
        compound["layers"].append(layer_record)
    return compound


def _wall_type_warning(field, operation, exc):
    return {"field": field, "operation": operation,
            "exception_type": type(exc).__name__, "message": str(exc)[:240]}

def validate_walls(runtime, uiapp, doc, payload):
    try:
        request = _validated_payload(payload)
        identity = _current_identity(runtime, uiapp, doc, request["expected_identity"])
        resolved = _resolve_walls(doc, request["walls"])
        return {"status": "success", "document_identity": _identity_summary(identity), "walls": resolved}
    except Exception as exc:
        return {"status": "error", "error": _safe_error(exc)}


def execute_walls(runtime, uiapp, doc, payload):
    request_id = None
    request_hash = None
    try:
        request = _validated_payload(payload, execution=True)
        request_id = request["request_id"]
        request_hash = hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()
        cached = runtime.get_execution_receipt(request["request_id"])
        if cached:
            if cached["request_hash"] != request_hash:
                return {"status": "error", "error": {"code": "request_id_reused",
                        "message": "Execution request ID was reused with different content."}}
            if cached["state"] == "complete":
                return cached["response"]
            return {"status": "error", "error": {"code": "request_in_progress",
                    "message": "This execution request is already being processed."}}
        runtime.store_execution_receipt(request["request_id"], request_hash, "in_progress", None)
        response = _execute_once(runtime, uiapp, doc, request)
        runtime.store_execution_receipt(request["request_id"], request_hash, "complete", response)
        return response
    except Exception as exc:
        response = {"status": "error", "committed": False, "error": _safe_error(exc)}
        if request_id and request_hash:
            runtime.store_execution_receipt(request_id, request_hash, "complete", response)
        return response


def verify_walls(runtime, uiapp, doc, payload):
    try:
        # Import in this handler's scope: Routes invokes this function independently
        # from execute_walls, so its local imports are not shared.
        from Autodesk.Revit.DB import Wall
        request = _validated_payload(payload, verification=True)
        identity = _current_identity(runtime, uiapp, doc, request["expected_identity"])
        results = []
        for item in request["elements"]:
            element_id = _parse_element_id(item["element_id"])
            wall = doc.GetElement(element_id)
            if wall is None or not isinstance(wall, Wall):
                results.append({"element_id": item["element_id"], "verified": False,
                                "error": "element_not_found_or_not_wall"})
                continue
            record = _wall_record(doc, wall, item.get("source_review_item_id"), item.get("expected") or {})
            record["element_id"] = item["element_id"]
            results.append(record)
        return {"status": "success" if all(value.get("verified") for value in results) else "partial",
                "document_identity": _identity_summary(identity), "elements": results,
                "verified_count": sum(1 for value in results if value.get("verified")),
                "requested_count": len(results)}
    except Exception as exc:
        return {"status": "error", "error": _safe_error(exc)}


def _execute_once(runtime, uiapp, doc, request):
    from Autodesk.Revit.DB import BuiltInParameter, Line, Transaction, TransactionStatus, Wall
    identity = _current_identity(runtime, uiapp, doc, request["expected_identity"])
    resolved = _resolve_walls(doc, request["walls"])
    if resolved != request["validated_walls"]:
        raise ValueError("wall_type_or_level_changed_after_preparation")
    _reject_existing_duplicates(doc, request["walls"], resolved)
    if not _confirm_in_revit_ui(request, resolved):
        return {"status": "cancelled", "committed": False, "post_execution_identity_verified": False,
                "created_elements": [], "transaction": {"status": "not_started"},
                "error": {"code": "user_cancelled", "message": "Revit user declined wall creation."}}
    before_transaction = _current_identity(runtime, uiapp, doc, request["expected_identity"])
    if _identity_summary(before_transaction) != _identity_summary(identity):
        raise ValueError("document_identity_changed_after_user_confirmation")
    resolved_after_confirmation = _resolve_walls(doc, request["walls"])
    if resolved_after_confirmation != resolved:
        raise ValueError("wall_type_or_level_changed_after_user_confirmation")
    _reject_existing_duplicates(doc, request["walls"], resolved_after_confirmation)
    transaction = Transaction(doc, "Lora AI Create Approved Basic Walls")
    try:
        transaction.Start()
    except Exception as exc:
        return {"status": "error", "committed": False, "post_execution_identity_verified": False,
                "created_elements": [], "transaction": {"status": "not_started"},
                "error": _safe_error(exc)}
    created = []
    try:
        resolved_by_plan_id = dict((item["planned_element_id"], item) for item in resolved)
        for item in request["walls"]:
            match = resolved_by_plan_id[item["planned_element_id"]]
            curve = Line.CreateBound(_xyz(item["centerline_internal_feet"][0]),
                                     _xyz(item["centerline_internal_feet"][1]))
            creation_height = (float(match["top_level_elevation_internal_feet"]) + float(item.get("top_offset_internal_feet", 0.0)) -
                               float(match["base_level_elevation_internal_feet"]) - float(item["base_offset_internal_feet"])
                               if item.get("top_constraint") is not None else float(item["unconnected_height_internal_feet"]))
            if creation_height <= 0:
                raise ValueError("invalid_resolved_wall_height")
            wall = Wall.Create(doc, curve, _element_id(match["wall_type_id"]),
                               _element_id(match["base_level_id"]),
                               creation_height,
                               float(item["base_offset_internal_feet"]),
                               bool(item["flip"]), bool(item["structural"]))
            # Preserve the Revit identity immediately. Later parameter assignment or
            # verification can fail while this element is still inside the transaction.
            creation_record = _created_element_record(wall, item)
            created.append(creation_record)
            if item.get("top_constraint") is not None:
                top_param = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE)
                top_offset_param = wall.get_Parameter(BuiltInParameter.WALL_TOP_OFFSET)
                if top_param is None or top_param.IsReadOnly or not top_param.Set(_element_id(match["top_level_id"])):
                    raise ValueError("wall_top_constraint_assignment_failed")
                if top_offset_param is None or top_offset_param.IsReadOnly or not top_offset_param.Set(float(item.get("top_offset_internal_feet", 0.0))):
                    raise ValueError("wall_top_offset_assignment_failed")
            _apply_parameters(wall, item.get("parameter_assignments") or {})
            record = _wall_record(doc, wall, item.get("source_review_item_id"), item)
            if not record.get("verified"):
                raise ValueError("post_creation_element_verification_failed")
            record.update({"creation_state": "created_in_transaction", "transaction_outcome": "uncommitted"})
            _replace_created_record(created, creation_record, record)
        precommit_identity = _current_identity(runtime, uiapp, doc, request["expected_identity"])
        if _identity_summary(precommit_identity) != _identity_summary(identity):
            raise ValueError("document_identity_changed_before_commit")
        status = transaction.Commit()
        if status != TransactionStatus.Committed:
            if str(status) == "Pending":
                return {"status": "commit_pending", "committed": None,
                        "post_execution_identity_verified": False,
                        "created_elements": _with_transaction_outcome(created, "uncertain"),
                        "created_outcome": "uncertain", "transaction": {"name": "Lora AI Create Approved Basic Walls", "status": "pending",
                                        "created_count": len(created)},
                        "error": {"code": "transaction_commit_pending",
                                  "message": "Revit is still processing transaction failures; outcome is not yet known."}}
            raise ValueError("transaction_did_not_commit_{}".format(status))
    except Exception as exc:
        try:
            transaction_status = transaction.GetStatus()
        except Exception:
            transaction_status = None
        if transaction_status == TransactionStatus.Started:
            try:
                transaction.RollBack()
                rollback_status = transaction.GetStatus()
                if str(rollback_status) != "RolledBack":
                    return {"status": "rollback_failed", "committed": None,
                            "post_execution_identity_verified": False,
                            "created_elements": _with_transaction_outcome(created, "uncertain"),
                            "transaction": {"name": "Lora AI Create Approved Basic Walls",
                                            "status": str(rollback_status),
                                            "created_count": len(created)},
                            "error": _safe_error(exc)}
                rolled_back = _with_transaction_outcome(created, "rolled_back")
                return {"status": "error", "committed": False,
                        "post_execution_identity_verified": False, "created_elements": rolled_back,
                        "transaction": {"name": "Lora AI Create Approved Basic Walls",
                                        "status": "rolled_back", "created_count": len(rolled_back)},
                        "error": _safe_error(exc)}
            except Exception as rollback_error:
                return {"status": "rollback_failed", "committed": None,
                        "post_execution_identity_verified": False,
                        "created_elements": _with_transaction_outcome(created, "uncertain"),
                        "transaction": {"name": "Lora AI Create Approved Basic Walls",
                                        "status": "unknown", "created_count": len(created)},
                        "error": {"code": "transaction_rollback_failed",
                                  "message": "Creation failed and Revit did not confirm transaction rollback.",
                                  "exception_type": type(rollback_error).__name__}}
        elif str(transaction_status) == "Pending":
            return {"status": "commit_pending", "committed": None,
                    "post_execution_identity_verified": False,
                    "created_elements": _with_transaction_outcome(created, "uncertain"),
                    "created_outcome": "uncertain", "transaction": {"name": "Lora AI Create Approved Basic Walls", "status": "pending",
                                    "created_count": len(created)},
                    "error": _safe_error(exc)}
        elif transaction_status == TransactionStatus.Committed:
            return {"status": "verification_failed", "committed": True,
                    "post_execution_identity_verified": False, "created_elements": created,
                    "transaction": {"name": "Lora AI Create Approved Basic Walls", "status": "committed",
                                    "created_count": len(created)}, "error": _safe_error(exc)}
        elif str(transaction_status) == "RolledBack":
            rolled_back = _with_transaction_outcome(created, "rolled_back")
            return {"status": "error", "committed": False,
                    "post_execution_identity_verified": False, "created_elements": rolled_back,
                    "transaction": {"name": "Lora AI Create Approved Basic Walls",
                                    "status": "rolled_back", "created_count": len(rolled_back)},
                    "error": _safe_error(exc)}
        return {"status": "indeterminate", "committed": None, "created_outcome": "uncertain",
                "post_execution_identity_verified": False,
                "created_elements": _with_transaction_outcome(created, "uncertain"),
                "transaction": {"name": "Lora AI Create Approved Basic Walls",
                                "status": "unknown", "created_count": len(created)},
                "error": _safe_error(exc)}
    try:
        post_identity = _current_identity(runtime, uiapp, doc, request["expected_identity"])
        request_by_id = dict((item["planned_element_id"], item) for item in request["walls"])
        final = [_wall_record(doc, doc.GetElement(_parse_element_id(item["element_id"])),
                              item.get("source_review_item_id"), request_by_id.get(item.get("planned_element_id")))
                 for item in created]
        final = _merge_created_identity(final, created)
        verified = (len(final) == len(request["walls"]) and
                    all(item.get("verified") for item in final) and
                    _identity_summary(post_identity) == _identity_summary(identity))
        final = _with_transaction_outcome(final, "committed")
        return {"status": "success" if verified else "verification_failed", "committed": True,
                "post_execution_identity_verified": _identity_summary(post_identity) == _identity_summary(identity),
                "transaction": {"name": "Lora AI Create Approved Basic Walls", "status": "committed",
                                "created_count": len(final)}, "created_elements": final,
                "diagnostics": {"precommit_identity_verified": True,
                                "postcommit_element_verification": verified}}
    except Exception as exc:
        committed_records = _with_transaction_outcome(created, "committed")
        return {"status": "verification_failed", "committed": True,
                "post_execution_identity_verified": False, "created_elements": committed_records,
                "transaction": {"name": "Lora AI Create Approved Basic Walls", "status": "committed",
                                "created_count": len(created)},
                "error": _safe_error(exc)}


def _confirm_in_revit_ui(request, resolved):
    from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult
    resolved_by_id = dict((item["planned_element_id"], item) for item in resolved)
    lines = []
    for wall in request["walls"]:
        type_level = resolved_by_id[wall["planned_element_id"]]
        vertical = ("top {} ({}) top offset {:.4f} ft".format(type_level.get("top_level_name"),
                    type_level.get("top_level_id"), wall.get("top_offset_internal_feet", 0.0))
                    if wall.get("top_constraint") is not None else
                    "unconnected height {:.4f} ft".format(wall.get("unconnected_height_internal_feet")))
        lines.append("• {} | {} ({:.4f} ft thick) | base {} ({}) | {} | base offset {:.4f} ft | structural={} flip={} | {}".format(
            wall["source_review_item_id"], type_level["wall_type_name"],
            type_level["wall_thickness_internal_feet"], type_level["base_level_name"],
            type_level["base_level_id"], vertical,
            wall["base_offset_internal_feet"], wall["structural"], wall["flip"],
            wall["parameter_assignments"]))
    warnings = request.get("validation_warnings") or []
    warning_lines = ["{}: {}".format(item.get("code", "warning"), item.get("message", ""))
                     for item in warnings if isinstance(item, dict)]
    content = ("Document: {}\nPath: {}\nPreview: {}\nRequest: {}\n\nWalls:\n{}\n\n"
               "Preview warnings:\n{}\n\nApproval statement: {}\n\n"
               "Click Yes to create all listed walls in one transaction. No cancels without changes.").format(
                   request["expected_identity"].get("document_title"),
                   request["expected_identity"].get("document_path") or "(unsaved document)",
                   request.get("preview_id"), request["request_id"], "\n".join(lines),
                   "\n".join(warning_lines) if warning_lines else "None",
                   request.get("approval_text", ""))
    dialog = TaskDialog("Lora AI — Confirm Basic Wall Creation")
    dialog.MainInstruction = "Create exactly {} Basic Wall(s) in the active Revit document?".format(len(lines))
    dialog.MainContent = content
    dialog.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    dialog.DefaultButton = TaskDialogResult.No
    return dialog.Show() == TaskDialogResult.Yes


def _validated_payload(payload, execution=False, verification=False):
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    expected = payload.get("expected_identity")
    required_identity = ("process_id", "process_session_id", "application_session_id", "document_session_id",
                         "document_lifecycle_sequence", "document_lifecycle_generation",
                         "active_document_switch_counter", "identity_provider_version")
    if not isinstance(expected, dict) or any(expected.get(key) in (None, "") for key in required_identity):
        raise ValueError("verified_document_session_identity_required")
    if execution:
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or len(request_id) > 100:
            raise ValueError("invalid_request_id")
        walls = payload.get("walls")
        validated = payload.get("validated_walls")
        if not isinstance(walls, list) or not walls or len(walls) > 25 or not isinstance(validated, list):
            raise ValueError("invalid_wall_batch")
        warnings = payload.get("validation_warnings")
        if not isinstance(warnings, list):
            raise ValueError("preview_validation_warnings_required")
        return {"request_id": request_id, "expected_identity": expected, "walls": walls,
                "validated_walls": validated, "preview_id": payload.get("preview_id"),
                "approval_text": payload.get("approval_text"), "validation_warnings": warnings}
    if verification:
        elements = payload.get("elements")
        if not isinstance(elements, list) or not elements or len(elements) > 500:
            raise ValueError("invalid_verification_batch")
        for item in elements:
            if not isinstance(item, dict) or not _valid_element_id(item.get("element_id")):
                raise ValueError("invalid_element_id")
        return {"expected_identity": expected, "elements": elements}
    walls = payload.get("walls")
    if not isinstance(walls, list) or not walls or len(walls) > 25:
        raise ValueError("invalid_wall_batch")
    return {"expected_identity": expected, "walls": walls}


def _current_identity(runtime, uiapp, doc, expected):
    result = runtime.identity_response(uiapp, doc)
    identity = result.get("identity") if result.get("status") == "success" else None
    if not isinstance(identity, dict):
        raise ValueError("active_document_identity_unavailable")
    actual = {"process_id": identity.get("process_id"),
              "process_session_id": identity.get("process_session_id"),
              "application_session_id": identity.get("application_session_id"),
              "document_session_id": identity.get("document_session_id"),
              "document_title": identity.get("document_title"), "document_path": identity.get("document_path"),
              "document_type": identity.get("document_type"), "revit_version": identity.get("revit_version"),
              "identity_provider_version": identity.get("identity_provider_version"),
              "document_lifecycle_sequence": identity.get("document_lifecycle_sequence"),
              "document_lifecycle_generation": identity.get("document_lifecycle_generation"),
              "active_document_switch_counter": identity.get("active_document_switch_counter")}
    for key in actual:
        if key in expected and expected.get(key) != actual.get(key):
            raise ValueError("document_identity_mismatch_{}".format(key))
    if actual["process_id"] != expected.get("process_id"):
        raise ValueError("revit_process_mismatch")
    return actual


def _resolve_walls(doc, walls):
    from Autodesk.Revit.DB import FilteredElementCollector, Level, WallType
    types = list(FilteredElementCollector(doc).OfClass(WallType).WhereElementIsElementType())
    levels = list(FilteredElementCollector(doc).OfClass(Level).WhereElementIsNotElementType())
    result = []
    seen = set()
    if bool(getattr(doc, "IsFamilyDocument", False)):
        raise ValueError("wall_generation_requires_project_document")
    for wall in walls:
        _validate_wall(wall)
        planned = wall["planned_element_id"]
        if planned in seen:
            raise ValueError("duplicate_planned_element_id")
        seen.add(planned)
        type_matches = [value for value in types if
                        (wall.get("wall_type_id") is not None and _element_id_text(value.Id) == str(wall.get("wall_type_id"))) or
                        (wall.get("wall_type_id") is None and str(value.Name) == wall["wall_type_name"])]
        base_ref = wall.get("base_constraint") or {"name": wall["base_level_name"]}
        level_matches = [value for value in levels if
                         (base_ref.get("element_id") is not None and _element_id_text(value.Id) == str(base_ref.get("element_id"))) or
                         (base_ref.get("element_id") is None and str(value.Name) == wall["base_level_name"])]
        if len(type_matches) != 1:
            raise ValueError("wall_type_missing_or_ambiguous")
        if len(level_matches) != 1:
            raise ValueError("base_level_missing_or_ambiguous")
        wall_type, level = type_matches[0], level_matches[0]
        if str(wall_type.Name) != wall["wall_type_name"] or str(level.Name) != wall["base_level_name"]:
            raise ValueError("wall_type_or_base_level_id_name_mismatch")
        if str(wall_type.Kind).lower() != "basic":
            raise ValueError("unsupported_wall_type_kind")
        top_ref = wall.get("top_constraint")
        top_level = None
        if top_ref is not None:
            top_matches = [value for value in levels if _element_id_text(value.Id) == str(top_ref.get("element_id"))]
            if len(top_matches) != 1 or str(top_matches[0].Name) != top_ref.get("name"):
                raise ValueError("top_level_missing_or_id_name_mismatch")
            top_level = top_matches[0]
            if float(top_level.Elevation) + float(wall.get("top_offset_internal_feet", 0.0)) <= float(level.Elevation) + float(wall.get("base_offset_internal_feet", 0.0)):
                raise ValueError("top_constraint_not_above_base_constraint")
        width = float(wall_type.Width)
        line = wall["centerline_internal_feet"]
        expected_z = float(level.Elevation) + float(wall.get("base_offset_internal_feet", 0.0))
        if (abs(float(line[0][2]) - expected_z) > 1e-4 or abs(float(line[1][2]) - expected_z) > 1e-4):
            raise ValueError("mapped_wall_elevation_does_not_match_selected_base_level")
        expected_width = wall.get("expected_thickness_internal_feet")
        if not _finite(width) or width <= 0:
            raise ValueError("wall_type_thickness_unavailable")
        if expected_width is not None and (not _finite(expected_width) or abs(width - float(expected_width)) > 1e-4):
            raise ValueError("mapped_wall_type_thickness_differs_from_approved_preview")
        result.append({"planned_element_id": planned, "wall_type_id": _element_id_text(wall_type.Id),
                       "wall_type_name": str(wall_type.Name), "wall_thickness_internal_feet": width,
                       "base_level_id": _element_id_text(level.Id), "base_level_name": str(level.Name),
                       "base_level_elevation_internal_feet": float(level.Elevation),
                       "top_level_id": _element_id_text(top_level.Id) if top_level else None,
                       "top_level_name": str(top_level.Name) if top_level else None,
                       "top_level_elevation_internal_feet": float(top_level.Elevation) if top_level else None})
    return result


def _validate_wall(wall):
    if not isinstance(wall, dict):
        raise ValueError("invalid_wall_item")
    required = ("planned_element_id", "source_review_item_id", "wall_type_name", "wall_type_id", "base_level_name",
                "centerline_internal_feet", "base_offset_internal_feet",
                "structural", "flip", "parameter_assignments")
    if any(wall.get(key) in (None, "") for key in required):
        raise ValueError("wall_item_missing_required_mapping")
    for key in ("planned_element_id", "source_review_item_id", "wall_type_name", "base_level_name"):
        if not isinstance(wall.get(key), str) or len(wall[key]) > 300:
            raise ValueError("wall_mapping_text_invalid")
    line = wall["centerline_internal_feet"]
    if not isinstance(line, list) or len(line) != 2 or any(not _point3(point) for point in line):
        raise ValueError("invalid_wall_centerline")
    length = math.sqrt(sum((line[1][index] - line[0][index]) ** 2 for index in range(3)))
    if length <= 1e-4:
        raise ValueError("wall_centerline_too_short")
    if wall.get("top_constraint") is None:
        if not _finite(wall.get("unconnected_height_internal_feet")) or float(wall["unconnected_height_internal_feet"]) <= 0:
            raise ValueError("invalid_unconnected_wall_height")
    elif wall.get("unconnected_height_internal_feet") is not None:
        raise ValueError("unconnected_height_not_allowed_with_top_constraint")
    if wall.get("top_constraint") is not None and (not isinstance(wall.get("top_constraint"), dict) or
            not _valid_element_id(wall["top_constraint"].get("element_id")) or
            not isinstance(wall["top_constraint"].get("name"), str)):
        raise ValueError("invalid_top_constraint")
    base_ref = wall.get("base_constraint")
    if (not isinstance(base_ref, dict) or not _valid_element_id(base_ref.get("element_id")) or
            not isinstance(base_ref.get("name"), str) or base_ref.get("name") != wall.get("base_level_name")):
        raise ValueError("invalid_base_constraint")
    if abs(float(line[0][2]) - float(line[1][2])) > 1e-6:
        raise ValueError("wall_centerline_must_be_horizontal")
    expected_length = wall.get("expected_length_internal_feet")
    actual_length = math.sqrt(sum((line[1][index] - line[0][index]) ** 2 for index in range(3)))
    if expected_length is not None and (not _finite(expected_length) or
            abs(actual_length - float(expected_length)) > max(0.5 / 304.8, float(expected_length) * 1e-6)):
        raise ValueError("wall_length_does_not_match_approved_geometry")
    if not _finite(wall["base_offset_internal_feet"]):
        raise ValueError("invalid_base_offset")
    if not _finite(wall.get("top_offset_internal_feet", 0.0)):
        raise ValueError("invalid_top_offset")
    if not isinstance(wall["structural"], bool) or not isinstance(wall["flip"], bool):
        raise ValueError("structural_and_flip_must_be_explicit_booleans")
    assignments = wall["parameter_assignments"]
    if not isinstance(assignments, dict) or set(assignments.keys()) - set(("Mark", "Comments")):
        raise ValueError("unsupported_wall_parameter_assignment")
    if any(not isinstance(value, str) for value in assignments.values()):
        raise ValueError("wall_parameter_value_must_be_text")


def _reject_existing_duplicates(doc, planned, resolved=None):
    from Autodesk.Revit.DB import BuiltInParameter, FilteredElementCollector, Wall
    existing = []
    for wall in FilteredElementCollector(doc).OfClass(Wall).WhereElementIsNotElementType():
        location = getattr(wall, "Location", None)
        curve = getattr(location, "Curve", None) if location else None
        if curve is not None and hasattr(curve, "GetEndPoint"):
            existing.append({"line": (curve.GetEndPoint(0), curve.GetEndPoint(1)),
                             "vertical": _existing_vertical_span(doc, wall),
                             "width": _wall_width(doc, wall)})
    resolved_by_id = dict((item["planned_element_id"], item) for item in (resolved or []))
    planned_lines = []
    for item in planned:
        line = (_xyz(item["centerline_internal_feet"][0]), _xyz(item["centerline_internal_feet"][1]))
        match = resolved_by_id.get(item["planned_element_id"], {})
        bottom = float(match.get("base_level_elevation_internal_feet", line[0].Z)) + float(item.get("base_offset_internal_feet", 0.0))
        top_level_z = match.get("top_level_elevation_internal_feet")
        top = (float(top_level_z) + float(item.get("top_offset_internal_feet", 0.0)) if top_level_z is not None
               else bottom + float(item.get("unconnected_height_internal_feet") or 0.0))
        planned_lines.append({"id": item["planned_element_id"], "line": line, "vertical": (bottom, top),
                              "width": float(match.get("wall_thickness_internal_feet") or item.get("expected_thickness_internal_feet") or 0.0)})
    for index, current in enumerate(planned_lines):
        for other in existing:
            if _vertical_overlap(current["vertical"], other["vertical"]):
                interaction = _classify_wall_interaction(current["line"], other["line"], current["width"], other["width"])
                _raise_for_wall_interaction(interaction, "existing")
        for other in planned_lines[index + 1:]:
            if _vertical_overlap(current["vertical"], other["vertical"]):
                interaction = _classify_wall_interaction(current["line"], other["line"], current["width"], other["width"])
                _raise_for_wall_interaction(interaction, "planned")


def _raise_for_wall_interaction(interaction, source):
    if interaction == "separate":
        return
    if interaction.endswith("_manual_review") or interaction == "ambiguous_thickness_overlap":
        raise ValueError("{}_wall_{}_manual_review_required".format(source, interaction))
    raise ValueError("{}_wall_{}".format(source, interaction))


def _wall_width(doc, wall):
    try:
        element = doc.GetElement(wall.GetTypeId())
        return float(element.Width) if element is not None else 0.0
    except Exception:
        return 0.0


def _created_element_record(wall, request_item):
    """Capture IDs immediately after Create, before any fallible follow-up calls."""
    if wall is None or getattr(wall, "Id", None) is None:
        raise ValueError("wall_creation_returned_no_element_id")
    element_id = _element_id_text(wall.Id)
    if not element_id:
        raise ValueError("wall_creation_returned_invalid_element_id")
    try:
        unique_id = getattr(wall, "UniqueId", None)
    except Exception:
        unique_id = None
    return {"element_id": element_id, "unique_id": str(unique_id) if unique_id else None,
            "planned_element_id": request_item.get("planned_element_id"),
            "source_review_item_id": request_item.get("source_review_item_id"),
            "verified": False, "creation_state": "created_in_transaction",
            "transaction_outcome": "uncommitted"}


def _replace_created_record(records, old_record, new_record):
    for index, record in enumerate(records):
        if record is old_record:
            new_record["element_id"] = old_record["element_id"]
            new_record["unique_id"] = old_record["unique_id"]
            records[index] = new_record
            return


def _with_transaction_outcome(records, outcome):
    result = []
    for record in records:
        updated = dict(record)
        updated["transaction_outcome"] = outcome
        if outcome == "rolled_back":
            updated["creation_state"] = "rolled_back"
            updated["verified"] = False
        elif outcome == "committed":
            updated["creation_state"] = "committed"
        else:
            updated["creation_state"] = "uncertain"
        result.append(updated)
    return result


def _merge_created_identity(records, captured_records):
    captured = dict((item.get("element_id"), item) for item in captured_records)
    merged = []
    for record in records:
        value = dict(record)
        original = captured.get(value.get("element_id"), {})
        for key in ("unique_id", "planned_element_id", "source_review_item_id"):
            if key not in value and original.get(key) is not None:
                value[key] = original[key]
        merged.append(value)
    return merged


def _existing_vertical_span(doc, wall):
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        level = doc.GetElement(wall.LevelId)
        base_offset = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET)
        bottom = float(level.Elevation) + (float(base_offset.AsDouble()) if base_offset and base_offset.HasValue else 0.0)
        top_ref = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE)
        top_offset = wall.get_Parameter(BuiltInParameter.WALL_TOP_OFFSET)
        top_id = top_ref.AsElementId() if top_ref and top_ref.HasValue else None
        top_level = doc.GetElement(top_id) if top_id is not None else None
        if top_level is not None:
            return bottom, float(top_level.Elevation) + (float(top_offset.AsDouble()) if top_offset and top_offset.HasValue else 0.0)
        height = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)
        if height and height.HasValue:
            return bottom, bottom + float(height.AsDouble())
    except Exception:
        pass
    return (float("-inf"), float("inf"))


def _vertical_overlap(first, second):
    return min(first[1], second[1]) - max(first[0], second[0]) > 1e-6


def _wall_footprints_overlap(first, second, first_width, second_width):
    interaction = _classify_wall_interaction(first, second, first_width, second_width)
    return interaction != "separate"


def _classify_wall_interaction(first, second, first_width, second_width):
    """Classify plan interactions; junctions remain blocked for manual review."""
    if _segments_collinear_overlap(first[0], first[1], second[0], second[1]):
        if _same_segment(first, second):
            return "duplicate"
        return "collinear_overlap"
    if _segments_intersect_xy(first[0], first[1], second[0], second[1]):
        kind = _classify_intersection(first, second)
        return kind
    clearance = max(0.0, (float(first_width) + float(second_width)) / 2.0 - 1e-6)
    if _segment_distance_xy(first[0], first[1], second[0], second[1]) < clearance:
        return "ambiguous_thickness_overlap"
    return "separate"


def _classify_intersection(first, second):
    ends_a = (first[0], first[1])
    ends_b = (second[0], second[1])
    shared = [(a, b) for a in ends_a for b in ends_b if _point_distance_xy(a, b) <= 1e-6]
    if len(shared) == 1:
        if _segments_perpendicular_xy(first, second):
            return "l_junction_manual_review"
        return "endpoint_to_endpoint_junction_manual_review"
    if shared:
        return "duplicate"
    a_endpoint = any(_point_on_segment_xy(point, second[0], second[1]) for point in ends_a)
    b_endpoint = any(_point_on_segment_xy(point, first[0], first[1]) for point in ends_b)
    if a_endpoint or b_endpoint:
        return "t_junction_manual_review"
    return "unintended_intersection"


def _same_segment(first, second):
    return ((_point_distance_xy(first[0], second[0]) <= 1e-6 and _point_distance_xy(first[1], second[1]) <= 1e-6) or
            (_point_distance_xy(first[0], second[1]) <= 1e-6 and _point_distance_xy(first[1], second[0]) <= 1e-6))


def _segments_collinear_overlap(a, b, c, d):
    cross1 = (b.X-a.X)*(c.Y-a.Y) - (b.Y-a.Y)*(c.X-a.X)
    cross2 = (b.X-a.X)*(d.Y-a.Y) - (b.Y-a.Y)*(d.X-a.X)
    if abs(cross1) > 1e-8 or abs(cross2) > 1e-8:
        return False
    axis = 0 if abs(b.X-a.X) >= abs(b.Y-a.Y) else 1
    av, bv = (a.X, b.X) if axis == 0 else (a.Y, b.Y)
    cv, dv = (c.X, d.X) if axis == 0 else (c.Y, d.Y)
    return min(max(av, bv), max(cv, dv)) - max(min(av, bv), min(cv, dv)) > 1e-6


def _point_distance_xy(first, second):
    return math.hypot(first.X-second.X, first.Y-second.Y)


def _point_on_segment_xy(point, start, end):
    cross = (end.X-start.X)*(point.Y-start.Y) - (end.Y-start.Y)*(point.X-start.X)
    if abs(cross) > 1e-6:
        return False
    dot = (point.X-start.X)*(end.X-start.X) + (point.Y-start.Y)*(end.Y-start.Y)
    length2 = (end.X-start.X)**2 + (end.Y-start.Y)**2
    return -1e-6 <= dot <= length2+1e-6


def _segments_perpendicular_xy(first, second):
    ax, ay = first[1].X-first[0].X, first[1].Y-first[0].Y
    bx, by = second[1].X-second[0].X, second[1].Y-second[0].Y
    denom = math.hypot(ax, ay) * math.hypot(bx, by)
    return denom > 1e-12 and abs((ax*bx+ay*by)/denom) <= 1e-6


def _segments_intersect_xy(a, b, c, d):
    def orient(p, q, r):
        return (q.X-p.X)*(r.Y-p.Y) - (q.Y-p.Y)*(r.X-p.X)
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    eps = 1e-10
    def on_segment(p, q, r):
        return min(p.X, r.X)-eps <= q.X <= max(p.X, r.X)+eps and min(p.Y, r.Y)-eps <= q.Y <= max(p.Y, r.Y)+eps
    if abs(o1) <= eps and on_segment(a, c, b): return True
    if abs(o2) <= eps and on_segment(a, d, b): return True
    if abs(o3) <= eps and on_segment(c, a, d): return True
    if abs(o4) <= eps and on_segment(c, b, d): return True
    return (o1 < 0) != (o2 < 0) and (o3 < 0) != (o4 < 0)


def _segment_distance_xy(a, b, c, d):
    def point_segment(p, x, y):
        dx, dy = y.X-x.X, y.Y-x.Y
        denom = dx*dx + dy*dy
        if denom <= 1e-20:
            return math.hypot(p.X-x.X, p.Y-x.Y)
        t = max(0.0, min(1.0, ((p.X-x.X)*dx + (p.Y-x.Y)*dy)/denom))
        return math.hypot(p.X-(x.X+t*dx), p.Y-(x.Y+t*dy))
    return min(point_segment(a, c, d), point_segment(b, c, d),
               point_segment(c, a, b), point_segment(d, a, b))


def _wall_record(doc, wall, source_review_item_id, request_item):
    from Autodesk.Revit.DB import BuiltInCategory, BuiltInParameter, Wall
    if wall is None or not isinstance(wall, Wall):
        return {"verified": False, "error": "wall_unavailable_after_creation"}
    type_element = doc.GetElement(wall.GetTypeId())
    level = doc.GetElement(wall.LevelId)
    location = getattr(wall, "Location", None)
    curve = getattr(location, "Curve", None) if location else None
    if curve is None:
        return {"verified": False, "element_id": _element_id_text(wall.Id), "error": "wall_location_unavailable"}
    endpoints = [curve.GetEndPoint(0), curve.GetEndPoint(1)]
    height_param = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)
    base_offset_param = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET)
    top_constraint_param = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE)
    top_offset_param = wall.get_Parameter(BuiltInParameter.WALL_TOP_OFFSET)
    height = height_param.AsDouble() if height_param and height_param.HasValue else None
    offset = base_offset_param.AsDouble() if base_offset_param and base_offset_param.HasValue else None
    top_element_id = top_constraint_param.AsElementId() if top_constraint_param and top_constraint_param.HasValue else None
    top_level = doc.GetElement(top_element_id) if top_element_id is not None else None
    top_level_id = _element_id_text(top_element_id) if top_level is not None else None
    top_offset = top_offset_param.AsDouble() if top_offset_param and top_offset_param.HasValue else None
    if top_level is not None and level is not None:
        height = float(top_level.Elevation) + float(top_offset or 0.0) - float(level.Elevation) - float(offset or 0.0)
    expected = request_item or {}
    expected_type_name = expected.get("wall_type_name", expected.get("wall_type"))
    expected_level_name = expected.get("base_level_name", expected.get("base_level"))
    expected_base = expected.get("base_constraint") or {}
    expected_top = expected.get("top_constraint") or {}
    expected_height = expected.get("unconnected_height_internal_feet", expected.get("height_internal_feet"))
    checks = {"category": wall.Category is not None and wall.Category.Id.IntegerValue == int(BuiltInCategory.OST_Walls),
              "wall_type": type_element is not None and str(type_element.Name) == (expected_type_name or (str(type_element.Name) if type_element else "")),
              "wall_type_id": type_element is not None and (not expected.get("wall_type_id") or
                             _element_id_text(wall.GetTypeId()) == str(expected.get("wall_type_id"))),
              "base_level": (level is not None and str(level.Name) == (expected_level_name or (str(level.Name) if level else "")) and
                             (not expected_base.get("element_id") or _element_id_text(wall.LevelId) == str(expected_base.get("element_id")))),
              "height_mode": ((expected.get("top_constraint") is not None and top_level_id == str(expected_top.get("element_id")) and
                               top_level is not None and str(top_level.Name) == expected_top.get("name") and
                               (expected_height is None or (height is not None and abs(height-float(expected_height)) <= 1e-6))) or
                              (expected.get("top_constraint") is None and height is not None and
                               (expected_height is None or abs(height - float(expected_height)) <= 1e-6))),
              "top_offset": (expected.get("top_constraint") is None or (top_offset is not None and
                              abs(top_offset - float(expected.get("top_offset_internal_feet", 0.0))) <= 1e-6)),
              "base_offset": offset is not None and ("base_offset_internal_feet" not in expected or abs(offset - float(expected["base_offset_internal_feet"])) <= 1e-6),
              "length": abs(float(curve.Length) - float(expected.get("length_internal_feet", curve.Length))) <= 1e-6,
              "thickness": type_element is not None and abs(float(type_element.Width) - float(expected.get("thickness_internal_feet", type_element.Width))) <= 1e-6,
              "structural": ("structural" not in expected or _structural_value(wall) == expected.get("structural")),
              "flip": ("flip" not in expected or bool(getattr(wall, "Flipped", False)) == expected.get("flip")),
              "location": _location_matches(endpoints, expected.get("location_internal_feet"))}
    for name, value in (expected.get("parameter_assignments") or {}).items():
        checks["parameter_{}".format(name)] = _parameter_string(wall, name) == value
    return {"verified": all(checks.values()), "planned_element_id": expected.get("planned_element_id"),
            "element_id": _element_id_text(wall.Id),
            "category": str(wall.Category.Name) if wall.Category else None,
            "wall_type": str(type_element.Name) if type_element else None,
            "wall_type_id": _element_id_text(wall.GetTypeId()),
            "base_level": str(level.Name) if level else None,
            "base_level_id": _element_id_text(wall.LevelId), "height_internal_feet": height,
            "top_constraint_level_id": top_level_id, "top_offset_internal_feet": top_offset,
            "top_constraint_level_name": str(top_level.Name) if top_level else None,
            "base_offset_internal_feet": offset, "length_internal_feet": float(curve.Length),
            "thickness_internal_feet": float(type_element.Width) if type_element else None,
            "location_internal_feet": [[float(endpoints[0].X), float(endpoints[0].Y), float(endpoints[0].Z)],
                                        [float(endpoints[1].X), float(endpoints[1].Y), float(endpoints[1].Z)]],
            "structural": _structural_value(wall), "source_review_item_id": source_review_item_id,
            "flip": bool(getattr(wall, "Flipped", False)),
            "parameter_assignments": {name: _parameter_string(wall, name) for name in ("Mark", "Comments")},
            "requested_parameter_assignments": dict(expected.get("parameter_assignments") or {}),
            "verification_checks": checks}


def _apply_parameters(wall, values):
    for name, value in values.items():
        parameter = wall.LookupParameter(name)
        if parameter is None or parameter.IsReadOnly or str(parameter.StorageType) != "String":
            raise ValueError("wall_parameter_missing_read_only_or_not_string")
        if not bool(parameter.Set(value)):
            raise ValueError("wall_parameter_assignment_failed")


def _structural_value(wall):
    from Autodesk.Revit.DB import BuiltInParameter
    try:
        parameter = wall.get_Parameter(BuiltInParameter.WALL_STRUCTURAL_SIGNIFICANT)
        return bool(parameter.AsInteger()) if parameter and parameter.HasValue else None
    except Exception:
        return None


def _parameter_string(element, name):
    try:
        parameter = element.LookupParameter(name)
        return parameter.AsString() if parameter and parameter.HasValue else None
    except Exception:
        return None


def _location_matches(actual, expected):
    if expected is None:
        return True
    if not isinstance(expected, list) or len(expected) != 2:
        return False
    tol = 1e-6
    def close(left, right):
        coordinates = (left.X, left.Y, left.Z) if hasattr(left, "X") else left
        return all(abs(float(coordinates[index]) - float(right[index])) <= tol for index in range(3))
    return ((close(actual[0], expected[0]) and close(actual[1], expected[1])) or
            (close(actual[0], expected[1]) and close(actual[1], expected[0])))


def _identity_summary(identity):
    return {key: identity.get(key) for key in ("process_id", "process_session_id", "application_session_id",
            "document_session_id", "document_title", "document_path", "document_type", "revit_version",
            "document_lifecycle_sequence", "document_lifecycle_generation", "active_document_switch_counter")}


def _element_id(value):
    from Autodesk.Revit.DB import ElementId
    try:
        from System import Int64
        return ElementId(Int64.Parse(str(value)))
    except Exception:
        return ElementId(int(value))


def _element_id_text(value):
    try:
        return str(int(value.Value))
    except Exception:
        return str(int(value.IntegerValue))


def _parse_element_id(value):
    if not _valid_element_id(value):
        raise ValueError("invalid_element_id")
    return _element_id(value)


def _valid_element_id(value):
    try:
        int(value)
        return str(value).strip() != ""
    except Exception:
        return False


def _xyz(value):
    from Autodesk.Revit.DB import XYZ
    return XYZ(float(value[0]), float(value[1]), float(value[2]))


def _point3(value):
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(_finite(item) for item in value)


def _finite(value):
    try:
        number = float(value)
        return not math.isnan(number) and not math.isinf(number)
    except Exception:
        return False


def _points_same(a, b, c, d):
    tol = 1e-6
    direct = _distance(a, c) <= tol and _distance(b, d) <= tol
    reverse = _distance(a, d) <= tol and _distance(b, c) <= tol
    return direct or reverse


def _distance(a, b):
    return math.sqrt((a.X - b.X) ** 2 + (a.Y - b.Y) ** 2 + (a.Z - b.Z) ** 2)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _safe_error(exc):
    return {"code": str(exc) if str(exc) and len(str(exc)) < 180 else "revit_operation_failed",
            "message": "Revit rejected the wall operation or its validation.",
            "exception_type": type(exc).__name__}
