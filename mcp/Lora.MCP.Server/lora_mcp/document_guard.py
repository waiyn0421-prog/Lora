"""Document identity checks for future guarded Revit operations; no Revit operation is implemented here."""

from .client import RevitMCPError, _identity_key


class ReadOnlyDocumentGuard:
    def __init__(self, client):
        self.client = client

    def capture(self):
        return self.client.active_document_identity()

    def verify(self, expected_identity):
        if not isinstance(expected_identity, dict):
            return {"status": "rejected", "code": "expected_identity_invalid",
                    "message": "Expected document identity must be an object.",
                    "identity_certain": False, "safe_to_proceed": False,
                    "expected_identity": expected_identity, "current_identity": None}
        required = ("document_title", "connection_endpoint", "revit_process_id")
        missing = [name for name in required if expected_identity.get(name) in (None, "")]
        if missing:
            return {"status": "rejected", "code": "expected_identity_incomplete",
                    "message": "Expected identity is missing required context fields.", "missing_fields": missing,
                    "identity_certain": False, "safe_to_proceed": False,
                    "expected_identity": expected_identity, "current_identity": None}
        try:
            current = self.capture()
        except RevitMCPError as exc:
            return {"status": "rejected", "code": exc.code, "message": str(exc),
                    "details": exc.details, "identity_certain": False,
                    "safe_to_proceed": False, "expected_identity": expected_identity,
                    "current_identity": None}
        matches = _identity_key(expected_identity) == _identity_key(current)
        required_session_fields = ("process_session_id", "application_session_id", "document_session_id")
        certain = (expected_identity.get("identity_provider_status") == "ready" and
                   current.get("identity_provider_status") == "ready" and
                   all(expected_identity.get(field) and current.get(field) for field in required_session_fields) and
                   expected_identity.get("document_identifier") == expected_identity.get("document_session_id") and
                   current.get("document_identifier") == current.get("document_session_id"))
        safe = matches and certain
        return {"status": "verified" if safe else "rejected",
                "code": "identity_verified" if safe else ("document_changed" if not matches else "identity_uncertain"),
                "message": ("Stable document identifier and context match." if safe else
                    "A verified Revit-side process, application, and document session token is missing; guarded operations are rejected." if matches else
                    "Active endpoint, process, or document identity differs from the expected snapshot."),
                "matches": matches, "identity_certain": certain, "safe_to_proceed": safe,
                "expected_identity": expected_identity, "current_identity": current}

    def run_guarded_read(self, expected_identity, read_operation):
        """Run a read callback only when identity can be proven before and after it."""
        before = self.verify(expected_identity)
        if not before["safe_to_proceed"]:
            return {"status": "rejected", "operation_executed": False,
                    "before": before, "after": None, "result": None}
        try:
            value = read_operation()
        except Exception as exc:
            return {"status": "operation_error", "operation_executed": True,
                    "before": before, "after": None,
                    "error": {"code": "operation_error", "message": type(exc).__name__}, "result": None}
        after = self.verify(expected_identity)
        return {"status": "success" if after["safe_to_proceed"] else "rejected_after_operation",
                "operation_executed": True, "before": before, "after": after,
                "result": value if after["safe_to_proceed"] else None}
