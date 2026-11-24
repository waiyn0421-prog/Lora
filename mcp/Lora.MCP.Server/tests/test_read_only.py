import json
import unittest
from unittest.mock import patch

import requests

from lora_mcp.client import RevitMCPClient, RevitMCPError, normalize_parameters, paginate


class FakeResponse:
    def __init__(self, body, status_code=200, raw=None):
        self.status_code = status_code
        self._body = body
        self._raw = raw if raw is not None else json.dumps(body).encode("utf-8")

    def iter_content(self, chunk_size=65536):
        yield self._raw

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses, identity_responses=None):
        self.responses = list(responses)
        self.identity_responses = list(identity_responses or [])
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if "/lora-identity-v1/session_identity" in url:
            result = self.identity_responses.pop(0) if self.identity_responses else FakeResponse(
                {"exception": {"source": "pyrevit.routes",
                 "message": 'Server error (RouteHandlerNotDefinedException): Route does not exits: "GET lora-identity-v1/session_identity"'}},
                status_code=500)
            if isinstance(result, BaseException):
                raise result
            return result
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def info():
    return {"project_name": "Demo", "file_path": "fixture://Demo.rvt"}


def diagnostic(title="Demo", path="fixture://Demo.rvt"):
    return {"status": "success", "process": {"pid": 22}, "document_state": {
        "has_document": True, "title": title, "path": path,
    }}


