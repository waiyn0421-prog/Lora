# -*- coding: utf-8 -*-
"""Read-only inventory of a Revit family document for compatibility review.

This module inventories content. It does not claim that content will behave
identically in a target Revit release and never starts a transaction.
"""

CLASSIFICATIONS = ("compatible", "incompatible", "requires_reconstruction",
                   "requires_manual_review", "not_inspected")
FIELD_STATUSES = ("not_inspected", "inspected", "unavailable", "failed",
                  "not_applicable")
MAX_RECORDS = 2000
MAX_GEOMETRY_DEPTH = 8
MAX_GEOMETRY_OBJECTS = 10000
MAX_TYPE_PARAMETER_VALUES = 10000


def analyze_family_document(document, db, target_version, running_version,
                            saved_format_version=None):
    """Inspect an already-open family document without modifying it.

    The only automatic incompatibility conclusion made here is that a file
    saved in a newer major format cannot be consumed by an older target.
    Other feature compatibility remains manual review because target-version
    API assemblies and behavioral fixtures are not available to this module.
    """
    target = _major_version(target_version)
    running = _major_version(running_version)
    saved = _major_version(saved_format_version)
    result = {
        "status": "not_inspected",
        "compatibility_classification": "requires_manual_review",
        "target_revit_version": target,
        "running_revit_version": running,
        "saved_format_version": saved,
        "document_is_family": None,
        "document_modified_before": None,
        "source_modified": False,
        "analysis_scope": "read_only_family_content_inventory",
        "warnings": [],
        "fields": {},
        "limitations": [
            "Content inventory does not certify behavior in a target Revit release.",
            "Target-version API assemblies and behavioral comparison families were not available.",
            "No migration, reconstruction, save, or model write was performed.",
        ],
    }
    if document is None or db is None:
        result["status"] = "failed"
        result["warnings"].append(_warning("document", "failed",
            "A family document and Revit DB API are required."))
        return result

    state, is_family, error = _read(document, "IsFamilyDocument")
    result["document_is_family"] = is_family if state == "inspected" else None
    if state != "inspected" or not is_family:
        result["status"] = "failed"
        result["warnings"].append(_warning("document.IsFamilyDocument", state,
            error or "The selected document is not a Revit family document."))
        return result

    state, modified, error = _read(document, "IsModified")
    result["document_modified_before"] = modified if state == "inspected" else None
    if state != "inspected":
        result["warnings"].append(_warning("document.IsModified", state, error))
    elif modified:
        result["warnings"].append(_warning("document.IsModified", "failed",
            "The family document already has unsaved changes; inventory is read-only and will not save them."))

    owner_state, owner, error = _read(document, "OwnerFamily")
    if owner_state != "inspected" or owner is None:
        result["status"] = "failed"
        result["warnings"].append(_warning("document.OwnerFamily", owner_state,
                                            error or "OwnerFamily is null."))
        return result

    result["fields"]["category"] = _inspect_category(owner)
    result["fields"]["types"] = _inspect_types(document)
    result["fields"]["parameters"] = _inspect_parameters(document)
    result["fields"]["geometry"] = _inspect_geometry(document, db)
    result["fields"]["materials_subcategories"] = _inspect_materials_and_subcategories(
        document, owner, db)
    result["fields"]["visibility"] = _inspect_visibility(document, db)
    result["fields"]["references_constraints"] = _inspect_references_constraints(
        document, db)
    result["fields"]["nested_families"] = _inspect_nested_families(document, db)
    result["fields"]["hosting"] = _inspect_hosting(owner, document, db)
    result["fields"]["mep_connectors"] = _inspect_connectors(document, db)

    for name, field in result["fields"].items():
        for item in field.get("warnings", []):
            item["field_group"] = name
            result["warnings"].append(item)

    if target is not None and saved is not None and saved > target:
        result["compatibility_classification"] = "incompatible"
        result["compatibility_reason"] = "saved_format_newer_than_target"
    elif target is None:
        result["compatibility_classification"] = "requires_manual_review"
        result["compatibility_reason"] = "target_version_unavailable"
    else:
        result["compatibility_classification"] = "requires_manual_review"
        result["compatibility_reason"] = "content_compatibility_not_proven"

    if running is not None and target is not None and running != target:
        result["warnings"].append(_warning("target_revit_version", "unavailable",
            "The active Revit API version differs from the target; target API behavior was not evaluated."))
    if saved is None:
        result["warnings"].append(_warning("saved_format_version", "unavailable",
            "Saved family format could not be determined from BasicFileInfo."))

    statuses = [field.get("status") for field in result["fields"].values()]
    if any(status == "failed" for status in statuses):
        result["status"] = "partial"
    elif all(status in ("inspected", "not_applicable") for status in statuses):
        result["status"] = "success"
    else:
        result["status"] = "partial"

    after_state, after_modified, error = _read(document, "IsModified")
    result["document_modified_after"] = after_modified if after_state == "inspected" else None
    if after_state != "inspected":
        result["warnings"].append(_warning("document.IsModified.after", after_state, error))
    elif after_modified and not modified:
        result["warnings"].append(_warning("document.IsModified.after", "failed",
            "The document became modified during analysis; do not save it."))
        result["status"] = "partial"
    return result


