import builtins
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lora_drawing.coordinate_system import mm_to_pdf_points, pdf_points_to_mm, pixels_to_mm
from lora_drawing.exceptions import DrawingError
from lora_drawing.file_validation import resolve_input
from lora_drawing.models import ProcessingLimits
from lora_drawing.processing_pipeline import DrawingProcessingPipeline


class DrawingProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pymupdf
            from PIL import Image, ImageDraw
            cls.fitz = pymupdf
            cls.Image = Image
            cls.ImageDraw = ImageDraw
        except ImportError as exc:
            raise unittest.SkipTest("PDF and image runtime dependencies are not installed: {}".format(exc))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"LORA_DRAWING_INBOX": str(self.inbox)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)
        self.pipeline = DrawingProcessingPipeline()

    def make_pdf(self, name="two_pages.pdf", text=True, rotation=0):
        doc = self.fitz.open()
        page1 = doc.new_page(width=612, height=792)
        if text:
            page1.insert_text((72, 72), "Synthetic plan title")
        page1.draw_line((20, 20), (200, 200))
        page1.draw_rect((100, 100, 200, 180))
        page2 = doc.new_page(width=792, height=612)
        page2.set_rotation(rotation)
        doc.save(str(self.inbox / name))
        doc.close()
        return self.inbox / name

    def make_image(self, name="plan.png", size=(800, 600), text=None):
        image = self.Image.new("RGB", size, "white")
        draw = self.ImageDraw.Draw(image)
        draw.line((10, 20, size[0] - 10, 20), fill="black", width=4)
        draw.rectangle((40, 50, min(size[0] - 20, 200), min(size[1] - 20, 180)), outline="black", width=3)
        if text:
            draw.text((50, 60), text, fill="black")
        image.save(self.inbox / name)
        return self.inbox / name

    def test_mult_page_pdf_text_vectors_rotation_and_render(self):
        self.make_pdf(rotation=90)
        result = self.pipeline.process_pdf("two_pages.pdf", dpi=72)
        self.assertEqual(result["page_count"], 2)
        self.assertIn("Synthetic plan title", result["pages"][0]["extracted_text"])
        self.assertGreaterEqual(result["pages"][0]["vector_geometry"]["lines"], 1)
        self.assertGreaterEqual(result["pages"][0]["vector_geometry"]["rectangles"], 1)
        self.assertEqual(result["pages"][1]["rotation_degrees_clockwise"], 90)
        self.assertEqual(result["pages"][0]["render"]["status"], "rendered_in_memory")
        self.assertEqual(result["pages"][0]["render"]["pixel_width"], 612)
        self.assertEqual(result["pages"][0]["ocr"]["status"], "not_requested")

    def test_scanned_pdf_candidate_and_ocr_status(self):
        self.make_pdf(text=False)
        result = self.pipeline.process_pdf("two_pages.pdf")
        self.assertTrue(result["pages"][0]["scanned_page_candidate"])
        self.assertEqual(result["pages"][0]["ocr"]["status"], "not_requested")
        self.assertTrue(any("may require OCR" in warning for warning in result["warnings"]))

    def test_requested_pdf_ocr_dependency_failure_is_incomplete(self):
        self.make_pdf(text=False)
        from lora_drawing import pdf_processor
        with patch.object(pdf_processor, "_run_ocr", return_value={"status": "engine_unavailable", "text_blocks": []}):
            result = self.pipeline.process_pdf("two_pages.pdf", run_ocr=True)
        self.assertFalse(result["extraction_complete"])
        self.assertEqual(result["pages"][0]["ocr"]["status"], "engine_unavailable")

    def test_pdf_embedded_raster_image_metadata(self):
        image = self.Image.new("RGB", (20, 10), "red")
        image.save(self.inbox / "embedded.png")
        doc = self.fitz.open()
        page = doc.new_page(width=300, height=200)
        page.insert_image(self.fitz.Rect(20, 20, 120, 70), filename=str(self.inbox / "embedded.png"))
        doc.save(str(self.inbox / "embedded.pdf"))
        doc.close()
        result = self.pipeline.process_pdf("embedded.pdf")
        page_result = result["pages"][0]
        self.assertEqual(page_result["embedded_raster_image_summary"]["count"], 1)
        self.assertEqual(page_result["embedded_raster_images"][0]["width_pixels"], 20)
        self.assertEqual(len(page_result["embedded_raster_images"][0]["placement_bboxes_pdf_points"]), 1)

    def test_image_dimensions_orientation_resize_and_edges(self):
        self.make_image(size=(800, 600))
        result = self.pipeline.process_image("plan.png", max_dimension=400)
        self.assertEqual(result["image"]["width_pixels"], 800)
        self.assertEqual(result["image"]["height_pixels"], 600)
        self.assertEqual(result["preparation"]["width_pixels"], 400)
        self.assertEqual(result["preparation"]["height_pixels"], 300)
        self.assertEqual(result["image"]["coordinate_system"]["units"], "pixels")
        self.assertEqual(result["image"]["coordinate_system"]["real_world_scale"], None)
        self.assertEqual(result["geometry"]["status"], "completed")
        self.assertGreater(result["geometry"]["edge_pixel_count"], 0)
        self.assertGreater(result["geometry"]["line_count"], 0)

    def test_pdf_processing_uses_verified_bytes_after_inbox_path_changes(self):
        original = self.make_pdf("snapshot.pdf", text=True)
        original_bytes = original.read_bytes()
        replacement = self.inbox / "replacement.pdf"
        doc = self.fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Replacement after validation")
        doc.save(str(replacement))
        doc.close()
        replacement_bytes = replacement.read_bytes()
        from lora_drawing import processing_pipeline
        resolve = processing_pipeline.resolve_input

        def resolve_then_replace(file_name, limits=None):
            result = resolve(file_name, limits)
            original.write_bytes(replacement_bytes)
            return result

        with patch.object(processing_pipeline, "resolve_input", side_effect=resolve_then_replace):
            result = self.pipeline.process_pdf("snapshot.pdf")
        self.assertIn("Synthetic plan title", result["pages"][0]["extracted_text"])
        self.assertNotIn("Replacement after validation", result["pages"][0]["extracted_text"])
        self.assertEqual(result["file_size_bytes"], len(original_bytes))

    def test_image_processing_uses_verified_bytes_after_inbox_path_changes(self):
        original = self.make_image("snapshot.png", size=(800, 600))
        original_bytes = original.read_bytes()
        self.make_image("replacement.png", size=(200, 100))
        replacement_bytes = (self.inbox / "replacement.png").read_bytes()
        from lora_drawing import processing_pipeline
        resolve = processing_pipeline.resolve_input

        def resolve_then_replace(file_name, limits=None):
            result = resolve(file_name, limits)
            original.write_bytes(replacement_bytes)
            return result

        with patch.object(processing_pipeline, "resolve_input", side_effect=resolve_then_replace):
            result = self.pipeline.process_image("snapshot.png")
        self.assertEqual((result["image"]["width_pixels"], result["image"]["height_pixels"]), (800, 600))
        self.assertEqual(result["file_size_bytes"], len(original_bytes))

    def test_missing_edge_dependency_and_requested_ocr_are_reported(self):
        self.make_image()
        from lora_drawing import image_processor
        real_import = builtins.__import__
        def blocked_cv2(name, *args, **kwargs):
            if name == "cv2":
                raise ImportError("simulated missing OpenCV")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=blocked_cv2):
            edge = image_processor._detect_edges_and_lines(self.Image.new("RGB", (32, 32)))
        self.assertEqual(edge["status"], "dependency_missing")
        with patch.object(image_processor, "_run_ocr", return_value={
                "status": "engine_unavailable", "text_blocks": [], "message": "missing tesseract"}):
            result = self.pipeline.process_image("plan.png", run_ocr=True)
        self.assertFalse(result["extraction_complete"])
        self.assertEqual(result["ocr"]["status"], "engine_unavailable")

    def test_exif_orientation_and_transform_are_reported(self):
        image = self.Image.new("RGB", (400, 200), "white")
        exif = image.getexif()
        exif[274] = 6
        image.save(self.inbox / "rotated.jpg", exif=exif)
        result = self.pipeline.process_image("rotated.jpg")
        self.assertEqual(result["image"]["exif_orientation"], 6)
        self.assertTrue(result["image"]["orientation_applied"])
        self.assertEqual(result["image"]["oriented_width_pixels"], 200)
        self.assertEqual(result["image"]["oriented_height_pixels"], 400)
        self.assertEqual(len(result["image"]["source_to_processed_pixel_transform"]), 3)
        transform = result["image"]["source_to_processed_pixel_transform"]
        self.assertEqual([transform[0][2], transform[1][2]], [199.0, 0.0])

    def test_empty_pdf_with_mocked_zero_pages(self):
        self.make_pdf("empty.pdf")
        with patch("lora_drawing.pdf_processor._dependency") as dep:
            class EmptyDocument:
                needs_pass = False
                page_count = 0
                metadata = {}
                def close(self):
                    pass
            class FakeFitz:
                @staticmethod
                def open(path=None, stream=None, filetype=None):
                    return EmptyDocument()
            dep.return_value = FakeFitz
            with self.assertRaises(DrawingError) as err:
                self.pipeline.process_pdf("empty.pdf")
        self.assertEqual(err.exception.code, "empty_pdf")

    def test_invalid_mismatch_unsupported_and_traversal(self):
        (self.inbox / "invalid.pdf").write_bytes(b"not a pdf")
        (self.inbox / "wrong.png").write_bytes(b"%PDF-1.4\n")
        with self.assertRaises(DrawingError) as err:
            resolve_input("invalid.pdf")
        self.assertEqual(err.exception.code, "invalid_file")
        with self.assertRaises(DrawingError) as err:
            resolve_input("wrong.png")
        self.assertEqual(err.exception.code, "format_mismatch")
        with self.assertRaises(DrawingError) as err:
            resolve_input("../secret.pdf")
        self.assertEqual(err.exception.code, "invalid_file_name")
        with self.assertRaises(DrawingError) as err:
            resolve_input("unknown.tif")
        self.assertEqual(err.exception.code, "unsupported_format")

    def test_oversized_file_and_page_limit(self):
        (self.inbox / "large.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        with self.assertRaises(DrawingError) as err:
            resolve_input("large.png", ProcessingLimits(max_file_bytes=32))
        self.assertEqual(err.exception.code, "file_too_large")
        self.make_pdf()
        limited_pipeline = DrawingProcessingPipeline(limits=ProcessingLimits(max_pdf_pages=1))
        with self.assertRaises(DrawingError) as err:
            limited_pipeline.process_pdf("two_pages.pdf")
        self.assertEqual(err.exception.code, "page_limit_exceeded")

    def test_corrupt_pdf_and_image(self):
        (self.inbox / "bad.pdf").write_bytes(b"%PDF-1.4\ninvalid\n%%EOF")
        (self.inbox / "bad.jpg").write_bytes(b"\xff\xd8\xffinvalid")
        with self.assertRaises(DrawingError):
            self.pipeline.process_pdf("bad.pdf")
        with self.assertRaises(DrawingError) as err:
            self.pipeline.process_image("bad.jpg")
        self.assertEqual(err.exception.code, "corrupt_image")

    def test_image_pixel_limit_and_invalid_dpi(self):
        self.make_image(size=(800, 600))
        limits = ProcessingLimits(max_image_pixels=100)
        from lora_drawing.image_processor import process_image
        with self.assertRaises(DrawingError) as err:
            process_image(self.inbox / "plan.png", "plan.png", limits=limits)
        self.assertEqual(err.exception.code, "image_too_large")
        self.make_pdf()
        with self.assertRaises(DrawingError) as err:
            self.pipeline.process_pdf("two_pages.pdf", dpi=301)
        self.assertEqual(err.exception.code, "invalid_input")

    def test_pdf_text_output_truncation_is_explicit(self):
        self.make_pdf()
        limited = DrawingProcessingPipeline(limits=ProcessingLimits(max_text_chars=4))
        result = limited.process_pdf("two_pages.pdf")
        self.assertFalse(result["extraction_complete"])
        self.assertTrue(any("text exceeded" in warning for warning in result["warnings"]))

    def test_render_pixel_budget_rejects_before_render(self):
        self.make_pdf()
        limited = DrawingProcessingPipeline(limits=ProcessingLimits(max_render_pixels_per_page=10))
        with self.assertRaises(DrawingError) as err:
            limited.process_pdf("two_pages.pdf", dpi=100)
        self.assertEqual(err.exception.code, "render_limit_exceeded")

    def test_processing_time_limit_is_checked(self):
        self.make_pdf()
        limited = DrawingProcessingPipeline(limits=ProcessingLimits(max_processing_seconds=0))
        with self.assertRaises(DrawingError) as err:
            limited.process_pdf("two_pages.pdf")
        self.assertEqual(err.exception.code, "processing_time_limit")

    def test_coordinate_conversion_and_known_dpi_only(self):
        self.assertAlmostEqual(pdf_points_to_mm(72), 25.4)
        self.assertAlmostEqual(mm_to_pdf_points(25.4), 72)
        self.assertAlmostEqual(pixels_to_mm(300, 300), 25.4)
        with self.assertRaises(ValueError):
            pixels_to_mm(300, None)

    def test_processing_summary_is_in_memory_and_bounded(self):
        self.make_pdf()
        result = self.pipeline.inspect("two_pages.pdf")
        summary = self.pipeline.get_summary(result["processing_id"])
        self.assertEqual(summary["file_name"], "two_pages.pdf")
        self.assertTrue(summary["pages"][0]["render"]["retrieval_available"])
        rendered = self.pipeline.get_render(result["processing_id"], 1)
        self.assertEqual(rendered["data"][:8], b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(DrawingError) as err:
            self.pipeline.get_summary("unknown")
        self.assertEqual(err.exception.code, "summary_not_found")
        self.assertEqual({path.name for path in self.inbox.iterdir()}, {"two_pages.pdf"})

    def test_render_image_cache_limits_and_unavailable_result(self):
        self.make_pdf()
        limited = DrawingProcessingPipeline()
        limited.MAX_CACHED_PAGE_BYTES = 1
        result = limited.inspect("two_pages.pdf")
        self.assertFalse(result["pages"][0]["render"]["retrieval_available"])
        with self.assertRaises(DrawingError) as err:
            limited.get_render(result["processing_id"], 1)
        self.assertEqual(err.exception.code, "render_not_available")

    def test_missing_dependency_is_clear(self):
        self.make_pdf()
        real_import = builtins.__import__
        def blocked(name, *args, **kwargs):
            if name == "pymupdf":
                raise ImportError("simulated missing PyMuPDF")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=blocked):
            with self.assertRaises(DrawingError) as err:
                self.pipeline.process_pdf("two_pages.pdf")
        self.assertEqual(err.exception.code, "dependency_missing")

    def test_dwg_is_identified_without_claiming_geometry(self):
        (self.inbox / "future.dwg").write_bytes(b"AC1032" + b"0" * 20)
        result = self.pipeline.inspect("future.dwg")
        self.assertEqual(result["file_type"], "dwg")
        self.assertEqual(result["native_extraction"], "unsupported")
        self.assertFalse(result["extraction_complete"])


if __name__ == "__main__":
    unittest.main()
