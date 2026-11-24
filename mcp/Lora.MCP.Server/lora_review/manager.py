"""Review workflow over recognition/calibration snapshots, persisted separately from drawings."""

import copy
import hashlib
import json
import math
import uuid

from lora_drawing.coordinate_system import apply_affine, invert_affine_point

from .errors import ReviewError
from .repository import ReviewRepository


STATUSES = ("pending", "approved", "rejected", "needs_correction", "unresolved")
_CATEGORIES = {"Walls", "Doors", "Windows", "Columns", "Rooms", "Dimensions", "Levels", "Grids", "Openings", "Other"}
_CATEGORY_MAP = {"Wall": "Walls", "Door": "Doors", "Window": "Windows", "Column": "Columns",
                 "Room boundary": "Rooms", "Dimension": "Dimensions", "Dimension line": "Dimensions",
                 "Level annotation": "Levels", "Grid": "Grids", "Grid line": "Grids",
                 "Door or window opening": "Openings"}
_EDITABLE = {"category", "label", "name", "mark", "type_name", "fire_rating", "comments", "dimensions"}


class DrawingReviewManager:
    def __init__(self, repository=None):
        self.repository = repository or ReviewRepository()

    def create(self, recognition, calibration_results=None, reviewer=None, title=None):
        if not isinstance(recognition, dict) or not recognition.get("recognition_id"):
            raise ReviewError("invalid_recognition", "A stored recognition result is required")
        calibrations = {}
        for calibration in calibration_results or []:
            if calibration.get("processing_id") != recognition.get("processing_id"):
                raise ReviewError("document_mismatch", "Recognition and calibration must refer to the same processed drawing")
            if calibration.get("recognition_id") != recognition.get("recognition_id"):
                raise ReviewError("recognition_mismatch", "Calibration must refer to this recognition result")
            if calibration["page_number"] in calibrations:
                raise ReviewError("duplicate_calibration_page", "Supply at most one calibration per page")
            calibrations[calibration["page_number"]] = calibration
        session_id = str(uuid.uuid4())
        created = self.repository.timestamp()
        calibration_meta = {str(page): _calibration_snapshot(value) for page, value in calibrations.items()}
        session = {"session_id": session_id, "schema_version": 1,
                   "title": _text(title or recognition.get("source_file") or "Drawing review", "title", 300),
                   "source_file": recognition.get("source_file"),
                   "processing_id": recognition.get("processing_id"),
                   "recognition_id": recognition["recognition_id"],
                   "calibrations_by_page": calibration_meta,
                   "reviewer": _text(reviewer or "", "reviewer", 200),
                   "created_at": created, "updated_at": created,
                   "visual_review": {"render_tool": "lora_get_drawing_render",
                                     "processing_id": recognition.get("processing_id"),
                                     "note": "Use the existing render tool with each page number; overlays are in review items."}}
        spatial_by_id = {}
        for calibration in calibrations.values():
            for spatial in calibration.get("spatial_geometry", []):
                spatial_by_id[spatial.get("recognized_element_id")] = spatial
        items = []
        for page in recognition.get("pages", []):
            page_number = page.get("page_number")
            calibration = calibrations.get(page_number)
            for element in page.get("elements", []):
                item = _make_item(session_id, recognition, page_number, element,
                                  calibration, spatial_by_id.get(element.get("recognized_element_id")), created)
                items.append(item)
        self.repository.create_session(session, items)
        return {"session": session, "item_count": len(items),
                "summary": self.summary(session_id)}

    def create_explicit_wall_review(self, coordinate_system, units, start, end, explicit_length,
                                    origin_description, reviewer=None, title=None):
        """Persist one user-supplied wall geometry without inventing a drawing or calibration."""
        feet = {"mm": 1.0 / 304.8, "cm": 1.0 / 30.48, "m": 1.0 / 0.3048,
                "in": 1.0 / 12.0, "ft": 1.0}.get(str(units).lower())
        if not feet or coordinate_system not in ("revit_internal", "user_local"):
            raise ReviewError("explicit_geometry_reference_invalid", "Specify supported units and coordinate_system (revit_internal or user_local)")
        origin_value = _text(origin_description, "origin_description", 500)
        if not origin_value.strip():
            raise ReviewError("explicit_geometry_origin_required", "Describe the coordinate origin explicitly")
        points = [_finite_point(start), _finite_point(end)]
        if abs(points[0][2] - points[1][2]) > 1e-9:
            raise ReviewError("explicit_wall_not_horizontal", "Explicit wall geometry must be a straight horizontal line")
        converted = [[value * feet for value in point] for point in points]
        actual_length = math.dist(converted[0], converted[1])
        supplied_length = _finite_number(explicit_length, "explicit_length") * feet
        if actual_length <= 1e-4 or abs(actual_length - supplied_length) > max(0.5 / 304.8, supplied_length * 1e-6):
            raise ReviewError("explicit_wall_length_mismatch", "Explicit length must match the non-zero straight geometry")
        geometry = {"coordinate_system": coordinate_system, "units": str(units).lower(),
                    "origin_description": origin_value.strip(), "start": points[0], "end": points[1],
                    "length": _finite_number(explicit_length, "explicit_length"),
                    "internal_feet": {"start": converted[0], "end": converted[1], "length": actual_length}}
        fingerprint = _explicit_geometry_fingerprint(geometry)
        session_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
        created = self.repository.timestamp()
        session = {"session_id": session_id, "schema_version": 1, "source_kind": "explicit_geometry",
                   "title": _text(title or "Explicit wall geometry review", "title", 300),
                   "source_file": None, "processing_id": None, "recognition_id": None,
                   "calibrations_by_page": {}, "reviewer": _text(reviewer or "", "reviewer", 200),
                   "coordinate_system": {"name": coordinate_system, "units": str(units).lower(),
                   "origin_description": origin_value.strip(), "user_confirmed": True},
                   "created_at": created, "updated_at": created,
                   "visual_review": {"render_tool": None, "note": "No drawing source; geometry supplied explicitly by the user."}}
        item = {"item_id": item_id, "session_id": session_id, "page_number": 0,
                "source_file": None, "category": "Walls", "recognition_type": "explicit_user_geometry",
                "original_properties": {"category": "Walls"}, "current_properties": {"category": "Walls"},
                "original_geometry": {"coordinate_system": coordinate_system, "units": str(units).lower(),
                    "start": points[0], "end": points[1]},
                "current_geometry": {"explicit_user_geometry": geometry},
                "original_geometry_references": [], "calibration_id": None,
                "calibration_fingerprint": None, "calibration_state": "not_applicable",
                "explicit_geometry_fingerprint": fingerprint, "explicit_geometry_status": "pending_confirmation",
                "status": "pending", "status_reason": "", "reviewer": "",
                "confidence": None, "warnings": [], "corrections": [],
                "created_at": created, "updated_at": created}
        self.repository.create_session(session, [item])
        statement = _explicit_geometry_confirmation(session_id, item_id, geometry, fingerprint)
        public_item = copy.deepcopy(item)
        public_item["page_number"] = None
        return {"session": session, "item": public_item, "required_geometry_confirmation": statement,
                "summary": self.summary(session_id)}

    def approve_explicit_geometry(self, session_id, item_id, statement, reviewer=None):
        item = self.repository.get_item(session_id, item_id)
        session = self.repository.get_session(session_id)
        if session.get("source_kind") != "explicit_geometry" or item.get("category") != "Walls":
            raise ReviewError("explicit_geometry_review_required", "Item is not an explicit wall geometry review")
        geometry = (item.get("current_geometry") or {}).get("explicit_user_geometry")
        fingerprint = _explicit_geometry_fingerprint(geometry)
        expected = _explicit_geometry_confirmation(session_id, item_id, geometry, fingerprint)
        if item.get("explicit_geometry_fingerprint") != fingerprint or statement != expected:
            raise ReviewError("explicit_geometry_confirmation_mismatch", "Confirmation must exactly match the current geometry statement")
        if item.get("status") != "pending":
            raise ReviewError("invalid_status_transition", "Only a pending explicit geometry item can be confirmed")
        value = _text(reviewer or "", "reviewer", 200)
        event = {"action": "explicit_geometry_approved", "reviewer": value,
                 "geometry_fingerprint": fingerprint, "confirmation_statement": statement}
        def mutate(current):
            if _explicit_geometry_fingerprint((current.get("current_geometry") or {}).get("explicit_user_geometry")) != fingerprint:
                raise ReviewError("explicit_geometry_changed", "Geometry changed during approval")
            current["status"] = "approved"
            current["reviewer"] = value
            current["explicit_geometry_status"] = "confirmed"
            current["explicit_geometry_approval"] = {"fingerprint": fingerprint,
                "statement": statement, "reviewer": value, "approved_at": self.repository.timestamp()}
            current["corrections"].append({"kind": "explicit_geometry_approval", "reviewer": value,
                "fingerprint": fingerprint, "timestamp": self.repository.timestamp()})
            return True, "approved"
        result, _ = self.repository.mutate_item(session_id, item_id, mutate, event)
        result["page_number"] = None
        return result

    def session(self, session_id):
        session = self.repository.get_session(session_id)
        summary = self.summary(session_id)
        return {"session": session, "summary": summary,
                "pages": [] if session.get("source_kind") == "explicit_geometry" else self.repository.page_numbers(session_id),
                "visual_review": session["visual_review"]}

    def list_items(self, session_id, status=None, category=None, page_number=None, offset=0, limit=100):
        _pagination(offset, limit)
        if status is not None and status not in STATUSES:
            raise ReviewError("invalid_status", "status is not a supported review status")
        if category is not None and category not in _CATEGORIES:
            raise ReviewError("invalid_category", "category is not a supported review category")
        if page_number is not None and (isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1):
            raise ReviewError("invalid_page_number", "page_number must be a positive integer")
        result = self.repository.list_items(session_id, status, category, page_number, offset, limit)
        if self.repository.get_session(session_id).get("source_kind") == "explicit_geometry":
            for item in result["items"]:
                item["page_number"] = None
        return {**result, "offset": offset, "limit": limit,
                "truncated": offset + len(result["items"]) < result["total"]}

    def get_item(self, session_id, item_id):
        item = self.repository.get_item(session_id, item_id)
        if self.repository.get_session(session_id).get("source_kind") == "explicit_geometry":
            item["page_number"] = None
        item["review_history"] = self.repository.history(session_id, item_id, limit=200)["items"]
        return item

    def update(self, session_id, item_id, properties=None, geometry=None, status=None,
               reviewer=None, reason=None):
        changes = {}
        validated_properties = _validate_properties(properties) if properties else {}
        validated_geometry = _validate_geometry(geometry) if geometry is not None else None
        if status is not None and status not in ("pending", "needs_correction", "unresolved"):
            raise ReviewError("invalid_status_transition", "Use dedicated approve/reject tools; update supports pending, needs_correction, or unresolved")
        if status in ("needs_correction", "unresolved") and not str(reason or "").strip():
            raise ReviewError("reason_required", "A reason is required for needs_correction or unresolved status")
        if validated_properties:
            changes["properties"] = validated_properties
        if validated_geometry is not None:
            changes["geometry"] = validated_geometry
        if status is not None:
            changes["status"] = status
        if not changes:
            raise ReviewError("empty_update", "Provide properties, geometry, or a supported status")
        timestamp = self.repository.timestamp()
        event = {"action": "review_item_updated", "reviewer": _text(reviewer or "", "reviewer", 200),
                 "reason": _text(reason or "", "reason", 2000), "changes": changes}

        def mutate(item):
            if validated_properties:
                before = {key: copy.deepcopy(item["current_properties"].get(key)) for key in validated_properties}
                item["current_properties"].update(copy.deepcopy(validated_properties))
                item["category"] = item["current_properties"].get("category", item["category"])
                item["corrections"].append({"kind": "properties", "before": before,
                    "after": copy.deepcopy(validated_properties), "timestamp": timestamp,
                    "reviewer": event["reviewer"]})
            if validated_geometry is not None:
                item["current_geometry"] = _correct_geometry(item, validated_geometry)
                item["corrections"].append({"kind": "geometry", "value": copy.deepcopy(validated_geometry),
                    "timestamp": timestamp, "reviewer": event["reviewer"]})
            if status is not None:
                if item["status"] in ("rejected", "unresolved") and status != "pending":
                    raise ReviewError("invalid_status_transition", "Rejected or unresolved items must return to pending first")
                item["status"] = status
                item["status_reason"] = event["reason"]
            return True, None
        item, _ = self.repository.mutate_item(session_id, item_id, mutate, event)
        if self.repository.get_session(session_id).get("source_kind") == "explicit_geometry":
            item["page_number"] = None
        return item

    def approve(self, session_id, item_id, reviewer=None, reason=None):
        return self._transition(session_id, item_id, "approved", reviewer, reason)

    def reject(self, session_id, item_id, reason, reviewer=None):
        if not str(reason or "").strip():
            raise ReviewError("reason_required", "A rejection reason is required")
        return self._transition(session_id, item_id, "rejected", reviewer, reason)

    def _transition(self, session_id, item_id, target, reviewer, reason):
        reviewer_value = _text(reviewer or "", "reviewer", 200)
        reason_value = _text(reason or "", "reason", 2000)
        event = {"action": target, "reviewer": reviewer_value, "reason": reason_value}

        def mutate(item):
            if item["status"] == target:
                return False, "already_{}".format(target)
            if target == "approved":
                if item["status"] in ("rejected", "unresolved"):
                    raise ReviewError("invalid_status_transition", "Return this item to pending before approval")
                if item.get("calibration_state") == "stale":
                    raise ReviewError("calibration_stale", "Revalidate the changed calibration before approving this item")
                if item.get("calibration_state") != "current":
                    item["approval_warning"] = "Item approved, but calibrated geometry is missing or incomplete."
            elif item["status"] in ("rejected", "unresolved"):
                raise ReviewError("invalid_status_transition", "Return this item to pending before changing its decision")
            item["status"] = target
            item["status_reason"] = reason_value
            item["reviewer"] = reviewer_value
            item["corrections"].append({"kind": "status", "status": target,
                "reason": reason_value, "reviewer": reviewer_value,
                "timestamp": self.repository.timestamp()})
            return True, target
        item, result = self.repository.mutate_item(session_id, item_id, mutate, event)
        if self.repository.get_session(session_id).get("source_kind") == "explicit_geometry":
            item["page_number"] = None
        item["transition_result"] = result
        return item

    def history(self, session_id, item_id=None, offset=0, limit=100):
        _pagination(offset, limit)
        if item_id:
            self.repository.get_item(session_id, item_id)
        return {**self.repository.history(session_id, item_id, offset, limit),
                "offset": offset, "limit": limit}

    def summary(self, session_id):
        session = self.repository.get_session(session_id)
        total, groups = self.repository.aggregate(session_id)
        counts = {status: 0 for status in STATUSES}
        by_category = {}
        approved_ready = 0
        approved_missing_or_incomplete = 0
        approved_stale = 0
        approved_explicit_current = 0
        calibration_states = set()
        for group in groups:
            counts[group["status"]] += group["n"]
            bucket = by_category.setdefault(group["category"], {status: 0 for status in STATUSES})
            bucket[group["status"]] += group["n"]
            calibration_states.add(group["calibration_state"])
            if group["status"] == "approved" and group["calibration_state"] == "current":
                approved_ready += group["n"]
            elif group["status"] == "approved" and group["calibration_state"] in ("missing", "incomplete"):
                approved_missing_or_incomplete += group["n"]
            elif group["status"] == "approved" and group["calibration_state"] == "stale":
                approved_stale += group["n"]
        if session.get("source_kind") == "explicit_geometry":
            for item in self.repository.list_items(session_id, status="approved", limit=500)["items"]:
                if item.get("explicit_geometry_status") == "confirmed":
                    try:
                        if _explicit_geometry_fingerprint((item.get("current_geometry") or {}).get("explicit_user_geometry")) == item.get("explicit_geometry_fingerprint") == (item.get("explicit_geometry_approval") or {}).get("fingerprint"):
                            approved_explicit_current += 1
                    except Exception:
                        pass
        return {"session_id": session_id, "source_file": session["source_file"],
                "total_items": total, "counts_by_status": counts,
                "counts_by_category_and_status": by_category,
                "approved_collection_count": counts["approved"],
                "approved_with_current_calibration_count": approved_ready,
                "approved_explicit_geometry_current_count": approved_explicit_current,
                "approved_with_current_geometry_count": approved_ready + approved_explicit_current,
                "approved_missing_or_incomplete_calibration_count": approved_missing_or_incomplete,
                "approved_stale_calibration_count": approved_stale,
                "calibration_warnings": _calibration_state_warnings(calibration_states)}

    def validate(self, session_id, calibration_results=None):
        session = self.repository.get_session(session_id)
        current = {str(result["page_number"]): _calibration_snapshot(result)
                   for result in (calibration_results or [])}
        if len(current) != len(calibration_results or []):
            raise ReviewError("duplicate_calibration_page", "Supply at most one calibration per page")
        original = session.get("calibrations_by_page", {})
        findings = []
        now = self.repository.timestamp()
        for page_key, snapshot in current.items():
            previous = original.get(page_key)
            if previous is None:
                affected = self.repository.mutate_session_items(session_id,
                    lambda item, p=int(page_key): _flag_stale(item, p, snapshot, now),
                    {"action": "calibration_added_after_review", "page_number": int(page_key),
                     "current_fingerprint": snapshot["fingerprint"]})
                self.repository.update_session(session_id,
                    lambda data, p=page_key, s=snapshot: data["calibrations_by_page"].__setitem__(p, s) or True,
                    {"action": "calibration_snapshot_recorded", "page_number": int(page_key)})
                findings.append({"page_number": int(page_key), "status": "new_calibration",
                                 "affected_item_count": len(affected),
                                 "message": "This session has no prior calibration snapshot; existing review geometry was flagged for re-review."})
                continue
            if snapshot["fingerprint"] != previous["fingerprint"]:
                affected = self.repository.mutate_session_items(session_id,
                    lambda item, p=int(page_key): _flag_stale(item, p, snapshot, now),
                    {"action": "calibration_changed", "page_number": int(page_key),
                     "previous_fingerprint": previous["fingerprint"],
                     "current_fingerprint": snapshot["fingerprint"]})
                self.repository.update_session(session_id,
                    lambda data, p=page_key, s=snapshot: data["calibrations_by_page"].__setitem__(p, s) or True,
                    {"action": "calibration_snapshot_advanced", "page_number": int(page_key)})
                findings.append({"page_number": int(page_key), "status": "calibration_changed",
                                 "affected_item_count": len(affected),
                                 "affected_item_ids": affected,
                                 "message": "Affected review items were flagged stale; prior approvals are excluded from current-calibration readiness."})
            elif not snapshot["calibration_complete"]:
                findings.append({"page_number": int(page_key), "status": "calibration_incomplete",
                                 "message": "Current calibration is incomplete."})
            else:
                findings.append({"page_number": int(page_key), "status": "unchanged"})
        calibrated_pages = set(original)
        supplied_pages = set(current)
        for page_key in sorted(calibrated_pages - supplied_pages):
            findings.append({"page_number": int(page_key), "status": "current_calibration_not_supplied",
                             "message": "No current calibration was supplied for comparison; no change was assumed."})
        for state in self.repository.calibration_page_states(session_id):
            if state["calibration_state"] in ("missing", "incomplete"):
                findings.append({"page_number": state["page_number"],
                    "status": "missing_calibration" if state["calibration_state"] == "missing" else "calibration_incomplete",
                    "affected_item_count": state["n"],
                    "message": "Review items on this page do not have a complete calibrated geometry snapshot."})
        _, stale_groups = self.repository.aggregate(session_id)
        stale_count = sum(group["n"] for group in stale_groups if group["calibration_state"] == "stale")
        if stale_count:
            findings.append({"status": "review_items_stale", "affected_item_count": stale_count,
                             "message": "Stale items retain their review decision but are excluded from current-calibration readiness; create a new review session to review the updated geometry."})
        item_issues = []
        offset = 0
        while True:
            page = self.repository.list_items(session_id, offset=offset, limit=500)
            for item in page["items"]:
                issues = []
                if item.get("status") not in STATUSES:
                    issues.append("unsupported_review_status")
                if not item.get("original_geometry") or not item.get("original_properties"):
                    issues.append("original_snapshot_missing")
                for correction in item.get("current_geometry", {}).values():
                    if isinstance(correction, dict) and "reviewer_correction" in correction:
                        try:
                            _validate_geometry(correction["reviewer_correction"])
                        except ReviewError:
                            issues.append("invalid_geometry_correction")
                if issues:
                    item_issues.append({"item_id": item["item_id"], "issues": sorted(set(issues))})
            offset += len(page["items"])
            if offset >= page["total"] or not page["items"]:
                break
        if item_issues:
            findings.append({"status": "review_data_issues", "items": item_issues})
        return {"session_id": session_id, "validation_complete": not any(
                    finding["status"] in ("calibration_changed", "calibration_incomplete", "new_calibration",
                                           "current_calibration_not_supplied", "review_items_stale",
                                           "missing_calibration", "review_data_issues")
                    for finding in findings),
                "findings": findings, "summary": self.summary(session_id)}


