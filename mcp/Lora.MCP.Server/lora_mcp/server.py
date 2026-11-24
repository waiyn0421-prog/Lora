"""Lora local MCP server. Revit access is read-only; drawing-review actions persist locally."""

import logging
import json
import math
from typing import NotRequired, TypedDict

from mcp.server.fastmcp import FastMCP, Image as MCPImage
from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from .client import RevitMCPClient, RevitMCPError, normalize_parameters, paginate, validate_category
from .document_guard import ReadOnlyDocumentGuard
from lora_context.engine import BIMContextEngine, ContextError
from lora_context.registry import list_commands
from lora_drawing import DrawingProcessingPipeline
from lora_drawing.exceptions import DrawingError
from lora_drawing.geometry import analyze_relationships, group_geometry
from lora_drawing.candidates import detect_architectural_candidates
from lora_recognition import RecognitionEngine
from lora_recognition.errors import RecognitionError
from lora_calibration import CalibrationEngine
from lora_calibration.errors import CalibrationError
from lora_review import DrawingReviewManager, ReviewRepository
from lora_review.repository import SCHEMA_VERSION as REVIEW_SCHEMA_VERSION
from lora_review.errors import ReviewError
from lora_generation import (GenerationPlanner, GenerationPlanRepository,
                             GenerationPreviewEngine, GenerationPreviewRepository)
from lora_generation.errors import GenerationPlanError
from lora_execution import ExecutionEngine, ExecutionRepository
from lora_execution.errors import ExecutionError
from lora_generation.inventory import read_generation_inventory


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
mcp = FastMCP("Lora BIM")
client = RevitMCPClient()
document_guard = ReadOnlyDocumentGuard(client)
execution_repository = ExecutionRepository()


class ParameterFilter(TypedDict):
    name: str
    value: str | int | float | bool
    operator: NotRequired[str]
    condition: NotRequired[str]


class WallExecutionApproval(BaseModel):
    approval_nonce: str
    approval_text: str


def _error(exc):
    if isinstance(exc, ExecutionError):
        error = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            error["details"] = exc.details
        return {"status": "error", "error": error}
    if isinstance(exc, GenerationPlanError):
        error = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            error["details"] = exc.details
        return {"status": "error", "error": error}
    if isinstance(exc, RevitMCPError):
        return {"status": "error", "error": exc.as_dict()}
    logging.getLogger("lora_mcp").error("Unexpected tool failure (%s)", type(exc).__name__)
    return {"status": "error", "error": {"code": "internal_error", "message": "Unexpected internal error"}}


