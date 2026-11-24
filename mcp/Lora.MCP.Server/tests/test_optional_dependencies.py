import builtins
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from lora_drawing.exceptions import DrawingError
from lora_drawing.image_processor import process_image
from lora_drawing.pdf_processor import process_pdf


class OptionalDrawingDependencyTests(unittest.TestCase):
    def _block_import(self, blocked_name):
        original = builtins.__import__

        def import_without_optional(name, *args, **kwargs):
            if name == blocked_name:
                raise ImportError("blocked optional dependency for test")
            return original(name, *args, **kwargs)

        return import_without_optional

    def test_pdf_processing_reports_pymupdf_missing_without_import_failure_at_startup(self):
        with patch("builtins.__import__", side_effect=self._block_import("pymupdf")):
            with self.assertRaises(DrawingError) as caught:
                process_pdf("not-opened.pdf", "not-opened.pdf")
        self.assertEqual(caught.exception.code, "dependency_missing")
        self.assertEqual(caught.exception.details["dependency"], "pymupdf")
        self.assertIn("requirements-drawing.txt", str(caught.exception))

    def test_raster_processing_reports_pillow_missing(self):
        with patch("builtins.__import__", side_effect=self._block_import("PIL")):
            with self.assertRaises(DrawingError) as caught:
                process_image("not-opened.png", "not-opened.png")
        self.assertEqual(caught.exception.code, "dependency_missing")
        self.assertEqual(caught.exception.details["dependency"], "Pillow")
        self.assertIn("requirements-drawing.txt", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
