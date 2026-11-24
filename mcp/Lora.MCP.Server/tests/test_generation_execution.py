import sys
import tempfile
import unittest
import hashlib
import json
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_LIB = SERVER_ROOT / "revit_identity" / "LoraIdentity.extension" / "lib"
for path in (str(SERVER_ROOT), str(PROVIDER_LIB)):
    if path not in sys.path:
        sys.path.insert(0, path)

from lora_execution.engine import ExecutionEngine, _same_identity
from lora_execution.errors import ExecutionError
from lora_execution.repository import ExecutionRepository
from lora_identity_provider.wall_execution import _validate_wall, _validated_payload


class FakeHTTPResponse:
    status_code = 200

    def iter_content(self, chunk_size=65536):
        yield json.dumps({"status": "success"}).encode("utf-8")

    def close(self):
        pass


class FakeHTTPSession:
    def __init__(self):
        self.trust_env = True
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeHTTPResponse()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeHTTPResponse()


class ExecutionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = ExecutionRepository(Path(self.temp.name) / "execution.sqlite3")
        self.request = {"request_id": "request-1", "preview_id": "preview-1", "walls": [],
                        "approval_nonce": "nonce-1"}

    def nonce_hash(self):
        return hashlib.sha256(b"nonce-1").hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def test_persists_single_use_confirmation_and_result_history(self):
        prepared = self.repo.create_request(self.request, self.nonce_hash())
        self.assertEqual(prepared["state"], "prepared")
        self.repo.begin("request-1", self.nonce_hash())
        with self.assertRaises(ExecutionError) as caught:
            self.repo.begin("request-1", self.nonce_hash())
        self.assertEqual(caught.exception.code, "execution_request_consumed")
        finished = self.repo.finish("request-1", {"status": "success", "committed": True})
        self.assertEqual(finished["state"], "succeeded")

    def test_confirmation_mismatch_does_not_consume_request(self):
        self.repo.create_request(self.request, self.nonce_hash())
        with self.assertRaises(ExecutionError) as caught:
            self.repo.begin("request-1", "wrong")
        self.assertEqual(caught.exception.code, "confirmation_mismatch")
        self.assertEqual(self.repo.get_request("request-1")["state"], "prepared")

    def test_unverified_or_unknown_commit_is_not_recorded_as_success(self):
        self.repo.create_request(self.request, self.nonce_hash())
        self.repo.begin("request-1", self.nonce_hash())
        result = self.repo.finish("request-1", {"status": "indeterminate", "committed": None})
        self.assertEqual(result["state"], "indeterminate")

    def test_prepared_approval_is_invalidated_after_process_restart(self):
        self.repo.create_request(self.request, self.nonce_hash())
        restarted = ExecutionRepository(self.repo.path)
        self.assertEqual(restarted.get_request("request-1")["state"], "approval_lost")
        with self.assertRaises(ExecutionError) as caught:
            restarted.begin("request-1", self.nonce_hash())
        self.assertEqual(caught.exception.code, "execution_request_consumed")

    def test_expired_request_does_not_return_or_accept_its_nonce(self):
        self.repo.create_request(self.request, self.nonce_hash(), ttl_seconds=0)
        value = self.repo.get_request("request-1")
        self.assertEqual(value["state"], "expired")
        self.assertNotIn("approval_nonce", value)
        with self.assertRaises(ExecutionError) as caught:
            self.repo.begin("request-1", self.nonce_hash())
        self.assertEqual(caught.exception.code, "execution_request_consumed")


