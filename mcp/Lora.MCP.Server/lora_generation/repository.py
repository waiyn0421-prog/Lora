"""Versioned generation-plan persistence sharing the review SQLite database."""

import json
import uuid

from lora_review.errors import ReviewError
from lora_review.repository import ReviewRepository, _json

from .errors import GenerationPlanError


class GenerationPlanRepository:
    def __init__(self, review_repository=None):
        self.review_repository = review_repository or ReviewRepository()

    @property
    def path(self):
        return self.review_repository.path

    def create(self, plan):
        repo = self.review_repository
        try:
            repo.ensure_schema()
            plan_id = plan.get("plan_id") or str(uuid.uuid4())
            plan["plan_id"] = plan_id
            now = repo.timestamp()
            plan["created_at"] = now
            plan["updated_at"] = now
            plan["creation_timestamp"] = now
            plan["plan_version"] = int(plan.get("plan_version", 1))
            with repo._lock, repo._transaction() as db:
                db.execute("INSERT INTO generation_plans(plan_id,source_review_id,plan_json,created_at,updated_at) VALUES(?,?,?,?,?)",
                           (plan_id, plan["source_review_id"], _json(plan), now, now))
                _event(db, plan_id, {"action": "plan_created", "item_count": len(plan.get("items", []))}, now)
            return plan
        except ReviewError as exc:
            raise GenerationPlanError(exc.code, str(exc), exc.details)

    def get(self, plan_id):
        repo = self.review_repository
        try:
            repo.ensure_schema()
            db = repo._connect()
            try:
                row = db.execute("SELECT plan_json FROM generation_plans WHERE plan_id=?", (plan_id,)).fetchone()
                if row is None:
                    raise GenerationPlanError("plan_not_found", "Generation plan was not found")
                plan = json.loads(row[0])
                if not isinstance(plan, dict):
                    raise GenerationPlanError("plan_store_corrupt", "Stored generation plan is not an object")
                if plan.get("plan_schema_version") != 1:
                    raise GenerationPlanError("plan_schema_unsupported", "Stored generation plan schema is unsupported",
                                              {"version": plan.get("plan_schema_version")})
                return plan
            finally:
                db.close()
        except ReviewError as exc:
            raise GenerationPlanError(exc.code, str(exc), exc.details)
        except (ValueError, TypeError) as exc:
            raise GenerationPlanError("plan_store_corrupt", "Stored generation plan JSON is invalid",
                                      {"reason": type(exc).__name__})

    def mutate(self, plan_id, mutator, event):
        repo = self.review_repository
        try:
            repo.ensure_schema()
            now = repo.timestamp()
            with repo._lock, repo._transaction() as db:
                row = db.execute("SELECT plan_json FROM generation_plans WHERE plan_id=?", (plan_id,)).fetchone()
                if row is None:
                    raise GenerationPlanError("plan_not_found", "Generation plan was not found")
                plan = json.loads(row[0])
                plan["plan_version"] = int(plan.get("plan_version", 1)) + 1
                mutator(plan)
                plan["updated_at"] = now
                db.execute("UPDATE generation_plans SET plan_json=?,updated_at=? WHERE plan_id=?",
                           (_json(plan), now, plan_id))
                _event(db, plan_id, dict(event, timestamp=now), now)
            return plan
        except ReviewError as exc:
            raise GenerationPlanError(exc.code, str(exc), exc.details)
        except (ValueError, TypeError) as exc:
            raise GenerationPlanError("plan_store_corrupt", "Stored generation plan JSON is invalid",
                                      {"reason": type(exc).__name__})

    def history(self, plan_id, limit=100):
        repo = self.review_repository
        try:
            repo.ensure_schema()
            db = repo._connect()
            try:
                if db.execute("SELECT 1 FROM generation_plans WHERE plan_id=?", (plan_id,)).fetchone() is None:
                    raise GenerationPlanError("plan_not_found", "Generation plan was not found")
                rows = db.execute("SELECT event_json,created_at FROM generation_plan_history WHERE plan_id=? ORDER BY sequence LIMIT ?",
                                  (plan_id, limit)).fetchall()
                try:
                    return [{"event": json.loads(row[0]), "created_at": row[1]} for row in rows]
                except (ValueError, TypeError) as exc:
                    raise GenerationPlanError("plan_history_corrupt", "Stored generation plan history is invalid",
                                              {"reason": type(exc).__name__})
            finally:
                db.close()
        except ReviewError as exc:
            raise GenerationPlanError(exc.code, str(exc), exc.details)


def _event(db, plan_id, event, timestamp):
    db.execute("INSERT INTO generation_plan_history(plan_id,event_json,created_at) VALUES(?,?,?)",
               (plan_id, _json(event), timestamp))
