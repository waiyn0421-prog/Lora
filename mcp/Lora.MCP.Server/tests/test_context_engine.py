import re
import unittest
from pathlib import Path

from lora_context.engine import BIMContextEngine, ContextError, INTERNAL_FEET_TO_MM, normalize_level
from lora_context.registry import COMMANDS, find_command
from lora_mcp.client import READ_ROUTES
from lora_mcp.server import context_engine as server_context_engine, mcp


IDENTITY = {"title": "Demo Building Sample Architectural", "path": "fixture://Demo.rvt", "process_id": 42}


class FakeTools:
    def __init__(self, ids=None, total=None, page_size=2, parameter_values=None, truncated=False):
        self.ids = ids if ids is not None else [101, 102, 103]
        self.total = total if total is not None else len(self.ids)
        self.page_size = page_size
        self.parameter_values = parameter_values or {}
        self.truncated = truncated
        self.calls = []

    def __getitem__(self, name):
        return getattr(self, name)

    def __contains__(self, name):
        return name in BIMContextEngine.REQUIRED_TOOLS

    def lora_get_project_info(self):
        self.calls.append("project")
        return {"status": "success", "project_name": "Demo Building",
                "project_number": "DEMO-001", "status_value": "Construction Documents",
                "document_identity": dict(IDENTITY), "revit_version": None}

    def lora_get_levels(self, offset=0, limit=200):
        self.calls.append("levels")
        levels = [{"level_name": "L1", "elevation_internal_feet": 0.0},
                  {"level_name": "L2", "elevation_internal_feet": 10.0}]
        return {"status": "success", "items": levels[offset:offset + limit], "total": 2,
                "complete": True, "has_more": False}

    def lora_get_views(self, offset=0, limit=60):
        self.calls.append("views")
        views = [{"id": "1", "name": "L1 Plan", "type": "FloorPlan"},
                 {"id": "2", "name": "{3D}", "type": "ThreeD"}]
        items = views[offset:offset + limit]
        return {"status": "success", "items": items, "total": 2,
                "offset": offset, "limit": limit, "has_more": offset + len(items) < 2,
                "complete": offset + len(items) >= 2, "upstream_complete": True}

    def lora_analyze_model(self, top_n=200, include_levels=True):
        self.calls.append("analysis")
        categories = [
            {"category_name": "Walls", "element_count": 2},
            {"category_name": "Doors", "element_count": 1},
            {"category_name": "Windows", "element_count": 0},
        ]
        return {"status": "success", "summary": {"total_elements": 3, "total_categories": 3,
                "total_levels": 2}, "categories": categories, "levels": [], "complete": True,
                "categories_complete": True, "levels_complete": True}

    def lora_get_elements(self, category, level="", offset=0, limit=40, include_parameters=False):
        self.calls.append(("elements", category, level, offset))
        items = self.ids[offset:offset + min(self.page_size, limit)]
        next_offset = offset + len(items)
        has_more = next_offset < min(self.total, len(self.ids)) or self.truncated
        return {"status": "success", "items": [{"element_id": value, "category": category} for value in items],
                "total": self.total, "offset": offset, "limit": limit, "has_more": has_more,
                "complete": not has_more and self.total == len(self.ids),
                "upstream_complete": not self.truncated}

    def lora_get_element_parameters(self, element_ids, parameter_names=None, offset=0, limit=40):
        self.calls.append(("parameters", list(element_ids)))
        rows = []
        for element_id in element_ids:
            value = self.parameter_values.get(int(element_id), {"value_status": "populated", "value": "A"})
            rows.append({"element_id": str(element_id), "parameters": [dict({"name": "Mark"}, **value)]})
        return {"status": "success", "items": rows, "total": len(element_ids), "complete": True,
                "upstream_complete": True, "has_more": False}

    def lora_get_element_geometry(self, element_ids, entity_offset=0, entity_limit=100):
        self.calls.append(("geometry", list(element_ids)))
        items = [{"element_id": str(value), "geometry_status": "inspected",
                  "geometry_complete": True, "location_status": "inspected",
                  "bounding_box_status": "inspected"} for value in element_ids]
        return {"status": "success", "items": items, "geometry_entities": [],
                "entity_has_more": False, "truncated": False, "failures": [],
                "clearance_status": "unverified"}

    def lora_get_category_inventory(self, discipline=None, category_id=None,
                                    offset=0, limit=50, failure_offset=0, failure_limit=50,
                                    category_offset=0, category_limit=50):
        self.calls.append(("category_inventory", discipline, offset, limit, category_offset, category_limit))
        return {"status": "success", "categories": [
                    {"category_id": "-2000011", "built_in_category": "OST_Walls",
                     "category_name": "Walls", "discipline": "architecture",
                     "counts": {"model_instance": 2}, "element_type_count": 3, "total": 2}],
                "category_offset": category_offset, "category_limit": category_limit,
                "category_page_has_more": False, "total_categories": 1,
                "elements_examined": 5, "element_types_examined": 3,
                "total_failures": 0, "failures": [], "inventory_complete": True,
                "category_pagination_complete": True,
                "discipline_inspection_incomplete_elements": 0,
                "clearance_status": "unverified"}


