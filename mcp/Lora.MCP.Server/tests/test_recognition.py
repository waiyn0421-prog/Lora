import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from lora_drawing.coordinate_system import apply_affine, invert_affine_bbox, invert_affine_point
from lora_recognition.dimension_interpreter import parse_dimension_text
from lora_recognition.engine import RecognitionEngine
from lora_recognition.errors import RecognitionError


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.engine = RecognitionEngine()

    def test_synthetic_floor_plan_returns_cautious_hypotheses(self):
        entities = [
            {"id": "wall-a", "kind": "line", "start": [100, 100], "end": [100, 600], "coordinate_units": "PDF page points"},
            {"id": "wall-b", "kind": "line", "start": [120, 100], "end": [120, 600], "coordinate_units": "PDF page points"},
            {"id": "win-a", "kind": "line", "start": [100, 700], "end": [500, 700], "coordinate_units": "PDF page points"},
            {"id": "win-b", "kind": "line", "start": [100, 720], "end": [500, 720], "coordinate_units": "PDF page points"},
            {"id": "win-t1", "kind": "line", "start": [220, 700], "end": [220, 720], "coordinate_units": "PDF page points"},
            {"id": "win-t2", "kind": "line", "start": [300, 700], "end": [300, 720], "coordinate_units": "PDF page points"},
            {"id": "room", "kind": "polyline", "points": [[600, 600], [750, 600], [750, 750], [600, 750], [600, 600]], "closed": True, "coordinate_units": "PDF page points"},
            {"id": "column", "kind": "rectangle", "bbox": [800, 800, 830, 830], "closed": True, "coordinate_units": "PDF page points"},
            {"id": "dim-a", "kind": "line", "start": [500, 100], "end": [900, 100], "coordinate_units": "PDF page points"},
            {"id": "tick-a", "kind": "line", "start": [500, 90], "end": [500, 110], "coordinate_units": "PDF page points"},
            {"id": "tick-b", "kind": "line", "start": [900, 90], "end": [900, 110], "coordinate_units": "PDF page points"},
        ]
        result = {"processing_id": "synthetic", "file_name": "plan.pdf", "file_type": "pdf",
                  "extraction_complete": True, "pages": [{"page_number": 1, "width": 1000, "height": 1000,
                  "coordinate_system": {"units": "PDF page points"}, "extraction_complete": True,
                  "geometry_extraction": {"entities": entities, "paths": [],
                      "text_annotations": [{"id": "t1", "text": "LEVEL 02 EL +6.100 m", "bbox": [20, 20, 90, 40]},
                                           {"id": "t2", "text": "ROOM 101", "bbox": [650, 650, 700, 670]},
                                           {"id": "t3", "text": "4.5 m", "bbox": [680, 80, 720, 95]}]},
                  "ocr": {"status": "not_requested"}, "warnings": []}]}
        with patch("lora_recognition.engine.verify_tesseract", return_value={"status": "executable_not_found", "verified": False}):
            output = self.engine.recognize(result)
        categories = {item["proposed_category"] for item in output["elements"]}
        self.assertIn("Wall", categories)
        self.assertIn("Window", categories)
        self.assertIn("Room boundary", categories)
        self.assertIn("Column", categories)
        self.assertIn("Level annotation", categories)
        self.assertIn("Dimension", categories)
        self.assertTrue(all(item["review_status"] in ("candidate", "requires_review") for item in output["elements"]))
        self.assertEqual(output["calibration"]["status"], "not_provided")
        self.assertTrue(any("Tesseract OCR is unavailable" in warning for warning in output["warnings"]))

    def test_dimension_units_are_never_inferred(self):
        self.assertEqual(parse_dimension_text("4500 mm")["unit"], "mm")
        imperial = parse_dimension_text("4'-6\"")
        self.assertEqual(imperial["components"], {"feet": 4, "inches": 6.0})
        bare = parse_dimension_text("4500")
        self.assertFalse(bare["explicit_unit"])
        self.assertIsNone(bare["unit"])
        self.assertIsNone(parse_dimension_text("DOOR TYPE A"))

    def test_coordinate_transform_roundtrip_and_bbox(self):
        matrix = [[0, -2, 400], [2, 0, 30], [0, 0, 1]]
        point = [10, 20]
        processed = apply_affine(point, matrix)
        self.assertEqual(invert_affine_point(processed, matrix), point)
        self.assertEqual(invert_affine_bbox([360, 50, 380, 70], matrix), [10, 10, 20, 20])

    def test_empty_text_and_ocr_unavailable_are_explicit(self):
        with patch("lora_recognition.engine.verify_tesseract", return_value={"status": "executable_not_found", "verified": False}):
            output = self.engine.recognize({"file_name": "empty.png", "file_type": "png",
                "extraction_complete": True, "geometry_entities": [],
                "image": {"width_pixels": 100, "height_pixels": 100, "coordinate_system": {"units": "pixels"}},
                "ocr": {"status": "engine_unavailable", "text_blocks": []}, "warnings": []})
        self.assertEqual(output["element_count"], 0)
        self.assertFalse(output["pages"][0]["text_extraction_complete"])
        self.assertIn("No source coordinates", output["calibration"]["warning"])

    def test_invalid_page_and_expired_result_are_errors(self):
        with self.assertRaises(RecognitionError):
            self.engine.recognize({"file_type": "pdf"}, page_number=2)
        with self.assertRaises(RecognitionError):
            self.engine.store.get("unknown")

    def test_multimodal_provider_is_not_configured_or_called(self):
        self.assertEqual(self.engine.provider.status, "not_configured")

    def test_mcp_tools_retrieve_paginated_results_without_revit_calls(self):
        from lora_mcp import server
        source = {"processing_id": "synthetic", "file_name": "plan.pdf", "file_type": "pdf",
                  "extraction_complete": True, "pages": [{"page_number": 1, "width": 100,
                  "height": 100, "coordinate_system": {"units": "PDF page points"},
                  "extraction_complete": True, "geometry_extraction": {"entities": [], "paths": [],
                  "text_annotations": [{"id": "text", "text": "LEVEL 01", "bbox": [0, 0, 10, 5]}]},
                  "ocr": {"status": "not_requested"}, "warnings": []}]}
        with patch.object(server.drawing_pipeline, "get_summary", return_value=source), \
             patch("lora_recognition.engine.verify_tesseract", return_value={"status": "executable_not_found", "verified": False}):
            recognized = server.lora_recognize_architecture("synthetic")
        self.assertEqual(recognized["status"], "success")
        recognition_id = recognized["recognition_id"]
        page = server.lora_get_recognized_elements(recognition_id, offset=0, limit=1)
        self.assertEqual(page["total"], 1)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(server.lora_analyze_recognition(recognition_id)["element_count"], 1)
        warning_report = server.lora_get_recognition_warnings(recognition_id)
        self.assertFalse(warning_report["pages"][0]["text_extraction_complete"])


if __name__ == "__main__":
    unittest.main()
