"""Read-only BIM context assembly and deterministic natural-language routing.

The engine receives the six existing Lora MCP tool callables. It contains no Revit
API or HTTP client and never dispatches a ribbon command.
"""

import re

from .registry import COMMANDS


INTERNAL_FEET_TO_MM = 304.8
PAGE_SIZE = 100
MAX_RESULT_IDS = 200
MAX_QUESTION_LENGTH = 500
KNOWN_LIMITATIONS = [
    "The RevitMCP view route omits view templates and drawing sheets.",
    "The RevitMCP element routes return the full matching ID list upstream; Lora pages locally and reports upstream count mismatches.",
    "RevitMCP parameter reads can merge instance and type values and do not report parameter read-only state or reliable source scope.",
    "RevitMCP levels are returned through model statistics; elevations originate in internal feet.",
    "Document identity is title, path, and Revit process ID; RevitMCP does not expose a stable document GUID.",
]

CATEGORY_ALIASES = {
    "structural columns": "Columns", "structural column": "Columns",
    "columns": "Columns", "column": "Columns",
    "structural framing": "Structural Framing", "beams": "Structural Framing", "beam": "Structural Framing",
    "walls": "Walls", "wall": "Walls", "doors": "Doors", "door": "Doors",
    "windows": "Windows", "window": "Windows", "floors": "Floors", "floor": "Floors",
    "ceilings": "Ceilings", "ceiling": "Ceilings", "roofs": "Roofs", "roof": "Roofs",
    "ducts": "Ducts", "duct": "Ducts", "pipes": "Pipes", "pipe": "Pipes",
    "rooms": "Rooms", "room": "Rooms", "furniture": "Furniture",
}

COMMAND_TRIGGERS = (
    ("Family Generator", ("family generator", "generate a family", "create a family", "new family")),
    ("Family Version Audit", ("family version audit", "inspect family version", "rfa version", "family saved version", "family compatibility audit", "check family compatibility", "analyze rfa compatibility")),
    ("Tag Studio", ("tag studio", "annotation tag design", "inspect tag family")),
    ("Auto Dim", ("auto dim", "dimension walls", "create dimensions", "add dimensions")),
    ("Data Sync", ("data sync", "export schedule", "import schedule", "sync schedule")),
    ("Draw Area Boundary", ("draw area boundary", "area boundary", "area boundary line")),
    ("TableGen", ("tablegen", "csv table", "import csv table")),
    ("InPlace to Family", ("in-place to family", "in place to family", "convert in-place", "convert in place")),
    ("Tag Creation", ("tag creation", "mep tag setup", "parameter generator", "set up mep tags")),
    ("Refresh", ("refresh mep", "update mep elevation", "update mep elevations")),
    ("Hide Section", ("hide section", "hide section markers")),
    ("View Range", ("view range", "edit view range")),
)


class ContextError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        result = {"code": self.code, "message": str(self)}
        if self.details is not None:
            result["details"] = self.details
        return result


def _norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def normalize_level(level):
    """Preserve internal feet and add a metric value using the exact conversion."""
    result = dict(level)
    feet = result.get("elevation_internal_feet")
    if isinstance(feet, (int, float)) and not isinstance(feet, bool):
        result["elevation_mm"] = feet * INTERNAL_FEET_TO_MM
        result["elevation_conversion"] = "Revit internal feet × 304.8 = millimetres"
    else:
        result["elevation_mm"] = None
        result["elevation_conversion"] = "unavailable: upstream did not return internal feet"
    result["elevation_units"] = {"internal": "feet", "display": "millimetres"}
    return result


def _validate_page_limit(limit, name="limit", maximum=200):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > maximum:
        raise ContextError("invalid_input", "{} must be an integer from 1 to {}".format(name, maximum))


def _validate_max_pages(max_pages):
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 100:
        raise ContextError("invalid_input", "max_pages must be an integer from 1 to 100")