def _inspect_category(owner):
    field = _field()
    state, category, error = _read(owner, "FamilyCategory")
    if state != "inspected" or category is None:
        _field_error(field, "Family.FamilyCategory", state, error or "Family category is null.")
        return field
    name_state, name, name_error = _read(category, "Name")
    id_state, category_id, id_error = _read(category, "Id")
    built_in_state, built_in, built_in_error = _read(category, "BuiltInCategory")
    field["status"] = "inspected" if all(x == "inspected" for x in
        (name_state, id_state, built_in_state)) else "partial"
    field["records"].append({"name": name if name_state == "inspected" else None,
                             "id": _string(category_id) if id_state == "inspected" else None,
                             "built_in_category": _string(built_in) if built_in_state == "inspected" else None})
    for key, status, message in (("Name", name_state, name_error),
                                 ("Id", id_state, id_error),
                                 ("BuiltInCategory", built_in_state, built_in_error)):
        if status != "inspected":
            _field_error(field, "FamilyCategory." + key, status, message)
    return field


def _inspect_types(document):
    field = _field()
    state, manager, error = _read(document, "FamilyManager")
    if state != "inspected" or manager is None:
        _field_error(field, "Document.FamilyManager", state, error or "FamilyManager is null.")
        return field
    state, types, error = _read(manager, "Types")
    if state != "inspected" or types is None:
        _field_error(field, "FamilyManager.Types", state, error or "Family types are unavailable.")
        return field
    parameter_state, parameters, parameter_error = _read(manager, "Parameters")
    if parameter_state != "inspected" or parameters is None:
        _field_error(field, "FamilyManager.Parameters", parameter_state,
                     parameter_error or "Family parameters are unavailable for type values.")
        parameters = []
    type_parameter_value_count = 0
    try:
        for index, family_type in enumerate(types):
            if index >= MAX_RECORDS:
                field["warnings"].append(_warning("FamilyManager.Types", "failed",
                    "Type inventory reached the {} record limit.".format(MAX_RECORDS)))
                break
            name_state, name, name_error = _read(family_type, "Name")
            if name_state != "inspected":
                _field_error(field, "FamilyType.Name", name_state, name_error)
            type_record = {"name": name if name_state == "inspected" else None,
                           "parameter_values": []}
            try:
                for parameter_index, parameter in enumerate(parameters):
                    if type_parameter_value_count >= MAX_TYPE_PARAMETER_VALUES:
                        field["warnings"].append(_warning("FamilyType.parameter_values", "failed",
                            "Type/parameter value inventory reached the {} record limit.".format(
                                MAX_TYPE_PARAMETER_VALUES)))
                        break
                    if parameter_index >= MAX_RECORDS:
                        field["warnings"].append(_warning("FamilyType.parameter_values", "failed",
                            "Type parameter-value inventory reached the {} record limit.".format(MAX_RECORDS)))
                        break
                    pstate, parameter_name, parameter_name_error = _parameter_name(parameter)
                    if pstate != "inspected":
                        _field_error(field, "FamilyParameter.Definition.Name", pstate, parameter_name_error)
                        continue
                    value_record = {"parameter_name": parameter_name}
                    has_state, has_value, has_error = _call(family_type, "HasValue", parameter)
                    if has_state != "inspected":
                        _field_error(field, "FamilyType.HasValue", has_state, has_error)
                        value_record["value"] = None
                        value_record["status"] = has_state
                    elif not has_value:
                        value_record["value"] = None
                        value_record["status"] = "not_applicable"
                    else:
                        storage_state, storage, storage_error = _read(parameter, "StorageType")
                        if storage_state != "inspected":
                            _field_error(field, "FamilyParameter.StorageType", storage_state, storage_error)
                            value_record["value"] = None
                            value_record["status"] = storage_state
                        else:
                            storage_name = _string(storage)
                            methods = {"Double": "AsDouble", "Integer": "AsInteger",
                                       "String": "AsString", "ElementId": "AsElementId"}
                            method = methods.get(storage_name)
                            if method is None:
                                value_record["value"] = None
                                value_record["status"] = "unavailable"
                                _field_error(field, "FamilyParameter.StorageType", "unavailable",
                                             "Unsupported storage type: {}".format(storage_name))
                            else:
                                value_state, value, value_error = _call(family_type, method, parameter)
                                if value_state == "inspected":
                                    value_record["value"] = _string(value) if storage_name == "ElementId" else value
                                    value_record["status"] = "inspected"
                                else:
                                    value_record["value"] = None
                                    value_record["status"] = value_state
                                    _field_error(field, "FamilyType." + method, value_state, value_error)
                    type_record["parameter_values"].append(value_record)
                    type_parameter_value_count += 1
            except Exception as exc:
                _field_error(field, "FamilyType.parameter_values", "failed", exc)
            field["records"].append(type_record)
            if type_parameter_value_count >= MAX_TYPE_PARAMETER_VALUES:
                break
        field["status"] = "inspected" if not field["warnings"] else "partial"
    except Exception as exc:
        _field_error(field, "FamilyManager.Types.enumeration", "failed", exc)
    return field


