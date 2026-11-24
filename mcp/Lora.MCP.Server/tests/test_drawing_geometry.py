import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lora_drawing.candidates import detect_architectural_candidates
from lora_drawing.exceptions import DrawingError
from lora_drawing.geometry import (analyze_relationships, are_parallel, are_perpendicular,
                                   bounding_box, endpoint_proximity, group_geometry,
                                   line_intersection, line_length, line_orientation_degrees,
                                   polygon_area, polygon_is_closed)
from lora_drawing.models import ProcessingLimits
from lora_drawing.pdf_geometry import extract_pdf_geometry
from lora_drawing.processing_pipeline import DrawingProcessingPipeline


class GeometryFunctionsTests(unittest.TestCase):
    def test_line_measures_orientations_intersection_and_proximity(self):
        self.assertAlmostEqual(line_length([0, 0], [3, 4]), 5)
        self.assertAlmostEqual(line_orientation_degrees([0, 0], [10, 0]), 0)
        self.assertAlmostEqual(line_orientation_degrees([0, 0], [0, 10]), 90)
        self.assertTrue(are_parallel(1, 179, 3))
        self.assertTrue(are_perpendicular(0, 90, 1))
        self.assertEqual(line_intersection([0, 0], [10, 0], [5, -5], [5, 5]), [5.0, 0.0])
        self.assertIsNone(line_intersection([0, 0], [1, 0], [2, -1], [2, 1]))
        self.assertEqual(endpoint_proximity([0, 0], [1, 0], [1.5, 0], [2, 0], 0.6)["distance"], 0.5)

    def test_polygon_bbox_and_geometry_groups(self):
        polygon = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        self.assertTrue(polygon_is_closed(polygon))
        self.assertEqual(polygon_area(polygon), 100)
        self.assertEqual(bounding_box(polygon), [0, 0, 10, 10])
        groups = group_geometry([
            {"id": "a", "kind": "line", "start": [0, 0], "end": [10, 0]},
            {"id": "b", "kind": "line", "start": [10, 0], "end": [10, 10]},
            {"id": "c", "kind": "line", "start": [100, 100], "end": [110, 100]},
        ], 0.1)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0]["geometry_ids"]), 2)

    def test_relationships_report_parallel_perpendicular_intersection_and_limits(self):
        lines = [
            {"id": "a", "kind": "line", "start": [0, 0], "end": [100, 0]},
            {"id": "b", "kind": "line", "start": [0, 10], "end": [100, 10]},
            {"id": "c", "kind": "line", "start": [50, -50], "end": [50, 50]},
        ]
        result = analyze_relationships(lines)
        self.assertEqual(result["counts"]["parallel"], 1)
        self.assertEqual(result["counts"]["perpendicular"], 2)
        self.assertEqual(result["counts"]["intersections"], 2)
        limited = analyze_relationships(lines, max_pairs=1)
        self.assertFalse(limited["complete"])
        self.assertTrue(limited["truncated"])


