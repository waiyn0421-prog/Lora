"""Strict orchestration for explicitly confirmed Basic Wall creation."""

import copy
import hashlib
import json
import math
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from lora_mcp.client import RevitMCPError, _identity_key
from lora_generation.engine import _build_item, _normalize_coordinate_mapping
from lora_generation.preview import _preview_item

from .errors import ExecutionError


_ALLOWED_ASSIGNMENTS = {"Mark", "Comments"}
_IDENTITY_FIELDS = ("revit_process_id", "process_session_id", "application_session_id",
                    "document_session_id", "document_title", "document_path", "document_type",
                    "revit_version", "identity_provider_version")


class ExecutionEngine:
    def __init__(self, planner, preview_engine, review_manager, client, repository,
                 timeout=(2.0, 60.0), max_response_bytes=2 * 1024 * 1024,
                 http_session=None):
        self.planner = planner
        self.preview_engine = preview_engine
        self.review_manager = review_manager
        self.client = client
        self.repository = repository
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.http_session = http_session or requests.Session()
        self.http_session.trust_env = False

    def prepare(self, preview_id, wall_constraints):
        preview = self.preview_engine.get(preview_id)
        inventory = self._inventory()
        plan = self.planner.repository.get(preview["source_plan_id"])
        checked = self.planner.validate_snapshot(plan, inventory)
        self._assert_current(preview, plan, checked, inventory)
        walls = self._build_walls(preview, wall_constraints, plan)
        resolution = self._post("/validate_walls", {"expected_identity": _sidecar_identity(inventory["document_identity"]),
                                                      "walls": walls})
        if resolution.get("status") != "success" or len(resolution.get("walls") or []) != len(walls):
            raise ExecutionError("wall_mapping_unavailable", "Revit could not resolve every explicit wall type and level",
                                 {"validation": resolution})
        request_id = str(uuid.uuid4())
        nonce = secrets.token_urlsafe(24)
        approval_text = _approval_text(len(walls), inventory["document_title"])
        request = {"execution_schema_version": 1, "request_id": request_id,
                   "preview_id": preview_id, "plan_id": preview["source_plan_id"],
                   "source_review_id": preview["source_review_id"],
                   "target_identity": _sidecar_identity(inventory["document_identity"]),
                   "document_display": inventory["document_title"], "revit_version": inventory["revit_version"],
                   "prepared_at": _now(), "approval_text": approval_text, "approval_nonce": nonce,
                   "wall_count": len(walls), "walls": walls, "validated_walls": resolution["walls"],
                   "validation_warnings": copy.deepcopy(preview["validation"].get("warnings", [])),
                   "state": "prepared", "execution_supported": True,
                   "restriction": "Basic Walls only. No execution until the exact approval text and one-time nonce are supplied."}
        return self.repository.create_request(request, _hash(nonce))

    def validate(self, request_id):
        request = self.repository.get_request(request_id)
        if request["state"] != "prepared":
            return {"status": "rejected", "code": "execution_request_consumed", "state": request["state"]}
        if _parse_time(request["expires_at"]) <= datetime.now(timezone.utc):
            return {"status": "rejected", "code": "execution_request_expired", "state": "expired"}
        preview = self.preview_engine.get(request["preview_id"])
        inventory = self._inventory()
        plan = self.planner.repository.get(request["plan_id"])
        checked = self.planner.validate_snapshot(plan, inventory)
        self._assert_current(preview, plan, checked, inventory)
        current = self._post("/validate_walls", {"expected_identity": request["target_identity"],
                                                 "walls": request["walls"]})
        if (current.get("status") != "success" or
                current.get("walls") != request.get("validated_walls")):
            return {"status": "rejected", "code": "wall_mapping_changed", "diagnostics": current}
        return {"status": "valid", "request_id": request_id, "preview_id": request["preview_id"],
                "wall_count": request["wall_count"], "document_identity": request["target_identity"],
                "warnings": request["validation_warnings"], "expires_at": request["expires_at"]}

    def execute(self, request_id, approval_nonce, approval_text):
        if not isinstance(approval_text, str) or not isinstance(approval_nonce, str):
            raise ExecutionError("confirmation_required", "Both the exact approval text and one-time nonce are required")
        request = self.repository.get_request(request_id)
        if approval_text != request["approval_text"]:
            raise ExecutionError("confirmation_mismatch", "Approval text must exactly match the reviewed execution preview")
        self.repository.begin(request_id, _hash(approval_nonce))
        try:
            validation = self.validate_consuming_request(request)
            payload = {"request_id": request_id, "expected_identity": request["target_identity"],
                       "preview_id": request["preview_id"], "approval_text": request["approval_text"],
                       "validation_warnings": request["validation_warnings"],
                       "walls": request["walls"], "validated_walls": request["validated_walls"]}
            sidecar_result = self._post("/execute_walls", payload, write=True)
            # A lost/partial response never becomes a success claim. The request is single-use.
            sidecar_status = sidecar_result.get("status")
            committed = sidecar_result.get("committed", False)
            if sidecar_status != "success":
                result = {"status": "cancelled" if sidecar_status == "cancelled" else sidecar_status or "failed",
                          "committed": committed,
                          "request_id": request_id, "error": sidecar_result.get("error"),
                          "diagnostics": sidecar_result.get("diagnostics"),
                          "transaction": sidecar_result.get("transaction"),
                          "created_elements": sidecar_result.get("created_elements") or []}
            else:
                source_by_item = {item["source_review_item_id"]: item for item in request["walls"]}
                created = sidecar_result.get("created_elements") or []
                for element in created:
                    source = source_by_item.get(element.get("source_review_item_id"), {})
                    element.update({key: source.get(key) for key in (
                        "planned_element_id", "source_drawing", "source_page", "source_geometry_references")})
                valid_created_set = (len(created) == request["wall_count"] and
                                     all(item.get("verified") is True and item.get("element_id")
                                         for item in created) and
                                     len({item.get("element_id") for item in created}) == len(created))
                result = {"status": "success" if valid_created_set else "verification_failed",
                          "committed": True, "request_id": request_id,
                          "preview_id": request["preview_id"], "document_identity": request["target_identity"],
                          "created_elements": created,
                          "transaction": sidecar_result.get("transaction"),
                          "post_execution_identity_verified": sidecar_result.get("post_execution_identity_verified") is True,
                          "pre_execution_validation": validation}
                if not result["post_execution_identity_verified"] or not valid_created_set:
                    result["status"] = "verification_failed"
                    result["error"] = {"code": "post_execution_identity_unverified",
                                        "message": ("Walls committed but the target document session could not be verified afterward."
                                                    if not result["post_execution_identity_verified"] else
                                                    "Walls committed but the created element set did not pass complete verification.")}
            return self.repository.finish(request_id, result)
        except Exception as exc:
            uncertain = isinstance(exc, ExecutionError) and exc.code in (
                "revit_execution_timeout", "revit_execution_connection_error")
            result = {"status": "indeterminate" if uncertain else "failed",
                      "committed": None if uncertain else False, "request_id": request_id,
                      "error": _safe_error(exc), "created_elements": []}
            self.repository.finish(request_id, result)
            raise

    def validate_consuming_request(self, request):
        # begin() has moved the request to executing; repeat all live checks without requiring prepared state.
        preview = self.preview_engine.get(request["preview_id"])
        inventory = self._inventory()
        plan = self.planner.repository.get(request["plan_id"])
        checked = self.planner.validate_snapshot(plan, inventory)
        self._assert_current(preview, plan, checked, inventory)
        resolved = self._post("/validate_walls", {"expected_identity": request["target_identity"],
                                                  "walls": request["walls"]})
        if resolved.get("status") != "success" or resolved.get("walls") != request["validated_walls"]:
            raise ExecutionError("wall_mapping_changed", "Wall type, level, or geometry resolution changed after approval",
                                 {"current": resolved})
        return {"status": "valid", "validated_at": _now()}

    def result(self, request_id):
        request = self.repository.get_request(request_id)
        if request.get("state") == "indeterminate":
            receipt = self._get_execution_status(request_id)
            if receipt and receipt.get("state") == "complete" and isinstance(receipt.get("response"), dict):
                response = receipt["response"]
                source_by_item = {item["source_review_item_id"]: item for item in request.get("walls", [])}
                created = response.get("created_elements") or []
                for element in created:
                    source = source_by_item.get(element.get("source_review_item_id"), {})
                    element.update({key: source.get(key) for key in (
                        "planned_element_id", "source_drawing", "source_page", "source_geometry_references")})
                normalized = {"status": response.get("status"), "committed": response.get("committed"),
                              "request_id": request_id, "created_elements": created,
                              "transaction": response.get("transaction"),
                              "post_execution_identity_verified": response.get("post_execution_identity_verified")}
                if response.get("status") != "success":
                    normalized["error"] = response.get("error")
                request = self.repository.finish(request_id, normalized)
        return request

    def verify(self, request_id):
        request, elements = self.repository.list_created_elements(request_id)
        if request["state"] not in ("succeeded", "committed_unverified"):
            raise ExecutionError("execution_not_committed", "There is no committed wall set to verify")
        response = self._post("/verify_walls", {"expected_identity": request["target_identity"],
                                                "elements": [{"element_id": item["element_id"],
                                                              "source_review_item_id": item["source_review_item_id"],
                                                              "expected": dict({key: item.get(key) for key in (
                                                                  "wall_type", "base_level", "height_internal_feet",
                                                                  "base_offset_internal_feet", "length_internal_feet",
                                                                  "thickness_internal_feet", "structural",
                                                                  "location_internal_feet", "requested_parameter_assignments")},
                                                                  parameter_assignments=item.get("requested_parameter_assignments") or {},
                                                                  wall_type_id=item.get("wall_type_id"),
                                                                  base_constraint={"element_id": item.get("base_level_id"),
                                                                      "name": item.get("base_level")},
                                                                  top_constraint=({"element_id": item.get("top_constraint_level_id"),
                                                                      "name": item.get("top_constraint_level_name")}
                                                                      if item.get("top_constraint_level_id") else None),
                                                                  top_offset_internal_feet=item.get("top_offset_internal_feet"))}
                                                             for item in elements]})
        return {"status": response.get("status"), "request_id": request_id,
                "document_identity": request["target_identity"], "verification": response,
                "source_references": [{"element_id": item.get("element_id"),
                                       "source_review_item_id": item.get("source_review_item_id"),
                                       "source_drawing": item.get("source_drawing"),
                                       "source_page": item.get("source_page"),
                                       "source_geometry_references": item.get("source_geometry_references")}
                                      for item in elements]}

    def _inventory(self):
        from lora_generation.inventory import read_generation_inventory
        try:
            value = read_generation_inventory(self.client)
            identity = self.client.active_document_identity()
        except RevitMCPError as exc:
            raise ExecutionError("revit_unavailable", "Live Revit inventory and session identity are required",
                                 {"cause": exc.as_dict()})
        if identity.get("identity_provider_status") != "ready" or not identity.get("identity_certain_for_guard"):
            raise ExecutionError("document_identity_uncertain", "A verified Revit-side document session identity is required")
        value["document_identity"] = identity
        value["document_title"] = identity.get("document_title")
        value["revit_version"] = identity.get("revit_version")
        return value

    def _assert_current(self, preview, plan, checked_plan, inventory):
        if preview.get("stale") or int(preview.get("source_plan_version", -1)) != int(plan.get("plan_version", 1)):
            raise ExecutionError("preview_stale", "The generation preview is stale")
        if preview.get("inventory_verification") != "live_verified" or preview.get("inventory_source") != "live_revitmcp":
            raise ExecutionError("preview_not_live_verified", "A live Revit-verified generation preview is required")
        if preview.get("document_identity") is None or not _same_identity(preview["document_identity"], inventory["document_identity"]):
            raise ExecutionError("document_changed", "The active Revit document session differs from the preview target")
        rejected_ids = {item.get("review_item_id") for item in checked_plan.get("unresolved_items", [])
                        if item.get("status") == "rejected"}
        blocking_errors = [issue for issue in checked_plan.get("validation", {}).get("errors", [])
                           if issue.get("review_item_id") not in rejected_ids]
        blocking_unresolved = [item for item in checked_plan.get("unresolved_items", [])
                               if item.get("status") != "rejected"]
        if blocking_errors or blocking_unresolved:
            raise ExecutionError("generation_plan_blocked", "The current plan has validation errors or unresolved drawing items",
                                 {"errors": blocking_errors, "unresolved_items": blocking_unresolved})
        blocking_preview_errors = [issue for issue in preview.get("validation", {}).get("errors", [])
                                   if issue.get("review_item_id") not in rejected_ids]
        if blocking_preview_errors:
            raise ExecutionError("preview_blocked", "The approved preview contains validation errors",
                                 {"errors": blocking_preview_errors})
        if not plan.get("items") or any(item.get("planned_category") != "Walls" for item in plan["items"]):
            raise ExecutionError("unsupported_plan_contents", "Every planned item must be an approved Basic Wall candidate")
        preview_by_id = {item.get("planned_element_id"): item for item in preview.get("items", [])}
        if len(preview_by_id) != len(preview.get("items", [])):
            raise ExecutionError("preview_duplicate_items", "Preview contains duplicate planned element IDs")
        expected_review_ids = {item.get("review_item_id") for item in plan["items"]}
        checked_review_ids = [item.get("review_item_id") for item in checked_plan["items"]]
        preview_review_ids = [item.get("source_review_item_id") for item in preview.get("items", [])]
        if (len(expected_review_ids) != len(plan["items"]) or
                len(checked_review_ids) != len(set(checked_review_ids)) or
                len(preview_review_ids) != len(set(preview_review_ids)) or
                len(preview_review_ids) != len(plan["items"]) or
                set(checked_review_ids) != expected_review_ids or
                set(preview_review_ids) != expected_review_ids):
            raise ExecutionError("preview_plan_mismatch", "Preview items do not exactly match current approved plan items")
        plan_by_item_id = {item.get("plan_item_id"): item for item in checked_plan["items"]}
        if len(plan_by_item_id) != len(checked_plan["items"]):
            raise ExecutionError("plan_duplicate_items", "Current plan contains duplicate planned element IDs")
        coordinate_mapping = _normalize_coordinate_mapping(
            (checked_plan.get("target_coordinate_system") or {}).get("user_mapping"))
        expected_preview_items = {}
        for plan_item in checked_plan["items"]:
            try:
                current_review_item = self.review_manager.get_item(
                    checked_plan["source_review_id"], plan_item["review_item_id"])
                rebuilt = _build_item(current_review_item, plan_item.get("mapping") or {}, inventory,
                                      coordinate_mapping)
            except Exception as exc:
                raise ExecutionError("review_item_unavailable", "Current approved review item could not be reconstructed",
                                     {"review_item_id": plan_item.get("review_item_id"),
                                      "reason": type(exc).__name__})
            compared_fields = ("review_item_id", "source_drawing", "source_page", "source_geometry_references",
                               "calibration_id", "calibration_fingerprint", "recognition_type",
                               "recognition_evidence", "confidence", "review_status", "planned_category",
                               "planned_operation", "geometry_source", "explicit_geometry_fingerprint",
                               "explicit_geometry_approval", "geometry", "mapping", "dependencies",
                               "parameter_assignments", "warnings")
            if any(rebuilt.get(field) != plan_item.get(field) for field in compared_fields):
                raise ExecutionError("review_item_changed", "Approved drawing item changed after plan creation",
                                     {"review_item_id": plan_item.get("review_item_id")})
            expected_preview_items[plan_item["plan_item_id"]] = _preview_item(plan_item, inventory)
        for actual in preview.get("items", []):
            planned_id = actual.get("planned_element_id")
            source_plan_item = plan_by_item_id.get(planned_id)
            expected = expected_preview_items.get(planned_id)
            if (source_plan_item is None or expected is None or
                    actual.get("source_review_item_id") != source_plan_item.get("review_item_id") or
                    actual != expected):
                raise ExecutionError("preview_item_changed", "Preview item is substituted, changed, or does not match its current plan item",
                                     {"planned_element_id": planned_id})
        for item in plan["items"]:
            if item.get("review_status") != "approved" or item.get("plan_status") == "blocked":
                raise ExecutionError("approval_or_validation_missing", "Every wall source item must remain approved and valid")
            preview_item = next((value for value in preview_by_id.values()
                                 if value.get("source_review_item_id") == item.get("review_item_id")), None)
            if preview_item is None:
                raise ExecutionError("preview_item_missing", "The current preview does not include every approved wall")
            if (preview_item.get("type_verification") != "verified_by_live_inventory" or
                    preview_item.get("level_verification") != "verified_by_live_inventory"):
                raise ExecutionError("wall_mapping_not_live_verified", "Wall type and level must be confirmed in the live preview inventory")
        if checked_plan.get("target_revit_document_identity") is None or not _same_identity(
                checked_plan["target_revit_document_identity"], inventory["document_identity"]):
            raise ExecutionError("document_changed", "The plan target document is not the active document session")

    def _build_walls(self, preview, constraints, plan):
        if not isinstance(constraints, dict) or not constraints:
            raise ExecutionError("wall_constraints_required", "Explicit per-wall height, base offset, structural setting, and orientation are required")
        items = preview.get("items") or []
        walls = [item for item in items if item.get("revit_category") == "Walls"]
        if len(walls) != len(items) or not walls:
            raise ExecutionError("unsupported_preview_items", "The preview must contain only Basic Walls")
        if len(walls) > 25:
            raise ExecutionError("wall_batch_too_large", "A single user-confirmed transaction is limited to 25 walls")
        output = []
        ids = set()
        plan_items = {item.get("plan_item_id"): item for item in plan.get("items", [])}
        for item in walls:
            planned_id = item.get("planned_element_id")
            plan_item = plan_items.get(planned_id)
            if (plan_item is None or plan_item.get("review_item_id") != item.get("source_review_item_id")):
                raise ExecutionError("preview_plan_mismatch", "Preview wall does not map to the exact plan item",
                                     {"planned_element_id": planned_id})
            values = constraints.get(planned_id)
            constrained = {"base_constraint", "top_constraint", "base_offset_internal_feet", "top_offset_internal_feet", "structural", "flip"}
            unconstrained = {"base_constraint", "top_constraint", "unconnected_height_internal_feet", "base_offset_internal_feet", "structural", "flip"}
            if not isinstance(values, dict) or not (set(values) == constrained or set(values) == unconstrained):
                raise ExecutionError("wall_constraints_incomplete", "Specify a base Level, offsets, explicit flags, and either a top Level or unconnected height",
                                     {"planned_element_id": planned_id})
            height = values.get("unconnected_height_internal_feet")
            base_offset = values["base_offset_internal_feet"]
            top_offset = values.get("top_offset_internal_feet", 0.0)
            if (not _finite(base_offset) or not _finite(top_offset) or
                    (height is not None and (not _finite(height) or height <= 0))):
                raise ExecutionError("wall_dimensions_invalid", "Wall offsets must be finite and unconnected height, when used, must be positive",
                                     {"planned_element_id": planned_id})
            base_ref = values.get("base_constraint")
            top_ref = values.get("top_constraint")
            if not self._level_reference(base_ref) or (top_ref is not None and not self._level_reference(top_ref)):
                raise ExecutionError("wall_level_mapping_invalid", "Base and top constraints must identify actual Level element IDs and names")
            if (top_ref is None) != (height is not None):
                raise ExecutionError("wall_height_mode_invalid", "Use top_constraint with top_offset, or unconnected_height only when top_constraint is null")
            if base_ref.get("name") != item.get("proposed_level"):
                raise ExecutionError("wall_base_constraint_mismatch", "Explicit base Level must match the approved preview base level")
            planned_mapping = plan_item.get("mapping") or {}
            planned_level_id = planned_mapping.get("level_id")
            if planned_level_id is None or str(planned_level_id) != str(base_ref["element_id"]):
                raise ExecutionError("wall_base_level_id_mismatch", "Base Level element ID must match the explicit approved plan mapping")
            planned_top_id = planned_mapping.get("top_level_id")
            if top_ref is not None and (planned_top_id is None or str(planned_top_id) != str(top_ref["element_id"]) or
                                        planned_mapping.get("top_level_name") != top_ref["name"]):
                raise ExecutionError("wall_top_level_id_mismatch", "Top Level element ID and name must match the explicit approved plan mapping")
            if top_ref is None and planned_top_id is not None:
                raise ExecutionError("wall_height_mode_mapping_mismatch", "Plan contains a top Level but execution requested unconnected height")
            planned_type = plan_item.get("revit_type_mapping") or {}
            if not planned_type.get("type_id"):
                raise ExecutionError("wall_type_id_unavailable", "A verified WallType element ID is required in the current plan")
            if str(planned_type.get("type_id")) != str((planned_mapping.get("wall_type_id") or planned_type.get("type_id"))):
                raise ExecutionError("wall_type_mapping_mismatch", "Planned WallType ID does not match the approved type mapping")
            if not isinstance(values["structural"], bool) or not isinstance(values["flip"], bool):
                raise ExecutionError("wall_flags_invalid", "Structural and flip values must be explicitly true or false")
            geometry = item.get("geometry") or {}
            revit_geometry = geometry.get("revit_geometry_internal_feet") or {}
            line = revit_geometry.get("centerline_internal_feet")
            if not isinstance(line, list) or len(line) != 2 or any(not _point3(point) for point in line):
                raise ExecutionError("wall_geometry_unresolved", "Wall needs a calibrated, project-mapped two-point centerline",
                                     {"planned_element_id": planned_id})
            length = math.dist(line[0], line[1])
            if not math.isfinite(length) or length <= 1e-4:
                raise ExecutionError("wall_geometry_invalid", "Wall centerline length must exceed Revit's short-curve tolerance",
                                     {"planned_element_id": planned_id})
            level_name, type_name = item.get("proposed_level"), item.get("proposed_type")
            if not isinstance(level_name, str) or not level_name or not isinstance(type_name, str) or not type_name:
                raise ExecutionError("wall_mapping_incomplete", "Wall type and base level mappings must be explicit")
            parameters = item.get("parameter_assignments") or {}
            if not isinstance(parameters, dict) or set(parameters) - _ALLOWED_ASSIGNMENTS or any(not isinstance(v, str) for v in parameters.values()):
                raise ExecutionError("wall_parameters_unsupported", "Only explicit string Mark and Comments assignments are supported")
            if planned_id in ids:
                raise ExecutionError("duplicate_plan_item", "Preview contains duplicate planned wall IDs")
            ids.add(planned_id)
            output.append({"planned_element_id": planned_id,
                           "source_review_item_id": item.get("source_review_item_id"),
                           "source_drawing": plan_item.get("source_drawing") or plan.get("source_drawing"),
                           "source_page": plan_item.get("source_page"),
                           "source_geometry_references": copy.deepcopy((geometry.get("source_geometry_references") or [])),
                           "wall_type_name": type_name, "wall_type_id": planned_type.get("type_id"),
                           "base_level_name": level_name,
                           "base_constraint": base_ref or {"name": level_name, "element_id": None},
                           "top_constraint": top_ref,
                           "expected_thickness_internal_feet": item.get("dimensions_internal_feet", {}).get("thickness_internal_feet"),
                           "expected_length_internal_feet": item.get("dimensions_internal_feet", {}).get("length_internal_feet"),
                           "centerline_internal_feet": line,
                           "unconnected_height_internal_feet": float(height) if height is not None else None,
                           "base_offset_internal_feet": float(base_offset), "top_offset_internal_feet": float(top_offset),
                           "structural": values["structural"],
                           "flip": values["flip"], "parameter_assignments": parameters})
        if set(constraints) != ids:
            raise ExecutionError("wall_constraints_unmatched", "Constraints must match all and only preview wall IDs")
        return output

    @staticmethod
    def _level_reference(value):
        return (isinstance(value, dict) and isinstance(value.get("name"), str) and bool(value["name"].strip()) and
                isinstance(value.get("element_id"), (str, int)) and str(value.get("element_id")).isdigit())

    def _post(self, route, payload, write=False):
        self.client._ensure_base_url()
        parsed = urlsplit(self.client.base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise ExecutionError("invalid_endpoint", "Execution endpoint must use the pinned IPv4 loopback Routes port")
        url = "http://127.0.0.1:{}/lora-identity-v1{}".format(parsed.port, route)
        try:
            response = self.http_session.post(url, json=payload, timeout=self.timeout, allow_redirects=False,
                                     stream=True,
                                     headers={"X-Lora-Execution-Operation": "wall-write" if write else "wall-validate"})
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    raw.extend(chunk)
                    if len(raw) > self.max_response_bytes:
                        raise ExecutionError("execution_response_too_large", "Revit execution response exceeded configured limit")
            if response.status_code < 200 or response.status_code >= 300:
                raise ExecutionError("revit_execution_http_error", "Revit-side execution route returned HTTP {}".format(response.status_code),
                                     {"http_status": response.status_code})
            value = json.loads(bytes(raw).decode("utf-8-sig"))
            if not isinstance(value, dict):
                raise ExecutionError("invalid_revit_response", "Revit-side execution response must be a JSON object")
            return value
        except requests.Timeout as exc:
            raise ExecutionError("revit_execution_timeout", "Timed out waiting for the Revit-side execution route; request status may be uncertain") from exc
        except requests.RequestException as exc:
            raise ExecutionError("revit_execution_connection_error", "Could not reach the pinned local Revit execution endpoint") from exc
        except ValueError as exc:
            raise ExecutionError("invalid_revit_response", "Revit-side execution route returned invalid JSON") from exc
        finally:
            if "response" in locals() and response is not None:
                response.close()

    def _get_execution_status(self, request_id):
        self.client._ensure_base_url()
        parsed = urlsplit(self.client.base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            return None
        url = "http://127.0.0.1:{}/lora-identity-v1/execution_status".format(parsed.port)
        try:
            response = self.http_session.get(url, params={"request_id": request_id}, timeout=self.timeout,
                                    allow_redirects=False, stream=True)
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    raw.extend(chunk)
                    if len(raw) > self.max_response_bytes:
                        return None
            if response.status_code < 200 or response.status_code >= 300:
                return None
            value = json.loads(bytes(raw).decode("utf-8-sig"))
            return value if isinstance(value, dict) and value.get("status") == "success" else None
        except (requests.RequestException, ValueError):
            return None
        finally:
            if "response" in locals() and response is not None:
                response.close()


def _sidecar_identity(identity):
    if not isinstance(identity, dict):
        raise ExecutionError("document_identity_unavailable", "Verified Revit session identity is required")
    return {"process_id": identity.get("revit_process_id"),
            "process_session_id": identity.get("process_session_id"),
            "application_session_id": identity.get("application_session_id"),
            "document_session_id": identity.get("document_session_id"),
            "document_title": identity.get("document_title"),
            "document_path": identity.get("document_path"),
            "document_type": identity.get("document_type"),
            "revit_version": identity.get("revit_version"),
            "identity_provider_version": identity.get("identity_provider_version"),
            "document_lifecycle_sequence": identity.get("document_lifecycle_sequence"),
            "document_lifecycle_generation": identity.get("document_lifecycle_generation"),
            "active_document_switch_counter": identity.get("active_document_switch_counter")}


def _same_identity(first, second):
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    a = {"document_title": first.get("document_title"), "document_path": first.get("document_path"),
         "document_type": first.get("document_type"), "revit_version": first.get("revit_version"),
         "revit_process_id": first.get("process_id", first.get("revit_process_id")),
         "document_identifier": first.get("document_session_id"), "connection_endpoint": first.get("connection_endpoint"),
         "process_session_id": first.get("process_session_id"), "application_session_id": first.get("application_session_id"),
         "document_session_id": first.get("document_session_id"), "document_lifecycle_sequence": first.get("document_lifecycle_sequence"),
         "document_lifecycle_generation": first.get("document_lifecycle_generation"),
         "active_document_switch_counter": first.get("active_document_switch_counter"),
         "identity_provider_version": first.get("identity_provider_version"), "identity_provider_status": "ready"}
    b = {"document_title": second.get("document_title"), "document_path": second.get("document_path"),
         "document_type": second.get("document_type"), "revit_version": second.get("revit_version"),
         "revit_process_id": second.get("process_id", second.get("revit_process_id")),
         "document_identifier": second.get("document_session_id"), "connection_endpoint": second.get("connection_endpoint"),
         "process_session_id": second.get("process_session_id"), "application_session_id": second.get("application_session_id"),
         "document_session_id": second.get("document_session_id"), "document_lifecycle_sequence": second.get("document_lifecycle_sequence"),
         "document_lifecycle_generation": second.get("document_lifecycle_generation"),
         "active_document_switch_counter": second.get("active_document_switch_counter"),
         "identity_provider_version": second.get("identity_provider_version"), "identity_provider_status": "ready"}
    return _identity_key(a) == _identity_key(b)


def _approval_text(count, title):
    return "I approve creating exactly {} Basic Wall(s) in Revit document: {}".format(count, title)


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _parse_time(value):
    return datetime.fromisoformat(value)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _point3(value):
    return isinstance(value, list) and len(value) == 3 and all(_finite(item) for item in value)


def _safe_error(exc):
    if isinstance(exc, ExecutionError):
        result = {"code": exc.code, "message": str(exc)}
        if exc.details:
            result["details"] = exc.details
        return result
    return {"code": "execution_error", "message": type(exc).__name__}
