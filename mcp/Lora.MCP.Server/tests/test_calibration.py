import sys
import unittest
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from lora_calibration.engine import CalibrationEngine
from lora_calibration.errors import CalibrationError
from lora_drawing.coordinate_system import apply_affine, invert_affine_point


def fixture(page_count=1):
    pages, recognized = [], []
    for number in range(1, page_count + 1):
        entities = [
            {"id": "h{}".format(number), "kind": "line", "start": [0, 0], "end": [100, 0],
             "bbox": [0, 0, 100, 0]},
            {"id": "h2-{}".format(number), "kind": "line", "start": [0, 2], "end": [100, 2],
             "bbox": [0, 2, 100, 2]},
            {"id": "v{}".format(number), "kind": "line", "start": [100, 0], "end": [100, 100],
             "bbox": [100, 0, 100, 100]},
            {"id": "room{}".format(number), "kind": "polyline", "points": [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]],
             "bbox": [0, 0, 100, 100], "closed": True},
            {"id": "noise{}".format(number), "kind": "line", "start": [150, 40], "end": [177, 63],
             "bbox": [150, 40, 177, 63]},
        ]
        pages.append({"page_number": number, "coordinate_system": {"units": "PDF points"},
                      "geometry_extraction": {"entities": entities},
                      "transformation_history": [{"operation": "pdf_page_rotation", "degrees_clockwise": 90 if number == 2 else 0}]})
        recognized.append({"page_number": number,
            "transformation_history": [],
            "elements": [{"recognized_element_id": "wall{}".format(number), "proposed_category": "Wall",
                          "source_geometry_references": ["h{}".format(number), "h2-{}".format(number)],
                          "bounding_box": [0, 0, 100, 2], "position": [50, 1],
                          "dimensions": {"centerline": [[0, 1], [100, 1]], "wall_thickness_candidate": {"value": 2}},
                          "review_status": "requires_review", "warnings": []},
                         {"recognized_element_id": "room-rec{}".format(number), "proposed_category": "Room boundary",
                          "source_geometry_references": ["room{}".format(number)],
                          "bounding_box": [0, 0, 100, 100], "position": [50, 50],
                          "dimensions": {"area_source_units_squared": 10000},
                          "review_status": "candidate", "warnings": []}]})
    drawing = {"processing_id": "proc", "file_name": "synthetic.pdf", "file_type": "pdf", "pages": pages}
    recognition = {"processing_id": "proc", "recognition_id": "rec", "pages": recognized}
    return drawing, recognition


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = CalibrationEngine()
        self.drawing, self.recognition = fixture(2)

    def reference(self, ref_id, start, end, value, unit="mm", axis="x", confirmed=True, source="user_confirmed"):
        return {"reference_id": ref_id, "source_start": start, "source_end": end,
                "value": value, "unit": unit, "axis": axis,
                "user_confirmed": confirmed, "evidence_source": source}

    def test_explicit_scale_origin_rotation_and_unit_conversion(self):
        result = self.engine.calibrate(self.drawing, self.recognition, 1,
            explicit_scale_mm_per_source_unit=25.4, origin=[10, 20], rotation_degrees=90, target_unit="in")
        matrix = result["transformation_matrix_3x3"]
        self.assertAlmostEqual(result["scale_factors_target_units_per_source_unit"]["x"], 1.0)
        self.assertEqual(apply_affine([10, 20], matrix), [0.0, 0.0])
        point = apply_affine([11, 20], matrix)
        self.assertAlmostEqual(point[0], 0.0, places=8)
        self.assertAlmostEqual(point[1], 1.0, places=8)
        self.assertTrue(result["calibration_complete"])
        self.assertEqual(result["spatial_geometry_count"], 2)
        self.assertEqual(result["spatial_geometry"][0]["coordinate_units"], "in")
        self.assertAlmostEqual(result["spatial_geometry"][0]["dimensions"]["wall_thickness"]["value"], 2)
        self.assertAlmostEqual(result["spatial_geometry"][1]["dimensions"]["area"]["value"], 10000)

    def test_one_and_multiple_verified_references_determine_scale(self):
        one = self.reference("d1", [0, 0], [100, 0], 1000)
        result = self.engine.calibrate(self.drawing, self.recognition, 1, calibration_references=[one])
        self.assertEqual(result["calibration_method"], "median_verified_dimension_references")
        self.assertEqual(result["scale_factors_mm_per_source_unit"]["uniform"], 10)
        second = self.reference("d2", [100, 0], [100, 100], 1000, axis="y")
        multiple = self.engine.calibrate(self.drawing, self.recognition, 1,
                                         calibration_references=[one, second])
        self.assertTrue(multiple["reference_consistency"]["consistent"])
        self.assertEqual(multiple["reference_consistency"]["reference_count"], 2)

    def test_unconfirmed_ocr_is_reported_but_does_not_calibrate(self):
        ref = self.reference("ocr1", [0, 0], [100, 0], 1000, confirmed=False, source="ocr")
        result = self.engine.calibrate(self.drawing, self.recognition, 1, calibration_references=[ref])
        self.assertEqual(result["calibration_status"], "uncalibrated")
        self.assertFalse(result["calibration_references"][0]["used_for_scale"])
        self.assertIn("were not used", result["warnings"][0])
        self.assertIsNone(result["transformation_matrix_3x3"])

    def test_inconsistent_references_flag_conflict_and_preserve_transform(self):
        first = self.reference("a", [0, 0], [100, 0], 1000, axis="x")
        second = self.reference("b", [0, 0], [0, 100], 2000, axis="y")
        result = self.engine.calibrate(self.drawing, self.recognition, 1,
                                       calibration_references=[first, second])
        self.assertEqual(result["calibration_status"], "conflicted")
        self.assertFalse(result["calibration_complete"])
        self.assertIsNotNone(result["transformation_matrix_3x3"])
        self.assertTrue(any("inconsistent" in warning for warning in result["warnings"]))

    def test_independent_xy_scale_requires_both_axes(self):
        refs = [self.reference("x", [0, 0], [100, 0], 1000, axis="x"),
                self.reference("y", [0, 0], [0, 100], 2000, axis="y")]
        result = self.engine.calibrate(self.drawing, self.recognition, 1,
                                       calibration_references=refs, independent_xy=True)
        self.assertEqual(result["scale_factors_mm_per_source_unit"]["x"], 10)
        self.assertEqual(result["scale_factors_mm_per_source_unit"]["y"], 20)
        self.assertNotEqual(result["scale_factors_target_units_per_source_unit"]["uniform"], 10)
        with self.assertRaises(CalibrationError):
            self.engine.calibrate(self.drawing, self.recognition, 1,
                                  calibration_references=refs[:1], independent_xy=True)

    def test_rotated_non_square_transform_and_round_trip(self):
        result = self.engine.calibrate(self.drawing, self.recognition, 1,
            independent_xy=True,
            calibration_references=[self.reference("x", [0, 0], [100, 0], 2000, axis="x"),
                                    self.reference("y", [0, 0], [0, 100], 3000, axis="y")],
            rotation_degrees=30)
        matrix = result["transformation_matrix_3x3"]
        mapped = apply_affine([15, 25], matrix)
        # The analytical expected point includes scale (20, 30), source y inversion, then 30° rotation.
        import math
        self.assertAlmostEqual(mapped[0], 20 * 15 * math.cos(math.pi / 6) + 30 * 25 * math.sin(math.pi / 6))
        self.assertAlmostEqual(mapped[1], 20 * 15 * math.sin(math.pi / 6) - 30 * 25 * math.cos(math.pi / 6))
        restored = invert_affine_point(mapped, matrix)
        self.assertAlmostEqual(restored[0], 15)
        self.assertAlmostEqual(restored[1], 25)

    def test_page_specific_scales_and_shared_reference_alignment(self):
        first = self.engine.calibrate(self.drawing, self.recognition, 1, explicit_scale_mm_per_source_unit=10)
        second = self.engine.calibrate(self.drawing, self.recognition, 2, explicit_scale_mm_per_source_unit=20,
            alignment_references=[{"source_point": [0, 0], "common_point": [500, 200], "user_confirmed": True},
                                  {"source_point": [100, 0], "common_point": [2500, 200], "user_confirmed": True}])
        self.assertEqual(first["page_alignment"]["status"], "not_aligned")
        self.assertEqual(second["page_alignment"]["status"], "aligned")
        self.assertEqual(second["transformation_history"][0]["degrees_clockwise"], 90)
        self.assertEqual(second["page_alignment"]["reference_count"], 2)
        self.assertAlmostEqual(apply_affine([0, 0], second["transformation_matrix_3x3"])[0], 500)

    def test_missing_calibration_and_document_identity(self):
        missing = self.engine.calibrate(self.drawing, self.recognition, 1)
        self.assertEqual(missing["calibration_status"], "uncalibrated")
        self.assertEqual(missing["spatial_geometry"], [])
        bad_recognition = dict(self.recognition, processing_id="other")
        with self.assertRaises(CalibrationError):
            self.engine.calibrate(self.drawing, bad_recognition, 1, explicit_scale_mm_per_source_unit=1)

    def test_reference_validation_matches_extracted_segment_and_flags_unconfirmed(self):
        refs = [self.reference("good", [0, 0], [100, 0], 1000),
                self.reference("bad", [5, 5], [10, 10], 50, confirmed=False, source="ocr")]
        result = self.engine.validate_references(self.drawing, self.recognition, 1, refs)
        self.assertEqual(result["accepted_count"], 1)
        self.assertEqual(result["references"][0]["status"], "valid")
        self.assertIn(result["references"][1]["status"], ("unmatched_geometry", "unconfirmed_ocr"))

    def test_ambiguous_text_geometry_binding_is_reported(self):
        self.recognition["pages"][0]["elements"].append({
            "recognized_element_id": "dim-rec", "proposed_category": "Dimension", "text_id": "dim-text",
            "dimensions": {"text_source": "ocr", "association_status": "ambiguous"}})
        ref = self.reference("ambig", [0, 0], [100, 0], 1000, confirmed=False, source="ocr")
        ref["dimension_text_id"] = "dim-text"
        result = self.engine.validate_references(self.drawing, self.recognition, 1, [ref])
        self.assertEqual(result["references"][0]["status"], "ambiguous_dimension_association")
        self.assertTrue(any("ambiguous geometry association" in message for message in result["references"][0]["messages"]))

    def test_mcp_calibration_tools_use_cached_read_only_inputs(self):
        from unittest.mock import patch
        from lora_mcp import server
        drawing, recognition = fixture(1)
        recognition["recognition_id"] = server.recognition_engine.store.put(recognition)
        with patch.object(server.drawing_pipeline, "get_summary", return_value=drawing):
            calibrated = server.lora_calibrate_drawing("proc", recognition["recognition_id"],
                explicit_scale_mm_per_source_unit=10)
            stored = server.lora_get_calibration_result(calibrated["calibration_id"])
            geometry = server.lora_get_reconstructed_spatial_geometry(calibrated["calibration_id"], limit=1)
            validated = server.lora_validate_calibration_references("proc", recognition["recognition_id"],
                calibration_references=[self.reference("v", [0, 0], [100, 0], 1000)])
        self.assertEqual(calibrated["status"], "success")
        self.assertEqual(stored["status"], "success")
        self.assertTrue(geometry["truncated"])
        self.assertEqual(validated["accepted_count"], 1)


if __name__ == "__main__":
    unittest.main()