@mcp.tool()
def lora_get_project_info() -> dict:
    """Read project parameters and active document identity. Revit version is null if upstream does not provide it."""
    try:
        data = client.call("GET", "/project_info")
        identity = data.pop("_lora_document_identity")
        return {"status": "success", "project_name": data.get("project_name"),
                "project_number": data.get("project_number"), "status_value": data.get("status"),
                "document_identity": identity, "revit_version": None,
                "revit_version_availability": "not_provided_by_project_info_route"}
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def lora_diagnose_revit_connection() -> dict:
    """Probe configured localhost Routes ports, pin only a unique endpoint, and compare consecutive document identities."""
    try:
        return {"status": "success", **client.diagnose_connection()}
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def lora_get_active_document_identity() -> dict:
    """Read the active document identity from RevitMCP diagnostics; missing GUID, type or version remains null."""
    try:
        identity = document_guard.capture()
        return {"status": "success", "identity": identity,
                "process_metadata": client.local_process_metadata(identity.get("revit_process_id"))}
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def lora_validate_document_identity(expected_identity: dict) -> dict:
    """Compare an expected snapshot with the active document and reject uncertain identities for guarded operations."""
    try:
        return {"status": "success", **document_guard.verify(expected_identity)}
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def lora_get_revit_connection_status() -> dict:
    """Return current Routes reachability, active-document availability, process identity and endpoint without fallback."""
    try:
        result = client.diagnose_connection()
        return {"status": "success", "connection_status": result.get("connection_status"),
                "pinned_endpoint": result.get("pinned_endpoint"),
                "responding_endpoint_count": result.get("responding_endpoint_count"),
                "document_status": result.get("document_status"),
                "identity_stability": result.get("identity_stability"),
                "active_document_identity": result.get("active_document_identity"),
                "local_revit_process": result.get("local_revit_process"),
                "diagnostic_error": result.get("diagnostic_error")}
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def lora_get_levels(offset: int = 0, limit: int = 200) -> dict:
    """Read levels from model statistics; elevations are converted from Revit internal feet to millimetres (exact factor 304.8)."""
    try:
        data = client.call("POST", "/model/statistics", {"include_levels": True, "include_detailed_types": False, "top_n": 200})
        levels = data.get("levels") or []
        converted = []
        for level in levels:
            feet = level.get("elevation_internal_feet")
            converted.append({**level, "elevation_mm": feet * 304.8 if isinstance(feet, (int, float)) else None,
                              "conversion": "internal feet × 304.8 = millimetres"})
        page = paginate(converted, offset, limit, data.get("summary", {}).get("total_levels", len(converted)))
        return {"status": "success", **page, "source_route": "/model/statistics"}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Enumerate Revit wall types", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_get_wall_types() -> dict:
    """Read actual Autodesk.Revit.DB WallType elements through the local Lora sidecar."""
    try:
        result = client.get_wall_types()
        result.pop("_lora_document_identity", None)
        return result
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Read Revit coordinate diagnostic", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_get_coordinate_diagnostic() -> dict:
    """Read Internal Origin, base points, active ProjectLocation and coordinate transform diagnostics."""
    try:
        result = client.get_coordinate_diagnostic()
        result.pop("_lora_document_identity", None)
        return result
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Read Revit level catalog", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_get_level_catalog(offset: int = 0, limit: int = 100,
                            failure_offset: int = 0, failure_limit: int = 50) -> dict:
    """Read exact Level element IDs, names, and elevations with independent failure pagination."""
    try:
        result = client.get_level_catalog(offset=offset, limit=limit,
            failure_offset=failure_offset, failure_limit=failure_limit)
        result.pop("_lora_document_identity", None)
        return result
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Read Revit geometry inventory", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_get_geometry_inventory(offset: int = 0, limit: int = 50,
                                failure_offset: int = 0, failure_limit: int = 50) -> dict:
    """Read a bounded element page and independent failure page; clearance is never inferred."""
    try:
        result = client.get_geometry_inventory(offset=offset, limit=limit,
                                               failure_offset=failure_offset,
                                               failure_limit=failure_limit)
        result.pop("_lora_document_identity", None)
        return result
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Read Revit category inventory", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_get_category_inventory(discipline: str | None = None, category_id: str | None = None,
                                offset: int = 0, limit: int = 50,
                                failure_offset: int = 0, failure_limit: int = 50,
                                category_offset: int = 0, category_limit: int = 50) -> dict:
    """Summarize BuiltInCategory counts and return bounded element details; never claims clearance."""
    try:
        result = client.get_category_inventory(discipline=discipline, category_id=category_id,
            offset=offset, limit=limit, failure_offset=failure_offset, failure_limit=failure_limit,
            category_offset=category_offset, category_limit=category_limit)
        result.pop("_lora_document_identity", None)
        return result
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Read selected Revit element geometry", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_get_element_geometry(element_ids: list[str], entity_offset: int = 0,
                              entity_limit: int = 100) -> dict:
    """Extract bounded geometry evidence for up to 25 explicitly requested element IDs; no clearance test."""
    try:
        result = client.get_geometry_detail(element_ids, entity_offset, entity_limit)
        result.pop("_lora_document_identity", None)
        return result
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def lora_get_views(offset: int = 0, limit: int = 60) -> dict:
    """List non-template, non-sheet views returned by RevitMCP's sheet-placeable views route, with bounded local pagination."""
    try:
        data = client.call("GET", "/sheets/list_views")
        views = data.get("views") or []
        page = paginate(views, offset, limit, data.get("count", len(views)))
        return {"status": "success", **page, "scope": "upstream views excluding templates and drawing sheets"}
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def lora_get_elements(category: str, level: str = "", filters: list[ParameterFilter] | None = None,
                      offset: int = 0, limit: int = 40, include_parameters: bool = False) -> dict:
    """Read category elements, optionally filtered by level/parameters, using bounded result pages."""
    try:
        category = validate_category(category)
        offset = _valid_page(offset, limit)
        if not isinstance(level, str) or len(level) > 120:
            raise RevitMCPError("invalid_input", "level must be a string of at most 120 characters")
        filters = filters or []
        if not isinstance(filters, list) or len(filters) > 20:
            raise RevitMCPError("invalid_input", "filters must be a list containing at most 20 entries")
        if any(not isinstance(item, dict) or not isinstance(item.get("name"), str) or "value" not in item for item in filters):
            raise RevitMCPError("invalid_input", "each filter needs a string name and value")
        if any(len(item["name"]) > 120 or ("operator" in item and not isinstance(item["operator"], str)) for item in filters):
            raise RevitMCPError("invalid_input", "filter names and operators must be bounded strings")
        if any(not isinstance(item["value"], (str, int, float, bool)) or
               (isinstance(item["value"], str) and len(item["value"]) > 4096) for item in filters):
            raise RevitMCPError("invalid_input", "filter values must be scalar and strings may not exceed 4096 characters")
        if not isinstance(include_parameters, bool):
            raise RevitMCPError("invalid_input", "include_parameters must be a boolean")
        if level or filters:
            data = client.call("POST", "/elements/filter", {
                "category_name": category, "level_name": level or None, "parameters": filters,
            })
        else:
            data = client.call("POST", "/get_elements_by_category", {"category_name": category})
        ids = data.get("element_ids") or []
        upstream_total = data.get("count", len(ids))
        start, end = offset, offset + limit
        selected_ids = ids[start:end]
        items = [{"element_id": element_id, "category": data.get("category", category)} for element_id in selected_ids]
        parameter_enrichment_complete = True
        if include_parameters and selected_ids:
            props = client.call("POST", "/elements/get_properties", {
                "element_ids": selected_ids, "include_all_parameters": False,
            })
            if data.get("_lora_document_identity") != props.get("_lora_document_identity"):
                raise RevitMCPError("document_changed", "Active Revit document changed between element and property reads")
            prop_map = {str(item.get("element_id")): item for item in props.get("elements", [])}
            for item in items:
                found = prop_map.get(str(item["element_id"]))
                if found:
                    item["properties"] = found.get("properties", {})
            parameter_enrichment_complete = props.get("count", len(props.get("elements", []))) == len(selected_ids)
        complete_upstream = upstream_total == len(ids)
        complete = end >= upstream_total and complete_upstream
        return {"status": "success", "items": items, "offset": offset, "limit": limit,
                "total": upstream_total, "has_more": end < upstream_total or not complete_upstream,
                "complete": complete, "upstream_complete": complete_upstream,
                "parameter_enrichment_complete": parameter_enrichment_complete,
                "filters": {"level": level or None, "parameters": filters}}
    except Exception as exc:
        return _error(exc)


def _valid_page(offset, limit):
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > 10_000_000:
        raise RevitMCPError("invalid_input", "offset must be an integer from 0 to 10000000")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
        raise RevitMCPError("invalid_input", "limit must be an integer from 1 to 100")
    return offset