def _inspect_parameters(document):
    field = _field()
    state, manager, error = _read(document, "FamilyManager")
    if state != "inspected" or manager is None:
        _field_error(field, "Document.FamilyManager", state, error or "FamilyManager is null.")
        return field
    state, parameters, error = _read(manager, "Parameters")
    if state != "inspected" or parameters is None:
        _field_error(field, "FamilyManager.Parameters", state, error or "Family parameters are unavailable.")
        return field
    try:
        for index, parameter in enumerate(parameters):
            if index >= MAX_RECORDS:
                field["warnings"].append(_warning("FamilyManager.Parameters", "failed",
                    "Parameter inventory reached the {} record limit.".format(MAX_RECORDS)))
                break
            record = {"field_statuses": {}}
            for prop, key in (("Definition", "definition"), ("IsShared", "is_shared"),
                              ("GUID", "guid"), ("IsInstance", "is_instance"),
                              ("IsReporting", "is_reporting"), ("StorageType", "storage_type"),
                              ("Formula", "formula"), ("IsDeterminedByFormula", "is_formula_driven"),
                              ("CanAssignFormula", "can_assign_formula")):
                if prop == "GUID":
                    shared_state, is_shared, shared_error = _read(parameter, "IsShared")
                    if shared_state == "inspected" and not is_shared:
                        record[key] = None
                        record["field_statuses"][key] = "not_applicable"
                        continue
                    if shared_state != "inspected":
                        _field_error(field, "FamilyParameter.IsShared", shared_state, shared_error)
                        record[key] = None
                        record["field_statuses"][key] = shared_state
                        continue
                state, value, error = _read(parameter, prop)
                if state == "unavailable" and prop in ("GUID", "Formula", "IsDeterminedByFormula", "CanAssignFormula"):
                    record[key] = None
                    record["field_statuses"][key] = "unavailable"
                    continue
                if state != "inspected":
                    _field_error(field, "FamilyParameter." + prop, state, error)
                    record[key] = None
                    record["field_statuses"][key] = state
                    continue
                record["field_statuses"][key] = "inspected"
                if prop == "Definition" and value is not None:
                    nstate, name, nerror = _read(value, "Name")
                    if nstate == "inspected":
                        record["name"] = name
                        record["field_statuses"]["name"] = "inspected"
                    else:
                        _field_error(field, "FamilyParameter.Definition.Name", nstate, nerror)
                        record["field_statuses"]["name"] = nstate
                elif prop == "GUID":
                    record[key] = str(value) if value is not None else None
                elif prop == "StorageType":
                    record[key] = _string(value)
                else:
                    record[key] = value
            assoc_state, associated, assoc_error = _read(parameter, "AssociatedParameters")
            if assoc_state == "inspected" and associated is not None:
                record["field_statuses"]["associated_parameters"] = "inspected"
                names = []
                try:
                    for associated_parameter in associated:
                        definition_state, definition, definition_error = _read(associated_parameter, "Definition")
                        if definition_state == "inspected" and definition is not None:
                            parameter_name_state, parameter_name, parameter_name_error = _read(definition, "Name")
                            if parameter_name_state == "inspected":
                                names.append(parameter_name)
                            else:
                                _field_error(field, "AssociatedParameter.Definition.Name", parameter_name_state, parameter_name_error)
                        else:
                            _field_error(field, "AssociatedParameter.Definition", definition_state, definition_error)
                except Exception as exc:
                    _field_error(field, "FamilyParameter.AssociatedParameters.enumeration", "failed", exc)
                record["associated_parameter_names"] = names
            elif assoc_state == "unavailable":
                record["associated_parameter_names"] = None
                record["field_statuses"]["associated_parameters"] = "unavailable"
            else:
                _field_error(field, "FamilyParameter.AssociatedParameters", assoc_state, assoc_error)
                record["associated_parameter_names"] = None
                record["field_statuses"]["associated_parameters"] = assoc_state
            field["records"].append(record)
        field["status"] = "inspected" if not field["warnings"] else "partial"
    except Exception as exc:
        _field_error(field, "FamilyManager.Parameters.enumeration", "failed", exc)
    return field


