"""Safe filename-only processing API and bounded in-memory summary retrieval."""

from collections import OrderedDict
import time

from .cad_adapter import UnavailableCadAdapter
from .exceptions import DrawingError
from .file_validation import resolve_input
from .image_processor import process_image
from .models import ProcessingLimits
from .pdf_processor import process_pdf


class DrawingProcessingPipeline:
    MAX_CACHED_IMAGE_BYTES = 32 * 1024 * 1024
    MAX_CACHED_PAGE_BYTES = 8 * 1024 * 1024

    def __init__(self, limits=None, cad_adapter=None, summary_limit=20):
        self.limits = limits or ProcessingLimits()
        self.cad_adapter = cad_adapter or UnavailableCadAdapter()
        self.summary_limit = summary_limit
        self._summaries = OrderedDict()
        self._rendered = OrderedDict()
        self._rendered_bytes = 0

    def inspect(self, file_name, dpi=100, run_ocr=False, max_dimension=3000):
        validated = resolve_input(file_name, self.limits)
        return self._process_validated(validated, dpi, run_ocr, max_dimension)

    def _process_validated(self, validated, dpi, run_ocr, max_dimension):
        file_name = validated["file_name"]
        started = time.monotonic()
        rendered_pages = {}
        rendered_size = [0]

        def capture_render(page_number, data, metadata):
            size = len(data)
            if size > self.MAX_CACHED_PAGE_BYTES or rendered_size[0] + size > self.MAX_CACHED_IMAGE_BYTES:
                return False
            rendered_pages[page_number] = {"data": data, "metadata": dict(metadata)}
            rendered_size[0] += size
            return True

        if validated["file_type"] == "pdf":
            result = process_pdf(validated["path"], file_name, dpi=dpi, run_ocr=run_ocr,
                                 limits=self.limits, render_handler=capture_render,
                                 source_bytes=validated.get("data"))
        elif validated["file_type"] in ("jpeg", "png"):
            result = process_image(validated["path"], file_name, max_dimension=max_dimension,
                                   run_ocr=run_ocr, limits=self.limits, render_handler=capture_render,
                                   source_bytes=validated.get("data"))
        elif validated["file_type"] == "dwg":
            result = {"file_name": file_name, "file_type": "dwg", "native_extraction": "unsupported",
                      "adapter_status": "interface_available_no_implementation",
                      "extraction_complete": False,
                      "warnings": ["DWG native geometry was not parsed or converted"]}
        else:
            raise DrawingError("unsupported_format", "This drawing format is not supported")
        if time.monotonic() - started > self.limits.max_processing_seconds:
            result["extraction_complete"] = False
            result.setdefault("warnings", []).append("processing exceeded the configured time limit")
        processing_id = self._store(result)
        if rendered_pages:
            self._store_renders(processing_id, rendered_pages)
        result["processing_id"] = processing_id
        result["processing_limits"] = {
            "max_file_bytes": self.limits.max_file_bytes,
            "max_pdf_pages": self.limits.max_pdf_pages,
            "max_image_pixels": self.limits.max_image_pixels,
            "max_processing_seconds": self.limits.max_processing_seconds,
        }
        return result

    def process_pdf(self, file_name, dpi=100, run_ocr=False):
        validated = resolve_input(file_name, self.limits)
        if validated["file_type"] != "pdf":
            raise DrawingError("format_mismatch", "process_pdf accepts only PDF files")
        return self._process_validated(validated, dpi, run_ocr, 3000)

    def process_image(self, file_name, max_dimension=3000, run_ocr=False):
        validated = resolve_input(file_name, self.limits)
        if validated["file_type"] not in ("jpeg", "png"):
            raise DrawingError("format_mismatch", "process_image accepts only JPG, JPEG, or PNG files")
        return self._process_validated(validated, 100, run_ocr, max_dimension)

    def get_summary(self, processing_id):
        if not isinstance(processing_id, str) or len(processing_id) > 64:
            raise DrawingError("invalid_processing_id", "processing_id must be a valid identifier")
        value = self._summaries.get(processing_id)
        if value is None:
            raise DrawingError("summary_not_found", "No in-memory result exists for this processing_id")
        self._summaries.move_to_end(processing_id)
        return value

    def get_render(self, processing_id, page_number=1):
        if not isinstance(processing_id, str) or len(processing_id) > 64:
            raise DrawingError("invalid_processing_id", "processing_id must be a valid identifier")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise DrawingError("invalid_page_number", "page_number must be a positive integer")
        pages = self._rendered.get(processing_id)
        if pages is None or page_number not in pages:
            raise DrawingError("render_not_available", "This page image is no longer available in the in-memory cache")
        self._rendered.move_to_end(processing_id)
        return pages[page_number]

    def _store(self, result):
        import uuid
        processing_id = str(uuid.uuid4())
        result["processing_id"] = processing_id
        self._summaries[processing_id] = result
        while len(self._summaries) > self.summary_limit:
            evicted_id, _ = self._summaries.popitem(last=False)
            self._drop_renders(evicted_id)
        return processing_id

    def _store_renders(self, processing_id, pages):
        page_bytes = sum(len(page["data"]) for page in pages.values())
        while self._rendered and self._rendered_bytes + page_bytes > self.MAX_CACHED_IMAGE_BYTES:
            old_id, old_pages = self._rendered.popitem(last=False)
            self._rendered_bytes -= sum(len(page["data"]) for page in old_pages.values())
            self._mark_render_unavailable(old_id)
        if page_bytes > self.MAX_CACHED_IMAGE_BYTES:
            self._mark_render_unavailable(processing_id)
            return
        self._rendered[processing_id] = pages
        self._rendered_bytes += page_bytes

    def _drop_renders(self, processing_id):
        pages = self._rendered.pop(processing_id, None)
        if pages:
            self._rendered_bytes -= sum(len(page["data"]) for page in pages.values())
            self._mark_render_unavailable(processing_id)

    def _mark_render_unavailable(self, processing_id):
        result = self._summaries.get(processing_id)
        if not result:
            return
        for page in result.get("pages", []):
            render = page.get("render")
            if render:
                render["retrieval_available"] = False
        preparation = result.get("preparation")
        if preparation:
            preparation["render_retrieval_available"] = False