@mcp.tool()
def lora_get_element_parameters(element_ids: list[str | int], parameter_names: list[str] | None = None,
                                offset: int = 0, limit: int = 40) -> dict:
    """Read parameter values for element IDs; provenance and read-only state are marked unavailable when upstream omits them."""
    try:
        if not isinstance(element_ids, list) or not element_ids or len(element_ids) > 500:
            raise RevitMCPError("invalid_input", "element_ids must be a non-empty list of at most 500 IDs")
        if any(not isinstance(value, (str, int)) or isinstance(value, bool) for value in element_ids):
            raise RevitMCPError("invalid_input", "element_ids must contain only strings or integers")
        if any(len(str(value)) > 100 for value in element_ids):
            raise RevitMCPError("invalid_input", "element IDs may not exceed 100 characters")
        if parameter_names is not None and (not isinstance(parameter_names, list) or len(parameter_names) > 100):
            raise RevitMCPError("invalid_input", "parameter_names must be a list of at most 100 names")
        if parameter_names is not None and any(not isinstance(name, str) or not name or len(name) > 120 for name in parameter_names):
            raise RevitMCPError("invalid_input", "parameter_names must contain non-empty strings of at most 120 characters")
        _valid_page(offset, limit)
        selected = element_ids[offset:offset + limit]
        payload = {"element_ids": selected, "include_all_parameters": parameter_names is None}
        if parameter_names is not None:
            payload["parameter_names"] = parameter_names
        data = client.call("POST", "/elements/get_properties", payload)
        items = []
        for element in data.get("elements", []):
            normalized = normalize_parameters(element)
            present = {parameter["name"] for parameter in normalized}
            if parameter_names is not None:
                for name in parameter_names:
                    if name not in present:
                        normalized.append({"name": name, "value": None, "storage_type": None,
                                          "has_value": None, "is_numeric": None, "numeric_value": None,
                                          "value_status": "missing", "parameter_scope": "upstream_does_not_report_instance_or_type_source",
                                          "is_read_only": None})
            items.append({"element_id": element.get("element_id"), "error": element.get("error"),
                          "parameters": normalized})
        upstream_complete = data.get("count", len(items)) == len(items)
        complete = offset + len(selected) >= len(element_ids) and upstream_complete
        return {"status": "success", "items": items, "offset": offset, "limit": limit,
                "total": len(element_ids), "has_more": offset + len(selected) < len(element_ids) or not upstream_complete,
                "complete": complete, "upstream_complete": upstream_complete,
                "upstream_parameter_scope": "instance and type values may be merged; source not reported",
                "read_only_parameter_metadata": "not exposed by RevitMCP route"}
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def lora_analyze_model(top_n: int = 200, include_levels: bool = True) -> dict:
    """Read model statistics and identify category/level truncation reported by RevitMCP."""
    try:
        if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1 or top_n > 200:
            raise RevitMCPError("invalid_input", "top_n must be an integer from 1 to 200")
        if not isinstance(include_levels, bool):
            raise RevitMCPError("invalid_input", "include_levels must be a boolean")
        data = client.call("POST", "/model/statistics", {
            "include_levels": bool(include_levels), "include_detailed_types": False, "top_n": top_n,
        })
        categories = data.get("categories") or []
        levels = data.get("levels") or []
        summary = data.get("summary") or {}
        categories_complete = not data.get("categories_truncated", False) and data.get("categories_total", len(categories)) == len(categories)
        levels_complete = not include_levels or summary.get("total_levels", len(levels)) == len(levels)
        return {"status": "success", "summary": summary, "categories": categories, "levels": levels,
                "complete": categories_complete and levels_complete,
                "categories_complete": categories_complete, "levels_complete": levels_complete,
                "omissions": [] if categories_complete and levels_complete else ["upstream category or level response is truncated"]}
    except Exception as exc:
        return _error(exc)


context_engine = BIMContextEngine({
    "lora_get_project_info": lora_get_project_info,
    "lora_get_levels": lora_get_levels,
    "lora_get_views": lora_get_views,
    "lora_get_elements": lora_get_elements,
    "lora_get_element_parameters": lora_get_element_parameters,
    "lora_analyze_model": lora_analyze_model,
    "lora_get_category_inventory": lora_get_category_inventory,
    "lora_get_element_geometry": lora_get_element_geometry,
    "lora_get_level_catalog": lora_get_level_catalog,
})
drawing_pipeline = DrawingProcessingPipeline()
recognition_engine = RecognitionEngine()
calibration_engine = CalibrationEngine()
review_repository = ReviewRepository()
review_manager = DrawingReviewManager(review_repository)
generation_repository = GenerationPlanRepository(review_repository)
generation_planner = GenerationPlanner(review_manager, generation_repository)
generation_preview_repository = GenerationPreviewRepository(review_repository)
generation_preview_engine = GenerationPreviewEngine(generation_planner, generation_preview_repository)
execution_engine = ExecutionEngine(generation_planner, generation_preview_engine,
                                   review_manager, client, execution_repository)


def _context_error(exc):
    if isinstance(exc, ContextError):
        result = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            result["details"] = exc.details
        return {"status": "error", "error": result}
    return _error(exc)


@mcp.tool()
def lora_get_bim_context(view_offset: int = 0, view_limit: int = 60,
                         category: str | None = None, parameter_names: list[str] | None = None,
                         element_offset: int = 0, element_limit: int = 20,
                         top_n: int = 200) -> dict:
    """Assemble a read-only overview of project, levels, views and category statistics; optionally read one category element page with parameters."""
    try:
        return context_engine.build_context(view_offset=view_offset, view_limit=view_limit,
                                            category=category, parameter_names=parameter_names,
                                            element_offset=element_offset, element_limit=element_limit,
                                            top_n=top_n)
    except Exception as exc:
        return _context_error(exc)


@mcp.tool()
def lora_answer_bim_question(question: str, max_pages: int = 20) -> dict:
    """Answer supported natural-language BIM questions from current Revit data or recommend a verified Lora command without executing it."""
    try:
        return context_engine.answer(question, max_pages=max_pages)
    except Exception as exc:
        return _context_error(exc)


