import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from lora_review import DrawingReviewManager, ReviewRepository
from lora_review.errors import ReviewError


def source_fixture():
    elements = [
        {"recognized_element_id": "wall-1", "proposed_category": "Wall",
         "source_geometry_references": ["line-a", "line-b"], "bounding_box": [0, 0, 100, 10],
         "position": [50, 5], "dimensions": {"wall_thickness_candidate": {"value": 10, "unit": "pixels"}},
         "recognition_method": "parallel_lines", "evidence_score": 0.82,
         "evidence": ["paired parallel lines"], "warnings": ["candidate needs review"]},
        {"recognized_element_id": "door-1", "proposed_category": "Door or window opening",
         "source_geometry_references": ["line-c"], "bounding_box": [100, 0, 130, 10],
         "position": [115, 5], "dimensions": {}, "recognition_method": "opening_gap",
         "evidence_score": 0.45, "evidence": ["collinear gap"], "warnings": ["ambiguous opening"]},
        {"recognized_element_id": "room-1", "proposed_category": "Room boundary",
         "source_geometry_references": ["poly-1"], "bounding_box": [0, 0, 100, 100],
         "position": [50, 50], "dimensions": {"area_source_units_squared": 10000},
         "recognition_method": "closed_polygon", "evidence_score": 0.9,
         "evidence": ["closed boundary"], "warnings": []},
    ]
    return {"recognition_id": "rec-1", "processing_id": "proc-1", "source_file": "plan.pdf",
            "pages": [{"page_number": 1, "elements": elements}]}


def calibration(matrix=None, complete=True, calibration_id="cal-1"):
    matrix = matrix or [[2.0, 0.0, 0.0], [0.0, -2.0, 0.0], [0.0, 0.0, 1.0]]
    return {"calibration_id": calibration_id, "processing_id": "proc-1", "recognition_id": "rec-1",
            "page_number": 1, "calibration_complete": complete,
            "transformation_matrix_3x3": matrix,
            "scale_factors_mm_per_source_unit": {"x": 2.0, "y": 2.0, "uniform": 2.0},
            "target_units": "mm", "calibration_references": [],
            "spatial_geometry": [
                {"recognized_element_id": "wall-1", "geometry": [{"source_entity_id": "line-a", "kind": "line",
                  "start": [0, 0], "end": [100, 0]}, {"source_entity_id": "line-b", "kind": "line",
                  "start": [0, 10], "end": [100, 10]}], "bounding_box": [0, -20, 200, 0],
                  "position": [100, -10], "dimensions": {"wall_thickness": {"value": 20}}, "coordinate_units": "mm"},
                {"recognized_element_id": "door-1", "geometry": [], "bounding_box": [200, -20, 260, 0],
                  "position": [230, -10], "dimensions": {}, "coordinate_units": "mm"},
                {"recognized_element_id": "room-1", "geometry": [{"source_entity_id": "poly-1", "kind": "polyline",
                  "points": [[0, 0], [200, 0], [200, -200], [0, -200], [0, 0]]}],
                  "bounding_box": [0, -200, 200, 0], "position": [100, -100],
                  "dimensions": {"area": {"value": 40000}}, "coordinate_units": "mm"},
            ]}


class DrawingReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "reviews.sqlite3"
        self.repository = ReviewRepository(self.path)
        self.manager = DrawingReviewManager(self.repository)
        self.created = self.manager.create(source_fixture(), [calibration()])
        self.review_id = self.created["session"]["session_id"]
        self.items = self.manager.list_items(self.review_id, limit=20)["items"]
        self.by_type = {item["recognition_type"]: item for item in self.items}

    def tearDown(self):
        self.temp.cleanup()

    def test_create_persists_pending_items_and_visual_render_references(self):
        self.assertEqual(self.created["item_count"], 3)
        self.assertTrue(all(item["status"] == "pending" for item in self.items))
        self.assertEqual(self.by_type["Wall"]["calibration_state"], "current")
        review = self.manager.session(self.review_id)
        self.assertEqual(review["visual_review"]["render_tool"], "lora_get_drawing_render")
        self.assertEqual(review["pages"], [1])
        self.assertEqual(self.created["summary"]["counts_by_status"]["pending"], 3)

    def test_explicit_wall_geometry_requires_exact_persistent_confirmation(self):
        created = self.manager.create_explicit_wall_review("revit_internal", "mm",
            [1000, 2000, 0], [5000, 2000, 0], 4000,
            "User-confirmed Revit internal origin", reviewer="tester")
        session_id = created["session"]["session_id"]
        item_id = created["item"]["item_id"]
        self.assertEqual(created["session"]["source_kind"], "explicit_geometry")
        self.assertIsNone(created["item"]["page_number"])
        self.assertEqual(created["summary"]["total_items"], 1)
        self.assertIsNone(self.manager.list_items(session_id)["items"][0]["page_number"])
        self.assertEqual(self.manager.session(session_id)["pages"], [])
        with self.assertRaises(ReviewError):
            self.manager.approve_explicit_geometry(session_id, item_id, "CONFIRM IT")
        approved = self.manager.approve_explicit_geometry(session_id, item_id,
            created["required_geometry_confirmation"], reviewer="tester")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["explicit_geometry_status"], "confirmed")
        history = self.manager.history(session_id, item_id)["items"]
        self.assertTrue(any(value["event"].get("action") == "explicit_geometry_approved" for value in history))

    def test_explicit_geometry_rejects_unmatched_length_and_missing_origin(self):
        with self.assertRaises(ReviewError):
            self.manager.create_explicit_wall_review("user_local", "mm", [0, 0, 0], [4000, 0, 0],
                3999, "origin")
        with self.assertRaises(ReviewError):
            self.manager.create_explicit_wall_review("user_local", "mm", [0, 0, 0], [4000, 0, 0],
                4000, "")

    def test_filtering_pagination_and_item_history(self):
        first = self.manager.list_items(self.review_id, offset=0, limit=1)
        second = self.manager.list_items(self.review_id, offset=1, limit=1)
        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["items"]), 1)
        self.assertTrue(first["truncated"])
        self.assertNotEqual(first["items"][0]["item_id"], second["items"][0]["item_id"])
        doors = self.manager.list_items(self.review_id, category="Openings")
        self.assertEqual(doors["total"], 1)
        self.assertGreaterEqual(self.manager.history(self.review_id)["total"], 1)

    def test_property_and_geometry_corrections_preserve_original(self):
        wall = self.by_type["Wall"]
        changed = self.manager.update(self.review_id, wall["item_id"],
            properties={"mark": "W-01", "comments": "Checked"},
            geometry={"coordinate_space": "source", "geometry_type": "polyline",
                      "coordinates": [[0, 0], [100, 0], [100, 5]]}, reviewer="reviewer-a")
        self.assertEqual(changed["current_properties"]["mark"], "W-01")
        self.assertEqual(changed["original_properties"]["mark"], "")
        self.assertNotIn("reviewer_correction", changed["original_geometry"]["source"])
        self.assertEqual(changed["current_geometry"]["source"]["reviewer_correction"]["coordinates"][-1], [100.0, 5.0])
        self.assertEqual(changed["current_geometry"]["calibrated"]["reviewer_correction"]["coordinates"][-1], [200.0, -10.0])
        history = self.manager.history(self.review_id, wall["item_id"])
        self.assertEqual(history["total"], 1)
        changed_again = self.manager.update(self.review_id, wall["item_id"], geometry={
            "coordinate_space": "calibrated", "geometry_type": "bbox", "coordinates": [20, -20, 60, 0]})
        corrected = changed_again["current_geometry"]
        self.assertEqual(corrected["calibrated"]["reviewer_correction"]["coordinates"], [20.0, -20.0, 60.0, 0.0])
        self.assertEqual(corrected["source"]["reviewer_correction"]["coordinates"], [10.0, 0.0, 30.0, 10.0])
        self.assertEqual(changed_again["original_geometry"]["source"]["bounding_box"], [0, 0, 100, 10])

    def test_approval_duplicate_is_idempotent_and_rejection_requires_reason(self):
        item_id = self.by_type["Wall"]["item_id"]
        approved = self.manager.approve(self.review_id, item_id, reviewer="reviewer-a")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["transition_result"], "approved")
        repeated = self.manager.approve(self.review_id, item_id)
        self.assertEqual(repeated["transition_result"], "already_approved")
        self.assertEqual(self.manager.history(self.review_id, item_id)["total"], 1)
        door_id = self.by_type["Door or window opening"]["item_id"]
        with self.assertRaises(ReviewError):
            self.manager.reject(self.review_id, door_id, "")
        rejected = self.manager.reject(self.review_id, door_id, "Not an opening")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.manager.summary(self.review_id)["counts_by_status"]["rejected"], 1)

    def test_rejected_and_unresolved_excluded_until_returned_to_pending(self):
        door_id = self.by_type["Door or window opening"]["item_id"]
        self.manager.reject(self.review_id, door_id, "ambiguous")
        with self.assertRaises(ReviewError):
            self.manager.approve(self.review_id, door_id)
        self.manager.update(self.review_id, door_id, status="pending", reason="Recheck requested")
        self.manager.approve(self.review_id, door_id)
        room_id = self.by_type["Room boundary"]["item_id"]
        self.manager.update(self.review_id, room_id, status="unresolved", reason="No room name")
        summary = self.manager.summary(self.review_id)
        self.assertEqual(summary["approved_collection_count"], 1)
        self.assertEqual(summary["counts_by_status"]["unresolved"], 1)

    def test_invalid_properties_geometry_and_status_are_rejected(self):
        wall_id = self.by_type["Wall"]["item_id"]
        for props in ({"confidence": 1}, {"category": "Unknown"}, {"mark": 4}, {"dimensions": {"x": float("nan")}}):
            with self.assertRaises(ReviewError):
                self.manager.update(self.review_id, wall_id, properties=props)
        with self.assertRaises(ReviewError):
            self.manager.update(self.review_id, wall_id,
                geometry={"coordinate_space": "source", "geometry_type": "polygon", "coordinates": [[0, 0], [1, 0], [1, 1]]})
        with self.assertRaises(ReviewError):
            self.manager.update(self.review_id, wall_id, status="approved")

    def test_missing_calibration_is_reported_and_geometry_correction_is_source_only(self):
        source_only = self.manager.create(source_fixture())["session"]["session_id"]
        item = self.manager.list_items(source_only, limit=1)["items"][0]
        warning = self.manager.approve(source_only, item["item_id"])
        self.assertIn("missing or incomplete", warning["approval_warning"])
        summary = self.manager.summary(source_only)
        self.assertEqual(summary["approved_with_current_calibration_count"], 0)
        self.assertTrue(summary["calibration_warnings"])
        validation = self.manager.validate(source_only)
        self.assertTrue(any(finding["status"] == "missing_calibration" for finding in validation["findings"]))
        self.assertFalse(validation["validation_complete"])
        with self.assertRaises(ReviewError):
            self.manager.update(source_only, item["item_id"], geometry={"coordinate_space": "calibrated",
                "geometry_type": "point", "coordinates": [0, 0]})

    def test_calibration_change_flags_prior_approval_and_blocks_reapproval(self):
        wall_id = self.by_type["Wall"]["item_id"]
        self.manager.approve(self.review_id, wall_id)
        changed_cal = calibration([[3, 0, 0], [0, -3, 0], [0, 0, 1]], calibration_id="cal-2")
        result = self.manager.validate(self.review_id, [changed_cal])
        self.assertFalse(result["validation_complete"])
        self.assertEqual(result["findings"][0]["status"], "calibration_changed")
        stale = self.manager.get_item(self.review_id, wall_id)
        self.assertEqual(stale["status"], "approved")
        self.assertEqual(stale["calibration_state"], "stale")
        self.assertEqual(result["summary"]["approved_with_current_calibration_count"], 0)
        with self.assertRaises(ReviewError):
            self.manager.approve(self.review_id, self.by_type["Room boundary"]["item_id"])
        repeated = self.manager.validate(self.review_id, [changed_cal])
        self.assertEqual(repeated["findings"][-1]["status"], "review_items_stale")

    def test_persistence_survives_manager_reopen(self):
        item_id = self.by_type["Wall"]["item_id"]
        self.manager.update(self.review_id, item_id, properties={"mark": "W-99"})
        recovered = DrawingReviewManager(ReviewRepository(self.path))
        self.assertEqual(recovered.get_item(self.review_id, item_id)["current_properties"]["mark"], "W-99")

    def test_corrupt_database_is_preserved_and_reported(self):
        bad_path = Path(self.temp.name) / "bad.sqlite3"
        original = b"not a sqlite database"
        bad_path.write_bytes(original)
        manager = DrawingReviewManager(ReviewRepository(bad_path))
        with self.assertRaises(ReviewError) as error:
            manager.create(source_fixture())
        self.assertIn(error.exception.code, ("review_store_corrupt", "review_store_unavailable"))
        self.assertEqual(bad_path.read_bytes(), original)

    def test_schema_version_one_migrates_to_two(self):
        old_path = Path(self.temp.name) / "v1.sqlite3"
        db = sqlite3.connect(str(old_path))
        db.execute("CREATE TABLE review_sessions (session_id TEXT PRIMARY KEY, data_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE review_items (session_id TEXT NOT NULL, item_id TEXT NOT NULL, page_number INTEGER NOT NULL, category TEXT NOT NULL, status TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(session_id,item_id))")
        db.execute("CREATE TABLE review_history (session_id TEXT NOT NULL, sequence INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, event_json TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.execute("PRAGMA user_version=1")
        db.commit()
        db.close()
        ReviewRepository(old_path).ensure_schema()
        db = sqlite3.connect(str(old_path))
        version = db.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in db.execute("PRAGMA table_info(review_items)")}
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.close()
        self.assertEqual(version, 4)
        self.assertIn("calibration_state", columns)
        self.assertIn("generation_plans", tables)
        self.assertIn("generation_previews", tables)


if __name__ == "__main__":
    unittest.main()