def _make_item(session_id, recognition, page_number, element, calibration, calibrated, created):
    category = _CATEGORY_MAP.get(element.get("proposed_category"), "Other")
    source_geometry = {"coordinate_space": "source", "bounding_box": copy.deepcopy(element.get("bounding_box")),
                       "position": copy.deepcopy(element.get("position")),
                       "geometry_references": copy.deepcopy(element.get("source_geometry_references", [])),
                       "dimensions": copy.deepcopy(element.get("dimensions", {})),
                       "coordinate_units": element.get("coordinate_units")}
    calibrated_geometry = None
    calibration_state = "missing"
    matrix = None
    calibration_id = None
    warnings = list(element.get("warnings", []))
    if calibration:
        calibration_id = calibration.get("calibration_id")
        matrix = calibration.get("transformation_matrix_3x3")
        calibration_state = "current" if calibration.get("calibration_complete") else "incomplete"
        if calibrated:
            calibrated_geometry = {"coordinate_space": "calibrated",
                "bounding_box": copy.deepcopy(calibrated.get("bounding_box")),
                "position": copy.deepcopy(calibrated.get("position")),
                "geometry": copy.deepcopy(calibrated.get("geometry", [])),
                "dimensions": copy.deepcopy(calibrated.get("dimensions", {})),
                "coordinate_units": calibrated.get("coordinate_units")}
        if calibration_state != "current":
            warnings.extend(calibration.get("warnings", []) or ["Calibration is incomplete."])
    else:
        warnings.append("No calibration was attached to this review item.")
    original_geometry = {"source": source_geometry, "calibrated": copy.deepcopy(calibrated_geometry),
                         "calibration_matrix_3x3": copy.deepcopy(matrix)}
    props = {"category": category, "label": element.get("proposed_category"),
             "name": "", "mark": "", "type_name": "", "fire_rating": "", "comments": "",
             "dimensions": copy.deepcopy(element.get("dimensions", {}))}
    return {"item_id": str(uuid.uuid4()), "session_id": session_id,
            "source_file": recognition.get("source_file"), "page_number": page_number,
            "original_geometry_references": copy.deepcopy(element.get("source_geometry_references", [])),
            "calibrated_geometry_references": copy.deepcopy(calibrated.get("geometry", []) if calibrated else []),
            "recognition_type": element.get("proposed_category"),
            "category": category,
            "recognition_evidence": copy.deepcopy(element.get("evidence", [])),
            "confidence": element.get("evidence_score"), "current_properties": props,
            "original_properties": copy.deepcopy(props),
            "original_geometry": original_geometry,
            "current_geometry": {**copy.deepcopy(original_geometry),
                                 "selected_coordinate_space": "calibrated" if calibrated_geometry else "source"},
            "status": "pending", "reviewer": "",
            "status_reason": "", "corrections": [], "warnings": list(dict.fromkeys(warnings)),
            "calibration_id": calibration_id,
            "calibration_fingerprint": _calibration_snapshot(calibration)["fingerprint"] if calibration else None,
            "calibration_state": calibration_state, "calibration_matrix_3x3": copy.deepcopy(matrix),
            "created_at": created, "updated_at": created}