@mcp.tool(annotations=ToolAnnotations(title="List Lora ribbon commands", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_list_commands() -> dict:
    """Return the source-verified descriptive registry of Lora ribbon commands without accessing Revit."""
    commands = list_commands()
    return {"status": "success", "source": "verified Lora command registry",
            "commands": commands, "total": len(commands), "not_executed": True}


def _drawing_error(exc):
    if isinstance(exc, DrawingError):
        result = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            result["details"] = exc.details
        return {"status": "error", "error": result}
    return _error(exc)


@mcp.tool()
def lora_inspect_drawing(file_name: str, dpi: int = 100, run_ocr: bool = False,
                         max_dimension: int = 3000) -> dict:
    """Inspect a PDF, JPG/JPEG, PNG, or identify a DWG in the configured local drawing inbox; accepts a file name only, never a path."""
    try:
        return {"status": "success", **drawing_pipeline.inspect(file_name, dpi=dpi,
                                                                  run_ocr=run_ocr,
                                                                  max_dimension=max_dimension)}
    except Exception as exc:
        return _drawing_error(exc)


@mcp.tool()
def lora_process_pdf(file_name: str, dpi: int = 100, run_ocr: bool = False) -> dict:
    """Extract PDF metadata, text and vector summaries and render pages in memory; filename must be in the configured drawing inbox."""
    try:
        return {"status": "success", **drawing_pipeline.process_pdf(file_name, dpi=dpi, run_ocr=run_ocr)}
    except Exception as exc:
        return _drawing_error(exc)


@mcp.tool()
def lora_process_image(file_name: str, max_dimension: int = 3000, run_ocr: bool = False) -> dict:
    """Inspect and prepare a JPG/JPEG/PNG for OCR and edge/line detection; filename must be in the configured drawing inbox."""
    try:
        return {"status": "success", **drawing_pipeline.process_image(file_name,
                                                                         max_dimension=max_dimension,
                                                                         run_ocr=run_ocr)}
    except Exception as exc:
        return _drawing_error(exc)


@mcp.tool()
def lora_get_drawing_summary(processing_id: str) -> dict:
    """Retrieve a bounded in-memory result from an earlier drawing inspection in this server process."""
    try:
        return {"status": "success", **drawing_pipeline.get_summary(processing_id)}
    except Exception as exc:
        return _drawing_error(exc)


@mcp.tool()
def lora_get_drawing_render(processing_id: str, page_number: int = 1):
    """Return a processed page/image as MCP image content from the bounded process-memory cache; no image file is persisted."""
    try:
        render = drawing_pipeline.get_render(processing_id, page_number)
        return MCPImage(data=render["data"], format="png")
    except Exception as exc:
        return _drawing_error(exc)


@mcp.tool()
def lora_extract_drawing_geometry(file_name: str, dpi: int = 100,
                                  max_dimension: int = 3000) -> dict:
    """Extract bounded PDF vector geometry or raster edges, lines, components and contours from the configured inbox."""
    try:
        result = drawing_pipeline.inspect(file_name, dpi=dpi, max_dimension=max_dimension)
        return {"status": "success", **result}
    except Exception as exc:
        return _drawing_error(exc)


def _geometry_pages(result, page_number=None):
    if result.get("file_type") not in ("pdf", "jpeg", "png"):
        raise DrawingError("geometry_unavailable", "Native geometry extraction is unavailable for this file type")
    if page_number is not None and (isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1):
        raise DrawingError("invalid_page_number", "page_number must be a positive integer")
    if result.get("file_type") == "pdf":
        pages = result.get("pages", [])
        if page_number is not None:
            pages = [page for page in pages if page.get("page_number") == page_number]
            if not pages:
                raise DrawingError("page_not_found", "The requested page number is not present in this drawing")
        return [(page["page_number"], page.get("geometry_extraction", {}).get("entities", []),
                 page.get("width"), page.get("height"), page.get("coordinate_system")) for page in pages]
    if page_number not in (None, 1):
        raise DrawingError("page_not_found", "Raster image processing has only page 1")
    image = result.get("image", {})
    return [(1, result.get("geometry_entities", []), image.get("width_pixels"),
             image.get("height_pixels"), image.get("coordinate_system"))]


@mcp.tool()
def lora_analyze_drawing_geometry(processing_id: str, page_number: int | None = None,
                                  parallel_tolerance_degrees: float = 3.0,
                                  perpendicular_tolerance_degrees: float = 3.0,
                                  intersection_tolerance: float = 0.25,
                                  endpoint_tolerance: float = 2.0,
                                  max_pairs: int = 100000) -> dict:
    """Analyze line relationships and geometry groups using source-coordinate tolerances; requires a prior extraction ID."""
    try:
        tolerances = _geometry_tolerances(parallel_tolerance_degrees,
                                           perpendicular_tolerance_degrees,
                                           intersection_tolerance, endpoint_tolerance)
        if isinstance(max_pairs, bool) or not isinstance(max_pairs, int) or not 1 <= max_pairs <= 100000:
            raise DrawingError("invalid_input", "max_pairs must be an integer from 1 to 100000")
        result = drawing_pipeline.get_summary(processing_id)
        pages = []
        for number, entities, width, height, coords in _geometry_pages(result, page_number):
            analysis = analyze_relationships(entities, tolerances=tolerances, max_pairs=max_pairs)
            group_result = group_geometry(entities, tolerances["endpoint_proximity_units"],
                                          return_status=True)
            analysis["geometry_groups"] = group_result["groups"]
            analysis["group_analysis_complete"] = group_result["complete"]
            analysis["group_pair_checks"] = group_result["pair_checks"]
            analysis["warnings"] = []
            if not group_result["complete"]:
                analysis["complete"] = False
                analysis["warnings"].append("geometry grouping reached its configured pair-check limit")
            if analysis.get("truncated"):
                analysis["warnings"].append("relationship analysis reached its configured pair-check or output limit")
            analysis.update({"page_number": number, "coordinate_system": coords,
                             "geometry_entity_count": len(entities)})
            pages.append(analysis)
        return {"status": "success", "processing_id": processing_id, "file_name": result["file_name"],
                "pages": pages, "complete": all(page["complete"] for page in pages),
                "warnings": ["Relationship tolerances use source-coordinate units, not assumed millimetres."]}
    except Exception as exc:
        return _drawing_error(exc)


@mcp.tool()
def lora_get_architectural_candidates(processing_id: str, page_number: int | None = None) -> dict:
    """Return conservative unconfirmed wall/opening/junction/room/dimension geometry candidates."""
    try:
        result = drawing_pipeline.get_summary(processing_id)
        outputs = []
        total_candidates = 0
        stopped_early = False
        for number, entities, width, height, _ in _geometry_pages(result, page_number):
            if total_candidates >= 5000:
                stopped_early = True
                break
            relation = analyze_relationships(entities)
            outputs.append(detect_architectural_candidates(result["file_name"], number,
                                                            width or 1, height or 1, entities, relation,
                                                            max_candidates=min(500, 5000 - total_candidates)))
            total_candidates += outputs[-1]["candidate_count"]
        candidates = [candidate for page in outputs for candidate in page["candidates"]]
        return {"status": "success", "processing_id": processing_id,
                "source_file": result["file_name"], "pages": outputs,
                "candidate_count": len(candidates), "candidates": candidates,
                "complete": not stopped_early and all(page["complete"] for page in outputs),
                "warnings": ["Candidates are hypotheses and must be reviewed; no walls, doors, windows, rooms or dimensions are confirmed."] +
                            (["Global candidate output cap was reached."] if stopped_early else [])}
    except Exception as exc:
        return _drawing_error(exc)


def _recognition_error(exc):
    if isinstance(exc, RecognitionError):
        return {"status": "error", "error": {"code": exc.code, "message": str(exc)}}
    logging.getLogger("lora_recognition").exception("Recognition tool failed (%s)", type(exc).__name__)
    return {"status": "error", "error": {"code": "internal_error", "message": "Recognition failed"}}


@mcp.tool()
def lora_recognize_architecture(processing_id: str, page_number: int | None = None) -> dict:
    """Recognize cautious architectural hypotheses from a processed drawing; never creates Revit elements."""
    try:
        source = drawing_pipeline.get_summary(processing_id)
        return {"status": "success", **recognition_engine.recognize(source, page_number)}
    except Exception as exc:
        return _recognition_error(exc)


@mcp.tool()
def lora_get_recognized_elements(recognition_id: str, category: str | None = None,
                                 page_number: int | None = None, offset: int = 0,
                                 limit: int = 100) -> dict:
    """Return a bounded page of stored recognition hypotheses, optionally filtered by category/page."""
    try:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise RecognitionError("invalid_pagination", "offset must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise RecognitionError("invalid_pagination", "limit must be an integer from 1 to 500")
        if page_number is not None and (isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1):
            raise RecognitionError("invalid_page_number", "page_number must be a positive integer")
        result = recognition_engine.store.get(recognition_id)
        items = result["elements"]
        if category:
            items = [item for item in items if item["proposed_category"].casefold() == category.casefold()]
        if page_number is not None:
            items = [item for item in items if item["page_number"] == page_number]
        return {"status": "success", "recognition_id": recognition_id,
                "total": len(items), "offset": offset, "limit": limit,
                "items": items[offset:offset + limit], "complete": result["complete"],
                "truncated": offset + limit < len(items), "warnings": result["warnings"]}
    except Exception as exc:
        return _recognition_error(exc)


@mcp.tool()
def lora_analyze_recognition(recognition_id: str) -> dict:
    """Summarize recognition hypotheses by category and review status."""
    try:
        result = recognition_engine.store.get(recognition_id)
        categories, statuses = {}, {}
        for element in result["elements"]:
            category = element["proposed_category"]
            status = element["review_status"]
            categories[category] = categories.get(category, 0) + 1
            statuses[status] = statuses.get(status, 0) + 1
        return {"status": "success", "recognition_id": recognition_id,
                "source_file": result["source_file"], "page_count": len(result["pages"]),
                "element_count": result["element_count"], "counts_by_category": categories,
                "counts_by_review_status": statuses, "complete": result["complete"],
                "ocr_runtime": result["ocr_runtime"], "ai_provider": result["ai_provider"],
                "calibration": result["calibration"], "warnings": result["warnings"]}
    except Exception as exc:
        return _recognition_error(exc)


@mcp.tool()
def lora_get_recognition_warnings(recognition_id: str) -> dict:
    """Return global and per-page completeness, OCR and recognition warnings."""
    try:
        result = recognition_engine.store.get(recognition_id)
        return {"status": "success", "recognition_id": recognition_id,
                "complete": result["complete"], "warnings": result["warnings"],
                "pages": [{"page_number": page["page_number"],
                           "recognition_complete": page["recognition_complete"],
                           "text_extraction_complete": page["text_extraction_complete"],
                           "ocr_status": page["ocr_status"], "warnings": page["warnings"]}
                          for page in result["pages"]]}
    except Exception as exc:
        return _recognition_error(exc)


def _calibration_error(exc):
    if isinstance(exc, (CalibrationError, RecognitionError, DrawingError)):
        details = {"code": exc.code, "message": str(exc)}
        if getattr(exc, "details", None) is not None:
            details["details"] = exc.details
        return {"status": "error", "error": details}
    logging.getLogger("lora_calibration").exception("Calibration tool failed (%s)", type(exc).__name__)
    return {"status": "error", "error": {"code": "internal_error", "message": "Calibration failed"}}


@mcp.tool()
def lora_calibrate_drawing(processing_id: str, recognition_id: str, page_number: int = 1,
                           explicit_scale_mm_per_source_unit: float | None = None,
                           calibration_references: list[dict] | None = None,
                           independent_xy: bool = False, origin: list[float] | None = None,
                           rotation_degrees: float = 0.0, target_unit: str = "mm",
                           consistency_tolerance: float = 0.025,
                           alignment_references: list[dict] | None = None) -> dict:
    """Calibrate one drawing page from explicit user scale or user-confirmed dimension references; no Revit operations."""
    try:
        drawing = drawing_pipeline.get_summary(processing_id)
        recognition = recognition_engine.store.get(recognition_id)
        result = calibration_engine.calibrate(drawing, recognition, page_number,
            explicit_scale_mm_per_source_unit=explicit_scale_mm_per_source_unit,
            calibration_references=calibration_references, independent_xy=independent_xy,
            origin=origin, rotation_degrees=rotation_degrees, target_unit=target_unit,
            consistency_tolerance=consistency_tolerance,
            alignment_references=alignment_references)
        return {"status": "success", **result}
    except Exception as exc:
        return _calibration_error(exc)


@mcp.tool()
def lora_get_calibration_result(calibration_id: str) -> dict:
    """Retrieve a prior page-local calibration result from bounded process memory."""
    try:
        return {"status": "success", **calibration_engine.store.get(calibration_id)}
    except Exception as exc:
        return _calibration_error(exc)


@mcp.tool()
def lora_get_reconstructed_spatial_geometry(calibration_id: str, category: str | None = None,
                                              offset: int = 0, limit: int = 100) -> dict:
    """Retrieve a bounded page of reconstructed geometry in calibrated coordinates."""
    try:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise CalibrationError("invalid_pagination", "offset must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise CalibrationError("invalid_pagination", "limit must be an integer from 1 to 500")
        result = calibration_engine.store.get(calibration_id)
        items = result["spatial_geometry"]
        if category:
            items = [item for item in items if item["proposed_category"].casefold() == category.casefold()]
        return {"status": "success", "calibration_id": calibration_id,
                "target_units": result["target_units"], "total": len(items),
                "offset": offset, "limit": limit, "items": items[offset:offset + limit],
                "truncated": offset + limit < len(items),
                "calibration_complete": result["calibration_complete"],
                "warnings": result["warnings"]}
    except Exception as exc:
        return _calibration_error(exc)


@mcp.tool()
def lora_validate_calibration_references(processing_id: str, recognition_id: str,
                                         page_number: int = 1,
                                         calibration_references: list[dict] | None = None) -> dict:
    """Validate calibration input shape, source segment correspondence and text association without accepting unconfirmed scale evidence."""
    try:
        drawing = drawing_pipeline.get_summary(processing_id)
        recognition = recognition_engine.store.get(recognition_id)
        result = calibration_engine.validate_references(drawing, recognition, page_number,
                                                        calibration_references or [])
        return {"status": "success", "processing_id": processing_id,
                "recognition_id": recognition_id, **result}
    except Exception as exc:
        return _calibration_error(exc)


def _review_error(exc):
    if isinstance(exc, GenerationPlanError):
        return _error(exc)
    if isinstance(exc, RevitMCPError):
        return _error(exc)
    if isinstance(exc, ReviewError):
        error = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            error["details"] = exc.details
        return {"status": "error", "error": error}
    logging.getLogger("lora_review").exception("Review tool failed (%s)", type(exc).__name__)
    return {"status": "error", "error": {"code": "internal_error", "message": "Drawing review operation failed"}}


@mcp.tool()
def lora_create_drawing_review(recognition_id: str, calibration_ids: list[str] | None = None,
                               title: str = "", reviewer: str = "") -> dict:
    """Create a persistent review session from existing recognition and optional calibration records; no drawing files are copied."""
    try:
        recognition = recognition_engine.store.get(recognition_id)
        calibrations = [calibration_engine.store.get(item) for item in (calibration_ids or [])]
        created = review_manager.create(recognition, calibrations, reviewer, title)
        return {"status": "success", **created,
                "persistence": {"storage_path": str(review_repository.path),
                                "schema_version": REVIEW_SCHEMA_VERSION, "drawings_copied": False}}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Create explicit wall geometry review", readOnlyHint=False,
                                     destructiveHint=False, idempotentHint=False))
def lora_create_explicit_wall_review(coordinate_system: str, units: str, start: list[float],
                                     end: list[float], explicit_length: float,
                                     origin_description: str, reviewer: str = "", title: str = "") -> dict:
    """Persist user-entered wall geometry as pending review; does not imply approval or touch Revit."""
    try:
        result = review_manager.create_explicit_wall_review(coordinate_system, units, start, end,
            explicit_length, origin_description, reviewer, title)
        return {"status": "success", **result,
                "persistence": {"storage_path": str(review_repository.path),
                    "schema_version": REVIEW_SCHEMA_VERSION, "source_drawing_bytes_stored": False}}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Approve explicit wall geometry", readOnlyHint=False,
                                     destructiveHint=False, idempotentHint=False))