def _inspect_geometry(document, db):
    field = _field()
    field["coverage"] = "GenericForm-derived geometry only; other family element geometry is not enumerated."
    field["summary"] = {"forms": 0, "solids": 0, "meshes": 0,
                        "geometry_instances": 0, "other_objects": 0,
                        "positive_volume_solids": 0, "geometry_objects": 0}
    forms_field = _collect(document, db, "GenericForm")
    if forms_field["status"] != "inspected":
        return forms_field
    options_state, options, error = _construct(db, "Options")
    if options_state != "inspected":
        _field_error(field, "Options", options_state, error)
        return field
    for prop, value in (("DetailLevel", getattr(db, "ViewDetailLevel", None)),
                        ("IncludeNonVisibleObjects", True)):
        if value is None:
            continue
        try:
            setattr(options, prop, value.Fine if prop == "DetailLevel" else value)
        except Exception as exc:
            _field_error(field, "Options." + prop, "failed", exc)
    try:
        for form in forms_field["records"]:
            field["summary"]["forms"] += 1
            state, geometry, error = _call(form, "get_Geometry", options)
            if state != "inspected" or geometry is None:
                _field_error(field, "GenericForm.get_Geometry", state,
                             error or "Geometry is null.")
                continue
            _walk_geometry(geometry, db, field["summary"], 0, field["warnings"])
        field["status"] = "inspected" if not field["warnings"] else "partial"
    except Exception as exc:
        _field_error(field, "GenericForm.enumeration", "failed", exc)
    return field


