"""Local execution request/result history, separate from drawing review data."""

import json
import os
import hmac
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import ExecutionError


def default_path():
    configured = os.environ.get("LORA_EXECUTION_DB")
    if configured:
        return Path(configured).expanduser().resolve()
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Lora" / "MCP"
    return root / "executions.sqlite3"


def utc_now():
    return datetime.now(timezone.utc)


class ExecutionRepository:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.RLock()
        self._nonces = {}

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def ensure_schema(self):
        try:
            with self._lock, self._connection() as db:
                db.execute("CREATE TABLE IF NOT EXISTS execution_requests ("
                           "request_id TEXT PRIMARY KEY, preview_id TEXT NOT NULL, state TEXT NOT NULL, "
                           "request_json TEXT NOT NULL, confirmation_hash TEXT NOT NULL, expires_at TEXT NOT NULL, "
                           "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result_json TEXT)")
                db.execute("CREATE INDEX IF NOT EXISTS execution_requests_preview_idx "
                           "ON execution_requests(preview_id,created_at)")
        except sqlite3.DatabaseError as exc:
            raise ExecutionError("execution_store_unavailable", "Execution history database is unavailable",
                                 {"reason": type(exc).__name__})

    def create_request(self, request, confirmation_hash, ttl_seconds=300):
        self.ensure_schema()
        now = utc_now()
        request_id = request["request_id"]
        created = dict(request, state="prepared", expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat())
        self._nonces[request_id] = request.get("approval_nonce")
        stored = dict(created)
        stored.pop("approval_nonce", None)
        try:
            with self._lock, self._connection() as db:
                db.execute("INSERT INTO execution_requests(request_id,preview_id,state,request_json,confirmation_hash,expires_at,created_at,updated_at) "
                           "VALUES(?,?,?,?,?,?,?,?)", (request_id, request["preview_id"], "prepared",
                           _dump(stored), confirmation_hash, created["expires_at"], now.isoformat(), now.isoformat()))
            return created
        except sqlite3.DatabaseError as exc:
            self._nonces.pop(request_id, None)
            raise ExecutionError("execution_store_unavailable", "Could not persist execution request",
                                 {"reason": type(exc).__name__})

    def get_request(self, request_id):
        self.ensure_schema()
        try:
            with self._lock, self._connection() as db:
                row = db.execute("SELECT request_json,state,result_json,expires_at FROM execution_requests WHERE request_id=?",
                                 (request_id,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ExecutionError("execution_store_unavailable", "Execution history database could not be read",
                                 {"reason": type(exc).__name__})
        if row is None:
            raise ExecutionError("execution_request_not_found", "Execution request was not found")
        try:
            value = json.loads(row["request_json"])
            value["state"] = row["state"]
            value["result"] = json.loads(row["result_json"]) if row["result_json"] else None
            value["expires_at"] = row["expires_at"]
        except (ValueError, TypeError, KeyError) as exc:
            raise ExecutionError("execution_store_corrupt", "Execution request data is malformed",
                                 {"reason": type(exc).__name__})
        if value["state"] == "prepared":
            if datetime.fromisoformat(value["expires_at"]) <= utc_now():
                with self._lock, self._connection() as db:
                    db.execute("UPDATE execution_requests SET state='expired',updated_at=? "
                               "WHERE request_id=? AND state='prepared'",
                               (utc_now().isoformat(), request_id))
                self._nonces.pop(request_id, None)
                value["state"] = "expired"
                return value
            nonce = self._nonces.get(request_id)
            if nonce:
                value["approval_nonce"] = nonce
            else:
                # A prepared request loaded after process restart has lost its
                # only usable approval nonce. Never revive it from the hash.
                with self._lock, self._connection() as db:
                    db.execute("UPDATE execution_requests SET state='approval_lost',updated_at=? "
                               "WHERE request_id=? AND state='prepared'",
                               (utc_now().isoformat(), request_id))
                value["state"] = "approval_lost"
        return value

    def begin(self, request_id, confirmation_hash):
        self.ensure_schema()
        now = utc_now()
        expired = False
        approval_lost = False
        with self._lock, self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,confirmation_hash,expires_at FROM execution_requests WHERE request_id=?",
                             (request_id,)).fetchone()
            if row is None:
                raise ExecutionError("execution_request_not_found", "Execution request was not found")
            if row["state"] != "prepared":
                raise ExecutionError("execution_request_consumed", "Execution requests are single-use",
                                     {"state": row["state"]})
            nonce = self._nonces.get(request_id)
            if not nonce:
                db.execute("UPDATE execution_requests SET state='approval_lost',updated_at=? WHERE request_id=?",
                           (now.isoformat(), request_id))
                approval_lost = True
            elif not hmac.compare_digest(row["confirmation_hash"], confirmation_hash) or not hmac.compare_digest(
                    row["confirmation_hash"], _nonce_hash(nonce)):
                raise ExecutionError("confirmation_mismatch", "Explicit confirmation did not match this request")
            elif datetime.fromisoformat(row["expires_at"]) <= now:
                db.execute("UPDATE execution_requests SET state='expired',updated_at=? WHERE request_id=?",
                           (now.isoformat(), request_id))
                expired = True
            elif not approval_lost:
                db.execute("UPDATE execution_requests SET state='executing',updated_at=? WHERE request_id=?",
                           (now.isoformat(), request_id))
        if approval_lost:
            self._nonces.pop(request_id, None)
            raise ExecutionError("approval_lost", "The one-time approval nonce is unavailable; prepare a new request")
        if expired:
            self._nonces.pop(request_id, None)
            raise ExecutionError("execution_request_expired", "Execution request confirmation expired")
        self._nonces.pop(request_id, None)

    def finish(self, request_id, result):
        self.ensure_schema()
        state = ("succeeded" if result.get("status") == "success" else
                 "cancelled" if result.get("status") == "cancelled" else
                 "committed_unverified" if result.get("committed") is True else
                 "indeterminate" if result.get("status") == "indeterminate" or result.get("committed") is None else "failed")
        with self._lock, self._connection() as db:
            db.execute("UPDATE execution_requests SET state=?,result_json=?,updated_at=? WHERE request_id=?",
                       (state, _dump(result), utc_now().isoformat(), request_id))
        return self.get_request(request_id)

    def list_created_elements(self, request_id):
        result = self.get_request(request_id)
        if not result.get("result"):
            raise ExecutionError("execution_result_unavailable", "Execution request has no completed result")
        return result, result["result"].get("created_elements") or []


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _nonce_hash(value):
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