class ExecutionValidationTests(unittest.TestCase):
    def engine(self):
        return ExecutionEngine(None, None, None, None, None)

    def test_sidecar_execution_transport_ignores_environment_proxy_and_netrc(self):
        session = FakeHTTPSession()

        class Client:
            base_url = "http://127.0.0.1:48884/revit-mcp-v1"

            def _ensure_base_url(self):
                pass

        engine = ExecutionEngine(None, None, None, Client(), None,
                                 http_session=session)
        self.assertFalse(session.trust_env)
        self.assertEqual(engine._post("/validate_walls", {}).get("status"), "success")
        self.assertEqual(engine._get_execution_status("request-1").get("status"), "success")
        self.assertEqual([call[0] for call in session.calls], ["POST", "GET"])
        self.assertTrue(all(call[1].startswith("http://127.0.0.1:48884/")
                            for call in session.calls))
        self.assertTrue(all(call[2]["allow_redirects"] is False for call in session.calls))

    def test_execution_timeout_is_persisted_as_indeterminate(self):
        class Repository:
            def __init__(self):
                self.request = {"request_id": "request-1", "state": "prepared",
                                "approval_text": "Approve this wall", "approval_nonce": "secret",
                                "target_identity": {}, "preview_id": "preview-1",
                                "walls": [], "validated_walls": [], "validation_warnings": []}
                self.finished = None

            def get_request(self, request_id):
                return self.request

            def begin(self, request_id, nonce_hash):
                self.request["state"] = "executing"

            def finish(self, request_id, result):
                self.finished = result
                self.request.update(result)
                self.request["state"] = "indeterminate" if result["status"] == "indeterminate" else "failed"
                return self.request

        repository = Repository()
        engine = ExecutionEngine(None, None, None, None, repository)
        engine.validate_consuming_request = lambda request: {"status": "valid"}

        def timeout(*args, **kwargs):
            raise ExecutionError("revit_execution_timeout", "response timed out")

        engine._post = timeout
        with self.assertRaises(ExecutionError) as caught:
            engine.execute("request-1", "secret", "Approve this wall")

        self.assertEqual(caught.exception.code, "revit_execution_timeout")
        self.assertEqual(repository.finished["status"], "indeterminate")
        self.assertIsNone(repository.finished["committed"])

    def test_unavailable_sidecar_receipt_never_resolves_indeterminate_as_success(self):
        class Repository:
            def get_request(self, request_id):
                return {"request_id": request_id, "state": "indeterminate",
                        "committed": None, "created_elements": []}

        engine = ExecutionEngine(None, None, None, None, Repository())
        engine._get_execution_status = lambda request_id: None

        result = engine.result("request-1")

        self.assertEqual(result["state"], "indeterminate")
        self.assertIsNone(result["committed"])

    def preview(self):
        return {"source_drawing": "plan.pdf", "items": [{
            "planned_element_id": "wall-1", "source_review_item_id": "review-1",
            "revit_category": "Walls", "proposed_type": "Basic - 200 mm",
            "proposed_level": "Level 01", "source_page": 2,
            "geometry": {"revit_geometry_internal_feet": {
                "centerline_internal_feet": [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]],
                "source_geometry_references": ["geom-1"]}},
            "dimensions_internal_feet": {"thickness_internal_feet": 0.65616798},
            "parameter_assignments": {"Mark": "W-01"}}]}

    def constraints(self):
        return {"wall-1": {"base_constraint": {"element_id": "1", "name": "Level 01"},
                            "top_constraint": None,
                            "unconnected_height_internal_feet": 10.0,
                            "base_offset_internal_feet": 0.0,
                            "structural": False, "flip": False}}

    def plan(self):
        return {"source_drawing": "plan.pdf", "items": [{"plan_item_id": "wall-1",
                "review_item_id": "review-1", "source_page": 2,
                "mapping": {"level_id": "1"}, "revit_type_mapping": {"type_id": "20"}}]}

    def test_wall_request_requires_explicit_mapping_and_preserves_sources(self):
        walls = self.engine()._build_walls(self.preview(), self.constraints(), self.plan())
        self.assertEqual(len(walls), 1)
        self.assertEqual(walls[0]["wall_type_name"], "Basic - 200 mm")
        self.assertEqual(walls[0]["base_level_name"], "Level 01")
        self.assertEqual(walls[0]["source_drawing"], "plan.pdf")
        self.assertEqual(walls[0]["source_page"], 2)
        self.assertEqual(walls[0]["parameter_assignments"], {"Mark": "W-01"})

    def test_missing_constraints_or_extra_candidates_are_rejected(self):
        with self.assertRaises(ExecutionError):
            self.engine()._build_walls(self.preview(), {}, self.plan())
        preview = self.preview()
        preview["items"].append(dict(preview["items"][0], planned_element_id="door", revit_category="Doors"))
        with self.assertRaises(ExecutionError):
            self.engine()._build_walls(preview, dict(self.constraints(), door=self.constraints()["wall-1"]), self.plan())

    def test_preview_to_plan_matching_rejects_substituted_review_item_and_extra_constraints(self):
        preview = self.preview()
        preview["items"][0]["source_review_item_id"] = "substituted"
        with self.assertRaises(ExecutionError) as caught:
            self.engine()._build_walls(preview, self.constraints(), self.plan())
        self.assertEqual(caught.exception.code, "preview_plan_mismatch")
        with self.assertRaises(ExecutionError) as caught:
            self.engine()._build_walls(self.preview(), dict(self.constraints(), extra={}), self.plan())
        self.assertEqual(caught.exception.code, "wall_constraints_unmatched")

    def test_type_level_geometry_and_parameters_must_be_resolved(self):
        preview = self.preview()
        preview["items"][0]["geometry"]["revit_geometry_internal_feet"] = None
        with self.assertRaises(ExecutionError) as caught:
            self.engine()._build_walls(preview, self.constraints(), self.plan())
        self.assertEqual(caught.exception.code, "wall_geometry_unresolved")
        preview = self.preview()
        preview["items"][0]["parameter_assignments"] = {"Unsupported": "value"}
        with self.assertRaises(ExecutionError) as caught:
            self.engine()._build_walls(preview, self.constraints(), self.plan())
        self.assertEqual(caught.exception.code, "wall_parameters_unsupported")

    def test_top_constraint_is_explicit_and_never_reduced_to_unconnected_height(self):
        values = {"base_constraint": {"element_id": "1", "name": "Level 01"},
                  "top_constraint": {"element_id": "2", "name": "Level 02"},
                  "base_offset_internal_feet": 0.0, "top_offset_internal_feet": 0.0,
                  "structural": False, "flip": False}
        plan = self.plan()
        plan["items"][0]["mapping"].update(top_level_id="2", top_level_name="Level 02")
        wall = self.engine()._build_walls(self.preview(), {"wall-1": values}, plan)[0]
        self.assertEqual(wall["top_constraint"]["element_id"], "2")
        self.assertIsNone(wall["unconnected_height_internal_feet"])
        values["unconnected_height_internal_feet"] = 10.0
        with self.assertRaises(ExecutionError) as caught:
            self.engine()._build_walls(self.preview(), {"wall-1": values}, plan)
        self.assertEqual(caught.exception.code, "wall_constraints_incomplete")

    def test_document_identity_comparison_ignores_observation_timestamp_only(self):
        base = {"document_title": "Test", "document_path": "fixture://Test.rvt", "document_type": "project",
                "revit_version": "2024", "revit_process_id": 20, "document_identifier": "doc-1",
                "connection_endpoint": "http://127.0.0.1:48884/revit-mcp-v1", "document_session_id": "doc-1",
                "process_session_id": "proc-1", "application_session_id": "app-1",
                "document_lifecycle_sequence": 1, "document_lifecycle_generation": 1,
                "active_document_switch_counter": 0, "identity_provider_version": 1,
                "identity_provider_status": "ready"}
        changed = dict(base, observed_at="different")
        self.assertTrue(_same_identity(base, changed))
        self.assertFalse(_same_identity(base, dict(changed, document_session_id="doc-2", document_identifier="doc-2")))

    def test_sidecar_accepts_only_verified_identity_and_valid_wall_payload(self):
        identity = {"process_id": 1, "process_session_id": "p", "application_session_id": "a",
                    "document_session_id": "d", "document_lifecycle_sequence": 1,
                    "document_lifecycle_generation": 1, "active_document_switch_counter": 0,
                    "identity_provider_version": 1}
        walls = [{"planned_element_id": "w", "source_review_item_id": "r", "wall_type_name": "Basic", "wall_type_id": "20",
                  "base_level_name": "L1", "base_constraint": {"element_id": "1", "name": "L1"},
                  "centerline_internal_feet": [[0, 0, 0], [1, 0, 0]],
                  "unconnected_height_internal_feet": 10, "base_offset_internal_feet": 0,
                  "structural": False, "flip": False, "parameter_assignments": {}}]
        self.assertEqual(_validated_payload({"expected_identity": identity, "walls": walls})["walls"], walls)
        _validate_wall(walls[0])
        invalid = dict(walls[0], structural=None)
        with self.assertRaises(ValueError):
            _validate_wall(invalid)
        invalid_mode = dict(walls[0], top_constraint={"element_id": "2", "name": "L2"})
        with self.assertRaises(ValueError):
            _validate_wall(invalid_mode)
        with self.assertRaises(ValueError):
            _validated_payload({"expected_identity": {"process_id": 1}, "walls": walls})


if __name__ == "__main__":
    unittest.main()
