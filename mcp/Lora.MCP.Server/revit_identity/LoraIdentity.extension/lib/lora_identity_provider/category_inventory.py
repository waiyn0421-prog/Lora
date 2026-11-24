# -*- coding: utf-8 -*-
"""Read-only BuiltInCategory inventory with bounded element details.

The category catalog is an index, not a physical-geometry or clearance test.
Kept compatible with the pyRevit/IronPython-side runtime.
"""

from . import coordinate_geometry

_MAX_PAGE_SIZE = 100
_MAX_SCAN = 100000

# Exact enum member names are the source of discipline mapping. Category display
# names are returned for humans but are never used to assign a discipline.
_DISCIPLINES = {
    "architecture": (
        "OST_Walls", "OST_Doors", "OST_Windows", "OST_Floors", "OST_Ceilings",
        "OST_Roofs", "OST_Rooms", "OST_Areas", "OST_Stairs", "OST_Railings",
        "OST_Columns", "OST_CurtainWallMullions", "OST_CurtainWallPanels",
        "OST_GenericModel", "OST_Curtain_Systems", "OST_Cornices", "OST_Casework",
        "OST_Furniture", "OST_FurnitureSystems", "OST_SpecialityEquipment",
        "OST_StairsRuns", "OST_StairsLandings", "OST_StairsSupports",
        "OST_StairsStringerCarriage", "OST_StairsTrisers", "OST_StairsRailing",
        "OST_StairsRailingBaluster", "OST_StairsRailingRail",
    ),
    "structural": (
        "OST_StructuralColumns", "OST_StructuralFraming", "OST_StructuralFoundation",
        "OST_StructConnections", "OST_StructConnectionSymbols", "OST_Rebar",
        "OST_AreaRein", "OST_StructuralStiffener", "OST_StructuralTruss",
        "OST_FabricReinforcement", "OST_StructuralFramingSystem",
        "OST_StructuralFramingOther", "OST_StructuralTendons",
        "OST_StructConnectionAnchors", "OST_StructConnectionBolts",
        "OST_StructConnectionHoles", "OST_StructConnectionModifiers",
        "OST_StructConnectionOthers", "OST_StructConnectionPlates",
        "OST_StructConnectionProfiles", "OST_StructConnectionShearStuds",
        "OST_StructConnectionWelds", "OST_StructSubConnections",
    ),
    "mep": (
        "OST_PipeCurves", "OST_PipeFitting", "OST_PipeAccessory", "OST_PipeInsulations",
        "OST_DuctCurves", "OST_DuctFitting", "OST_DuctAccessory", "OST_DuctInsulations",
        "OST_CableTray", "OST_CableTrayFitting", "OST_Conduit", "OST_ConduitFitting",
        "OST_MechanicalEquipment", "OST_PlumbingFixtures", "OST_ElectricalFixtures",
        "OST_LightingFixtures", "OST_LightingDevices", "OST_DuctTerminal",
        "OST_ElectricalEquipment", "OST_Sprinklers", "OST_FlexPipeCurves", "OST_FlexDuctCurves",
        "OST_DuctCurvesInsulation", "OST_DuctCurvesLining", "OST_DuctFittingInsulation",
        "OST_DuctFittingLining", "OST_DuctLinings", "OST_PipeCurvesInsulation",
        "OST_PipeFittingInsulation", "OST_FlexDuctCurvesInsulation",
        "OST_FlexPipeCurvesInsulation", "OST_WireInsulations",
        "OST_FabricationDuctwork", "OST_FabricationDuctworkInsulation",
        "OST_FabricationDuctworkLining", "OST_FabricationDuctworkStiffeners",
        "OST_FabricationPipework", "OST_FabricationPipeworkInsulation",
    ),
}

_DISCIPLINE_BY_CATEGORY = {}
for _discipline, _members in _DISCIPLINES.items():
    for _member in _members:
        _DISCIPLINE_BY_CATEGORY[_member] = _discipline