class ClientTests(unittest.TestCase):
    def client_with(self, responses):
        session = FakeSession(responses)
        client = RevitMCPClient(session=session, max_response_bytes=2048)
        client._candidate_ports = lambda: (48885,)
        return client, session

    def test_successful_project_route_and_only_read_routes(self):
        c, s = self.client_with([FakeResponse(diagnostic()), FakeResponse(diagnostic()),
                                 FakeResponse(info()), FakeResponse(diagnostic())])
        c.discover()
        result = c.call("GET", "/project_info")
        self.assertEqual(result["project_name"], "Demo")
        self.assertEqual(len(s.calls), 6)
        self.assertTrue(all(call[0] == "GET" for call in s.calls))
        self.assertEqual(sum("/lora-identity-v1/" in call[1] for call in s.calls), 2)
        with self.assertRaises(RevitMCPError) as err:
            c.call("POST", "/elements/delete", {})
        self.assertEqual(err.exception.code, "route_not_allowed")

    def test_revit_requests_ignore_environment_proxy_and_netrc_configuration(self):
        client, session = self.client_with([FakeResponse(info())])
        self.assertFalse(session.trust_env)
        client.base_url = "http://127.0.0.1:48885/revit-mcp-v1"
        client._request_json("GET", "/project_info")
        self.assertTrue(session.calls)

    def test_coordinate_geometry_sidecar_calls_are_loopback_identity_guarded_and_bounded(self):
        route_identity = {"process_id": 22, "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}
        body = {"status": "success", "document_identity": route_identity,
                "items": [], "counts": {"total_elements_examined": 0},
                "clearance_status": "unverified"}
        session = FakeSession([FakeResponse(body)])
        client = RevitMCPClient(session=session)
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        current = {"revit_process_id": 22, "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}
        with patch.object(client, "active_document_identity", side_effect=[current, current]):
            result = client.get_geometry_inventory(offset=5, limit=10, failure_offset=2, failure_limit=3)
        self.assertEqual(result["clearance_status"], "unverified")
        self.assertEqual(session.calls[0][0], "POST")
        self.assertEqual(session.calls[0][1], "http://127.0.0.1:48884/lora-identity-v1/geometry_inventory")
        self.assertEqual(session.calls[0][2]["json"], {
            "offset": 5, "limit": 10, "failure_offset": 2, "failure_limit": 3})
        with self.assertRaises(RevitMCPError) as err:
            client._identity_sidecar_call("POST", "/execute_walls", {})
        self.assertEqual(err.exception.code, "route_not_allowed")

    def test_level_catalog_is_identity_guarded_paged_and_read_only(self):
        route_identity = {"process_id": 22, "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}
        body = {"status": "success", "document_identity": route_identity,
                "levels": [{"element_id": "12", "level_name": "L1"}],
                "total_levels": 3, "has_more": True, "inventory_complete": True}
        session = FakeSession([FakeResponse(body)])
        client = RevitMCPClient(session=session)
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        current = {"revit_process_id": 22, "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}
        with patch.object(client, "active_document_identity", side_effect=[current, current]):
            result = client.get_level_catalog(offset=1, limit=1, failure_offset=2, failure_limit=3)
        self.assertEqual(result["levels"][0]["element_id"], "12")
        self.assertEqual(session.calls[0][1], "http://127.0.0.1:48884/lora-identity-v1/level_catalog")
        self.assertEqual(session.calls[0][2]["json"], {
            "offset": 1, "limit": 1, "failure_offset": 2, "failure_limit": 3})
        with self.assertRaises(RevitMCPError):
            client.get_level_catalog(limit=0)

    def test_level_catalog_rejects_a_different_sidecar_document_session(self):
        body = {"status": "success", "document_identity": {"process_id": 22,
            "process_session_id": "other", "application_session_id": "a",
            "document_session_id": "d"}, "levels": [], "total_levels": 0}
        client = RevitMCPClient(session=FakeSession([FakeResponse(body)]))
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        current = {"revit_process_id": 22, "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}
        with patch.object(client, "active_document_identity", side_effect=[current, current]):
            with self.assertRaises(RevitMCPError) as error:
                client.get_level_catalog()
        self.assertEqual(error.exception.code, "document_changed")

    def test_coordinate_geometry_sidecar_rejects_identity_mismatch(self):
        body = {"status": "success", "document_identity": {"process_id": 999,
            "process_session_id": "other", "application_session_id": "a", "document_session_id": "d"}}
        client = RevitMCPClient(session=FakeSession([FakeResponse(body)]))
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        current = {"revit_process_id": 22, "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}
        with patch.object(client, "active_document_identity", side_effect=[current, current]):
            with self.assertRaises(RevitMCPError) as err:
                client.get_coordinate_diagnostic()
        self.assertEqual(err.exception.code, "document_changed")

    def test_category_inventory_is_loopback_identity_guarded_and_independently_paged(self):
        route_identity = {"process_id": 22, "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}
        body = {"status": "success", "document_identity": route_identity,
                "clearance_status": "unverified", "categories": [], "items": [], "failures": []}
        session = FakeSession([FakeResponse(body)])
        client = RevitMCPClient(session=session)
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        current = {"revit_process_id": 22, "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}
        with patch.object(client, "active_document_identity", side_effect=[current, current]):
            result = client.get_category_inventory(discipline="mep", category_id="-2008044",
                offset=7, limit=3, failure_offset=4, failure_limit=2,
                category_offset=1, category_limit=5)
        self.assertEqual(result["clearance_status"], "unverified")
        call = session.calls[0]
        self.assertEqual(call[1], "http://127.0.0.1:48884/lora-identity-v1/category_inventory")
        self.assertEqual(call[2]["json"], {"discipline": "mep", "category_id": "-2008044",
            "offset": 7, "limit": 3, "failure_offset": 4, "failure_limit": 2,
            "category_offset": 1, "category_limit": 5})

    def test_selected_geometry_route_is_loopback_identity_guarded_and_bounded(self):
        body = {"status": "success", "document_identity": {"process_id": 22,
            "process_session_id": "p", "application_session_id": "a", "document_session_id": "d"},
            "clearance_status": "unverified", "items": [], "geometry_entities": []}
        session = FakeSession([FakeResponse(body)])
        client = RevitMCPClient(session=session)
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        current = {"revit_process_id": 22, "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}
        with patch.object(client, "active_document_identity", side_effect=[current, current]):
            result = client.get_geometry_detail(["21", 22], entity_offset=4, entity_limit=10)
        self.assertEqual(result["clearance_status"], "unverified")
        self.assertEqual(session.calls[0][1], "http://127.0.0.1:48884/lora-identity-v1/geometry_detail")
        self.assertEqual(session.calls[0][2]["json"], {"element_ids": ["21", "22"],
            "entity_offset": 4, "entity_limit": 10})
        with self.assertRaises(RevitMCPError):
            client.get_geometry_detail(["x"])

    def test_coordinate_geometry_sidecar_rejects_endpoint_change(self):
        body = {"status": "success", "document_identity": {"process_id": 22,
            "process_session_id": "p", "application_session_id": "a", "document_session_id": "d"}}
        client = RevitMCPClient(session=FakeSession([FakeResponse(body)]))
        client.base_url = "http://127.0.0.1:48884/revit-mcp-v1"
        current = {"revit_process_id": 22, "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}

        def switch_endpoint():
            client.base_url = "http://127.0.0.1:48885/revit-mcp-v1"
            return current

        calls = [0]

        def identity_read():
            calls[0] += 1
            return switch_endpoint() if calls[0] == 2 else current

        with patch.object(client, "active_document_identity", side_effect=identity_read):
            with self.assertRaises(RevitMCPError) as err:
                client.get_coordinate_diagnostic()
        self.assertEqual(err.exception.code, "endpoint_changed")

    def test_empty_and_pagination_responses(self):
        self.assertEqual(paginate([], 0, 10)["items"], [])
        page = paginate([1, 2, 3], 1, 1)
        self.assertEqual(page["items"], [2])
        self.assertTrue(page["has_more"])
        self.assertFalse(page["complete"])

    def test_upstream_truncation_is_not_claimed_complete(self):
        page = paginate([1, 2], 0, 10, total=5)
        self.assertFalse(page["upstream_complete"])
        self.assertFalse(page["complete"])

    def test_invalid_inputs_and_categories(self):
        with self.assertRaises(RevitMCPError):
            paginate([], 0, 201)
        from lora_mcp.client import validate_category
        with self.assertRaises(RevitMCPError):
            validate_category("")

    def test_document_identity_mismatch(self):
        c, _ = self.client_with([FakeResponse(diagnostic()), FakeResponse(diagnostic()),
                                 FakeResponse(info()), FakeResponse(diagnostic(title="Other"))])
        c.discover()
        with self.assertRaises(RevitMCPError) as err:
            c.call("GET", "/project_info")
        self.assertEqual(err.exception.code, "document_changed")

    def test_connection_unavailable(self):
        c, _ = self.client_with([requests.ConnectionError("offline")])
        with self.assertRaises(RevitMCPError) as err:
            c.discover()
        self.assertEqual(err.exception.code, "routes_unavailable")

    def test_timeout_handling(self):
        c, _ = self.client_with([FakeResponse(diagnostic()), FakeResponse(diagnostic()),
                                 requests.Timeout("slow")])
        c.discover()
        with self.assertRaises(RevitMCPError) as err:
            c.call("GET", "/project_info")
        self.assertEqual(err.exception.code, "timeout")

    def test_invalid_json(self):
        c, _ = self.client_with([FakeResponse({}, raw=b"not-json")])
        c.base_url = "http://127.0.0.1:48885/revit-mcp-v1"
        with self.assertRaises(RevitMCPError) as err:
            c._request_json("GET", "/project_info")
        self.assertEqual(err.exception.code, "invalid_upstream_json")

    def test_large_response_rejected(self):
        c, _ = self.client_with([FakeResponse({}, raw=b" " * 2049)])
        c.base_url = "http://127.0.0.1:48885/revit-mcp-v1"
        with self.assertRaises(RevitMCPError) as err:
            c._request_json("GET", "/project_info")
        self.assertEqual(err.exception.code, "response_too_large")

    def test_unit_conversion(self):
        self.assertEqual(1.0 * 304.8, 304.8)

    def test_large_result_set_is_paged(self):
        result = paginate(list(range(1000)), 400, 100)
        self.assertEqual(len(result["items"]), 100)
        self.assertEqual(result["items"][0], 400)
        self.assertTrue(result["has_more"])

    def test_missing_parameter_field_is_reported_missing(self):
        from lora_mcp import server

        class StubClient:
            def call(self, method, route, payload):
                return {"elements": [{"element_id": "17", "properties": {}, "typed_properties": {}}]}

        with patch.object(server, "client", StubClient()):
            result = server.lora_get_element_parameters([17], ["Fire Rating"])
        self.assertEqual(result["items"][0]["parameters"][0]["value_status"], "missing")

    def test_parameter_value_status_and_upstream_limits(self):
        result = normalize_parameters({"properties": {"Blank": "", "Unknown": "Not available"},
                                       "typed_properties": {"Blank": {"storage_type": "String", "has_value": True},
                                                            "Unknown": {"storage_type": None, "has_value": False}}})
        self.assertEqual(result[0]["value_status"], "empty")
        self.assertEqual(result[1]["value_status"], "unavailable")
        self.assertIsNone(result[0]["is_read_only"])
        self.assertIn("upstream_does_not_report", result[0]["parameter_scope"])

    def test_original_six_lora_tools_remain_registered(self):
        from lora_mcp.server import mcp
        names = {tool.name for tool in mcp._tool_manager.list_tools()}
        self.assertTrue({
            "lora_get_project_info", "lora_get_levels", "lora_get_views",
            "lora_get_elements", "lora_get_element_parameters", "lora_analyze_model",
        }.issubset(names))

    def test_drawing_tools_are_read_only_and_file_name_scoped(self):
        from lora_mcp.server import mcp
        names = {tool.name for tool in mcp._tool_manager.list_tools()}
        self.assertEqual(names, {
            "lora_get_project_info", "lora_get_levels", "lora_get_views",
            "lora_get_elements", "lora_get_element_parameters", "lora_analyze_model",
            "lora_get_bim_context", "lora_answer_bim_question", "lora_inspect_drawing",
            "lora_process_pdf", "lora_process_image", "lora_get_drawing_summary",
            "lora_get_drawing_render", "lora_extract_drawing_geometry",
            "lora_analyze_drawing_geometry", "lora_get_architectural_candidates",
            "lora_recognize_architecture", "lora_get_recognized_elements",
            "lora_analyze_recognition", "lora_get_recognition_warnings",
            "lora_calibrate_drawing", "lora_get_calibration_result",
            "lora_get_reconstructed_spatial_geometry", "lora_validate_calibration_references",
            "lora_create_drawing_review", "lora_get_drawing_review", "lora_get_review_items",
            "lora_get_review_item", "lora_update_review_item", "lora_approve_review_item",
            "lora_reject_review_item", "lora_get_review_history", "lora_get_review_summary",
            "lora_validate_drawing_review", "lora_create_generation_plan", "lora_get_generation_plan",
            "lora_validate_generation_plan", "lora_get_generation_plan_items",
            "lora_update_generation_mapping", "lora_get_generation_plan_summary",
            "lora_create_generation_preview", "lora_get_generation_preview",
            "lora_get_generation_preview_items", "lora_validate_generation_preview",
            "lora_get_generation_preview_summary", "lora_compare_generation_previews",
            "lora_diagnose_revit_connection", "lora_get_active_document_identity",
            "lora_validate_document_identity", "lora_get_revit_connection_status",
            "lora_prepare_execution", "lora_get_execution_preview", "lora_validate_execution",
            "lora_execute_walls", "lora_get_execution_result", "lora_verify_created_elements",
            "lora_get_wall_types", "lora_create_explicit_wall_review", "lora_approve_explicit_wall_geometry",
            "lora_get_coordinate_diagnostic", "lora_get_geometry_inventory", "lora_get_category_inventory",
            "lora_get_element_geometry", "lora_list_commands", "lora_get_level_catalog",
        })
        from lora_drawing.processing_pipeline import DrawingProcessingPipeline
        pipeline = DrawingProcessingPipeline()
        self.assertFalse(hasattr(pipeline, "write_revit"))
        self.assertFalse(hasattr(pipeline, "create_element"))

    def test_render_tool_returns_mcp_image_content_from_memory(self):
        from lora_mcp import server
        from mcp.server.fastmcp import Image

        class RenderCache:
            def get_render(self, processing_id, page_number):
                return {"data": b"\x89PNG\r\n\x1a\nsynthetic", "metadata": {"mime_type": "image/png"}}

        with patch.object(server, "drawing_pipeline", RenderCache()):
            result = server.lora_get_drawing_render("known-id", 1)
        self.assertIsInstance(result, Image)
        self.assertEqual(result.data[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