class CandidateTests(unittest.TestCase):
    def test_candidates_are_hypotheses_and_cover_geometry_patterns(self):
        entities = [
            {"id": "wall-a", "kind": "line", "start": [100, 100], "end": [100, 600], "coordinate_units": "test units"},
            {"id": "wall-b", "kind": "line", "start": [120, 100], "end": [120, 600], "coordinate_units": "test units"},
            {"id": "win-a", "kind": "line", "start": [100, 700], "end": [500, 700], "coordinate_units": "test units"},
            {"id": "win-b", "kind": "line", "start": [100, 720], "end": [500, 720], "coordinate_units": "test units"},
            {"id": "win-t1", "kind": "line", "start": [220, 700], "end": [220, 720], "coordinate_units": "test units"},
            {"id": "win-t2", "kind": "line", "start": [300, 700], "end": [300, 720], "coordinate_units": "test units"},
            {"id": "gap-a", "kind": "line", "start": [100, 800], "end": [250, 800], "coordinate_units": "test units"},
            {"id": "gap-b", "kind": "line", "start": [280, 800], "end": [450, 800], "coordinate_units": "test units"},
            {"id": "door-arc", "kind": "curve", "points": [[255, 800], [260, 790], [270, 785], [280, 800]], "coordinate_units": "test units"},
            {"id": "room", "kind": "polyline", "points": [[600, 600], [750, 600], [750, 750], [600, 750], [600, 600]], "closed": True, "area": 22500, "coordinate_units": "test units"},
            {"id": "dim", "kind": "line", "start": [500, 100], "end": [900, 100], "coordinate_units": "test units"},
            {"id": "tick-a", "kind": "line", "start": [500, 90], "end": [500, 110], "coordinate_units": "test units"},
            {"id": "tick-b", "kind": "line", "start": [900, 90], "end": [900, 110], "coordinate_units": "test units"},
        ]
        relations = analyze_relationships(entities)
        output = detect_architectural_candidates("synthetic.pdf", 1, 1000, 1000, entities, relations)
        kinds = {candidate["candidate_type"] for candidate in output["candidates"]}
        self.assertIn("wall_like_parallel_line_group", kinds)
        self.assertIn("window_like_opening_candidate", kinds)
        self.assertIn("door_like_opening_candidate", kinds)
        self.assertIn("room_boundary_candidate", kinds)
        self.assertIn("dimension_line_candidate", kinds)
        self.assertIn("possible_wall_junction_candidate", kinds)
        for candidate in output["candidates"]:
            self.assertTrue(candidate["candidate_id"])
            self.assertEqual(candidate["source_file"], "synthetic.pdf")
            self.assertEqual(candidate["page_number"], 1)
            self.assertIn(candidate["recognition_status"], ("candidate", "requires_review"))
            self.assertTrue(candidate["warnings"])
            self.assertTrue(candidate["geometry_references"])

    def test_false_positive_candidate_is_explicitly_uncertain_and_bounded(self):
        lines = [{"id": str(index), "kind": "line", "start": [0, index * 10],
                  "end": [100, index * 10], "coordinate_units": "points"} for index in range(50)]
        result = detect_architectural_candidates("noise.pdf", 1, 200, 200, lines,
                                                 max_pairs=10, max_candidates=5)
        self.assertFalse(result["complete"])
        self.assertTrue(result["truncated"])
        for candidate in result["candidates"]:
            self.assertIn("not a confirmed", candidate["warnings"][-1])


class ProcessingGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pymupdf
            from PIL import Image, ImageDraw
            cls.pymupdf, cls.Image, cls.ImageDraw = pymupdf, Image, ImageDraw
        except ImportError as exc:
            raise unittest.SkipTest("drawing dependencies are not installed: {}".format(exc))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"LORA_DRAWING_INBOX": str(self.inbox)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)
        self.pipeline = DrawingProcessingPipeline()

    def test_pdf_vector_lines_rectangles_curves_polylines_text_and_style(self):
        document = self.pymupdf.open()
        page = document.new_page(width=500, height=500)
        page.draw_line((10, 10), (200, 10), color=(1, 0, 0), width=2.5)
        page.draw_rect((20, 30, 120, 100), color=(0, 0, 1), width=1.2)
        page.draw_bezier((150, 100), (170, 50), (220, 50), (240, 100), width=1.0)
        page.draw_polyline([(260, 100), (300, 100), (300, 140)], closePath=False)
        page.insert_text((40, 200), "Grid A")
        document.save(str(self.inbox / "vectors.pdf"))
        document.close()
        result = self.pipeline.inspect("vectors.pdf")
        extraction = result["pages"][0]["geometry_extraction"]
        kinds = {entity["kind"] for entity in extraction["entities"]}
        self.assertIn("line", kinds)
        self.assertIn("rectangle", kinds)
        self.assertIn("curve", kinds)
        self.assertIn("polyline", kinds)
        self.assertTrue(extraction["text_annotations"])
        self.assertTrue(any(entity.get("stroke_width") is not None for entity in extraction["entities"]))
        self.assertTrue(any(entity.get("closed") for entity in extraction["entities"]
                            if entity["kind"] == "rectangle"))
        self.assertTrue(any(not path["closed"] for path in extraction["paths"]))
        self.assertEqual(extraction["coordinate_units"], "PDF page points")

    def test_rotated_pdf_page_keeps_vector_page_coordinates(self):
        document = self.pymupdf.open()
        page = document.new_page(width=300, height=200)
        page.set_rotation(90)
        page.draw_line((20, 30), (120, 30))
        document.save(str(self.inbox / "rotated.pdf"))
        document.close()
        result = self.pipeline.inspect("rotated.pdf")
        page_result = result["pages"][0]
        self.assertEqual(page_result["rotation_degrees_clockwise"], 90)
        self.assertTrue(page_result["geometry_extraction"]["entities"])
        entity = next(entity for entity in page_result["geometry_extraction"]["entities"]
                      if entity["kind"] == "line")
        self.assertEqual(entity["page_number"], 1)
        self.assertEqual(entity["coordinate_units"], "PDF page points")

    def test_pdf_geometry_extractor_limits_output(self):
        document = self.pymupdf.open()
        page = document.new_page(width=500, height=500)
        for index in range(40):
            page.draw_line((10, index * 5 + 1), (300, index * 5 + 1))
        extracted = extract_pdf_geometry(page, 1, max_entities=10)
        self.assertFalse(extracted["complete"])
        self.assertEqual(extracted["entity_count"], 10)
        document.close()

    def test_raster_multiresolution_transforms_and_relations(self):
        for name, size in (("small.png", (400, 300)), ("large.png", (800, 600))):
            image = self.Image.new("RGB", size, "white")
            draw = self.ImageDraw.Draw(image)
            draw.line((size[0] * 0.1, size[1] * 0.2, size[0] * 0.8, size[1] * 0.2), fill="black", width=4)
            draw.line((size[0] * 0.1, size[1] * 0.25, size[0] * 0.8, size[1] * 0.25), fill="black", width=4)
            draw.line((size[0] * 0.1, size[1] * 0.2, size[0] * 0.1, size[1] * 0.25), fill="black", width=4)
            image.save(self.inbox / name)
        small = self.pipeline.inspect("small.png", max_dimension=400)
        large = self.pipeline.inspect("large.png", max_dimension=400)
        self.assertTrue(small["geometry_entities"])
        self.assertTrue(large["geometry_entities"])
        self.assertIn("source_points", small["geometry"]["line_segments"][0])
        self.assertIn("source_bbox", small["geometry"]["connected_components"][0])
        analysis = analyze_relationships(small["geometry_entities"])
        self.assertIn("parallel", analysis["counts"])
        self.assertEqual(small["preparation"]["width_pixels"], 400)
        self.assertEqual(large["preparation"]["width_pixels"], 400)

    def test_noisy_raster_output_is_bounded(self):
        import random
        image = self.Image.new("L", (320, 240), 0)
        pixels = image.load()
        random.seed(7)
        for y in range(240):
            for x in range(320):
                pixels[x, y] = random.randrange(256)
        image.convert("RGB").save(self.inbox / "noise.png")
        result = self.pipeline.inspect("noise.png", max_dimension=320)
        self.assertLessEqual(len(result["geometry"]["line_segments"]), 500)
        self.assertLessEqual(len(result["geometry"]["contours"]), 500)
        self.assertIn("transformation_history", result["image"])

    def test_image_geometry_candidates_use_original_coordinates(self):
        image = self.Image.new("RGB", (800, 600), "white")
        draw = self.ImageDraw.Draw(image)
        draw.line((100, 100, 600, 100), fill="black", width=4)
        draw.line((100, 120, 600, 120), fill="black", width=4)
        image.save(self.inbox / "candidate.png")
        result = self.pipeline.inspect("candidate.png", max_dimension=400)
        relation = analyze_relationships(result["geometry_entities"])
        candidate_result = detect_architectural_candidates("candidate.png", 1, 800, 600,
                                                           result["geometry_entities"], relation)
        self.assertTrue(candidate_result["candidates"])
        self.assertEqual(candidate_result["candidates"][0]["coordinate_units"], "original image pixels")


if __name__ == "__main__":
    unittest.main()