class ContextEngineTests(unittest.TestCase):
    def setUp(self):
        self.tools = FakeTools()
        self.engine = BIMContextEngine(self.tools)

    def test_context_assembly_uses_existing_tools_and_reports_completeness(self):
        context = self.engine.build_context(view_limit=1, category="Walls", element_limit=1,
                                            parameter_names=["Mark"])
        self.assertEqual(context["project"]["project_name"], "Demo Building")
        self.assertEqual(context["levels"]["items"][1]["elevation_mm"], 3048.0)
        self.assertEqual(context["views"]["total"], 2)
        self.assertFalse(context["views"]["complete"])
        self.assertEqual(context["category_summary"]["items"][0]["category_name"], "Walls")
        self.assertEqual(context["element_parameters"]["items"][0]["element_id"], 101)
        self.assertTrue(context["completeness"]["identity_stable"])
        self.assertIn("elements", [call[0] for call in self.tools.calls if isinstance(call, tuple)])

    def test_server_context_engine_wires_read_only_category_and_geometry_tools(self):
        self.assertTrue(server_context_engine._has_tool("lora_get_category_inventory"))
        self.assertTrue(server_context_engine._has_tool("lora_get_element_geometry"))
        self.assertTrue(server_context_engine._has_tool("lora_get_level_catalog"))

    def test_mult_page_category_retrieval_is_not_reported_complete_early(self):
        self.tools.page_size = 2
        result = self.engine._collect_elements("Walls", max_pages=1)
        self.assertEqual(result["element_ids"], [101, 102])
        self.assertFalse(result["complete"])
        result = self.engine._collect_elements("Walls", max_pages=2)
        self.assertEqual(result["element_ids"], [101, 102, 103])
        self.assertTrue(result["complete"])

    def test_missing_parameter_audit_counts_empty_missing_and_unavailable_separately(self):
        self.tools.parameter_values = {
            101: {"value_status": "populated", "value": "W-1"},
            102: {"value_status": "empty", "value": ""},
            103: {"value_status": "unavailable", "value": "Not available"},
        }
        result = self.engine.answer("Which walls have missing Mark parameters?")
        self.assertEqual(result["intent"], "missing_parameter_audit")
        self.assertEqual(result["evidence"]["missing_or_empty_count"], 1)
        self.assertEqual(result["evidence"]["unavailable_count"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["evidence"]["missing_element_ids"], ["102"])

    def test_empty_element_result_is_zero_not_unavailable(self):
        self.tools.ids = []
        self.tools.total = 0
        result = self.engine.answer("Which walls are in this project?")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["scope"]["elements_reported"], 0)
        self.assertTrue(result["complete"])
        self.assertEqual(result["evidence"]["element_ids"], [])

    def test_truncated_category_page_is_explicit(self):
        self.tools.truncated = True
        result = self.engine.answer("Which walls are in this project?", max_pages=1)
        self.assertFalse(result["complete"])
        self.assertTrue(result["warnings"])
        self.assertIn("partial result", result["answer"])

    def test_conversion_preserves_internal_and_display_units(self):
        level = normalize_level({"level_name": "L2", "elevation_internal_feet": 2.0})
        self.assertEqual(INTERNAL_FEET_TO_MM, 304.8)
        self.assertEqual(level["elevation_mm"], 609.6)
        self.assertEqual(level["elevation_units"], {"internal": "feet", "display": "millimetres"})

    def test_invalid_query_does_not_call_revit_tools(self):
        with self.assertRaises(ContextError) as caught:
            self.engine.answer("")
        self.assertEqual(caught.exception.code, "invalid_query")
        self.assertEqual(self.tools.calls, [])

    def test_category_counts_and_level_alias_are_grounded_in_tool_data(self):
        result = self.engine.answer("How many walls, doors and windows are in this project?")
        self.assertEqual([x["count"] for x in result["evidence"]["categories"]], [2, 1, 0])
        self.assertTrue(result["complete"])

    def test_level_specific_count_resolves_level_02(self):
        result = self.engine.answer("How many doors are on Level 02?")
        self.assertEqual(result["intent"], "category_count_by_level")
        self.assertEqual(result["scope"]["level"], "L2")
        self.assertEqual(result["evidence"]["categories"][0]["count"], 3)

    def test_natural_language_geometry_query_requires_and_uses_explicit_ids(self):
        result = self.engine.answer("Inspect geometry for elements 21, 22, and 23")
        self.assertEqual(result["intent"], "element_geometry")
        self.assertEqual(result["scope"]["element_ids"], ["21", "22", "23"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["evidence"]["clearance_status"], "unverified")
        self.assertIn(("geometry", ["21", "22", "23"]), self.tools.calls)

    def test_natural_language_geometry_without_ids_does_not_scan_model(self):
        result = self.engine.answer("Show model geometry")
        self.assertEqual(result["status"], "unsupported_query")
        self.assertEqual(result["scope"]["recommended_tool"], "lora_get_element_geometry")
        self.assertFalse(any(isinstance(call, tuple) and call[0] == "geometry" for call in self.tools.calls))

    def test_natural_language_geometry_enforces_selected_element_cap(self):
        ids = ", ".join(str(value) for value in range(1, 27))
        with self.assertRaises(ContextError):
            self.engine.answer("Inspect geometry for elements " + ids)
        self.assertFalse(any(isinstance(call, tuple) and call[0] == "geometry" for call in self.tools.calls))

    def test_natural_language_category_inventory_uses_discipline_filter_and_keeps_clearance_unverified(self):
        result = self.engine.answer("Show the architectural category inventory")
        self.assertEqual(result["intent"], "category_inventory")
        self.assertEqual(result["scope"]["discipline"], "architecture")
        self.assertTrue(result["complete"])
        self.assertEqual(result["evidence"]["clearance_status"], "unverified")
        self.assertIn(("category_inventory", "architecture", 0, 1, 0, 50), self.tools.calls)

    def test_command_registry_accuracy_against_bundle_names_and_files(self):
        root = Path(__file__).resolve().parents[3]
        expected = {
            "Family Generator": "Lora.tab/Annotation.panel/Family Generator.pushbutton/script.py",
            "Tag Studio": "Lora.tab/Annotation.panel/TagStudio.pushbutton/script.py",
            "Auto Dim": "Lora.tab/Documentation.panel/Auto Dim.pushbutton/script.py",
            "Data Sync": "Lora.tab/Documentation.panel/Data Sync.pushbutton/script.py",
            "Draw Area Boundary": "Lora.tab/Documentation.panel/Draw Area Boundary.pushbutton/script.py",
            "TableGen": "Lora.tab/Documentation.panel/TableGen.pushbutton/script.py",
            "InPlace to Family": "Lora.tab/Family.panel/InPlace to Family.pushbutton/script.py",
            "Family Version Audit": "Lora.tab/Family.panel/Family Version Audit.pushbutton/script.py",
            "Tag Creation": "Lora.tab/MEP.panel/TagCreation.pushbutton/script.py",
            "Refresh": "Lora.tab/MEP.panel/UpdateMEPElevations.pushbutton/script.py",
            "Hide Section": "Lora.tab/View.panel/HideSection.pushbutton/script.py",
            "View Range": "Lora.tab/View.panel/ViewRange.pushbutton/script.py",
        }
        discovered_paths = {
            path.relative_to(root).as_posix()
            for path in (root / "Lora.tab").glob("*/*.pushbutton/script.py")
        }
        self.assertEqual(discovered_paths, set(expected.values()))
        self.assertEqual(len(COMMANDS), len(expected))
        self.assertEqual({item["name"] for item in COMMANDS}, set(expected))
        self.assertEqual(len({item["path"] for item in COMMANDS}), len(expected))
        for item in COMMANDS:
            self.assertEqual(item["path"], expected[item["name"]])
            self.assertTrue((root / item["path"]).is_file())
            bundle = (root / item["path"]).parent / "bundle.yaml"
            title_line = next(line for line in bundle.read_text(encoding="utf-8").splitlines() if line.startswith("title:"))
            self.assertEqual(title_line.split(":", 1)[1].strip(), item["name"])
            self.assertTrue(((root / item["path"]).parent / "icon.png").is_file())
            for key in ("purpose", "required_input", "supported_context", "limitations", "model_effect"):
                self.assertTrue(item[key])
            self.assertIs(find_command(item["name"])["read_only"],
                           item["name"] == "Family Version Audit")

    def test_recommendation_does_not_execute_refresh(self):
        result = self.engine.answer("Please update MEP elevations for these selected pipes")
        self.assertEqual(result["intent"], "command_recommendation")
        self.assertEqual(result["evidence"]["recommended_commands"][0]["name"], "Refresh")
        self.assertIn("Writes elevation/service/size/status parameters", result["answer"])
        self.assertTrue(result["not_executed"])
        self.assertFalse(any(isinstance(call, tuple) and call[0] == "write" for call in self.tools.calls))

    def test_family_version_audit_recommendation_is_read_only_and_not_executed(self):
        result = self.engine.answer("Inspect the RFA version for this family")
        self.assertEqual(result["intent"], "command_recommendation")
        self.assertEqual(result["evidence"]["recommended_commands"][0]["name"], "Family Version Audit")
        self.assertIn("does not start a transaction", result["answer"].lower())
        self.assertTrue(result["not_executed"])
        self.assertEqual(self.tools.calls, [])

    def test_family_compatibility_audit_recommendation_remains_read_only(self):
        result = self.engine.answer("Check family compatibility for an RFA")
        self.assertEqual(result["intent"], "command_recommendation")
        self.assertEqual(result["evidence"]["recommended_commands"][0]["name"],
                         "Family Version Audit")
        self.assertIn("does not start a transaction", result["answer"].lower())
        self.assertTrue(result["not_executed"])
        self.assertEqual(self.tools.calls, [])

    def test_command_explanation_includes_model_effect_and_limitations(self):
        result = self.engine.answer("What does Family Generator do?")
        self.assertEqual(result["intent"], "command_information")
        self.assertIn("creates an external RFA", result["answer"])
        self.assertIn("Does not compose new tag label graphics", result["answer"])
        self.assertTrue(result["not_executed"])
        self.assertEqual(self.tools.calls, [])

    def test_natural_language_lists_verified_lora_commands_without_revit_queries(self):
        result = self.engine.answer("What Lora commands are available?")
        self.assertEqual(result["intent"], "command_list")
        self.assertEqual(len(result["evidence"]["commands"]), len(COMMANDS))
        self.assertEqual({item["name"] for item in result["evidence"]["commands"]},
                         {item["name"] for item in COMMANDS})
        self.assertTrue(result["not_executed"])
        self.assertTrue(result["complete"])
        self.assertEqual(self.tools.calls, [])

    def test_natural_language_level_id_query_uses_bounded_level_catalog(self):
        def level_catalog(**kwargs):
            self.tools.calls.append(("level_catalog", kwargs))
            return {"status": "success", "levels": [{"element_id": "10",
                "level_name": "L1", "element_id_status": "inspected",
                "name_status": "inspected", "elevation_internal_feet": 0.0}],
                "total_levels": 1, "offset": 0, "limit": 100, "has_more": False,
                "inventory_complete": True, "total_failures": 0, "failures": [],
                "warnings": [], "clearance_status": "unverified"}
        self.tools.lora_get_level_catalog = level_catalog
        result = self.engine.answer("What are the Revit level element IDs?")
        self.assertEqual(result["intent"], "level_ids")
        self.assertIn("L1 (element ID 10)", result["answer"])
        self.assertTrue(result["complete"])
        self.assertTrue(result["not_executed"])
        self.assertEqual(self.tools.calls[0][0], "level_catalog")
        self.assertEqual(self.tools.calls[0][1]["limit"], 100)

    def test_direct_command_registry_tool_is_read_only_and_does_not_call_revit(self):
        from lora_mcp import server
        result = server.lora_list_commands()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total"], len(COMMANDS))
        self.assertTrue(result["not_executed"])
        self.assertEqual(result["commands"], list(COMMANDS))
        tool = next(tool for tool in server.mcp._tool_manager.list_tools()
                    if tool.name == "lora_list_commands")
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)

    def test_wall_execution_tools_are_separate_from_read_route_allowlist(self):
        names = {tool.name for tool in mcp._tool_manager.list_tools()}
        self.assertEqual(names, {
            "lora_get_project_info", "lora_get_levels", "lora_get_views", "lora_get_elements",
            "lora_get_element_parameters", "lora_analyze_model", "lora_get_bim_context",
            "lora_answer_bim_question", "lora_inspect_drawing", "lora_process_pdf",
            "lora_process_image", "lora_get_drawing_summary", "lora_get_drawing_render",
            "lora_extract_drawing_geometry", "lora_analyze_drawing_geometry",
            "lora_get_architectural_candidates",
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
        self.assertEqual(len(names), 65)
        self.assertTrue(next(tool for tool in mcp._tool_manager.list_tools()
                             if tool.name == "lora_execute_walls").annotations.destructiveHint)
        self.assertEqual([tool.name for tool in mcp._tool_manager.list_tools()
                          if tool.annotations and tool.annotations.destructiveHint], ["lora_execute_walls"])
        for name in ("lora_get_coordinate_diagnostic", "lora_get_geometry_inventory",
                     "lora_get_category_inventory", "lora_get_element_geometry", "lora_get_level_catalog"):
            tool = next(tool for tool in mcp._tool_manager.list_tools() if tool.name == name)
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
        for method, route in READ_ROUTES:
            self.assertNotIn(route.split("/")[-1], {"delete", "create", "update", "tag", "duplicate", "place_view"})


if __name__ == "__main__":
    unittest.main()
