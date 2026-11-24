"""Persistent preview snapshots backed by the versioned review database."""

import json
import uuid

from lora_review.errors import ReviewError
from lora_review.repository import _json

from .errors import GenerationPlanError


class GenerationPreviewRepository:
    def __init__(self, review_repository):
        self.review_repository = review_repository

    @property
    def path(self):
        return self.review_repository.path

    def create(self, preview):
        repo = self.review_repository
        try:
            repo.ensure_schema()
            now = repo.timestamp()
            preview_id = preview.get("preview_id") or str(uuid.uuid4())
            preview["preview_id"] = preview_id
            preview["created_at"] = now
            preview["preview_created_at"] = now
            with repo._lock, repo._transaction() as db:
                row = db.execute("SELECT plan_json FROM generation_plans WHERE plan_id=?",
                                 (preview["source_plan_id"],)).fetchone()
                if row is None:
                    raise GenerationPlanError("plan_not_found", "Generation plan was not found")
                current_plan = json.loads(row[0])
                if int(current_plan.get("plan_version", 1)) != int(preview["source_plan_version"]):
                    raise GenerationPlanError("plan_changed", "Plan changed while this preview was being assembled")
                db.execute("UPDATE generation_previews SET stale=1 WHERE plan_id=? AND stale=0",
                           (preview["source_plan_id"],))
                db.execute("INSERT INTO generation_previews(preview_id,plan_id,source_plan_version,preview_json,stale,created_at) VALUES(?,?,?,?,0,?)",
                           (preview_id, preview["source_plan_id"], preview["source_plan_version"],
                            _json(preview), now))
            return preview
        except ReviewError as exc:
            raise GenerationPlanError(exc.code, str(exc), exc.details)
        except (ValueError, TypeError) as exc:
            raise GenerationPlanError("preview_store_corrupt", "Stored generation plan JSON is invalid",
                                      {"reason": type(exc).__name__})

    def get(self, preview_id):
        repo = self.review_repository
        try:
            repo.ensure_schema()
            with repo._lock, repo._transaction() as db:
                row = db.execute("SELECT p.preview_json,p.plan_id,p.source_plan_version,p.stale,g.plan_json "
                                 "FROM generation_previews p LEFT JOIN generation_plans g ON g.plan_id=p.plan_id "
                                 "WHERE p.preview_id=?", (preview_id,)).fetchone()
                if row is None:
                    raise GenerationPlanError("preview_not_found", "Generation preview was not found")
                preview = json.loads(row[0])
                if not isinstance(preview, dict):
                    raise GenerationPlanError("preview_store_corrupt", "Stored preview is not an object")
                current_version = None
                if row[4]:
                    plan = json.loads(row[4])
                    current_version = int(plan.get("plan_version", 1))
                stale = bool(row[3]) or current_version is None or current_version != int(row[2])
                if stale and not row[3]:
                    db.execute("UPDATE generation_previews SET stale=1 WHERE preview_id=?", (preview_id,))
                preview["stale"] = stale
                preview["current_plan_version"] = current_version
                return preview
        except ReviewError as exc:
            raise GenerationPlanError(exc.code, str(exc), exc.details)
        except (ValueError, TypeError) as exc:
            raise GenerationPlanError("preview_store_corrupt", "Stored generation preview JSON is invalid",
                                      {"reason": type(exc).__name__})

    def previous(self, plan_id):
        repo = self.review_repository
        try:
            repo.ensure_schema()
            db = repo._connect()
            try:
                row = db.execute("SELECT preview_json FROM generation_previews WHERE plan_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                                 (plan_id,)).fetchone()
                if row is None:
                    return None
                return json.loads(row[0])
            finally:
                db.close()
        except ReviewError as exc:
            raise GenerationPlanError(exc.code, str(exc), exc.details)
        except (ValueError, TypeError) as exc:
            raise GenerationPlanError("preview_store_corrupt", "Stored generation preview JSON is invalid",
                                      {"reason": type(exc).__name__})


def compare_previews(first, second):
    first_items = {item["source_review_item_id"]: item for item in first.get("items", [])}
    second_items = {item["source_review_item_id"]: item for item in second.get("items", [])}
    added = sorted(second_items.keys() - first_items.keys())
    removed = sorted(first_items.keys() - second_items.keys())
    changed = []
    compared_fields = ("revit_category", "proposed_family", "proposed_type", "proposed_level",
                       "geometry", "parameter_assignments", "dependencies", "validation")
    for item_id in sorted(first_items.keys() & second_items.keys()):
        fields = [field for field in compared_fields if first_items[item_id].get(field) != second_items[item_id].get(field)]
        if fields:
            changed.append({"source_review_item_id": item_id, "changed_fields": fields})
    return {"first_preview_id": first.get("preview_id"), "second_preview_id": second.get("preview_id"),
            "source_plan_id": first.get("source_plan_id"), "added_items": added,
            "removed_items": removed, "changed_items": changed,
            "changed_snapshot_fields": [field for field in ("document_identity", "inventory_source",
                "inventory_status", "inventory_verification", "validation", "readiness")
                if first.get(field) != second.get(field)],
            "different": bool(added or removed or changed or
                              first.get("source_plan_version") != second.get("source_plan_version") or
                              any(first.get(field) != second.get(field) for field in ("document_identity",
                                  "inventory_source", "inventory_status", "inventory_verification", "validation", "readiness"))),
            "same_source_plan": first.get("source_plan_id") == second.get("source_plan_id")}
