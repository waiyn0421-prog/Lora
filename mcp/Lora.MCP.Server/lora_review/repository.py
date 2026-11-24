"""Versioned SQLite persistence for review data only (no source drawing bytes)."""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .errors import ReviewError


SCHEMA_VERSION = 4


def default_database_path():
    configured = os.environ.get("LORA_REVIEW_DB")
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return root / "Lora" / "MCP" / "reviews.sqlite3"


class ReviewRepository:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_database_path()
        self._lock = threading.RLock()

    @staticmethod
    def timestamp():
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def _connect(self):
        db = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            return db
        except sqlite3.DatabaseError as exc:
            if db is not None:
                db.close()
            raise ReviewError("review_store_corrupt", "Review database is corrupt or inaccessible; original file was preserved",
                              {"path": str(self.path), "reason": type(exc).__name__,
                               "recovery": "Restore from a backup or inspect the database before replacing it."})
        except OSError as exc:
            if db is not None:
                db.close()
            raise ReviewError("review_store_unavailable", "Review database cannot be opened",
                              {"path": str(self.path), "reason": type(exc).__name__})

    def ensure_schema(self):
        with self._lock:
            try:
                db = self._connect()
                try:
                    version = db.execute("PRAGMA user_version").fetchone()[0]
                    if version > SCHEMA_VERSION:
                        raise ReviewError("review_schema_newer", "Review database schema is newer than this server",
                                          {"database_version": version, "supported_version": SCHEMA_VERSION})
                    if version == 0:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute("CREATE TABLE review_sessions (session_id TEXT PRIMARY KEY, data_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
                        db.execute("CREATE TABLE review_items (session_id TEXT NOT NULL, item_id TEXT NOT NULL, page_number INTEGER NOT NULL, category TEXT NOT NULL, status TEXT NOT NULL, calibration_state TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(session_id,item_id), FOREIGN KEY(session_id) REFERENCES review_sessions(session_id) ON DELETE CASCADE)")
                        db.execute("CREATE INDEX review_items_filter_idx ON review_items(session_id,status,category,page_number)")
                        db.execute("CREATE TABLE review_history (session_id TEXT NOT NULL, sequence INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, event_json TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(session_id) REFERENCES review_sessions(session_id) ON DELETE CASCADE)")
                        db.execute("PRAGMA user_version=2")
                        db.execute("COMMIT")
                        version = 2
                    elif version == 1:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute("ALTER TABLE review_items ADD COLUMN calibration_state TEXT NOT NULL DEFAULT 'missing'")
                        db.execute("PRAGMA user_version=2")
                        db.execute("COMMIT")
                        version = 2
                    if version == 2:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute("CREATE TABLE generation_plans (plan_id TEXT PRIMARY KEY, source_review_id TEXT NOT NULL, plan_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
                        db.execute("CREATE INDEX generation_plans_review_idx ON generation_plans(source_review_id,created_at)")
                        db.execute("CREATE TABLE generation_plan_history (plan_id TEXT NOT NULL, sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_json TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(plan_id) REFERENCES generation_plans(plan_id) ON DELETE CASCADE)")
                        db.execute("PRAGMA user_version=3")
                        db.execute("COMMIT")
                        version = 3
                    if version == 3:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute("CREATE TABLE generation_previews (preview_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, source_plan_version INTEGER NOT NULL, preview_json TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, FOREIGN KEY(plan_id) REFERENCES generation_plans(plan_id) ON DELETE CASCADE)")
                        db.execute("CREATE INDEX generation_previews_plan_idx ON generation_previews(plan_id,created_at)")
                        db.execute("PRAGMA user_version=4")
                        db.execute("COMMIT")
                finally:
                    db.close()
            except ReviewError:
                raise
            except (sqlite3.DatabaseError, OSError) as exc:
                raise ReviewError("review_store_corrupt", "Review database is corrupt or inaccessible; original file was preserved",
                                  {"path": str(self.path), "reason": type(exc).__name__,
                                   "recovery": "Restore from a backup or inspect the database before replacing it."})

    def create_session(self, session, items):
        self.ensure_schema()
        now = self.timestamp()
        with self._lock, self._transaction() as db:
            db.execute("INSERT INTO review_sessions(session_id,data_json,created_at,updated_at) VALUES(?,?,?,?)",
                       (session["session_id"], _json(session), now, now))
            for item in items:
                db.execute("INSERT INTO review_items(session_id,item_id,page_number,category,status,calibration_state,data_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (session["session_id"], item["item_id"], item["page_number"], item["category"],
                            item["status"], item.get("calibration_state", "missing"), _json(item), now, now))
            self._history(db, session["session_id"], None,
                          {"action": "review_created", "item_count": len(items)}, now)
        return session

    def get_session(self, session_id):
        self.ensure_schema()
        db = self._connect()
        try:
            row = db.execute("SELECT data_json FROM review_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise ReviewError("review_not_found", "Drawing review session was not found")
            return json.loads(row[0])
        except sqlite3.DatabaseError as exc:
            raise self._corrupt(exc)
        finally:
            db.close()

    def get_item(self, session_id, item_id):
        self.ensure_schema()
        db = self._connect()
        try:
            row = db.execute("SELECT data_json FROM review_items WHERE session_id=? AND item_id=?",
                             (session_id, item_id)).fetchone()
            if row is None:
                raise ReviewError("review_item_not_found", "Review item was not found in this session")
            return json.loads(row[0])
        except sqlite3.DatabaseError as exc:
            raise self._corrupt(exc)
        finally:
            db.close()

    def aggregate(self, session_id):
        self.get_session(session_id)
        db = self._connect()
        try:
            total = db.execute("SELECT COUNT(*) FROM review_items WHERE session_id=?", (session_id,)).fetchone()[0]
            rows = db.execute("SELECT category,status,calibration_state,COUNT(*) AS n FROM review_items WHERE session_id=? GROUP BY category,status,calibration_state",
                              (session_id,)).fetchall()
            return total, [dict(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise self._corrupt(exc)
        finally:
            db.close()

    def page_numbers(self, session_id):
        self.get_session(session_id)
        db = self._connect()
        try:
            return [row[0] for row in db.execute("SELECT DISTINCT page_number FROM review_items WHERE session_id=? ORDER BY page_number",
                                                 (session_id,)).fetchall()]
        except sqlite3.DatabaseError as exc:
            raise self._corrupt(exc)
        finally:
            db.close()

    def calibration_page_states(self, session_id):
        self.get_session(session_id)
        db = self._connect()
        try:
            rows = db.execute("SELECT page_number,calibration_state,COUNT(*) AS n FROM review_items WHERE session_id=? GROUP BY page_number,calibration_state ORDER BY page_number,calibration_state",
                              (session_id,)).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise self._corrupt(exc)
        finally:
            db.close()

    def list_items(self, session_id, status=None, category=None, page_number=None, offset=0, limit=100):
        self.get_session(session_id)
        clauses, values = ["session_id=?"], [session_id]
        if status:
            clauses.append("status=?"); values.append(status)
        if category:
            clauses.append("category=?"); values.append(category)
        if page_number is not None:
            clauses.append("page_number=?"); values.append(page_number)
        where = " AND ".join(clauses)
        db = self._connect()
        try:
            total = db.execute("SELECT COUNT(*) FROM review_items WHERE " + where, values).fetchone()[0]
            rows = db.execute("SELECT data_json FROM review_items WHERE " + where +
                              " ORDER BY page_number,item_id LIMIT ? OFFSET ?", values + [limit, offset]).fetchall()
            return {"total": total, "items": [json.loads(row[0]) for row in rows]}
        except sqlite3.DatabaseError as exc:
            raise self._corrupt(exc)
        finally:
            db.close()

    def mutate_item(self, session_id, item_id, mutator, event):
        self.ensure_schema()
        now = self.timestamp()
        with self._lock, self._transaction() as db:
            row = db.execute("SELECT data_json FROM review_items WHERE session_id=? AND item_id=?",
                             (session_id, item_id)).fetchone()
            if row is None:
                raise ReviewError("review_item_not_found", "Review item was not found in this session")
            item = json.loads(row[0])
            changed, result = mutator(item)
            if changed:
                item["updated_at"] = now
                db.execute("UPDATE review_items SET category=?,status=?,calibration_state=?,data_json=?,updated_at=? WHERE session_id=? AND item_id=?",
                           (item["category"], item["status"], item.get("calibration_state", "missing"), _json(item), now, session_id, item_id))
                db.execute("UPDATE review_sessions SET updated_at=? WHERE session_id=?", (now, session_id))
                self._history(db, session_id, item_id, event, now)
            return item, result

    def mutate_session_items(self, session_id, mutator, event):
        self.ensure_schema()
        now = self.timestamp()
        changed_items = []
        with self._lock, self._transaction() as db:
            if db.execute("SELECT 1 FROM review_sessions WHERE session_id=?", (session_id,)).fetchone() is None:
                raise ReviewError("review_not_found", "Drawing review session was not found")
            rows = db.execute("SELECT item_id,data_json FROM review_items WHERE session_id=?", (session_id,)).fetchall()
            for row in rows:
                item = json.loads(row["data_json"])
                if mutator(item):
                    item["updated_at"] = now
                    db.execute("UPDATE review_items SET category=?,status=?,calibration_state=?,data_json=?,updated_at=? WHERE session_id=? AND item_id=?",
                               (item["category"], item["status"], item.get("calibration_state", "missing"), _json(item), now, session_id, row["item_id"]))
                    changed_items.append(item["item_id"])
                    self._history(db, session_id, row["item_id"],
                                  dict(event, affected_item_id=row["item_id"]), now)
            if changed_items:
                db.execute("UPDATE review_sessions SET updated_at=? WHERE session_id=?", (now, session_id))
                self._history(db, session_id, None, dict(event, changed_item_ids=changed_items), now)
        return changed_items

    def update_session(self, session_id, mutator, event):
        self.ensure_schema()
        now = self.timestamp()
        with self._lock, self._transaction() as db:
            row = db.execute("SELECT data_json FROM review_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise ReviewError("review_not_found", "Drawing review session was not found")
            session = json.loads(row[0])
            changed = mutator(session)
            if changed:
                session["updated_at"] = now
                db.execute("UPDATE review_sessions SET data_json=?,updated_at=? WHERE session_id=?",
                           (_json(session), now, session_id))
                self._history(db, session_id, None, event, now)
        return session

    def history(self, session_id, item_id=None, offset=0, limit=100):
        self.get_session(session_id)
        where, values = "session_id=?", [session_id]
        if item_id:
            where += " AND item_id=?"; values.append(item_id)
        db = self._connect()
        try:
            total = db.execute("SELECT COUNT(*) FROM review_history WHERE " + where, values).fetchone()[0]
            rows = db.execute("SELECT item_id,event_json,created_at FROM review_history WHERE " + where +
                              " ORDER BY sequence LIMIT ? OFFSET ?", values + [limit, offset]).fetchall()
            return {"total": total, "items": [{"item_id": row["item_id"],
                     "event": json.loads(row["event_json"]), "created_at": row["created_at"]} for row in rows]}
        except sqlite3.DatabaseError as exc:
            raise self._corrupt(exc)
        finally:
            db.close()

    def _transaction(self):
        repository = self

        class Transaction:
            def __enter__(self):
                self.db = repository._connect()
                self.db.execute("BEGIN IMMEDIATE")
                return self.db

            def __exit__(self, exc_type, exc, traceback):
                try:
                    self.db.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    self.db.close()
        return Transaction()

    @staticmethod
    def _history(db, session_id, item_id, event, now):
        db.execute("INSERT INTO review_history(session_id,item_id,event_json,created_at) VALUES(?,?,?,?)",
                   (session_id, item_id, _json(event), now))

    def _corrupt(self, exc):
        return ReviewError("review_store_corrupt", "Review database is corrupt; original file was preserved",
                           {"path": str(self.path), "reason": type(exc).__name__})


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ReviewError("invalid_review_data", "Review data is not safely serializable",
                          {"reason": type(exc).__name__})
