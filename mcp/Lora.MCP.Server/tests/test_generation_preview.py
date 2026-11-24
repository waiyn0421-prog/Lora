import copy
import sys
import tempfile
import unittest
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from lora_generation import GenerationPlanRepository, GenerationPlanner, GenerationPreviewEngine, GenerationPreviewRepository
from lora_review import DrawingReviewManager, ReviewRepository
from test_drawing_review import calibration, source_fixture
from test_generation_planning import inventory


class GenerationPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.review_repository = ReviewRepository(Path(self.temp.name) / "reviews.sqlite3")
        self.review_manager = DrawingReviewManager(self.review_repository)
        recognition = source_fixture()
        recognition["pages"][0]["elements"] = [recognition["pages"][0]["elements"][0]]
        calibrated = calibration()
        calibrated["spatial_geometry"] = [calibrated["spatial_geometry"][0]]
        created = self.review_manager.create(recognition, [calibrated])
        self.review_id = created["session"]["session_id"]
        self.wall = self.review_manager.list_items(self.review_id, limit=10)["items"][0]
        self.wall = self.review_manager.approve(self.review_id, self.wall["item_id"], "tester", "approved")
        self.plan_repository = GenerationPlanRepository(self.review_repository)
        self.planner = GenerationPlanner(self.review_manager, self.plan_repository)
        self.preview_repository = GenerationPreviewRepository(self.review_repository)
        self.preview_engine = GenerationPreviewEngine(self.planner, self.preview_repository)
        self.target = inventory()
        self.mapping = {self.wall["item_id"]: {"level_name": "L1", "type_name": "Basic - 200 mm"}}

    def tearDown(self):
        self.temp.cleanup()

    def make_plan(self, coordinate=True, parameter_assignments=None):
        if parameter_assignments:
            self.mapping[self.wall["item_id"]]["parameter_assignments"] = parameter_assignments
        coordinate_mapping = ({"user_confirmed": True, "origin_internal_feet": [100, 200, 0],
                               "rotation_degrees": 90} if coordinate else None)
        return self.planner.create(self.review_id, self.target, self.mapping, coordinate_mapping)

    def test_valid_live_plan_preview_contains_2d_geometry_and_never_ready_for_execution(self):
        plan = self.make_plan(coordinate=True)
        live = copy.deepcopy(self.target)
        live["inventory_source"] = "live_revitmcp"
        live["verified_at"] = "2026-11-20T01:02:03+00:00"
        preview = self.preview_engine.create(plan["plan_id"], live)
        self.assertEqual(preview["inventory_verification"], "live_verified")
        self.assertFalse(preview["ready_for_execution"])
        self.assertFalse(preview["visual_preview"]["native_revit_3d"])
        self.assertEqual(preview["visual_preview"]["features"][0]["geometry_type"], "wall_centerline")
        self.assertEqual(preview["items"][0]["type_verification"], "verified_by_live_inventory")
        self.assertEqual(preview["items"][0]["level_verification"], "verified_by_live_inventory")

    def test_missing_coordinates_are_an_error_and_block_execution_readiness(self):
        plan = self.make_plan(coordinate=False)
        preview = self.preview_engine.create(plan["plan_id"], self.target)
        codes = {issue["code"] for issue in preview["validation"]["errors"]}
        self.assertIn("placement_coordinates_unresolved", codes)
        self.assertEqual(preview["readiness"], "blocked")
        self.assertFalse(preview["ready_for_execution"])

    def test_unavailable_inventory_is_explicit_then_reconnect_creates_verified_snapshot(self):
        plan = self.make_plan(coordinate=True)
        unavailable = {"status": "unavailable", "inventory_source": "live_revitmcp", "warnings": [],
                       "level_names": [], "family_types": [], "parameter_names": []}
        first = self.preview_engine.create(plan["plan_id"], unavailable)
        self.assertEqual(first["inventory_verification"], "not_live_verified")
        self.assertTrue(any(issue["code"] == "revitmcp_unavailable" for issue in first["validation"]["warnings"]))
        reconnected = copy.deepcopy(self.target)
        reconnected.update(inventory_source="live_revitmcp", verified_at="2026-11-20T01:05:00+00:00")
        second = self.preview_engine.validate(first["preview_id"], reconnected)
        self.assertEqual(second["revalidation_of_preview_id"], first["preview_id"])
        self.assertEqual(second["inventory_verification"], "live_verified")
        self.assertTrue(self.preview_engine.get(first["preview_id"])["stale"])

    def test_mapping_change_invalidates_old_snapshot_and_compare_reports_changes(self):
        plan = self.make_plan(coordinate=True)
        first = self.preview_engine.create(plan["plan_id"], self.target)
        self.planner.update_mapping(plan["plan_id"], self.wall["item_id"], {
            "level_name": "L1", "type_name": "Basic - 200 mm",
            "parameter_assignments": {"Comments": "reviewed preview"}}, self.target)
        self.assertTrue(self.preview_engine.get(first["preview_id"])["stale"])
        second = self.preview_engine.create(plan["plan_id"], self.target)
        comparison = self.preview_engine.compare(first["preview_id"], second["preview_id"])
        self.assertTrue(comparison["different"])
        self.assertEqual(comparison["changed_items"][0]["source_review_item_id"], self.wall["item_id"])

    def test_parameter_compatibility_is_warning_and_snapshot_recovers(self):
        plan = self.make_plan(coordinate=True, parameter_assignments={"NotAParameter": "value"})
        preview = self.preview_engine.create(plan["plan_id"], self.target)
        self.assertTrue(any(issue["code"] == "parameter_compatibility_unverified" for issue in preview["validation"]["warnings"]))
        restored = GenerationPreviewEngine(self.planner, GenerationPreviewRepository(
            ReviewRepository(Path(self.temp.name) / "reviews.sqlite3"))).get(preview["preview_id"])
        self.assertEqual(restored["preview_id"], preview["preview_id"])

    def test_unavailable_level_and_type_remain_errors(self):
        plan = self.make_plan(coordinate=True)
        bad = copy.deepcopy(self.target)
        bad["level_names"] = []
        bad["system_types_by_category"]["Walls"]["types"] = []
        preview = self.preview_engine.create(plan["plan_id"], bad)
        codes = {issue["code"] for issue in preview["validation"]["errors"]}
        self.assertIn("level_unavailable", codes)
        self.assertIn("revit_type_unavailable", codes)

    def test_invalid_opening_host_relationship_is_an_error(self):
        recognition = source_fixture()
        calibration_data = calibration()
        created = self.review_manager.create(recognition, [calibration_data])
        review_id = created["session"]["session_id"]
        items = self.review_manager.list_items(review_id, limit=10)["items"]
        by_type = {item["recognition_type"]: item for item in items}
        wall = self.review_manager.approve(review_id, by_type["Wall"]["item_id"], "tester", "approved")
        opening = by_type["Door or window opening"]
        self.review_manager.update(review_id, opening["item_id"], properties={"category": "Doors"})
        opening = self.review_manager.approve(review_id, opening["item_id"], "tester", "approved")
        plans = GenerationPlanner(self.review_manager, self.plan_repository)
        target = copy.deepcopy(self.target)
        target["family_types"] = [{"category": "Doors", "family_name": "Door Family",
                                   "type_name": "D-900", "symbol_id": "door-1"}]
        mappings = {wall["item_id"]: {"level_name": "L1", "type_name": "Basic - 200 mm"},
                    opening["item_id"]: {"level_name": "L1", "family_name": "Door Family",
                        "type_name": "D-900", "symbol_id": "door-1", "host_review_item_id": "missing-wall",
                        "dimensions": {"width": {"value": 900, "unit": "mm", "unit_explicit": True},
                                       "height": {"value": 2100, "unit": "mm", "unit_explicit": True}}}}
        plan = plans.create(review_id, target, mappings,
            {"user_confirmed": True, "origin_internal_feet": [0, 0, 0], "rotation_degrees": 0})
        preview = self.preview_engine.create(plan["plan_id"], target)
        codes = {issue["code"] for issue in preview["validation"]["errors"]}
        self.assertIn("host_dependency_invalid", codes)
        self.assertEqual(preview["readiness"], "blocked")


if __name__ == "__main__":
    unittest.main()
