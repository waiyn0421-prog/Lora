import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from lora_mcp.client import RevitMCPClient, RevitMCPError
from lora_mcp.document_guard import ReadOnlyDocumentGuard
from test_read_only import FakeResponse, FakeSession, diagnostic


class ConnectionDiagnosticTests(unittest.TestCase):
    def client(self, responses):
        value = RevitMCPClient(FakeSession(responses))
        value._candidate_ports = lambda: (48884,)
        return value

    def test_successful_connection_pins_unique_endpoint_and_samples_identity_twice(self):
        client = self.client([FakeResponse(diagnostic()), FakeResponse(diagnostic()), FakeResponse(diagnostic())])
        with patch.object(client, "local_process_metadata", return_value={"status": "unavailable"}):
            result = client.diagnose_connection()
        self.assertEqual(result["connection_status"], "connected")
        self.assertEqual(result["pinned_endpoint"], "http://127.0.0.1:48884/revit-mcp-v1")
        self.assertEqual(result["identity_stability"], "stable_between_queries")
        self.assertEqual(result["active_document_identity"]["identity_certainty"], "saved_document_composite")
        self.assertFalse(result["active_document_identity"]["identity_certain_for_guard"])

    def test_revit_side_session_identity_is_consumed_and_reopen_token_changes_are_detected(self):
        def session_identity(document_session_id, process_session_id="proc-A"):
            return FakeResponse({"status": "success", "identity": {
                "process_id": 22, "process_session_id": process_session_id,
                "application_session_id": "app-A", "document_session_id": document_session_id,
                "document_lifecycle_sequence": 1, "document_lifecycle_generation": 1,
                "active_document_switch_counter": 0, "active_document": True,
                "document_title": "Demo", "document_path": "fixture://Demo.rvt",
                "document_type": "project", "document_status": "open",
                "identity_provider_version": 1, "revit_version": "2024"}})
        session = FakeSession([FakeResponse(diagnostic()), FakeResponse(diagnostic()),
                               FakeResponse({"project_name": "Demo"}), FakeResponse(diagnostic())],
                              identity_responses=[session_identity("doc-A"), session_identity("doc-B")])
        client = RevitMCPClient(session)
        client._candidate_ports = lambda: (48884,)
        client.discover()
        with self.assertRaises(RevitMCPError) as caught:
            client.call("GET", "/project_info")
        self.assertEqual(caught.exception.code, "document_changed")

    def test_matching_process_application_and_document_session_is_guard_certain(self):
        response = FakeResponse({"status": "success", "identity": {
            "process_id": 22, "process_session_id": "proc-A", "application_session_id": "app-A",
            "document_session_id": "doc-A", "document_lifecycle_sequence": 1,
            "document_lifecycle_generation": 1, "active_document_switch_counter": 0,
            "active_document": True, "document_title": "Demo", "document_path": "fixture://Demo.rvt",
            "document_type": "project", "document_status": "open", "identity_provider_version": 1}})
        client = self.client([FakeResponse(diagnostic())])
        client.session.identity_responses = [response]
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        identity = client.active_document_identity()
        self.assertEqual(identity["identity_certainty"], "stable_document_session")
        self.assertEqual(identity["document_identifier"], "doc-A")
        self.assertTrue(identity["identity_certain_for_guard"])

    def test_timeout_is_reported_without_fallback(self):
        client = self.client([requests.Timeout("slow")])
        with patch.object(client, "local_revit_processes", return_value={"status": "available", "processes": [{"Id": 22}]}):
            result = client.diagnose_connection()
        self.assertEqual(result["connection_status"], "timeout")
        self.assertEqual(result["candidate_endpoints"][0]["connection_status"], "timeout")
        self.assertIsNone(client.base_url)

    def test_failed_pinned_endpoint_is_not_replaced_by_another_candidate(self):
        client = self.client([requests.ConnectionError("pinned endpoint offline")])
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        client._candidate_ports = lambda: (48885,)

        with self.assertRaises(RevitMCPError) as caught:
            client.active_document_identity()

        self.assertEqual(caught.exception.code, "connection_error")
        self.assertEqual(client.base_url, "http://127.0.0.1:48884/revit-mcp-v1")
        self.assertEqual(len(client.session.calls), 1)
        self.assertIn(":48884/", client.session.calls[0][1])
        self.assertNotIn(":48885/", client.session.calls[0][1])

    def test_revit_unavailable_and_routes_listener_unavailable_are_distinguished(self):
        client = self.client([requests.ConnectionError("offline")])
        with patch.object(client, "local_revit_processes", return_value={"status": "available", "processes": []}):
            absent = client.diagnose_connection()
        self.assertEqual(absent["connection_status"], "revit_unavailable")
        client = self.client([requests.ConnectionError("offline")])
        with patch.object(client, "local_revit_processes", return_value={"status": "available", "processes": [{"Id": 22}]}):
            present = client.diagnose_connection()
        self.assertEqual(present["connection_status"], "routes_listener_unavailable")

    def test_http_503_reports_document_unavailable(self):
        response = FakeResponse({"status": "error", "error": "No active document"}, status_code=503)
        client = self.client([response])
        with patch.object(client, "local_revit_processes", return_value={"status": "available", "processes": [{"Id": 22}]}):
            result = client.diagnose_connection()
        self.assertEqual(result["connection_status"], "document_unavailable")

    def test_multiple_routes_endpoints_are_ambiguous_and_not_pinned(self):
        client = RevitMCPClient(FakeSession([FakeResponse(diagnostic()), FakeResponse(diagnostic())]))
        client._candidate_ports = lambda: (48884, 48885)
        with self.assertRaises(RevitMCPError) as caught:
            client.discover()
        self.assertEqual(caught.exception.code, "endpoint_ambiguous")
        self.assertIsNone(client.base_url)

    def test_document_switch_and_process_switch_are_detected(self):
        for after in (diagnostic(title="Other"), {**diagnostic(), "process": {"pid": 99}}):
            client = self.client([FakeResponse(diagnostic()), FakeResponse(diagnostic()),
                                  FakeResponse({"project_name": "Demo"}), FakeResponse(after)])
            client.discover()
            with self.assertRaises(RevitMCPError) as caught:
                client.call("GET", "/project_info")
            self.assertEqual(caught.exception.code, "document_changed")

    def test_unsaved_document_and_missing_identity_are_reported(self):
        unsaved = diagnostic(path="")
        client = self.client([FakeResponse(unsaved)])
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        identity = client.active_document_identity()
        self.assertIsNone(identity["document_path"])
        self.assertEqual(identity["identity_certainty"], "unsaved_title_process_only")
        self.assertEqual(identity["identity_provider_status"], "not_installed")
        self.assertIsNone(identity["document_identifier"])
        missing = diagnostic()
        missing["document_state"]["title"] = None
        client = self.client([FakeResponse(missing)])
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        with self.assertRaises(RevitMCPError) as caught:
            client.active_document_identity()
        self.assertEqual(caught.exception.code, "document_identity_unavailable")

    def test_identity_provider_unexpected_server_error_does_not_fall_back(self):
        client = self.client([FakeResponse(diagnostic())])
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        client.session.identity_responses = [FakeResponse({"exception": {"message": "internal error"}}, status_code=500)]
        with self.assertRaises(RevitMCPError) as caught:
            client.active_document_identity()
        self.assertEqual(caught.exception.code, "identity_provider_unavailable")

    def test_active_document_changed_exposes_safe_provider_diagnostics(self):
        safe_diagnostics = {
            "captured_document_present": True,
            "current_active_document_present": True,
            "captured_document_reference_equal": False,
            "same_revit_document": True,
            "captured_document_is_open": True,
            "event_handler_count": 5,
            "event_subscriptions": {"ViewActivated": 1},
            "failure_reason": "route_context_and_active_uidocument_are_not_equal",
        }
        client = self.client([FakeResponse(diagnostic())])
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        client.session.identity_responses = [FakeResponse({
            "status": "error", "identity": None, "error": "active_document_changed",
            "diagnostics": safe_diagnostics,
        })]
        with self.assertRaises(RevitMCPError) as caught:
            client.active_document_identity()
        self.assertEqual(caught.exception.code, "identity_provider_unavailable")
        self.assertEqual(caught.exception.details["provider_error"]["details"]["provider_diagnostics"],
                         safe_diagnostics)
        self.assertNotIn("document_session_id", str(caught.exception.details))

    def test_endpoint_change_during_read_is_rejected(self):
        client = self.client([])
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        calls = [0]
        def identity():
            calls[0] += 1
            if calls[0] == 2:
                client.base_url = "http://127.0.0.1:48885/revit-mcp-v1"
            return {"document_title": "Demo", "document_path": "fixture://Demo.rvt",
                    "revit_process_id": 22, "connection_endpoint": client.base_url}
        client._identity = identity
        client._request_json = lambda *args, **kwargs: {"project_name": "Demo"}
        with self.assertRaises(RevitMCPError) as caught:
            client.call("GET", "/project_info")
        self.assertEqual(caught.exception.code, "endpoint_changed")

    def test_document_guard_rejects_uncertain_or_changed_identity_before_operation(self):
        class StubClient:
            def active_document_identity(self):
                return {"document_title": "Demo", "document_path": "fixture://Demo.rvt",
                        "document_type": None, "revit_version": None, "revit_process_id": 22,
                        "document_identifier": None, "connection_endpoint": "http://127.0.0.1:48884/revit-mcp-v1",
                        "identity_certainty": "saved_document_composite", "identity_certain_for_guard": False}
        expected = StubClient().active_document_identity()
        guard = ReadOnlyDocumentGuard(StubClient())
        called = []
        result = guard.run_guarded_read(expected, lambda: called.append(True))
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["operation_executed"])
        self.assertEqual(called, [])

    def test_document_guard_accepts_only_matching_revit_side_session_tokens(self):
        base = {"document_title": "Demo", "document_path": "fixture://Demo.rvt",
                "document_type": "project", "revit_version": "2024", "revit_process_id": 22,
                "document_identifier": "doc-A", "document_session_id": "doc-A",
                "process_session_id": "proc-A", "application_session_id": "app-A",
                "identity_provider_status": "ready", "connection_endpoint": "http://127.0.0.1:48884/revit-mcp-v1"}

        class StubClient:
            def __init__(self, current):
                self.current = current
            def active_document_identity(self):
                return self.current

        accepted = ReadOnlyDocumentGuard(StubClient(dict(base))).verify(base)
        self.assertTrue(accepted["safe_to_proceed"])
        for field, stale in (("document_session_id", "doc-old"),
                             ("process_session_id", "proc-old"),
                             ("application_session_id", "app-old")):
            current = dict(base)
            current[field] = stale
            if field == "document_session_id":
                current["document_identifier"] = stale
            result = ReadOnlyDocumentGuard(StubClient(current)).verify(base)
            self.assertFalse(result["safe_to_proceed"], field)
            self.assertEqual(result["code"], "document_changed")


if __name__ == "__main__":
    unittest.main()