def _walk_geometry(geometry, db, summary, depth, warnings):
    if depth > MAX_GEOMETRY_DEPTH:
        warnings.append(_warning("GeometryInstance", "failed", "Geometry nesting limit reached."))
        return
    for geometry_object in geometry:
        summary["geometry_objects"] += 1
        if summary["geometry_objects"] > MAX_GEOMETRY_OBJECTS:
            warnings.append(_warning("GeometryElement", "failed",
                "Geometry inspection reached the {} object limit.".format(MAX_GEOMETRY_OBJECTS)))
            return
        try:
            if isinstance(geometry_object, db.Solid):
                summary["solids"] += 1
                state, volume, error = _read(geometry_object, "Volume")
                if state != "inspected":
                    warnings.append(_warning("Solid.Volume", state, error))
                elif volume > 0:
                    summary["positive_volume_solids"] += 1
            elif isinstance(geometry_object, db.Mesh):
                summary["meshes"] += 1
            elif isinstance(geometry_object, db.GeometryInstance):
                summary["geometry_instances"] += 1
                state, nested, error = _call(geometry_object, "GetSymbolGeometry")
                if state == "inspected" and nested is not None:
                    _walk_geometry(nested, db, summary, depth + 1, warnings)
                else:
                    warnings.append(_warning("GeometryInstance.GetSymbolGeometry", state, error))
            else:
                summary["other_objects"] += 1
        except Exception as exc:
            warnings.append(_warning("GeometryObject", "failed", exc))


def _inspect_materials_and_subcategories(document, owner, db):
    field = _field()
    field["coverage"] = "Family category subcategories and materials referenced by GenericForm geometry faces."
    category_state, category, error = _read(owner, "FamilyCategory")
    if category_state != "inspected" or category is None:
        _field_error(field, "Family.FamilyCategory", category_state, error or "Category is null.")
    else:
        state, subcategories, error = _read(category, "SubCategories")
        if state != "inspected" or subcategories is None:
            _field_error(field, "Category.SubCategories", state, error or "Subcategories are unavailable.")
        else:
            try:
                for subcategory in subcategories:
                    name_state, name, name_error = _read(subcategory, "Name")
                    if name_state != "inspected":
                        _field_error(field, "SubCategory.Name", name_state, name_error)
                    field["records"].append({"kind": "subcategory", "name": name})
            except Exception as exc:
                _field_error(field, "Category.SubCategories.enumeration", "failed", exc)

    forms_field = _collect(document, db, "GenericForm")
    if forms_field["status"] == "inspected":
        for form in forms_field["records"]:
            state, subcategory, error = _read(form, "Subcategory")
            if state == "inspected" and subcategory is not None:
                name_state, name, name_error = _read(subcategory, "Name")
                if name_state == "inspected":
                    field["records"].append({"kind": "form_subcategory", "name": name})
                else:
                    _field_error(field, "GenericForm.Subcategory.Name", name_state, name_error)
            elif state not in ("unavailable", "inspected"):
                _field_error(field, "GenericForm.Subcategory", state, error)
    else:
        field["warnings"].extend(forms_field["warnings"])

    # Material assignments are read from geometry faces; only unique element IDs are reported.
    options_state, options, error = _construct(db, "Options")
    if options_state == "inspected":
        try:
            options.IncludeNonVisibleObjects = True
            material_ids = {}
            for form in forms_field.get("records", []):
                state, geometry, error = _call(form, "get_Geometry", options)
                if state != "inspected" or geometry is None:
                    _field_error(field, "GenericForm.get_Geometry.materials", state, error)
                    continue
                _collect_material_ids(geometry, db, material_ids, 0, field["warnings"])
            for material_id in sorted(material_ids.values(), key=_string):
                state, material, error = _call(document, "GetElement", material_id)
                if state != "inspected" or material is None:
                    _field_error(field, "Document.GetElement(material)", state, error)
                    continue
                name_state, name, name_error = _read(material, "Name")
                field["records"].append({"kind": "material",
                                         "id": _string(material_id),
                                         "name": name if name_state == "inspected" else None})
                if name_state != "inspected":
                    _field_error(field, "Material.Name", name_state, name_error)
        except Exception as exc:
            _field_error(field, "Material.geometry_scan", "failed", exc)
    else:
        _field_error(field, "Options", options_state, error)
    field["records"] = _deduplicate_records(field["records"])
    field["status"] = "inspected" if not field["warnings"] else "partial"
    return field