class BIMContextEngine:
    """Compose existing read-only MCP operations; dependency injection keeps it testable."""

    REQUIRED_TOOLS = (
        "lora_get_project_info", "lora_get_levels", "lora_get_views",
        "lora_get_elements", "lora_get_element_parameters", "lora_analyze_model",
    )
    OPTIONAL_TOOLS = ("lora_get_element_geometry", "lora_get_category_inventory",
                      "lora_get_level_catalog")

    def __init__(self, tools):
        missing = [name for name in self.REQUIRED_TOOLS if name not in tools]
        if missing:
            raise ValueError("Missing required read-only tool callables: {}".format(", ".join(missing)))
        self.tools = tools

    def _has_tool(self, name):
        if callable(getattr(self.tools, name, None)):
            return True
        try:
            return name in self.tools
        except Exception:
            try:
                return callable(self.tools[name])
            except Exception:
                return False

    def _call(self, tool_name, **kwargs):
        result = self.tools[tool_name](**kwargs)
        if not isinstance(result, dict):
            raise ContextError("invalid_tool_response", "{} returned a non-object response".format(tool_name))
        if result.get("status") != "success":
            error = result.get("error") or {}
            if isinstance(error, dict):
                raise ContextError(error.get("code", "tool_error"), error.get("message", "{} failed".format(tool_name)), error.get("details"))
            raise ContextError("tool_error", "{} failed".format(tool_name), error)
        return result

    def _project_identity(self):
        info = self._call("lora_get_project_info")
        return info.get("document_identity"), info

    @staticmethod
    def _same_identity(before, after):
        if before is None or after is None:
            return before is after
        return before == after

    def build_context(self, view_offset=0, view_limit=60, category=None,
                      parameter_names=None, element_offset=0, element_limit=20,
                      top_n=200):
        """Assemble current project, level, view, category, and optional element context."""
        _validate_page_limit(view_limit, "view_limit")
        _validate_page_limit(element_limit, "element_limit", 100)
        _validate_page_limit(top_n, "top_n")
        if isinstance(view_offset, bool) or not isinstance(view_offset, int) or view_offset < 0:
            raise ContextError("invalid_input", "view_offset must be a non-negative integer")
        if isinstance(element_offset, bool) or not isinstance(element_offset, int) or element_offset < 0:
            raise ContextError("invalid_input", "element_offset must be a non-negative integer")
        if category is not None and (not isinstance(category, str) or not category.strip()):
            raise ContextError("invalid_input", "category must be a non-empty string when provided")
        if parameter_names is not None and (not isinstance(parameter_names, list) or len(parameter_names) > 100 or
                                            any(not isinstance(name, str) or not name for name in parameter_names)):
            raise ContextError("invalid_input", "parameter_names must be a list of at most 100 non-empty strings")

        initial_identity, project = self._project_identity()
        levels = self._call("lora_get_levels", offset=0, limit=200)
        views = self._call("lora_get_views", offset=view_offset, limit=view_limit)
        analysis = self._call("lora_analyze_model", top_n=top_n, include_levels=True)

        level_records = [normalize_level(item) for item in levels.get("items", [])]
        category_records = analysis.get("categories", [])
        element_context = {"state": "not_requested", "items": [], "complete": False,
                           "message": "Pass category to retrieve an element page and optional parameters."}
        if category:
            elements = self._call("lora_get_elements", category=category, offset=element_offset,
                                  limit=element_limit, include_parameters=False)
            element_items = list(elements.get("items", []))
            parameter_result = None
            if element_items:
                parameter_result = self._call(
                    "lora_get_element_parameters",
                    element_ids=[item.get("element_id") for item in element_items],
                    parameter_names=parameter_names,
                    offset=0,
                    limit=min(100, len(element_items)),
                )
            parameter_map = {}
            if parameter_result:
                for item in parameter_result.get("items", []):
                    parameter_map[str(item.get("element_id"))] = item.get("parameters", [])
            for item in element_items:
                item["parameters"] = parameter_map.get(str(item.get("element_id")), [])
            element_context = {
                "state": "retrieved",
                "category": category,
                "items": element_items,
                "total": elements.get("total"),
                "offset": elements.get("offset"),
                "limit": elements.get("limit"),
                "has_more": elements.get("has_more"),
                "complete": bool(elements.get("complete")) and (parameter_result is None or bool(parameter_result.get("complete"))),
                "element_page_complete": elements.get("complete"),
                "parameter_page_complete": parameter_result.get("complete") if parameter_result else True,
            }

        final_identity, _final_project = self._project_identity()
        identity_stable = self._same_identity(initial_identity, final_identity)
        if not identity_stable:
            raise ContextError("document_changed", "Active Revit document changed while assembling BIM context")

        completeness = {
            "project": initial_identity is not None,
            "levels": bool(levels.get("complete")),
            "views_page": bool(views.get("upstream_complete")) and not bool(views.get("has_more")),
            "categories": bool(analysis.get("categories_complete")),
            "statistics": analysis.get("status") == "success",
            "element_parameters": element_context["complete"] if category else None,
            "identity_stable": identity_stable,
            "requested_view_page_complete": bool(views.get("complete")),
        }
        requested_sections_complete = all(
            value for key, value in completeness.items()
            if key not in ("element_parameters", "views_page", "requested_view_page_complete")
        ) and bool(views.get("complete"))
        if category:
            requested_sections_complete = requested_sections_complete and bool(element_context["complete"])
        warnings = []
        if views.get("has_more"):
            warnings.append("View listing contains more pages; this context includes only the requested view page.")
        if not views.get("upstream_complete", True):
            warnings.append("RevitMCP view result count did not match returned records.")
        if not analysis.get("categories_complete"):
            warnings.append("Category statistics are truncated upstream.")
        if category and element_context.get("has_more"):
            warnings.append("Element context is a page, not a complete category inventory.")
        if category and not element_context.get("complete"):
            warnings.append("Element parameters or element page are incomplete.")

        return {
            "status": "success",
            "complete": requested_sections_complete,
            "document_identity": initial_identity,
            "project": {key: project.get(key) for key in (
                "project_name", "project_number", "status_value", "document_identity", "revit_version",
                "revit_version_availability",
            )},
            "levels": {"items": level_records, "total": levels.get("total"),
                       "complete": levels.get("complete"), "source_route": "/model/statistics"},
            "views": {"items": views.get("items", []), "total": views.get("total"),
                      "offset": views.get("offset"), "limit": views.get("limit"),
                      "has_more": views.get("has_more"), "complete": views.get("complete"),
                      "upstream_complete": views.get("upstream_complete"),
                      "scope": "upstream views excluding templates and drawing sheets"},
            "category_summary": {"items": category_records, "total": analysis.get("summary", {}).get("total_categories"),
                                 "complete": analysis.get("categories_complete")},
            "model_statistics": analysis.get("summary", {}),
            "element_parameters": element_context,
            "completeness": completeness,
            "warnings": warnings,
            "known_limitations": list(KNOWN_LIMITATIONS),
        }

    def _analyze(self):
        return self._call("lora_analyze_model", top_n=200, include_levels=True)

    @staticmethod
    def _category_mentions(question):
        matches = []
        lower = question.casefold()
        for alias in sorted(CATEGORY_ALIASES, key=len, reverse=True):
            pattern = r"(?<![a-z0-9]){}(?![a-z0-9])".format(re.escape(alias))
            for match in re.finditer(pattern, lower):
                matches.append((match.start(), -len(alias), CATEGORY_ALIASES[alias]))
        found = []
        for _position, _length, canonical in sorted(matches):
            if canonical not in found:
                found.append(canonical)
        return found

    def _model_category_mentions(self, question):
        """Resolve exact category labels not covered by the common alias table."""
        analysis = self._analyze()
        found = []
        lower = question.casefold()
        for item in analysis.get("categories", []):
            name = item.get("category_name")
            if name and re.search(r"(?<![a-z0-9]){}(?![a-z0-9])".format(re.escape(name.casefold())), lower):
                if name not in found:
                    found.append(name)
        return found

    @staticmethod
    def _category_record(category, categories):
        wanted = _norm(category)
        for item in categories:
            if _norm(item.get("category_name")) == wanted:
                return item
        return None

    @staticmethod
    def _extract_level_phrase(question):
        match = re.search(r"\b(?:on|at)\s+(?:the\s+)?(.+?)(?:\?|$)", question, re.IGNORECASE)
        if not match:
            return None
        phrase = match.group(1).strip(" .?!")
        return phrase if re.match(r"^(?:level|storey|story|l\s*\d+)\b", phrase, re.IGNORECASE) else None

    @staticmethod
    def _resolve_level(phrase, levels):
        phrase_key = _norm(phrase)
        direct = [item for item in levels if _norm(item.get("level_name")) == phrase_key]
        if direct:
            return direct, "exact"
        number_match = re.match(r"^(?:level|storey|story|l)\s*0*(\d+)\b", phrase.strip(), re.IGNORECASE)
        if number_match:
            key = "l{}".format(int(number_match.group(1)))
            candidates = [item for item in levels if _norm(item.get("level_name", "")).startswith(key)]
            return candidates, "number_alias"
        return [], "none"

    def _collect_elements(self, category, level=None, max_pages=20):
        _validate_max_pages(max_pages)
        initial_identity, _ = self._project_identity()
        ids = []
        seen = set()
        offset = 0
        total = None
        upstream_complete = True
        stable_total = True
        pages = 0
        while pages < max_pages:
            page = self._call("lora_get_elements", category=category, level=level or "",
                              offset=offset, limit=PAGE_SIZE, include_parameters=False)
            pages += 1
            current_total = page.get("total")
            if total is None:
                total = current_total
            elif current_total != total:
                stable_total = False
            if not page.get("upstream_complete", False):
                upstream_complete = False
            items = page.get("items", [])
            for item in items:
                element_id = item.get("element_id")
                if element_id is not None and str(element_id) not in seen:
                    seen.add(str(element_id))
                    ids.append(element_id)
            offset += len(items)
            if not page.get("has_more"):
                break
            if not items:
                upstream_complete = False
                break
        pages_complete = total is not None and len(ids) >= total and upstream_complete and stable_total
        if pages >= max_pages and total is not None and len(ids) < total:
            pages_complete = False
        final_identity, _ = self._project_identity()
        if not self._same_identity(initial_identity, final_identity):
            raise ContextError("document_changed", "Active Revit document changed during paged element retrieval")
        return {"element_ids": ids, "total": total, "pages_read": pages,
                "complete": pages_complete, "upstream_complete": upstream_complete,
                "total_stable": stable_total, "document_identity": initial_identity}

    @staticmethod
    def _result(question, answer, scope, evidence, complete=True, warnings=None, **extra):
        return {"status": "success", "question": question, "answer": answer,
                "scope": scope, "evidence": evidence, "complete": bool(complete),
                "warnings": warnings or [], "not_executed": True, **extra}

    def _answer_category_counts(self, question, categories):
        analysis = self._analyze()
        mentioned = self._category_mentions(question) or list(categories or [])
        if not mentioned:
            return None
        level_phrase = self._extract_level_phrase(question)
        if level_phrase:
            levels_result = self._call("lora_get_levels", offset=0, limit=200)
            candidates, resolution = self._resolve_level(level_phrase, levels_result.get("items", []))
            if len(candidates) > 1:
                names = [item.get("level_name") for item in candidates]
                return self._result(question, "The level name is ambiguous; choose one of the matching model levels.",
                                    {"requested_level": level_phrase}, {"candidate_levels": names}, False,
                                    ["No element query was run because the level could not be resolved uniquely."],
                                    intent="ambiguous_level")
            if not candidates:
                return self._result(question, "No matching model level was found; no element count was inferred.",
                                    {"requested_level": level_phrase}, {"available_levels": [x.get("level_name") for x in levels_result.get("items", [])]},
                                    bool(levels_result.get("complete")), intent="level_not_found")
            level_name = candidates[0].get("level_name")
            results = []
            all_complete = True
            for category in mentioned:
                collected = self._collect_elements(category, level_name)
                results.append({"category": category, "count": len(collected["element_ids"]),
                                "total_reported": collected.get("total"),
                                "element_ids": collected["element_ids"][:MAX_RESULT_IDS],
                                "element_ids_truncated": len(collected["element_ids"]) > MAX_RESULT_IDS,
                                "complete": collected["complete"]})
                all_complete = all_complete and collected["complete"]
            details = "; ".join("{}: {}".format(x["category"], x["count"]) for x in results)
            answer = "At {}: {}.".format(level_name, details)
            if not all_complete:
                answer = "Partial retrieved counts only. " + answer
            warnings = [] if all_complete else ["At least one category result was paged incompletely; counts cover only retrieved elements."]
            return self._result(question, answer,
                                {"categories": mentioned, "level": level_name, "level_resolution": resolution,
                                 "page_size": PAGE_SIZE}, {"categories": results}, all_complete, warnings,
                                intent="category_count_by_level")

        categories = analysis.get("categories", [])
        by_category = []
        absent = []
        for category in mentioned:
            found = self._category_record(category, categories)
            if found:
                by_category.append({"category": found.get("category_name"), "count": found.get("element_count")})
            elif analysis.get("categories_complete"):
                by_category.append({"category": category, "count": 0})
            else:
                absent.append(category)
        details = "; ".join("{}: {}".format(x["category"], x["count"]) for x in by_category)
        if absent:
            details += ("; " if details else "") + "Unavailable because statistics are truncated: {}".format(", ".join(absent))
        complete = bool(analysis.get("categories_complete")) and not absent
        if not complete:
            details = "Partial statistics. " + details
        return self._result(question, details or "No matching category counts were returned.",
                            {"categories": mentioned, "scope": "model-wide category statistics"},
                            {"categories": by_category, "categories_complete": analysis.get("categories_complete"),
                             "categories_total": analysis.get("summary", {}).get("total_categories")}, complete,
                            [] if complete else ["Category statistics are incomplete or truncated."],
                            intent="category_count")

    def _answer_missing_parameter(self, question, category, parameter, max_pages):
        collected = self._collect_elements(category, max_pages=max_pages)
        missing_ids, unavailable_ids, populated_count, errors = [], [], 0, []
        parameter_results_complete = True
        inspected = 0
        ids = collected["element_ids"]
        for start in range(0, len(ids), PAGE_SIZE):
            batch = ids[start:start + PAGE_SIZE]
            result = self._call("lora_get_element_parameters", element_ids=batch,
                                parameter_names=[parameter], offset=0, limit=PAGE_SIZE)
            if not result.get("upstream_complete", False) or not result.get("complete", False):
                parameter_results_complete = False
            for element in result.get("items", []):
                inspected += 1
                if element.get("error"):
                    errors.append({"element_id": element.get("element_id"), "error": element.get("error")})
                    parameter_results_complete = False
                    continue
                parameter_value = next((item for item in element.get("parameters", [])
                                        if item.get("name", "").casefold() == parameter.casefold()), None)
                if parameter_value is None:
                    parameter_results_complete = False
                elif parameter_value.get("value_status") in ("empty", "missing"):
                    missing_ids.append(element.get("element_id"))
                elif parameter_value.get("value_status") == "unavailable":
                    unavailable_ids.append(element.get("element_id"))
                    parameter_results_complete = False
                else:
                    populated_count += 1
        element_scan_complete = collected["complete"]
        complete = element_scan_complete and parameter_results_complete and inspected == len(ids)
        shown_ids = missing_ids[:MAX_RESULT_IDS]
        answer = "{} of {} inspected {} have an empty or missing {} value.".format(
            len(missing_ids), inspected, category.casefold(), parameter)
        if not complete:
            answer += " This is not a complete model-wide check."
        warnings = []
        if not element_scan_complete:
            warnings.append("Element scan stopped before the reported category total was retrieved.")
        if unavailable_ids:
            warnings.append("{} parameter values were unavailable and were not counted as empty.".format(len(unavailable_ids)))
        if errors:
            warnings.append("Some elements returned parameter-read errors.")
        return self._result(question, answer,
                            {"category": category, "parameter": parameter, "elements_inspected": inspected,
                             "elements_reported": collected.get("total"), "pages_read": collected.get("pages_read")},
                            {"missing_or_empty_count": len(missing_ids), "missing_element_ids": shown_ids,
                             "missing_ids_truncated": len(missing_ids) > len(shown_ids),
                             "unavailable_count": len(unavailable_ids),
                             "unavailable_element_ids": unavailable_ids[:MAX_RESULT_IDS],
                             "populated_count": populated_count, "errors": errors[:50]},
                            complete, warnings, intent="missing_parameter_audit")

    def _answer_category_elements(self, question, category, max_pages):
        collected = self._collect_elements(category, max_pages=max_pages)
        shown = collected["element_ids"][:MAX_RESULT_IDS]
        complete = collected["complete"]
        answer = "Retrieved {} {} element IDs.".format(len(collected["element_ids"]), category)
        if not complete:
            answer += " This is a partial result; the reported category total is {}.".format(collected.get("total"))
        return self._result(question, answer,
                            {"category": category, "elements_inspected": len(collected["element_ids"]),
                             "elements_reported": collected.get("total"), "pages_read": collected.get("pages_read")},
                            {"element_ids": shown, "element_ids_truncated": len(collected["element_ids"]) > len(shown)},
                            complete,
                            [] if complete else ["The category inventory is incomplete; increase max_pages or query a narrower scope."],
                            intent="category_elements")

    @staticmethod
    def _explicit_geometry_ids(question):
        if not re.search(r"\b(geometry|bounding\s+box|location|solids?|meshes?)\b", question, re.IGNORECASE):
            return None
        match = re.search(r"\belements?\s*(?:ids?\s*)?(?:#\s*)?(\d+(?:\s*(?:,\s*(?:and\s+)?|and\s+)\d+)*)\b",
                          question, re.IGNORECASE)
        if not match:
            return None
        return [value.strip() for value in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", match.group(1))
                if value.strip()]

    def _answer_element_geometry(self, question, element_ids):
        if not self._has_tool("lora_get_element_geometry"):
            return None
        if not element_ids:
            return None
        if len(element_ids) > 25:
            raise ContextError("invalid_input", "Geometry inspection is limited to 25 explicitly named element IDs")
        result = self._call("lora_get_element_geometry", element_ids=element_ids,
                            entity_offset=0, entity_limit=100)
        records = result.get("items", [])
        complete = (not result.get("truncated") and not result.get("entity_has_more") and
                    not result.get("failures") and len(records) == len(element_ids) and
                    all(item.get("geometry_status") == "inspected" and item.get("geometry_complete")
                        for item in records))
        warnings = []
        if result.get("truncated") or result.get("entity_has_more"):
            warnings.append("Geometry evidence is truncated or paged; retrieve the remaining entity page before treating it as complete.")
        if result.get("failures") or len(records) != len(element_ids):
            warnings.append("One or more requested element IDs could not be fully inspected.")
        if any(item.get("location_status") in ("failed", "unavailable") or
               item.get("bounding_box_status") in ("failed", "unavailable") for item in records):
            warnings.append("Location or bounding-box evidence is unavailable or failed for at least one element.")
        warnings.append("Geometry evidence does not establish physical clearance or clash status.")
        return self._result(question,
            "Inspected geometry evidence for {} explicitly requested element ID(s).".format(len(records)),
            {"element_ids": element_ids, "scope": "explicit host-document IDs only"}, result,
            complete, warnings, intent="element_geometry")

    def _answer_category_inventory(self, question):
        if not self._has_tool("lora_get_category_inventory"):
            return None
        lower = question.casefold()
        if not re.search(r"\b(categories|category inventory|category summary|discipline inventory)\b", lower):
            return None
        discipline = ("architecture" if re.search(r"\b(architecture|architectural)\b", lower) else
                      next((name for name in ("structural", "mep")
                            if re.search(r"\b{}\b".format(name), lower)), None))
        result = self._call("lora_get_category_inventory", discipline=discipline,
                            offset=0, limit=1, failure_offset=0, failure_limit=25,
                            category_offset=0, category_limit=50)
        categories = result.get("categories", [])
        complete = (bool(result.get("inventory_complete")) and
                    result.get("category_pagination_complete") is True and
                    result.get("category_page_has_more") is False)
        warnings = []
        if not complete:
            warnings.append("Category inventory is incomplete or the returned category summary page has more entries.")
        if result.get("discipline_inspection_incomplete_elements"):
            warnings.append("Some category discipline assignments were unavailable or failed.")
        warnings.append("Category membership and element counts do not establish physical obstruction or clearance.")
        shown = [{"category_id": item.get("category_id"),
                  "built_in_category": item.get("built_in_category"),
                  "category_name": item.get("category_name"),
                  "discipline": item.get("discipline"), "counts": item.get("counts"),
                  "element_type_count": item.get("element_type_count"),
                  "total": item.get("total")} for item in categories]
        answer = "Retrieved {} category summaries for {}.".format(
            len(shown), discipline or "all disciplines")
        if not complete:
            answer += " The inventory is partial or reports an incomplete scan."
        return self._result(question, answer,
            {"discipline": discipline or "all", "scope": "category summary page",
             "total_categories": result.get("total_categories"),
             "elements_examined": result.get("elements_examined"),
             "element_types_examined": result.get("element_types_examined")},
            {"categories": shown, "category_offset": result.get("category_offset"),
             "category_limit": result.get("category_limit"),
             "category_has_more": result.get("category_page_has_more"),
             "failures_total": result.get("total_failures"),
             "failures_returned": len(result.get("failures", [])),
             "inventory_complete": result.get("inventory_complete"),
             "clearance_status": result.get("clearance_status")},
            complete, warnings, intent="category_inventory")

    def _answer_level_ids(self, question):
        if not self._has_tool("lora_get_level_catalog"):
            return {"status": "unsupported_query", "question": question,
                "answer": "Level element IDs require the read-only Lora Level catalog tool.",
                "scope": {"recommended_tool": "lora_get_level_catalog"}, "evidence": {},
                "complete": False, "warnings": [], "not_executed": True}
        result = self._call("lora_get_level_catalog", offset=0, limit=100,
                            failure_offset=0, failure_limit=50)
        records = result.get("levels", [])
        rendered = []
        for level in records:
            if level.get("element_id_status") == "inspected" and level.get("name_status") == "inspected":
                rendered.append("{} (element ID {})".format(level.get("level_name"), level.get("element_id")))
            else:
                rendered.append("{} (element ID unavailable; status {})".format(
                    level.get("level_name") or "Unnamed Level", level.get("element_id_status")))
        answer = "Level element IDs: " + "; ".join(rendered) if rendered else "No Levels were returned."
        complete = bool(result.get("inventory_complete")) and result.get("has_more") is False
        warnings = list(result.get("warnings") or [])
        if result.get("total_failures"):
            warnings.append("The Level catalog reported {} field or enumeration failures.".format(result.get("total_failures")))
        if result.get("has_more"):
            warnings.append("The Level catalog has additional pages; this response contains only the first page.")
        return self._result(question, answer,
            {"source": "identity-checked Revit Level catalog", "total_levels": result.get("total_levels"),
             "offset": result.get("offset"), "limit": result.get("limit"), "has_more": result.get("has_more")},
            {"levels": records, "failures": result.get("failures", []),
             "clearance_status": result.get("clearance_status", "unverified")},
            complete, warnings, intent="level_ids")

    def _answer_views(self, question, max_pages):
        _validate_max_pages(max_pages)
        all_views, total, complete, upstream_complete = [], None, False, True
        for page_index in range(max_pages):
            result = self._call("lora_get_views", offset=page_index * 200, limit=200)
            if total is None:
                total = result.get("total")
            elif total != result.get("total"):
                upstream_complete = False
            if not result.get("upstream_complete", False):
                upstream_complete = False
            all_views.extend(result.get("items", []))
            if not result.get("has_more"):
                complete = upstream_complete and total == len(all_views)
                break
        if len(all_views) > (max_pages * 200):
            complete = False
        type_counts = {}
        for view in all_views:
            name = view.get("type") or "Unavailable"
            type_counts[name] = type_counts.get(name, 0) + 1
        page_complete = complete
        shown = all_views[:50]
        answer = "Retrieved {} placeable views across {} view types.".format(len(all_views), len(type_counts))
        if not complete:
            answer += " The view list is incomplete."
        return self._result(question, answer,
                            {"views_reported": total, "views_retrieved": len(all_views),
                             "returned_view_records": len(shown), "view_records_truncated": len(all_views) > len(shown),
                             "pages_read": (len(all_views) + 199) // 200,
                             "upstream_scope": "excludes view templates and drawing sheets"},
                            {"view_types": type_counts, "views": shown}, page_complete,
                            [] if page_complete else ["More view pages remain or upstream view counts do not match."],
                            intent="views")

    def _recommend_commands(self, question):
        text = question.casefold()
        for name, triggers in COMMAND_TRIGGERS:
            if any(trigger in text for trigger in triggers):
                command = next(item for item in COMMANDS if item["name"] == name)
                return [command]
        # Explicit tag placement is not a supported outcome of the current Tag Creation button.
        if re.search(r"\b(place|put|add)\b.*\btags?\b|\btag all\b", text):
            return []
        return []

    def answer(self, question, max_pages=20):
        """Answer a bounded natural-language BIM query without executing any model action."""
        _validate_max_pages(max_pages)
        if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_LENGTH:
            raise ContextError("invalid_query", "question must contain 1 to {} characters".format(MAX_QUESTION_LENGTH))
        question = question.strip()
        q = question.casefold()

        if (re.search(r"\b(?:list|show|what|which|available)\b.{0,48}\b(?:lora\s+)?commands?\b", q) or
                re.search(r"\bcommands?\b.{0,48}\b(?:available|does\s+lora\s+have|in\s+lora)\b", q)):
            commands = [dict(command) for command in COMMANDS]
            names = ", ".join(command["name"] for command in commands)
            return self._result(question, "Current Lora ribbon commands: {}. This registry is descriptive; no command was executed.".format(names),
                {"source": "verified Lora command registry", "commands_returned": len(commands)},
                {"commands": commands}, True, intent="command_list")

        geometry_ids = self._explicit_geometry_ids(question)
        geometry_intent = bool(re.search(r"\b(geometry|bounding\s+box|location|solids?|meshes?)\b", q))
        if geometry_intent:
            if geometry_ids:
                geometry_result = self._answer_element_geometry(question, geometry_ids)
                if geometry_result is not None:
                    return geometry_result
            return {"status": "unsupported_query", "question": question,
                    "answer": "Selected-element geometry inspection requires explicit numeric Revit element IDs. No model-wide geometry scan was run.",
                    "scope": {"recommended_tool": "lora_get_element_geometry", "maximum_element_ids": 25},
                    "evidence": {}, "complete": False, "warnings": [], "not_executed": True}
        if re.search(r"\b(categories|category inventory|category summary|discipline inventory)\b", q):
            inventory_result = self._answer_category_inventory(question)
            if inventory_result is not None:
                return inventory_result

        if (re.search(r"\blevel\b.{0,24}\b(?:element\s+)?ids?\b", q) or
                re.search(r"\bids?\b.{0,24}\blevel\b", q)):
            return self._answer_level_ids(question)

        missing_match = re.search(r"\b(?:missing|empty|unpopulated)\s+([a-z][a-z0-9 _-]*?)(?:\s+parameters?|\s+values?|\?|$)", q)
        categories = self._category_mentions(q)
        if not categories and re.search(r"\b(how many|count|number of|which elements|what elements|category)\b", q):
            categories = self._model_category_mentions(q)
        if missing_match and categories:
            parameter = missing_match.group(1).strip().strip("- ").title()
            return self._answer_missing_parameter(question, categories[0], parameter, max_pages)

        if re.search(r"\b(how many|count|number of)\b", q) and categories:
            return self._answer_category_counts(question, categories)

        if "view" in q and re.search(r"\b(what|which|list|show|exist|available)\b", q):
            return self._answer_views(question, max_pages)

        if "level" in q and re.search(r"\b(what|which|list|show|elevation|elevations)\b", q):
            result = self._call("lora_get_levels", offset=0, limit=200)
            items = [normalize_level(item) for item in result.get("items", [])]
            parts = ["{}: {} mm ({} internal ft)".format(item.get("level_name"),
                    _format_number(item.get("elevation_mm")), _format_number(item.get("elevation_internal_feet")))
                     for item in items]
            answer_text = "Levels and elevations: " + "; ".join(parts) if parts else "No levels were returned."
            return self._result(question, answer_text, {"levels_retrieved": len(items), "levels_reported": result.get("total")},
                                {"levels": items}, bool(result.get("complete")),
                                [] if result.get("complete") else ["Level list is incomplete."], intent="levels")

        mentioned = categories
        if mentioned and re.search(r"\b(which|what|show|list|elements?|belong|category)\b", q):
            return self._answer_category_elements(question, mentioned[0], max_pages)

        if re.search(r"\b(incomplete|truncated|submission|before submission|checked)\b", q):
            analysis = self._analyze()
            view_result = self._answer_views(question, max_pages)
            issues = []
            if not analysis.get("categories_complete"):
                issues.append("Category statistics are truncated.")
            if not analysis.get("levels_complete"):
                issues.append("Level statistics are incomplete.")
            if not view_result.get("complete"):
                issues.append("The view inventory is incomplete or truncated.")
            if issues:
                text = "Available model summary reports: " + " ".join(issues)
            else:
                text = ("The retrieved summary and placeable-view inventory are complete for their reported scope. "
                        "It does not define project submission requirements or audit required parameter values, "
                        "so it cannot certify the model or flag unverified elements.")
            return self._result(question, text, {"scope": "model statistics and placeable views"},
                                {"statistics_complete": analysis.get("complete"),
                                 "categories_complete": analysis.get("categories_complete"),
                                 "levels_complete": analysis.get("levels_complete"),
                                 "views_complete": view_result.get("complete"),
                                 "view_evidence": view_result.get("evidence"), "issues": issues},
                                bool(analysis.get("complete")) and bool(view_result.get("complete")),
                                ["No project-specific submission rules or required-parameter schema were supplied."],
                                intent="model_completeness")

        commands = self._recommend_commands(q)
        if not commands and re.search(r"\b(what does|explain|describe)\b", q):
            for command in COMMANDS:
                if command["name"].casefold() in q:
                    commands = [command]
                    break
        if commands:
            command = commands[0]
            if re.search(r"\b(what does|explain|describe)\b", q):
                answer_text = "{}: {} Required input: {} Model/file effect: {} Limitations: {}".format(
                    command["name"], command["purpose"], command["required_input"],
                    command["model_effect"], command["limitations"])
                intent = "command_information"
            else:
                answer_text = "The matching Lora command is {}. {} Required input: {}. Model/file effect: {} Limitations: {} This recommendation was not executed.".format(
                    command["name"], command["purpose"], command["required_input"],
                    command["model_effect"], command["limitations"])
                intent = "command_recommendation"
            return self._result(question, answer_text, {"source": "verified Lora command registry"},
                                {"recommended_commands": commands}, True, intent=intent)
        if re.search(r"\b(place|put|add)\b.*\btags?\b|\btag all\b", q):
            return {"status": "unsupported_query", "question": question,
                    "answer": "No current Lora command in the verified registry places tags automatically. Tag Creation only generates/binds MEP shared parameters or checks tag readiness.",
                    "scope": {"source": "verified Lora command registry"}, "evidence": {},
                    "complete": True, "warnings": [], "not_executed": True}
        return {"status": "unsupported_query", "question": question,
                "answer": "This question does not match a supported BIM query or verified Lora command. Try category counts, a level/elevation query, a view query, category element lookup, or a missing-parameter check.",
                "scope": {"supported_intents": ["category_count", "category_count_by_level", "missing_parameter_audit", "levels", "views", "category_elements", "model_completeness", "command_list", "command_recommendation"]},
                "evidence": {}, "complete": False, "warnings": [], "not_executed": True}


def _format_number(value):
    if value is None:
        return "unavailable"
    return "{:.3f}".format(value).rstrip("0").rstrip(".")