def _finite_point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ReviewError("explicit_geometry_point_invalid", "Each endpoint must contain three finite coordinates")
    result = []
    for coordinate in value:
        if isinstance(coordinate, bool):
            raise ReviewError("explicit_geometry_point_invalid", "Each endpoint must contain three finite coordinates")
        try:
            number = float(coordinate)
        except (TypeError, ValueError):
            number = float("nan")
        if not math.isfinite(number):
            raise ReviewError("explicit_geometry_point_invalid", "Each endpoint must contain three finite coordinates")
        result.append(number)
    return result


def _finite_number(value, field):
    if isinstance(value, bool):
        raise ReviewError("explicit_geometry_length_invalid", "{} must be a positive finite number".format(field))
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float("nan")
    if not math.isfinite(number) or number <= 0:
        raise ReviewError("explicit_geometry_length_invalid", "{} must be a positive finite number".format(field))
    return number


def _explicit_geometry_fingerprint(geometry):
    raw = json.dumps(geometry, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _explicit_geometry_confirmation(session_id, item_id, geometry, fingerprint):
    return ("CONFIRM EXPLICIT WALL GEOMETRY | review={} | item={} | coordinate_system={} | units={} | "
            "origin={} | start={} | end={} | length={} | fingerprint={}").format(
                session_id, item_id, geometry["coordinate_system"], geometry["units"],
                geometry["origin_description"], json.dumps(geometry["start"], separators=(",", ":")),
                json.dumps(geometry["end"], separators=(",", ":")), geometry["length"], fingerprint)


def _calibration_snapshot(result):
    relevant = {"page_number": result.get("page_number"),
                "matrix": result.get("transformation_matrix_3x3"),
                "scale": result.get("scale_factors_mm_per_source_unit"),
                "target_units": result.get("target_units"),
                "references": result.get("calibration_references"),
                "complete": result.get("calibration_complete")}
    raw = json.dumps(relevant, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"calibration_id": result.get("calibration_id"), "page_number": result.get("page_number"),
            "calibration_complete": bool(result.get("calibration_complete")),
            "fingerprint": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "transformation_matrix_3x3": copy.deepcopy(result.get("transformation_matrix_3x3")),
            "target_units": result.get("target_units")}


def _flag_stale(item, page_number, snapshot, timestamp):
    if item["page_number"] != page_number:
        return False
    item["calibration_state"] = "stale"
    item["stale_calibration"] = {"previous_fingerprint": item.get("calibration_fingerprint"),
                                  "current_fingerprint": snapshot["fingerprint"],
                                  "current_calibration_id": snapshot.get("calibration_id"),
                                  "detected_at": timestamp}
    item["warnings"] = list(dict.fromkeys(item.get("warnings", []) +
                                  ["Calibration changed after review geometry was captured; re-review is required."]))
    return True


def _correct_geometry(item, correction):
    current = copy.deepcopy(item["current_geometry"])
    space, geometry = correction["coordinate_space"], correction
    matrix = item.get("calibration_matrix_3x3")
    source_geom = current["source"]
    calibrated_geom = current.get("calibrated")
    if space == "source":
        source_geom["reviewer_correction"] = copy.deepcopy(geometry)
        if matrix:
            if calibrated_geom is None:
                calibrated_geom = {"coordinate_space": "calibrated", "coordinate_units": item.get("target_units")}
            calibrated_geom["reviewer_correction"] = _transform_correction(geometry, matrix, inverse=False)
    else:
        if not matrix:
            raise ReviewError("calibration_missing", "Calibrated geometry correction requires an attached calibration")
        if calibrated_geom is None:
            calibrated_geom = {"coordinate_space": "calibrated"}
        calibrated_geom["reviewer_correction"] = copy.deepcopy(geometry)
        source_geom["reviewer_correction"] = _transform_correction(geometry, matrix, inverse=True)
    current["source"] = source_geom
    current["calibrated"] = calibrated_geom
    current["selected_coordinate_space"] = space
    return current


def _transform_correction(geometry, matrix, inverse=False):
    value = copy.deepcopy(geometry)
    fn = (lambda p: invert_affine_point(p, matrix)) if inverse else (lambda p: apply_affine(p, matrix))
    coords = value["coordinates"]
    if value["geometry_type"] == "bbox":
        x0, y0, x1, y1 = coords
        points = [fn(point) for point in ([x0, y0], [x1, y0], [x1, y1], [x0, y1])]
        coords = [min(p[0] for p in points), min(p[1] for p in points),
                  max(p[0] for p in points), max(p[1] for p in points)]
    elif value["geometry_type"] == "point":
        coords = fn(coords)
    else:
        coords = [fn(point) for point in coords]
    return {"coordinate_space": "calibrated" if not inverse else "source",
            "geometry_type": value["geometry_type"], "coordinates": coords}


def _validate_properties(properties):
    if not isinstance(properties, dict) or not properties or len(properties) > 30:
        raise ReviewError("invalid_properties", "properties must be a non-empty object of at most 30 fields")
    unknown = set(properties) - _EDITABLE
    if unknown:
        raise ReviewError("property_not_editable", "Unsupported review properties were supplied",
                          {"fields": sorted(unknown)})
    result = {}
    for key, value in properties.items():
        if key == "category":
            if value not in _CATEGORIES:
                raise ReviewError("invalid_property_value", "category must be one of the supported review categories")
            result[key] = value
        elif key == "dimensions":
            _validate_json_value(value)
            result[key] = copy.deepcopy(value)
        else:
            result[key] = _text(value, key, 500)
    if len(json.dumps(result, allow_nan=False)) > 16000:
        raise ReviewError("invalid_properties", "The property update exceeds 16 KB")
    return result


def _validate_geometry(value):
    if not isinstance(value, dict) or value.get("coordinate_space") not in ("source", "calibrated"):
        raise ReviewError("invalid_geometry", "geometry must specify source or calibrated coordinate_space")
    kind, coordinates = value.get("geometry_type"), value.get("coordinates")
    if kind == "point":
        coordinates = _point(coordinates)
    elif kind == "bbox":
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 4:
            raise ReviewError("invalid_geometry", "bbox coordinates must contain four numbers")
        coordinates = [_finite(number, "geometry coordinate") for number in coordinates]
        if coordinates[2] < coordinates[0] or coordinates[3] < coordinates[1]:
            raise ReviewError("invalid_geometry", "bbox max coordinates must not be below min coordinates")
    elif kind in ("polyline", "polygon"):
        minimum = 2 if kind == "polyline" else 3
        if not isinstance(coordinates, (list, tuple)) or not minimum <= len(coordinates) <= 10000:
            raise ReviewError("invalid_geometry", "geometry must contain between {} and 10000 points".format(minimum))
        coordinates = [_point(point) for point in coordinates]
        if kind == "polygon" and coordinates[0] != coordinates[-1]:
            raise ReviewError("invalid_geometry", "polygon coordinates must explicitly close")
    else:
        raise ReviewError("invalid_geometry", "geometry_type must be point, bbox, polyline, or polygon")
    return {"coordinate_space": value["coordinate_space"], "geometry_type": kind,
            "coordinates": coordinates}


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ReviewError("invalid_geometry", "point must contain exactly two coordinates")
    return [_finite(value[0], "geometry coordinate"), _finite(value[1], "geometry coordinate")]


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or abs(float(value)) > 1e9:
        raise ReviewError("invalid_geometry", "{} must be a finite coordinate between -1e9 and 1e9".format(name))
    return float(value)


def _text(value, name, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        raise ReviewError("invalid_property_value", "{} must be text no longer than {} characters".format(name, maximum))
    return value


def _validate_json_value(value, depth=0):
    if depth > 6:
        raise ReviewError("invalid_property_value", "Nested properties cannot exceed six levels")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReviewError("invalid_property_value", "Numeric property values must be finite")
        return
    if isinstance(value, list):
        if len(value) > 10000:
            raise ReviewError("invalid_property_value", "Property arrays are too large")
        for child in value:
            _validate_json_value(child, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 100:
            raise ReviewError("invalid_property_value", "Property objects are too large")
        for child in value.values():
            _validate_json_value(child, depth + 1)
        return
    raise ReviewError("invalid_property_value", "Property values must be JSON-compatible")


def _pagination(offset, limit):
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ReviewError("invalid_pagination", "offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ReviewError("invalid_pagination", "limit must be an integer from 1 to 500")


def _calibration_state_warnings(states):
    warnings = []
    for state, message in (("missing", "Some review items have no calibration."),
                           ("incomplete", "Some review items use incomplete calibration."),
                           ("stale", "Some review items are stale after calibration changes.")):
        if state in states:
            warnings.append(message)
    return warnings