def category_inventory(runtime, uiapp, doc, discipline=None, category_id=None,
                       offset=0, limit=50, failure_offset=0, failure_limit=50,
                       category_offset=0, category_limit=50):
    """Return category counts and a bounded detail page for the active document."""
    if discipline is not None and discipline not in ("architecture", "structural", "mep", "other", "unknown"):
        return {"status": "error", "error": "invalid_discipline"}
    if (not _valid_page(offset, limit) or not _valid_page(failure_offset, failure_limit) or
            not _valid_page(category_offset, category_limit)):
        return {"status": "error", "error": "invalid_pagination", "maximum_page_size": _MAX_PAGE_SIZE}
    try:
        identity = runtime.identity_response(uiapp, doc)
        if identity.get("status") != "success":
            return {"status": "error", "error": "document_identity_unavailable"}
        from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, FilteredElementCollector,
                                       LocationCurve, LocationPoint)
        from System import Enum

        enum_by_id = {}
        for value in Enum.GetValues(BuiltInCategory):
            try:
                enum_by_id[int(value)] = str(value).split(".")[-1]
            except Exception:
                continue
        collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
        reported_total = None
        try:
            reported_total = int(collector.GetElementCount())
        except Exception:
            pass

        category_counts = {}
        failures = []
        details = []
        failure_total = 0
        scanned = 0
        classification_counts = {"model_instance": 0, "annotation": 0, "view_specific": 0,
                                 "link_instance": 0, "no_category": 0, "other": 0, "element_type": 0}
        truncated = False
        matching_index = 0
        discipline_inspection_incomplete = 0
        for element in collector:
            if scanned >= _MAX_SCAN:
                truncated = True
                break
            scanned += 1
            try:
                category = element.Category
                category_name = str(category.Name) if category is not None else None
                raw_id = coordinate_geometry._id_text(category.Id) if category is not None else None
                enum_name = enum_by_id.get(int(raw_id)) if raw_id is not None else None
                resolved_discipline, discipline_status, discipline_warning = _resolve_discipline(
                    element, enum_name, BuiltInParameter)
                if discipline_status not in ("inspected", "not_applicable"):
                    discipline_inspection_incomplete += 1
                view_specific = bool(element.ViewSpecific)
                category_type = str(category.CategoryType) if category is not None else ""
                class_name = coordinate_geometry._class_name(element)
                if class_name == "RevitLinkInstance":
                    kind = "link_instance"
                elif view_specific:
                    kind = "view_specific"
                elif "annotation" in category_type.lower():
                    kind = "annotation"
                elif "model" in category_type.lower():
                    kind = "model_instance"
                elif category is None:
                    kind = "no_category"
                else:
                    kind = "other"
                classification_counts[kind] += 1
                key = (raw_id or "unmapped") + "|" + resolved_discipline
                entry = category_counts.get(key)
                if entry is None:
                    entry = {"category_id": raw_id, "built_in_category": enum_name,
                             "category_name": category_name,
                             "discipline": resolved_discipline, "counts": dict(
                                 (name, 0) for name in classification_counts),
                             "total": 0, "element_type_count": 0, "details_returned": 0}
                    category_counts[key] = entry
                entry["counts"][kind] += 1
                entry["total"] += 1
                if _matches_filter(resolved_discipline, raw_id, discipline, category_id):
                    if offset <= matching_index < offset + limit:
                        detail = {"element_id": coordinate_geometry._id_text(element.Id),
                            "category_id": raw_id, "built_in_category": enum_name,
                            "category": category_name, "discipline": resolved_discipline,
                            "discipline_resolution_status": discipline_status,
                            "discipline_warning": discipline_warning,
                            "element_class": class_name, "element_kind": kind,
                            "view_specific": view_specific,
                            "exclusion_reason": _exclusion(kind, enum_name),
                            "geometry_evidence_scope": "location and bounding box only; no solid/mesh extraction; clearance unverified"}
                        if kind == "model_instance" and enum_name is not None and resolved_discipline in (
                                "architecture", "structural", "mep"):
                            inspected = coordinate_geometry._inspect_physical(
                                element, category_name, class_name, LocationCurve, LocationPoint, doc)
                            detail.update(inspected)
                            detail.update({"category_id": raw_id, "built_in_category": enum_name,
                                "discipline": resolved_discipline, "element_kind": kind,
                                "exclusion_reason": "not_excluded; physical obstruction is not inferred"})
                        else:
                            exclusion_reason = ("built_in_category_unmapped" if enum_name is None else
                                ("discipline_not_supported_by_inventory" if resolved_discipline == "other" else
                                 _exclusion(kind, enum_name)))
                            classification = {"view_specific": "view_specific", "annotation": "annotation_or_detail",
                                "link_instance": "link_instance"}.get(kind, "unsupported_or_unclassified")
                            detail.update(coordinate_geometry._classification_record(
                                element, category_name, class_name, classification, exclusion_reason))
                            detail["exclusion_reason"] = exclusion_reason
                        details.append(detail)
                        entry["details_returned"] += 1
                    matching_index += 1
            except Exception as exc:
                index = failure_total
                failure_total += 1
                if failure_offset <= index < failure_offset + failure_limit:
                    failures.append({"failure_index": index,
                        "element_id": coordinate_geometry._id_text(getattr(element, "Id", None)),
                        "error": {"exception_type": type(exc).__name__, "message": str(exc)[:200]},
                        "field": "category_classification"})

        type_elements_examined = 0
        reported_type_total = None
        type_scan_truncated = False
        try:
            type_collector = FilteredElementCollector(doc).WhereElementIsElementType()
            try:
                reported_type_total = int(type_collector.GetElementCount())
            except Exception:
                pass
            for type_element in type_collector:
                if type_elements_examined >= _MAX_SCAN:
                    type_scan_truncated = True
                    break
                type_elements_examined += 1
                try:
                    category = type_element.Category
                    category_name = str(category.Name) if category is not None else None
                    raw_id = coordinate_geometry._id_text(category.Id) if category is not None else None
                    enum_name = enum_by_id.get(int(raw_id)) if raw_id is not None else None
                    # A type cannot reliably inherit instance-level structural usage.
                    type_discipline = ("unknown" if enum_name in ("OST_Walls", "OST_Floors") else
                        _DISCIPLINE_BY_CATEGORY.get(enum_name, "other" if enum_name else "unknown"))
                    key = (raw_id or "unmapped") + "|" + type_discipline
                    entry = category_counts.get(key)
                    if entry is None:
                        entry = {"category_id": raw_id, "built_in_category": enum_name,
                            "category_name": category_name, "discipline": type_discipline,
                            "counts": dict((name, 0) for name in classification_counts),
                            "total": 0, "element_type_count": 0, "details_returned": 0}
                        category_counts[key] = entry
                    entry["element_type_count"] += 1
                    entry["counts"]["element_type"] += 1
                    classification_counts["element_type"] += 1
                except Exception as exc:
                    index = failure_total
                    failure_total += 1
                    if failure_offset <= index < failure_offset + failure_limit:
                        failures.append({"failure_index": index,
                            "element_id": coordinate_geometry._id_text(getattr(type_element, "Id", None)),
                            "error": {"exception_type": type(exc).__name__, "message": str(exc)[:200]},
                            "field": "type_category_classification"})
        except Exception as exc:
            index = failure_total
            if failure_offset <= index < failure_offset + failure_limit:
                failures.append({"failure_index": index, "element_id": None,
                    "error": {"exception_type": type(exc).__name__, "message": str(exc)[:200]},
                    "field": "type_collector"})
            failure_total += 1

        visible_categories = [item for item in category_counts.values()
                              if _matches_filter(item["discipline"], item["category_id"], discipline, category_id)]
        visible_categories.sort(key=lambda item: (int(item["category_id"]) if item.get("category_id") is not None else 0,
                                                   item.get("built_in_category") or ""))
        category_total = len(visible_categories)
        category_page = visible_categories[category_offset:category_offset + category_limit]
        filtered_total = sum(item["total"] for item in visible_categories)
        for item in category_page:
            item["inspection_coverage"] = "category_counted_element_details_not_inspected"
            item["geometry_evidence"] = "not_requested"
        return {"status": "success", "document_identity": coordinate_geometry._identity_summary(identity.get("identity") or {}),
            "discipline_filter": discipline, "category_id_filter": str(category_id) if category_id is not None else None,
            "categories": category_page, "category_offset": category_offset,
            "category_limit": category_limit, "total_categories": category_total,
            "categories_has_more": (None if failure_total or truncated or type_scan_truncated or discipline_inspection_incomplete else
                category_offset + len(category_page) < category_total),
            "classification_counts": classification_counts,
            "discipline_inspection_incomplete_elements": discipline_inspection_incomplete,
            "element_kind_definitions": {"model_instance": "Model category instance; not a physical obstruction claim",
                "annotation": "Annotation category instance", "view_specific": "Element reports ViewSpecific true",
                "link_instance": "RevitLinkInstance; linked document not traversed",
                "no_category": "Category property returned null", "other": "Other category type"},
            "items": details, "offset": offset, "limit": limit,
            "total_matching_elements": filtered_total,
            "has_more": (None if failure_total or truncated or discipline_inspection_incomplete else
                offset + len(details) < filtered_total),
            "failure_offset": failure_offset, "failure_limit": failure_limit,
            "total_failures": failure_total, "failures": failures,
            "failure_has_more": failure_offset + len(failures) < failure_total,
            "category_pagination_complete": failure_total == 0 and not truncated and not type_scan_truncated and not discipline_inspection_incomplete,
            "category_has_more_status": ("known" if failure_total == 0 and not truncated and not type_scan_truncated and not discipline_inspection_incomplete else "unknown_due_to_incomplete_scan"),
            "category_page_has_more": (None if failure_total or truncated or type_scan_truncated or discipline_inspection_incomplete else
                category_offset + len(category_page) < category_total),
            "elements_examined": scanned, "reported_total_elements": reported_total,
            "element_types_examined": type_elements_examined,
            "reported_total_element_types": reported_type_total,
            "type_scan_complete": (not type_scan_truncated and
                (reported_type_total is None or type_elements_examined == reported_type_total)),
            "truncated": truncated or type_scan_truncated, "inventory_complete": (not truncated and
                not type_scan_truncated and (reported_total is None or scanned == reported_total) and
                (reported_type_total is None or type_elements_examined == reported_type_total) and failure_total == 0 and
                discipline_inspection_incomplete == 0),
            "clearance_status": "unverified", "coordinate_transform_status": "unchanged; see coordinate diagnostic"}
    except Exception as exc:
        return {"status": "error", "error": "category_inventory_failed",
                "diagnostic": {"exception_type": type(exc).__name__, "message": str(exc)[:200]}}