def _collect_material_ids(geometry, db, material_ids, depth, warnings):
    if depth > MAX_GEOMETRY_DEPTH:
        warnings.append(_warning("GeometryInstance", "failed", "Geometry nesting limit reached."))
        return
    for obj in geometry:
        if len(material_ids) >= MAX_RECORDS:
            warnings.append(_warning("Geometry.MaterialElementId", "failed",
                "Material inspection reached the {} unique ID limit.".format(MAX_RECORDS)))
            return
        if isinstance(obj, db.Solid):
            try:
                for face in obj.Faces:
                    state, material_id, error = _read(face, "MaterialElementId")
                    if state == "inspected" and material_id is not None:
                        material_ids[_string(material_id)] = material_id
                    elif state != "inspected":
                        warnings.append(_warning("Face.MaterialElementId", state, error))
            except Exception as exc:
                warnings.append(_warning("Solid.Faces", "failed", exc))
        elif isinstance(obj, db.GeometryInstance):
            state, nested, error = _call(obj, "GetSymbolGeometry")
            if state == "inspected" and nested is not None:
                _collect_material_ids(nested, db, material_ids, depth + 1, warnings)
            else:
                warnings.append(_warning("GeometryInstance.GetSymbolGeometry", state, error))


def _inspect_visibility(document, db):
    field = _field()
    field["coverage"] = "GenericForm visibility properties only."
    forms_field = _collect(document, db, "GenericForm")
    if forms_field["status"] != "inspected":
        return forms_field
    for form in forms_field["records"]:
        record = {}
        for prop, key in (("Name", "name"), ("Visible", "visible")):
            state, value, error = _read(form, prop)
            if state == "inspected":
                record[key] = value
            else:
                _field_error(field, "GenericForm." + prop, state, error)
        state, visibility, error = _call(form, "GetVisibility")
        if state == "inspected" and visibility is not None:
            for prop in ("IsShownInFine", "IsShownInMedium", "IsShownInCoarse",
                         "IsShownInLeftRight", "IsShownInFrontBack", "IsShownInTopBottom",
                         "IsShownInPlanRCPCut", "IsShownOnlyWhenCut"):
                prop_state, value, prop_error = _read(visibility, prop)
                if prop_state == "inspected":
                    record[prop] = value
                else:
                    _field_error(field, "FamilyElementVisibility." + prop, prop_state, prop_error)
        else:
            _field_error(field, "GenericForm.GetVisibility", state, error)
        field["records"].append(record)
    field["status"] = "inspected" if not field["warnings"] else "partial"
    return field


