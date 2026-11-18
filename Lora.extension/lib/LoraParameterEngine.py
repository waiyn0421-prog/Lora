# -*- coding: utf-8 -*-
"""Shared parameter discovery and safe project-binding helpers for Lora.

Designed for pyRevit / IronPython-compatible usage across Revit 2023-2026.
All project modifications must be called from a valid Revit API context.
"""

from pyrevit import DB


class ParameterIssue(Exception):
    pass


def _safe_get(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def iter_bindings(doc):
    """Yield (definition, binding) for all project parameter bindings."""
    iterator = doc.ParameterBindings.ForwardIterator()
    iterator.Reset()
    while iterator.MoveNext():
        yield iterator.Key, iterator.Current


def definition_guid(definition):
    """Return a stable shared-parameter GUID string when available."""
    try:
        guid = definition.GUID
        return str(guid).lower()
    except Exception:
        return None


def find_bound_definition(doc, name, guid=None):
    target_guid = str(guid).lower() if guid else None
    for definition, binding in iter_bindings(doc):
        try:
            if target_guid and definition_guid(definition) == target_guid:
                return definition, binding
            if not target_guid and str(definition.Name).lower() == str(name).lower():
                return definition, binding
        except Exception:
            continue
    return None, None


def find_shared_definition(shared_file, name):
    """Search all groups; do not assume the definition is in Lora's group."""
    if shared_file is None:
        return None, None
    for group in shared_file.Groups:
        try:
            definition = group.Definitions.get_Item(name)
            if definition:
                return group, definition
        except Exception:
            pass
        try:
            for definition in group.Definitions:
                if str(definition.Name).lower() == str(name).lower():
                    return group, definition
        except Exception:
            pass
    return None, None


def spec_for(kind):
    """Resolve parameter data type for Revit 2023-2026 APIs."""
    kind = str(kind or "Text").strip().lower()
    try:
        if kind == "text":
            return DB.SpecTypeId.String.Text
        if kind == "length":
            return DB.SpecTypeId.Length
        if kind == "integer":
            return DB.SpecTypeId.Int.Integer
        if kind == "number":
            return DB.SpecTypeId.Number
        if kind in ("yes/no", "yesno", "boolean", "bool"):
            return DB.SpecTypeId.Boolean.YesNo
    except Exception:
        pass
    legacy = {
        "text": "Text", "length": "Length", "integer": "Integer",
        "number": "Number", "yes/no": "YesNo", "yesno": "YesNo",
        "boolean": "YesNo", "bool": "YesNo"
    }
    try:
        return getattr(DB.ParameterType, legacy[kind])
    except Exception:
        raise ParameterIssue("Unsupported parameter data type: {}".format(kind))


def group_type_id(group_name):
    group_name = str(group_name or "Identity Data")
    modern_names = {
        "Identity Data": "IdentityData", "Constraints": "Constraints",
        "Dimensions": "Geometry", "Data": "Data", "Text": "Text",
        "Geometry": "Geometry", "Graphics": "Graphics",
        "Materials and Finishes": "MaterialsAndFinishes", "Phasing": "Phasing",
        "Other": "General"
    }
    try:
        return getattr(DB.GroupTypeId, modern_names.get(group_name, "IdentityData"))
    except Exception:
        legacy_names = {
            "Identity Data": "PG_IDENTITY_DATA", "Constraints": "PG_CONSTRAINTS",
            "Dimensions": "PG_GEOMETRY", "Data": "PG_DATA", "Text": "PG_TEXT",
            "Geometry": "PG_GEOMETRY", "Graphics": "PG_GRAPHICS",
            "Materials and Finishes": "PG_MATERIALS", "Phasing": "PG_PHASING",
            "Other": "PG_DATA"
        }
        return getattr(DB.BuiltInParameterGroup, legacy_names.get(group_name, "PG_IDENTITY_DATA"))


def _definition_data_type(definition):
    try:
        return definition.GetDataType().TypeId
    except Exception:
        try:
            return str(definition.ParameterType)
        except Exception:
            return None


def _requested_data_type(kind):
    try:
        spec = spec_for(kind)
        return spec.TypeId
    except Exception:
        return str(spec_for(kind))


def ensure_shared_definition(app, shared_file, group_name, name, kind, description="", guid=None):
    """Return (definition, action); refuse same-name incompatible definitions."""
    group, existing = find_shared_definition(shared_file, name)
    requested = _requested_data_type(kind)
    if existing:
        existing_type = _definition_data_type(existing)
        if existing_type and requested and existing_type != requested:
            # Legacy ParameterType string differs from ForgeTypeId; only hard-fail when both are comparable modern ids.
            if "." in str(existing_type) and "." in str(requested):
                raise ParameterIssue(
                    "Parameter '{}' already exists with a different data type. Existing: {}; requested: {}. "
                    "No definition was changed.".format(name, existing_type, requested))
        if guid:
            current_guid = definition_guid(existing)
            if current_guid and current_guid != str(guid).lower():
                raise ParameterIssue("Parameter '{}' exists with a different GUID.".format(name))
        return existing, "reused definition"

    group = shared_file.Groups.get_Item(group_name)
    if group is None:
        group = shared_file.Groups.Create(group_name)
    options = DB.ExternalDefinitionCreationOptions(name, spec_for(kind))
    options.Visible = True
    try:
        options.Description = description or "Created by Lora Annotation Tag Studio"
    except Exception:
        pass
    if guid:
        try:
            import System
            options.GUID = System.Guid(str(guid))
        except Exception as ex:
            raise ParameterIssue("Could not apply requested GUID for '{}': {}".format(name, ex))
    definition = group.Definitions.Create(options)
    return definition, "created definition"


def _category_set_from_binding(app, binding):
    catset = app.Create.NewCategorySet()
    if binding is not None:
        try:
            for category in binding.Categories:
                catset.Insert(category)
        except Exception:
            pass
    return catset


def ensure_project_binding(app, doc, definition, categories, is_instance=True, group_name="Identity Data"):
    """Bind definition while preserving existing category bindings when possible."""
    existing_def, existing_binding = find_bound_definition(doc, definition.Name, definition_guid(definition))
    catset = _category_set_from_binding(app, existing_binding)
    requested_ids = set()
    for category in categories:
        if category is None:
            continue
        try:
            cid = str(category.Id.Value)
        except Exception:
            cid = str(category.Id.IntegerValue)
        if cid not in requested_ids:
            catset.Insert(category)
            requested_ids.add(cid)
    if catset.Size == 0:
        raise ParameterIssue("No valid categories were selected for '{}'".format(definition.Name))

    # Preserve existing binding kind when updating. Changing instance/type can alter project behavior.
    if existing_binding is not None:
        try:
            is_instance = isinstance(existing_binding, DB.InstanceBinding)
        except Exception:
            pass
    binding = app.Create.NewInstanceBinding(catset) if is_instance else app.Create.NewTypeBinding(catset)
    group_id = group_type_id(group_name)
    if existing_def is not None:
        ok = doc.ParameterBindings.ReInsert(existing_def, binding, group_id)
        if not ok:
            raise ParameterIssue("Revit could not update the binding for '{}'".format(definition.Name))
        return "updated binding"
    ok = doc.ParameterBindings.Insert(definition, binding, group_id)
    if not ok:
        # A binding with a name collision may exist even when GUID matching did not find it.
        name_def, _ = find_bound_definition(doc, definition.Name)
        if name_def is not None:
            raise ParameterIssue("A project parameter named '{}' already exists with a different identity. Resolve the conflict manually.".format(definition.Name))
        raise ParameterIssue("Revit could not create the project binding for '{}'".format(definition.Name))
    return "created binding"


def inspect_parameter(doc, shared_file, name, kind=None, guid=None):
    """Return a human-readable status without changing the project."""
    group, shared_def = find_shared_definition(shared_file, name)
    bound_def, binding = find_bound_definition(doc, name, guid or definition_guid(shared_def) if shared_def else guid)
    if shared_def and bound_def:
        return {"status": "definition and binding exist", "shared_definition": shared_def,
                "bound_definition": bound_def, "binding": binding, "group": group}
    if shared_def:
        return {"status": "definition exists; project binding missing", "shared_definition": shared_def,
                "bound_definition": None, "binding": None, "group": group}
    if bound_def:
        return {"status": "project binding exists; shared-file definition not found", "shared_definition": None,
                "bound_definition": bound_def, "binding": binding, "group": None}
    return {"status": "definition and binding missing", "shared_definition": None,
            "bound_definition": None, "binding": None, "group": None}
