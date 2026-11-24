"""Deterministic architectural hypothesis engine over existing drawing geometry."""

import math

from lora_drawing.candidates import detect_architectural_candidates
from lora_drawing.geometry import analyze_relationships, group_geometry
from lora_drawing.ocr import verify_tesseract

from .dimension_interpreter import interpret_dimension_text
from .errors import RecognitionError
from .geometry_classifier import classify_geometry
from .provider import NoProviderConfigured
from .store import RecognitionStore
from .text_interpreter import extract_text_records, interpret_text


class RecognitionEngine:
    def __init__(self, store=None, provider=None):
        self.store = store or RecognitionStore()
        self.provider = provider or NoProviderConfigured()

    def recognize(self, drawing_result, page_number=None):
        if not isinstance(drawing_result, dict) or drawing_result.get("file_type") not in ("pdf", "jpeg", "png"):
            raise RecognitionError("recognition_unavailable", "Recognition requires a processed PDF, JPG/JPEG or PNG result")
        pages = self._pages(drawing_result, page_number)
        recognized_pages = []
        total_complete = bool(drawing_result.get("extraction_complete", False))
        global_warnings = []
        tesseract = verify_tesseract()
        for page_number_value, page_result, entities, width, height, coordinate_system, paths in pages:
            text_records = extract_text_records(drawing_result, page_number_value, page_result)
            levels = interpret_text(text_records)
            diagonal = math.hypot(float(width or 1), float(height or 1))
            candidates_result = detect_architectural_candidates(
                drawing_result["file_name"], page_number_value, width or 1, height or 1,
                entities, max_candidates=500)
            relationships = analyze_relationships(entities)
            groups = group_geometry(entities, return_status=True)
            dimension_candidates = interpret_dimension_text(text_records,
                                                              candidates_result["candidates"], diagonal)
            classified = classify_geometry(drawing_result["file_name"], page_number_value,
                                           width or 1, height or 1,
                                           _coordinate_units(coordinate_system, drawing_result),
                                           entities, candidates_result["candidates"], text_records,
                                           levels, dimension_candidates, relationships, paths)
            page_complete = (bool(page_result.get("extraction_complete", drawing_result.get("extraction_complete", False))) and
                             candidates_result["complete"] and relationships["complete"] and groups["complete"])
            warnings = []
            warnings.extend(page_result.get("warnings", []))
            warnings.extend(candidates_result.get("warnings", []))
            if not relationships["complete"]:
                warnings.append("Geometry relationship analysis was truncated.")
            if not groups["complete"]:
                warnings.append("Geometry grouping was truncated.")
            ocr = page_result.get("ocr", {}) if drawing_result.get("file_type") == "pdf" else drawing_result.get("ocr", {})
            if ocr.get("status") == "engine_unavailable" or tesseract.get("status") == "executable_not_found":
                warnings.append("Tesseract OCR is unavailable; embedded PDF text and geometry recognition remain available.")
            elif ocr.get("status") == "not_requested":
                warnings.append("OCR was not requested during drawing processing; scanned-page text may be absent.")
            recognized_pages.append({
                "page_number": page_number_value,
                "coordinate_system": coordinate_system,
                "transformation_history": page_result.get("transformation_history") or
                    drawing_result.get("image", {}).get("transformation_history", []),
                "text_records": text_records,
                "ocr_status": ocr.get("status", "unavailable"),
                "text_extraction_complete": (ocr.get("status") not in
                                              ("engine_unavailable", "not_requested", "unavailable")),
                "elements": classified["elements"],
                "element_count": classified["element_count"],
                "candidate_count": candidates_result["candidate_count"],
                "spatial_relationships": classified["spatial_relationships"],
                "recognition_complete": page_complete,
                "warnings": list(dict.fromkeys(warnings)),
            })
            total_complete = total_complete and page_complete
            global_warnings.extend(warnings)
        result = {
            "processing_id": drawing_result.get("processing_id"),
            "source_file": drawing_result["file_name"],
            "file_type": drawing_result["file_type"],
            "recognition_method": "deterministic_geometry_and_text_heuristics",
            "ai_provider": {"status": self.provider.status,
                            "message": "No AI provider was called; results are deterministic hypotheses."},
            "ocr_runtime": tesseract,
            "pages": recognized_pages,
            "elements": [element for page in recognized_pages for element in page["elements"]],
            "element_count": sum(page["element_count"] for page in recognized_pages),
            "complete": total_complete,
            "warnings": list(dict.fromkeys(global_warnings)),
            "calibration": {"status": "not_provided", "real_world_scale": None,
                            "warning": "No source coordinates were converted to Revit millimetres or real-world dimensions."},
        }
        recognition_id = self.store.put(result)
        return result

    def _pages(self, result, page_number):
        if page_number is not None and (isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1):
            raise RecognitionError("invalid_page_number", "page_number must be a positive integer")
        if result["file_type"] == "pdf":
            source_pages = result.get("pages", [])
            if page_number is not None:
                source_pages = [page for page in source_pages if page.get("page_number") == page_number]
                if not source_pages:
                    raise RecognitionError("page_not_found", "The requested page is not present in the processed drawing")
            return [(page["page_number"], page,
                     page.get("geometry_extraction", {}).get("entities", []),
                     page.get("width"), page.get("height"), page.get("coordinate_system"),
                     page.get("geometry_extraction", {}).get("paths", [])) for page in source_pages]
        if page_number not in (None, 1):
            raise RecognitionError("page_not_found", "Raster image processing has only page 1")
        image = result.get("image", {})
        page = {"page_number": 1, "extraction_complete": result.get("extraction_complete", False),
                "warnings": result.get("warnings", []), "ocr": result.get("ocr", {}),
                "transformation_history": image.get("transformation_history", [])}
        return [(1, page, result.get("geometry_entities", []), image.get("width_pixels"),
                 image.get("height_pixels"), image.get("coordinate_system"), [])]


def _coordinate_units(coordinate_system, result):
    if isinstance(coordinate_system, dict):
        return coordinate_system.get("units", "source coordinate units")
    return "original image pixels" if result.get("file_type") in ("jpeg", "png") else "PDF page points"