def _valid_page(offset, limit):
    return (not isinstance(offset, bool) and isinstance(offset, int) and 0 <= offset <= 10000000 and
            not isinstance(limit, bool) and isinstance(limit, int) and 1 <= limit <= _MAX_PAGE_SIZE)


def _matches_filter(item_discipline, item_id, discipline, category_id):
    return ((discipline is None or item_discipline == discipline) and
            (category_id is None or str(item_id) == str(category_id)))


def _resolve_discipline(element, enum_name, BuiltInParameter=None):
    discipline = _DISCIPLINE_BY_CATEGORY.get(enum_name, "other" if enum_name else "unknown")
    if enum_name == "OST_Floors":
        if BuiltInParameter is None:
            return "unknown", "unavailable", {"field": "discipline.is_structural",
                "code": "built_in_parameter_unavailable", "message": "FLOOR_PARAM_IS_STRUCTURAL is unavailable."}
        try:
            parameter_id = BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL
        except AttributeError:
            return "unknown", "unavailable", {"field": "discipline.is_structural",
                "code": "built_in_parameter_unavailable", "message": "FLOOR_PARAM_IS_STRUCTURAL is unavailable."}
        except Exception as exc:
            return "unknown", "failed", coordinate_geometry._warning("discipline.is_structural", exc)
        try:
            parameter = element.get_Parameter(parameter_id)
            if parameter is None:
                return "unknown", "unavailable", {"field": "discipline.is_structural",
                    "code": "built_in_parameter_unavailable", "message": "Floor structural parameter returned null."}
            value = parameter.AsInteger()
            return ("structural" if bool(value) else "architecture", "inspected", None)
        except Exception as exc:
            if isinstance(exc, AttributeError) and coordinate_geometry._member_is_declared(
                    element, "get_Parameter") is False:
                return "unknown", "unavailable", {"field": "discipline.is_structural",
                    "code": "built_in_parameter_unavailable", "message": "Element does not expose get_Parameter."}
            return "unknown", "failed", coordinate_geometry._warning("discipline.is_structural", exc)
    if enum_name == "OST_Walls":
        warnings = []
        status, value = coordinate_geometry._read_member(element, "StructuralUsage", "discipline.structural_usage", warnings)
        if status != "inspected":
            return "unknown", status, warnings[0] if warnings else "Wall StructuralUsage was unavailable."
        try:
            usage = str(value).split(".")[-1].lower().replace("_", "")
        except Exception as exc:
            return "unknown", "failed", coordinate_geometry._warning("discipline.structural_usage", exc)
        if usage == "nonbearing":
            return "architecture", "inspected", None
        if usage in ("bearing", "shear", "combined"):
            return "structural", "inspected", None
        return "unknown", "unavailable", "Wall StructuralUsage did not identify a supported usage."
    return discipline, "inspected" if enum_name else "unavailable", None


def _exclusion(kind, enum_name):
    if kind == "view_specific":
        return "view_specific_element"
    if kind == "annotation":
        return "annotation_category"
    if kind == "link_instance":
        return "linked_document_not_traversed"
    if kind == "no_category":
        return "category_unavailable"
    if kind != "model_instance":
        return "category_type_not_model"
    if enum_name is None:
        return "built_in_category_unmapped"
    return "none; geometry was not inspected by this category summary"
