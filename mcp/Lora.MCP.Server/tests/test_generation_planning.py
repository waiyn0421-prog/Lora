import sys
import tempfile
import unittest
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from lora_generation import GenerationPlanRepository, GenerationPlanner
from lora_generation.engine import _duplicate_pairs, _same_document_identity
from lora_generation.inventory import read_generation_inventory
from lora_mcp.client import RevitMCPError
from lora_review import DrawingReviewManager, ReviewRepository
from test_drawing_review import calibration, source_fixture


def inventory():
    return {"status": "success", "document_identity": {"title": "Sample", "path": "sample.rvt"},
            "document_title": "Sample", "document_path": "sample.rvt", "revit_version": "2024",
            "levels": [{"level_name": "L1", "elevation_internal_feet": 0.0}],
            "level_names": ["L1"], "family_types": [], "family_types_truncated": False,
            "system_types_by_category": {"Walls": {"types": ["Basic - 200 mm"], "truncated": False}},
            "parameter_names": ["Mark", "Comments"], "warnings": []}


class FakeClient:
    def __init__(self):
        self.calls = []

    def call(self, method, route, payload=None):
        self.calls.append((method, route))
        if route == "/project_info":
            return {"_lora_document_identity": "doc", "project_name": "Sample", "file_path": "sample.rvt"}
        if route == "/diagnostics/revit_state":
            return {"_lora_document_identity": "doc", "document_state": {"title": "Sample", "path": "sample.rvt"}}
        if route == "/model/statistics":
            return {"_lora_document_identity": "doc", "levels": [], "categories": []}
        if route == "/families/types":
            return {"_lora_document_identity": "doc", "family_types": []}
        if route == "/schema/context":
            return {"_lora_document_identity": "doc", "schema": {}}
        raise AssertionError("Unexpected route: {}".format(route))


class GenerationPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = ReviewRepository(Path(self.temp.name) / "reviews.sqlite3")
        self.manager = DrawingReviewManager(self.repo)
        created = self.manager.create(source_fixture(), [calibration()])
        self.review_id = created["session"]["session_id"]
        self.items = self.manager.list_items(self.review_id, limit=50)["items"]
        self.by_recognition = {item["recognition_type"]: item for item in self.items}
        self.plans = GenerationPlanRepository(self.repo)
        self.planner = GenerationPlanner(self.manager, self.plans)

    def tearDown(self):
        self.temp.cleanup()

    def approve(self, item):
        return self.manager.approve(self.review_id, item["item_id"], "tester", "explicit test approval")

    def test_unapproved_items_are_excluded_and_reported(self):
        self.approve(self.by_recognition["Door or window opening"])
        plan = self.planner.create(self.review_id, inventory())
        self.assertEqual(len(plan["items"]), 1)
        self.assertEqual(plan["items"][0]["planned_category"], "Openings")
        self.assertEqual(len(plan["unresolved_items"]), 2)
        self.assertTrue(any(issue["code"] == "approval_missing" for issue in plan["validation"]["errors"]))

    def test_wall_planning_converts_units_maps_type_level_and_persists(self):
        wall = self.approve(self.by_recognition["Wall"])
        mappings = {wall["item_id"]: {"level_name": "L1", "level_id": "1", "type_name": "Basic - 200 mm"}}
        plan = self.planner.create(self.review_id, inventory(), mappings)
        item = plan["items"][0]
        self.assertEqual(item["planned_category"], "Walls")
        self.assertAlmostEqual(item["geometry"]["dimensions"]["thickness_internal_feet"], 20 / 304.8)
        self.assertEqual(item["revit_type_mapping"]["type_name"], "Basic - 200 mm")
        self.assertEqual(item["plan_status"], "planned_with_warnings")
        self.assertEqual(plan["plan_status"], "blocked")  # Other review candidates remain unresolved.
        loaded = self.planner.get(plan["plan_id"])
        self.assertEqual(loaded["plan_id"], plan["plan_id"])
        self.assertEqual(loaded["history"][0]["event"]["action"], "plan_created")

    def test_stale_approved_calibration_is_excluded(self):
        wall = self.approve(self.by_recognition["Wall"])
        self.repo.mutate_item(self.review_id, wall["item_id"], lambda value: (value.update(calibration_state="stale") or True, None),
                              {"action": "test_stale"})
        plan = self.planner.create(self.review_id, inventory())
        self.assertEqual(plan["items"], [])
        self.assertTrue(any(issue["code"] == "calibration_stale" for issue in plan["validation"]["errors"]))

    def test_opening_requires_explicit_door_window_category_and_host(self):
        opening = self.by_recognition["Door or window opening"]
        self.manager.update(self.review_id, opening["item_id"], properties={"category": "Doors"})
        self.approve(self.manager.get_item(self.review_id, opening["item_id"]))
        plan = self.planner.create(self.review_id, inventory(), {opening["item_id"]: {"level_name": "L1"}})
        self.assertEqual(plan["items"][0]["planned_category"], "Doors")
        self.assertTrue(any(issue["code"] == "type_mapping_missing" for issue in plan["validation"]["errors"]))
        self.assertTrue(any(issue["code"] == "host_dependency_missing" for issue in plan["validation"]["errors"]))

    def test_explicit_wall_review_plans_without_fabricated_drawing_or_calibration(self):
        created = self.manager.create_explicit_wall_review("revit_internal", "mm",
            [1200, 2400, 0], [5200, 2400, 0], 4000,
            "User specified Revit internal origin")
        review_id = created["session"]["session_id"]
        item_id = created["item"]["item_id"]
        self.manager.approve_explicit_geometry(review_id, item_id,
            created["required_geometry_confirmation"], "reviewer")
        target = inventory()
        target["system_types_by_category"]["Walls"]["wall_types"] = [{
            "element_id": "20", "name": "Basic - 200 mm", "wall_kind": "Basic",
            "width_internal_feet": 200.0 / 304.8, "function": "Interior"}]
        mappings = {item_id: {"level_name": "L1", "level_id": "1", "type_name": "Basic - 200 mm"}}
        plan = self.planner.create(review_id, target, mappings)
        planned = plan["items"][0]
        self.assertEqual(plan["source_kind"], "explicit_geometry")
        self.assertIsNone(plan["source_drawing"])
        self.assertIsNone(planned["source_page"])
        self.assertEqual(planned["geometry_source"], "explicit_user_geometry")
        self.assertEqual(planned["revit_type_mapping"]["type_id"], "20")
        self.assertAlmostEqual(planned["geometry"]["dimensions"]["length_internal_feet"], 4000 / 304.8)
        self.assertFalse(plan["validation"]["errors"])

    def test_explicit_wall_plan_rejects_geometry_changed_after_approval(self):
        created = self.manager.create_explicit_wall_review("revit_internal", "mm",
            [0, 0, 0], [4000, 0, 0], 4000, "User specified internal origin")
        review_id, item_id = created["session"]["session_id"], created["item"]["item_id"]
        self.manager.approve_explicit_geometry(review_id, item_id,
            created["required_geometry_confirmation"], "reviewer")
        self.repo.mutate_item(review_id, item_id, lambda item: (
            item["current_geometry"]["explicit_user_geometry"]["end"].__setitem__(0, 4500) or True, None),
            {"action": "synthetic_geometry_change"})
        plan = self.planner.create(review_id, inventory(),
            {item_id: {"level_name": "L1", "level_id": "1", "type_name": "Basic - 200 mm"}})
        self.assertEqual(plan["items"], [])
        self.assertTrue(any(issue["code"] == "explicit_geometry_approval_stale" for issue in plan["validation"]["errors"]))

    def test_door_and_window_symbol_mapping_and_explicit_dimensions(self):
        wall = self.approve(self.by_recognition["Wall"])
        wall_mapping = {"level_name": "L1", "level_id": "1", "type_name": "Basic - 200 mm"}
        families = []
        mappings = {wall["item_id"]: wall_mapping}
        for recognition_type, category, family_name, type_name, symbol_id in (
                ("Door or window opening", "Doors", "Door Family", "D-900", "door-symbol"),):
            opening = self.by_recognition[recognition_type]
            self.manager.update(self.review_id, opening["item_id"], properties={"category": category})
            self.approve(self.manager.get_item(self.review_id, opening["item_id"]))
            families.append({"category": category, "family_name": family_name,
                             "type_name": type_name, "symbol_id": symbol_id})
            mappings[opening["item_id"]] = {"level_name": "L1", "family_name": family_name,
                "type_name": type_name, "symbol_id": symbol_id, "host_review_item_id": wall["item_id"],
                "dimensions": {"width": {"value": 900, "unit": "mm", "unit_explicit": True},
                               "height": {"value": 2100, "unit": "mm", "unit_explicit": True}}}
        target = inventory()
        target["family_types"] = families
        plan = self.planner.create(self.review_id, target, mappings)
        door = next(item for item in plan["items"] if item["planned_category"] == "Doors")
        self.assertAlmostEqual(door["geometry"]["dimensions"]["width_internal_feet"], 900 / 304.8)
        self.assertEqual(door["revit_type_mapping"]["symbol_id"], "door-symbol")
        self.assertEqual(door["dependencies"][-1]["status"], "mapped")
        self.assertNotIn("host_dependency_invalid", {issue["code"] for issue in door["validation"]["errors"]})

    def test_window_symbol_is_mapped_by_exact_symbol_id(self):
        recognition = source_fixture()
        recognition["pages"][0]["elements"][1]["proposed_category"] = "Window"
        created = self.manager.create(recognition, [calibration()])
        review_id = created["session"]["session_id"]
        items = self.manager.list_items(review_id, limit=20)["items"]
        window = next(item for item in items if item["category"] == "Windows")
        window = self.manager.approve(review_id, window["item_id"], "tester", "explicit")
        target = inventory()
        target["family_types"] = [{"category": "Windows", "family_name": "Window Family",
                                   "type_name": "W-1200", "symbol_id": "window-symbol"}]
        mapping = {window["item_id"]: {"level_name": "L1", "family_name": "Window Family",
            "type_name": "W-1200", "symbol_id": "window-symbol",
            "dimensions": {"width": {"value": 1200, "unit": "mm", "unit_explicit": True},
                           "height": {"value": 1500, "unit": "mm", "unit_explicit": True}}}}
        plan = GenerationPlanner(self.manager, self.plans).create(review_id, target, mapping)
        planned = plan["items"][0]
        self.assertEqual(planned["planned_category"], "Windows")
        self.assertEqual(planned["revit_type_mapping"]["symbol_id"], "window-symbol")

    def test_unconfirmed_coordinate_map_and_missing_type_do_not_guess(self):
        wall = self.approve(self.by_recognition["Wall"])
        bad = {wall["item_id"]: {"level_name": "L1", "level_id": "1", "type_name": "arbitrary"}}
        plan = self.planner.create(self.review_id, inventory(), bad)
        self.assertTrue(any(issue["code"] == "revit_type_unavailable" for issue in plan["validation"]["errors"]))
        self.assertIsNone(plan["items"][0]["geometry"]["revit_geometry_internal_feet"])

    def test_revalidation_detects_approval_revocation_and_calibration_change(self):
        wall = self.approve(self.by_recognition["Wall"])
        plan = self.planner.create(self.review_id, inventory(), {
            wall["item_id"]: {"level_name": "L1", "level_id": "1", "type_name": "Basic - 200 mm"}})
        self.manager.update(self.review_id, wall["item_id"], status="pending", reason="review again")
        checked = self.planner.validate(plan["plan_id"], inventory())
        codes = {issue["code"] for issue in checked["items"][0]["validation"]["errors"]}
        self.assertIn("approval_missing", codes)

    def test_read_inventory_uses_only_existing_read_routes_and_identity_checks(self):
        client = FakeClient()
        result = read_generation_inventory(client)
        self.assertEqual(result["status"], "success")
        self.assertEqual({method for method, _ in client.calls}, {"GET", "POST"})
        self.assertIn(("GET", "/schema/context"), client.calls)
        self.assertTrue(all(not route.endswith(("/create", "/update", "/delete")) for _, route in client.calls))

    def test_read_inventory_uses_identity_checked_level_catalog_ids_when_complete(self):
        client = FakeClient()
        client.get_level_catalog = lambda **kwargs: {
            "status": "success", "_lora_document_identity": "doc",
            "levels": [{"element_id": "10", "level_name": "L1",
                        "element_id_status": "inspected", "name_status": "inspected",
                        "elevation_internal_feet": 0.0, "elevation_mm": 0.0,
                        "elevation_status": "inspected"}],
            "total_levels": 1, "has_more": False, "truncated": False,
            "inventory_complete": True, "total_failures": 0}
        result = read_generation_inventory(client)
        self.assertTrue(result["level_ids_available"])
        self.assertEqual(result["level_catalog_status"], "complete")
        self.assertEqual(result["level_names"], ["L1"])
        self.assertEqual(result["levels"][0]["level_id"], "10")

    def test_read_inventory_rejects_level_catalog_document_identity_mismatch(self):
        client = FakeClient()
        client.get_level_catalog = lambda **kwargs: {
            "status": "success", "_lora_document_identity": "other",
            "levels": [], "total_levels": 0, "has_more": False,
            "truncated": False, "inventory_complete": True}
        with self.assertRaises(RevitMCPError) as error:
            read_generation_inventory(client)
        self.assertEqual(getattr(error.exception, "code", None), "document_changed")

    def test_read_inventory_collects_all_level_and_failure_pages_independently(self):
        client = FakeClient()
        calls = []
        levels = [{"element_id": str(index), "level_name": "L{}".format(index),
                   "element_id_status": "inspected", "name_status": "inspected",
                   "elevation_status": "inspected", "elevation_internal_feet": float(index),
                   "elevation_mm": float(index) * 304.8}
                  for index in range(101)]
        failures = [{"failure_index": index, "field": "level_name"} for index in range(101)]

        def catalog(offset=0, limit=100, failure_offset=0, failure_limit=50):
            calls.append((offset, limit, failure_offset, failure_limit))
            if offset == 0:
                page_levels = levels[:100]
                has_more = True
            elif offset == 100:
                page_levels = levels[100:]
                has_more = False
            else:
                page_levels = []
                has_more = False
            return {"status": "success", "_lora_document_identity": "doc",
                    "levels": page_levels, "total_levels": 101, "has_more": has_more,
                    "truncated": False, "inventory_complete": False,
                    "total_failures": 101, "failures": failures[failure_offset:failure_offset + failure_limit],
                    "failure_has_more": failure_offset + failure_limit < 101}

        client.get_level_catalog = catalog
        result = read_generation_inventory(client)
        self.assertEqual([call[0] for call in calls[:2]], [0, 100])
        self.assertTrue(any(call[2] == 100 for call in calls))
        self.assertEqual(len(result["levels"]), 101)
        self.assertTrue(result["level_ids_available"])
        self.assertEqual(result["level_catalog"]["total_levels"], 101)
        self.assertEqual(len(result["level_catalog"]["failures"]), 101)

    def test_level_catalog_id_and_name_must_match_planned_wall_mapping(self):
        wall = self.approve(self.by_recognition["Wall"])
        target = inventory()
        target.update({"levels": [{"level_name": "L1", "level_id": "10",
                                   "elevation_internal_feet": 0.0}],
                       "level_names": ["L1"], "level_ids_available": True})
        plan = self.planner.create(self.review_id, target, {
            wall["item_id"]: {"level_name": "L1", "level_id": "11",
                              "type_name": "Basic - 200 mm"}})
        item = plan["items"][0]
        codes = {issue["code"] for issue in item["validation"]["errors"]}
        self.assertIn("level_id_mapping_mismatch", codes)
        self.assertNotIn("revit_level_mapping", item)

        correct = self.planner.create(self.review_id, target, {
            wall["item_id"]: {"level_name": "L1", "level_id": "10",
                              "type_name": "Basic - 200 mm"}})
        self.assertEqual(correct["items"][0]["revit_level_mapping"]["verification"],
                         "matched_by_level_catalog_id_and_name")

    def test_duplicate_wall_geometry_and_reversed_line_are_detected(self):
        first = {"planned_category": "Walls", "review_item_id": "a", "geometry": {
            "local_geometry_internal_feet": {"centerline_internal_feet": [[0, 0], [2, 0]]}}}
        second = {"planned_category": "Walls", "review_item_id": "b", "geometry": {
            "local_geometry_internal_feet": {"centerline_internal_feet": [[2, 0], [0, 0]]}}}
        self.assertEqual(len(_duplicate_pairs([first, second])), 1)

    def test_dependency_order_is_recorded(self):
        wall = self.approve(self.by_recognition["Wall"])
        plan = self.planner.create(self.review_id, inventory(), {
            wall["item_id"]: {"level_name": "L1", "level_id": "1", "type_name": "Basic - 200 mm"}})
        self.assertEqual(plan["dependency_order"], [plan["items"][0]["plan_item_id"]])

    def test_document_identity_validation_ignores_observation_time_but_checks_session(self):
        first = {"document_title": "Sample", "document_path": "fixture://Sample.rvt",
                 "document_type": "project", "revit_version": "2024", "revit_process_id": 10,
                 "document_identifier": "doc-A", "connection_endpoint": "http://127.0.0.1:48884/revit-mcp-v1",
                 "process_session_id": "proc-A", "application_session_id": "app-A",
                 "document_session_id": "doc-A", "document_lifecycle_generation": 1,
                 "active_document_switch_counter": 0, "identity_provider_version": 1,
                 "identity_provider_status": "ready", "observed_at": "first"}
        self.assertTrue(_same_document_identity(first, dict(first, observed_at="later")))
        self.assertFalse(_same_document_identity(first, dict(first, document_session_id="doc-B")))


if __name__ == "__main__":
    unittest.main()