def lora_approve_explicit_wall_geometry(review_id: str, item_id: str,
                                        geometry_confirmation_statement: str,
                                        reviewer: str = "") -> dict:
    """Record explicit confirmation of the exact current geometry statement and fingerprint."""
    try:
        return {"status": "success", "review_id": review_id,
                "item": review_manager.approve_explicit_geometry(
                    review_id, item_id, geometry_confirmation_statement, reviewer)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_drawing_review(review_id: str) -> dict:
    """Retrieve a review session, summary, page list and pointers to existing in-memory drawing renders."""
    try:
        return {"status": "success", **review_manager.session(review_id)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_review_items(review_id: str, status: str | None = None,
                          category: str | None = None, page_number: int | None = None,
                          offset: int = 0, limit: int = 100) -> dict:
    """Retrieve review items with status/category/page filters and bounded pagination."""
    try:
        return {"status": "success", "review_id": review_id,
                **review_manager.list_items(review_id, status, category, page_number, offset, limit)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_review_item(review_id: str, item_id: str) -> dict:
    """Retrieve one review item with original/current geometry and its item history."""
    try:
        return {"status": "success", "review_id": review_id,
                "item": review_manager.get_item(review_id, item_id)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_update_review_item(review_id: str, item_id: str,
                            properties: dict | None = None, geometry: dict | None = None,
                            status: str | None = None, reviewer: str = "",
                            reason: str = "") -> dict:
    """Correct validated properties/geometry or set pending, needs_correction, or unresolved; originals are retained."""
    try:
        item = review_manager.update(review_id, item_id, properties, geometry, status, reviewer, reason)
        return {"status": "success", "review_id": review_id, "item": item}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_approve_review_item(review_id: str, item_id: str,
                             reviewer: str = "", reason: str = "") -> dict:
    """Explicitly approve one review item; stale calibration blocks approval and missing calibration is reported."""
    try:
        return {"status": "success", "review_id": review_id,
                "item": review_manager.approve(review_id, item_id, reviewer, reason)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_reject_review_item(review_id: str, item_id: str, reason: str,
                            reviewer: str = "") -> dict:
    """Reject one review item with a required reason; rejected items are excluded from approved status counts."""
    try:
        return {"status": "success", "review_id": review_id,
                "item": review_manager.reject(review_id, item_id, reason, reviewer)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_review_history(review_id: str, item_id: str | None = None,
                            offset: int = 0, limit: int = 100) -> dict:
    """Retrieve persistent session or item review history with pagination."""
    try:
        return {"status": "success", "review_id": review_id,
                **review_manager.history(review_id, item_id, offset, limit)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_review_summary(review_id: str) -> dict:
    """Summarize review counts by status/category and identify calibration readiness warnings."""
    try:
        return {"status": "success", **review_manager.summary(review_id)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_validate_drawing_review(review_id: str, calibration_ids: list[str] | None = None) -> dict:
    """Compare supplied current calibrations with the session snapshot and persist stale-review flags when they changed."""
    try:
        calibrations = [calibration_engine.store.get(item) for item in (calibration_ids or [])]
        return {"status": "success", **review_manager.validate(review_id, calibrations)}
    except Exception as exc:
        return _review_error(exc)


def _generation_inventory():
    return read_generation_inventory(client)


def _generation_inventory_or_unavailable():
    try:
        return _generation_inventory()
    except RevitMCPError as exc:
        return {"status": "unavailable", "inventory_source": "live_revitmcp",
                "verified_at": None, "document_identity": None, "levels": [], "level_names": [],
                "family_types": [], "family_types_truncated": True,
                "system_types_by_category": {}, "parameter_names": [],
                "warnings": ["Live RevitMCP inventory unavailable: {}.".format(exc.code)],
                "error": exc.as_dict()}


@mcp.tool()
def lora_create_generation_plan(review_id: str, mappings: dict | None = None,
                                coordinate_mapping: dict | None = None) -> dict:
    """Create and persist a deterministic plan from approved, currently calibrated review items; Revit queries are read-only."""
    try:
        plan = generation_planner.create(review_id, _generation_inventory(), mappings, coordinate_mapping)
        return {"status": "success", "plan": plan,
                "persistence": {"storage_path": str(review_repository.path),
                                "schema_version": REVIEW_SCHEMA_VERSION,
                                "history_preserved": True, "revit_write_operations": False}}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_generation_plan(plan_id: str) -> dict:
    """Retrieve a persisted generation plan and its mapping/revalidation history without contacting Revit."""
    try:
        return {"status": "success", "plan": generation_planner.get(plan_id)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_validate_generation_plan(plan_id: str) -> dict:
    """Revalidate mappings against the current active document using read-only Revit queries only."""
    try:
        return {"status": "success", "plan": generation_planner.validate(plan_id, _generation_inventory())}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_generation_plan_items(plan_id: str, category: str | None = None,
                                   status: str | None = None, offset: int = 0,
                                   limit: int = 100) -> dict:
    """List planned items with category/status filters and bounded pagination."""
    try:
        return {"status": "success", **generation_planner.items(plan_id, category, status, offset, limit)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_update_generation_mapping(plan_id: str, review_item_id: str, mapping: dict) -> dict:
    """Persist an explicit mapping change, preserve its history, and revalidate against read-only Revit inventory."""
    try:
        return {"status": "success", "plan": generation_planner.update_mapping(
            plan_id, review_item_id, mapping, _generation_inventory())}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_generation_plan_summary(plan_id: str) -> dict:
    """Summarize persisted plan readiness, unresolved sources, validation issues and counts."""
    try:
        return {"status": "success", **generation_planner.summary(plan_id)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_create_generation_preview(plan_id: str) -> dict:
    """Persist a proposed-element preview, validating with live Revit reads when reachable."""
    try:
        preview = generation_preview_engine.create(plan_id, _generation_inventory_or_unavailable())
        return {"status": "success", "preview": preview,
                "persistence": {"storage_path": str(review_repository.path),
                                "schema_version": REVIEW_SCHEMA_VERSION,
                                "source_drawing_bytes_stored": False}}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_generation_preview(preview_id: str) -> dict:
    """Retrieve a stable preview snapshot and dynamically marked plan staleness without contacting Revit."""
    try:
        return {"status": "success", "preview": generation_preview_engine.get(preview_id)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_generation_preview_items(preview_id: str, category: str | None = None,
                                      status: str | None = None, offset: int = 0,
                                      limit: int = 100) -> dict:
    """Retrieve bounded preview items by category or validation status."""
    try:
        return {"status": "success", **generation_preview_engine.items(preview_id, category, status, offset, limit)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_validate_generation_preview(preview_id: str) -> dict:
    """Create a fresh validation snapshot for the same source plan while preserving the old preview."""
    try:
        return {"status": "success", "preview": generation_preview_engine.validate(
            preview_id, _generation_inventory_or_unavailable())}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_get_generation_preview_summary(preview_id: str) -> dict:
    """Summarize preview counts, validation findings and live inventory verification state."""
    try:
        return {"status": "success", **generation_preview_engine.summary(preview_id)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool()
def lora_compare_generation_previews(first_preview_id: str, second_preview_id: str) -> dict:
    """Compare two persisted preview snapshots without contacting Revit."""
    try:
        return {"status": "success", **generation_preview_engine.compare(first_preview_id, second_preview_id)}
    except Exception as exc:
        return _review_error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Prepare wall execution", readOnlyHint=False,
                                     destructiveHint=False, idempotentHint=False))
def lora_prepare_execution(preview_id: str, wall_constraints: dict) -> dict:
    """Prepare (without modifying Revit) a single-use confirmation request for a fully validated Basic Wall preview."""
    try:
        request = execution_engine.prepare(preview_id, wall_constraints)
        return {"status": "success", "execution_preview": request,
                "confirmation_mechanism": {
                    "required_tool_call": "lora_execute_walls",
                    "required_approval_text": request["approval_text"],
                    "required_one_time_nonce": request["approval_nonce"],
                    "expires_at": request["expires_at"],
                    "mcp_destructive_hint": True,
                    "user_must_review": ["target document", "wall count", "wall type and thickness",
                        "base levels", "centerline coordinates", "height", "base offset", "orientation",
                        "structural setting", "parameter assignments", "validation warnings"]}}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Get wall execution preview", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_get_execution_preview(request_id: str) -> dict:
    """Retrieve the prepared wall execution snapshot and exact user confirmation requirements."""
    try:
        return {"status": "success", "execution_preview": execution_engine.result(request_id)}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Validate wall execution", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_validate_execution(request_id: str) -> dict:
    """Recheck preview freshness, approvals, calibration, target document session, wall types, and levels."""
    try:
        return {"status": "success", "validation": execution_engine.validate(request_id)}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Create approved Revit walls", readOnlyHint=False,
                                     destructiveHint=True, idempotentHint=False))
async def lora_execute_walls(request_id: str, ctx: Context) -> dict:
    """Ask the connected MCP client for human confirmation, then create only the prepared Basic Walls."""
    try:
        request = execution_engine.result(request_id)
        if request.get("state") != "prepared":
            raise ExecutionError("execution_request_consumed", "Only a prepared, unused execution request can be confirmed")
        details = []
        resolved_by_id = {item.get("planned_element_id"): item for item in request.get("validated_walls", [])}
        for wall in request.get("walls", []):
            resolved = resolved_by_id.get(wall["planned_element_id"], {})
            vertical = ("top {} ({}) top offset {:.4f} ft".format(resolved.get("top_level_name"),
                        resolved.get("top_level_id"), wall.get("top_offset_internal_feet", 0.0))
                        if wall.get("top_constraint") else
                        "unconnected height {:.4f} ft".format(wall.get("unconnected_height_internal_feet")))
            details.append("- {} | source {} page {} | {} type ID {} (thickness {:.4f} ft) | base {} ID {} | line {} | {} | base offset {:.4f} ft | structural={} flip={} | parameters {}".format(
                wall["planned_element_id"], wall.get("source_drawing"), wall.get("source_page"),
                wall["wall_type_name"], wall.get("wall_type_id"), resolved.get("wall_thickness_internal_feet"),
                wall["base_level_name"], (wall.get("base_constraint") or {}).get("element_id"),
                wall["centerline_internal_feet"], vertical,
                wall["base_offset_internal_feet"], wall["structural"], wall["flip"],
                wall["parameter_assignments"]))
        warning_text = json.dumps(request.get("validation_warnings", []), ensure_ascii=False)
        prompt = ("CONFIRM REVIT WALL CREATION\nDocument: {}\nPath: {}\nRevit version: {}\n"
                  "Basic Walls: {}\nSource/type/thickness/base and top constraints/geometry/height/offset/orientation/structural/parameters:\n{}\n"
                  "Validation warnings: {}\n\nTo approve, enter the exact approval text and one-time nonce shown below. "
                  "Decline or cancel to make no Revit changes.\nApproval text: {}\nNonce: {}\n"
                  "The single Revit transaction rolls back all created walls if creation or in-transaction verification fails.").format(
                      request.get("document_display"), request["target_identity"].get("document_path"),
                      request.get("revit_version"), request.get("wall_count"), "\n".join(details),
                      warning_text, request.get("approval_text"), request.get("approval_nonce"))
        confirmation = await ctx.elicit(prompt, WallExecutionApproval)
        if confirmation.action != "accept" or confirmation.data is None:
            return {"status": "cancelled", "execution_performed": False,
                    "message": "The user did not approve Revit wall creation."}
        result = execution_engine.execute(request_id, confirmation.data.approval_nonce,
                                          confirmation.data.approval_text)
        tool_status = ("success" if result.get("state") == "succeeded" else
                       "cancelled" if result.get("state") == "cancelled" else "error")
        return {"status": tool_status,
                "execution": result}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Get Revit wall execution result", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_get_execution_result(request_id: str) -> dict:
    """Retrieve local execution history, transaction outcome, created element IDs, and source review links."""
    try:
        return {"status": "success", "execution_result": execution_engine.result(request_id)}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations=ToolAnnotations(title="Verify created Revit walls", readOnlyHint=True,
                                     destructiveHint=False, idempotentHint=True))
def lora_verify_created_elements(request_id: str) -> dict:
    """Read back created wall IDs, type, level, height, length, thickness, location, and local source links."""
    try:
        return {"status": "success", "verification": execution_engine.verify(request_id)}
    except Exception as exc:
        return _error(exc)


def _geometry_tolerances(parallel_degrees, perpendicular_degrees, intersection, endpoint):
    values = (parallel_degrees, perpendicular_degrees, intersection, endpoint)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise DrawingError("invalid_input", "Geometry tolerances must be numeric")
    if any(not math.isfinite(float(value)) for value in values):
        raise DrawingError("invalid_input", "Geometry tolerances must be finite numbers")
    if not 0 <= parallel_degrees <= 15 or not 0 <= perpendicular_degrees <= 15:
        raise DrawingError("invalid_input", "Angle tolerances must be between 0 and 15 degrees")
    if intersection < 0 or endpoint < 0 or intersection > 10000 or endpoint > 10000:
        raise DrawingError("invalid_input", "Distance tolerances must be between 0 and 10000 source units")
    return {"parallel_angle_degrees": float(parallel_degrees),
            "perpendicular_angle_degrees": float(perpendicular_degrees),
            "intersection_units": float(intersection),
            "endpoint_proximity_units": float(endpoint)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
