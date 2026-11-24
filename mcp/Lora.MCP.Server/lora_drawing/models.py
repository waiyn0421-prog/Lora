"""Small JSON-safe models and processing bounds."""

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class ProcessingLimits:
    max_file_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 100
    max_image_pixels: int = 40_000_000
    max_render_pixels_per_page: int = 25_000_000
    max_render_pixels_total: int = 80_000_000
    max_text_chars: int = 100_000
    max_vector_items_per_page: int = 1_000
    max_vector_items_total: int = 5_000
    max_embedded_images_total: int = 5_000
    max_ocr_blocks_per_page: int = 2_000
    max_ocr_blocks_total: int = 20_000
    max_processing_seconds: float = 60.0


@dataclass
class ProcessingResult:
    file_name: str
    file_type: str
    extraction_complete: bool = True
    warnings: list = field(default_factory=list)
    processing_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: float = field(default_factory=time.monotonic, repr=False)

    def as_dict(self):
        return {
            "processing_id": self.processing_id,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "extraction_complete": self.extraction_complete,
            "warnings": list(self.warnings),
        }