def _inspect_references_constraints(document, db):
    field = _field()
    field["coverage"] = "ReferencePlane and Dimension elements; other constraint types are not enumerated."
    field["summary"] = {"reference_planes": 0, "dimensions": 0, "locked_dimensions": 0}
    for class_name, record_kind in (("ReferencePlane", "reference_plane"),
                                    ("Dimension", "dimension")):
        collected = _collect(document, db, class_name)
        if collected["status"] != "inspected":
            field["warnings"].extend(collected["warnings"])
            continue
        for element in collected["records"]:
            if class_name == "ReferencePlane":
                field["summary"]["reference_planes"] += 1
                name_state, name, name_error = _read(element, "Name")
                field["records"].append({"kind": record_kind,
                                         "name": name if name_state == "inspected" else None})
                if name_state != "inspected":
                    _field_error(field, "ReferencePlane.Name", name_state, name_error)
            else:
                field["summary"]["dimensions"] += 1
                lock_state, locked, lock_error = _read(element, "IsLocked")
                label_state, label, label_error = _read(element, "FamilyLabel")
                label_name = None
                if label_state == "inspected" and label is not None:
                    def_state, definition, def_error = _read(label, "Definition")
                    if def_state == "inspected" and definition is not None:
                        name_state, label_name, name_error = _read(definition, "Name")
                        if name_state != "inspected":
                            _field_error(field, "Dimension.FamilyLabel.Definition.Name", name_state, name_error)
                    elif def_state != "unavailable":
                        _field_error(field, "Dimension.FamilyLabel.Definition", def_state, def_error)
                if lock_state == "inspected" and locked:
                    field["summary"]["locked_dimensions"] += 1
                elif lock_state != "inspected":
                    _field_error(field, "Dimension.IsLocked", lock_state, lock_error)
                if label_state not in ("inspected", "unavailable"):
                    _field_error(field, "Dimension.FamilyLabel", label_state, label_error)
                field["records"].append({"kind": record_kind,
                                         "is_locked": locked if lock_state == "inspected" else None,
                                         "family_parameter": label_name})
    field["status"] = "inspected" if not field["warnings"] else "partial"
    return field


def _inspect_nested_families(document, db):
    field = _collect(document, db, "FamilyInstance")
    if field["status"] != "inspected":
        return field
    field["coverage"] = "FamilyInstance elements found in this family document."
    instances = field["records"]
    field["records"] = []
    for instance in instances:
        symbol_state, symbol, error = _read(instance, "Symbol")
        if symbol_state != "inspected" or symbol is None:
            _field_error(field, "FamilyInstance.Symbol", symbol_state, error or "Symbol is null.")
            continue
        family_state, family, error = _read(symbol, "Family")
        if family_state != "inspected" or family is None:
            _field_error(field, "FamilySymbol.Family", family_state, error or "Nested family is null.")
            continue
        name_state, name, error = _read(family, "Name")
        shared_state, shared, shared_error = _read(family, "IsShared")
        field["records"].append({"name": name if name_state == "inspected" else None,
                                 "is_shared": shared if shared_state == "inspected" else None,
                                 "is_shared_status": shared_state})
        if name_state != "inspected":
            _field_error(field, "Family.Name", name_state, error)
        if shared_state != "inspected":
            _field_error(field, "Family.IsShared", shared_state, shared_error)
    field["records"] = _deduplicate_records(field["records"])
    field["status"] = "inspected" if not field["warnings"] else "partial"
    return field


def _inspect_hosting(owner, document, db):
    field = _field()
    field["coverage"] = "FamilyPlacementType plus Host on nested FamilyInstance elements."
    field["summary"] = {"nested_instances": 0, "instances_with_host": 0}
    state, placement_type, error = _read(owner, "FamilyPlacementType")
    if state != "inspected":
        _field_error(field, "Family.FamilyPlacementType", state, error)
    else:
        field["records"].append({"family_placement_type": _string(placement_type)})
    instances = _collect(document, db, "FamilyInstance")
    if instances["status"] == "inspected":
        hosted = 0
        for instance in instances["records"]:
            state, host, error = _read(instance, "Host")
            if state != "inspected":
                _field_error(field, "FamilyInstance.Host", state, error)
            elif host is not None:
                hosted += 1
        field["summary"]["nested_instances"] = len(instances["records"])
        field["summary"]["instances_with_host"] = hosted
    else:
        field["warnings"].extend(instances["warnings"])
    field["status"] = "inspected" if not field["warnings"] else "partial"
    return field


def _inspect_connectors(document, db):
    field = _collect(document, db, "ConnectorElement")
    if field["status"] != "inspected":
        return field
    field["coverage"] = "ConnectorElement records and the listed system/shape fields."
    connectors = field["records"]
    field["records"] = []
    for connector in connectors:
        record = {}
        for prop, key in (("Domain", "domain"), ("SystemClassification", "system_classification"),
                          ("Shape", "shape"), ("IsPrimary", "is_primary"),
                          ("Radius", "radius"), ("Width", "width"), ("Height", "height")):
            state, value, error = _read(connector, prop)
            if state == "inspected":
                record[key] = _string(value) if key in ("domain", "system_classification", "shape") else value
            elif state == "unavailable" and prop in ("Radius", "Width", "Height"):
                record[key] = None
            else:
                _field_error(field, "ConnectorElement." + prop, state, error)
                record[key] = None
        field["records"].append(record)
    field["status"] = "inspected" if not field["warnings"] else "partial"
    return field


def _collect(document, db, class_name):
    field = _field()
    state, element_class, error = _read(db, class_name)
    if state != "inspected":
        _field_error(field, "DB." + class_name, state, error)
        return field
    try:
        collector = db.FilteredElementCollector(document).OfClass(element_class)
        for index, element in enumerate(collector):
            if index >= MAX_RECORDS:
                field["warnings"].append(_warning("FilteredElementCollector." + class_name,
                    "failed", "Element inventory reached the {} record limit.".format(MAX_RECORDS)))
                break
            field["records"].append(element)
        field["status"] = "inspected" if not field["warnings"] else "partial"
    except Exception as exc:
        _field_error(field, "FilteredElementCollector." + class_name, "failed", exc)
    return field


def _construct(db, name):
    state, constructor, error = _read(db, name)
    if state != "inspected":
        return state, None, error
    try:
        return "inspected", constructor(), None
    except Exception as exc:
        return "failed", None, _exception_text(exc)


def _call(obj, name, *args):
    state, method, error = _read(obj, name)
    if state != "inspected":
        return state, None, error
    if not callable(method):
        return "unavailable", None, "Member is not callable."
    try:
        return "inspected", method(*args), None
    except Exception as exc:
        return "failed", None, _exception_text(exc)


def _read(obj, name):
    if obj is None:
        return "unavailable", None, "Object is null."
    try:
        return "inspected", getattr(obj, name), None
    except AttributeError as exc:
        if _member_exists(obj, name):
            return "failed", None, _exception_text(exc)
        return "unavailable", None, "Member is not available."
    except Exception as exc:
        return "failed", None, _exception_text(exc)


def _member_exists(obj, name):
    try:
        clr_type = obj.GetType()
        if clr_type.GetProperty(name) is not None or clr_type.GetMethod(name) is not None:
            return True
    except Exception:
        pass
    try:
        if name in dir(obj):
            return True
    except Exception:
        pass
    for cls in getattr(obj, "__class__", type(obj)).__mro__:
        if name in getattr(cls, "__dict__", {}):
            return True
    return False


def _field():
    return {"status": "not_inspected", "compatibility_classification": "requires_manual_review",
            "records": [], "warnings": []}


def _field_error(field, name, status, error):
    field["status"] = "partial"
    field["warnings"].append(_warning(name, status, error))


def _warning(name, status, error):
    return {"field": name, "status": status, "message": _exception_text(error)}


def _exception_text(error):
    if isinstance(error, BaseException):
        return "{}: {}".format(type(error).__name__, str(error)[:240])
    return str(error or "Unknown access error")[:240]


def _major_version(value):
    try:
        parsed = int(str(value).strip())
        return parsed if 2000 <= parsed <= 2100 else None
    except Exception:
        return None


def _string(value):
    try:
        return str(value) if value is not None else None
    except Exception:
        return None


def _parameter_name(parameter):
    state, definition, error = _read(parameter, "Definition")
    if state != "inspected" or definition is None:
        return state, None, error or "Family parameter definition is null."
    return _read(definition, "Name")


def _deduplicate_records(records):
    result = []
    seen = set()
    for record in records:
        key = tuple(sorted((str(k), str(v)) for k, v in record.items()))
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result
