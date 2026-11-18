# -*- coding: utf-8 -*-
"""Lora Annotation Tag Studio — foundation release.

This first implementation provides category-aware custom shared-parameter
provisioning, project binding, definition inspection, external-family inspection,
and a portable design-definition file. Family label/graphics authoring is the
next implementation stage and is deliberately not represented as complete here.
"""
import os
import sys
import json
import datetime

from pyrevit import revit, DB, forms, script

_LIB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from LoraDialogTheme import lora_select_from_list, lora_alert, lora_confirm
from LoraParameterEngine import (
    ParameterIssue, ensure_shared_definition, ensure_project_binding,
    inspect_parameter, find_shared_definition, find_bound_definition
)

clr_import_error = None
try:
    import clr
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    from System.Windows.Markup import XamlReader
except Exception as _ex:
    clr_import_error = _ex


doc = revit.doc
app = __revit__.Application
logger = script.get_logger()

# English names are resolved to BuiltInCategory where available. The list is
# intentionally capability-driven: unsupported/localized categories are omitted.
CATEGORY_MAP = [
    ("Doors", "OST_Doors"), ("Windows", "OST_Windows"),
    ("Rooms", "OST_Rooms"), ("Spaces", "OST_MEPSpaces"),
    ("Walls", "OST_Walls"), ("Floors", "OST_Floors"),
    ("Ceilings", "OST_Ceilings"), ("Roofs", "OST_Roofs"),
    ("Structural Columns", "OST_StructuralColumns"),
    ("Structural Framing", "OST_StructuralFraming"),
    ("Structural Foundations", "OST_StructuralFoundation"),
    ("Pipes", "OST_PipeCurves"), ("Pipe Fittings", "OST_PipeFitting"),
    ("Pipe Accessories", "OST_PipeAccessory"), ("Ducts", "OST_DuctCurves"),
    ("Duct Fittings", "OST_DuctFitting"), ("Duct Accessories", "OST_DuctAccessory"),
    ("Cable Trays", "OST_CableTray"), ("Cable Tray Fittings", "OST_CableTrayFitting"),
    ("Conduits", "OST_Conduit"), ("Conduit Fittings", "OST_ConduitFitting"),
    ("Mechanical Equipment", "OST_MechanicalEquipment"),
    ("Electrical Fixtures", "OST_ElectricalFixtures"),
    ("Lighting Fixtures", "OST_LightingFixtures"),
    ("Generic Models", "OST_GenericModel"),
]
PARAMETER_TYPES = ["Text", "Length", "Integer", "Number", "Yes/No"]
PARAMETER_GROUPS = ["Identity Data", "Constraints", "Dimensions", "Data", "Text", "Geometry", "Graphics", "Materials and Finishes", "Phasing", "Other"]


def get_category(name, bic_name):
    try:
        bic = getattr(DB.BuiltInCategory, bic_name)
        cat = doc.Settings.Categories.get_Item(bic)
        if cat and cat.AllowsBoundParameters:
            return cat
    except Exception:
        pass
    try:
        cat = doc.Settings.Categories.get_Item(name)
        if cat and cat.AllowsBoundParameters:
            return cat
    except Exception:
        pass
    return None


def available_categories():
    result = []
    for name, bic in CATEGORY_MAP:
        cat = get_category(name, bic)
        if cat is not None:
            result.append((name, bic, cat))
    return result


def shared_parameter_path():
    current = app.SharedParametersFilename
    options = ["Use current shared parameter file", "Select or create a Lora shared parameter file"]
    choice = lora_select_from_list(options, title="Lora · Shared Parameter Source", button_name="Continue")
    if not choice:
        return None
    if choice == options[0]:
        if current and os.path.exists(current):
            return current
        lora_alert("Revit has no accessible shared parameter file configured. Select or create a Lora shared parameter file.", title="Lora Parameter Setup", warn_icon=True)
        choice = options[1]
    path = forms.pick_file(file_ext="txt", multi_file=False, title="Select existing shared parameter file")
    if path and os.path.isfile(path):
        return path
    path = forms.save_file(file_ext="txt", default_name="Lora_SharedParameters.txt", title="Create Lora shared parameter file")
    if not path:
        return None
    if not os.path.exists(path):
        with open(path, "w") as stream:
            stream.write("# This is a Revit shared parameter file.\n")
            stream.write("# Do not edit manually.\n")
            stream.write("*META\tVERSION\tMINVERSION\nMETA\t2\t1\n")
            stream.write("*GROUP\tID\tNAME\n")
            stream.write("*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\n")
    return path


def parse_field_lines(raw, advanced):
    """Simple mode: one name per line. Advanced mode: name|type|binding|group|description."""
    fields = []
    errors = []
    seen = set()
    for line_no, raw_line in enumerate((raw or "").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        name = parts[0] if parts else ""
        if not name:
            continue
        key = name.lower()
        if key in seen:
            errors.append("Line {}: duplicate parameter name '{}'".format(line_no, name))
            continue
        seen.add(key)
        kind = parts[1] if advanced and len(parts) > 1 and parts[1] else "Text"
        binding = parts[2].lower() if advanced and len(parts) > 2 and parts[2] else "instance"
        group = parts[3] if advanced and len(parts) > 3 and parts[3] else "Identity Data"
        description = parts[4] if advanced and len(parts) > 4 else "Created by Lora Annotation Tag Studio"
        if kind.lower() not in [x.lower() for x in PARAMETER_TYPES]:
            errors.append("Line {}: unsupported type '{}'. Use {}.".format(line_no, kind, ", ".join(PARAMETER_TYPES)))
            continue
        kind = [x for x in PARAMETER_TYPES if x.lower() == kind.lower()][0]
        if binding not in ("instance", "type"):
            errors.append("Line {}: binding must be Instance or Type.".format(line_no))
            continue
        if not name.replace("_", "").isalnum() or name[0].isdigit():
            errors.append("Line {}: parameter name '{}' contains unsupported characters.".format(line_no, name))
            continue
        if group not in PARAMETER_GROUPS:
            errors.append("Line {}: unknown group '{}'. Use {}.".format(line_no, group, ", ".join(PARAMETER_GROUPS)))
            continue
        fields.append({"name": name, "type": kind, "binding": binding, "group": group,
                       "description": description})
    return fields, errors


def show_field_editor(advanced, category_name):
    if clr_import_error:
        lora_alert("The Lora field editor could not load WPF: {}".format(clr_import_error), title="Lora Tag Studio", warn_icon=True)
        return None
    safe_category = "".join(ch for ch in str(category_name).replace(" ", "_") if ch.isalnum() or ch == "_")
    if advanced:
        sample = "Lora_{}_Tag_Description | Text | Instance | Identity Data | Tag description\nLora_{}_Tag_Reference | Text | Instance | Identity Data | Reference information\nLora_{}_Tag_Code | Text | Type | Identity Data | Classification code".format(safe_category, safe_category, safe_category)
        help_text = "Advanced format: Name | DataType | Instance/Type | ParameterGroup | Description\nTypes: Text, Length, Integer, Number, Yes/No. Groups: Identity Data, Constraints, Dimensions, Data, Text, Geometry, Graphics, Materials and Finishes, Phasing, Other.\nOnly enter custom fields that should be created or checked. Existing compatible definitions are reused."
        title = "Lora · Advanced Parameter Designer"
    else:
        sample = "Lora_{}_Tag_Description\nLora_{}_Tag_Reference\nLora_{}_Tag_Code".format(safe_category, safe_category, safe_category)
        help_text = "Enter one custom parameter name per line. Lora uses Text, Instance binding and Identity Data by default. Switch to Advanced mode for other data types or type parameters."
        title = "Lora · Simple Parameter Setup"
    xaml = r'''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Title="Lora Parameter Designer" Width="720" Height="560" MinWidth="620" MinHeight="470" WindowStartupLocation="CenterScreen" Background="#151C2D" Foreground="#F3F5FF" FontFamily="Segoe UI" FontSize="13">
<Grid Margin="20"><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
<StackPanel Grid.Row="0" Margin="0,0,0,12"><TextBlock Text="LORA  /  ANNOTATION TAG STUDIO" Foreground="#B18CFF" FontSize="10" FontWeight="Bold"/><TextBlock x:Name="Heading" Text="Parameter Designer" FontSize="23" FontWeight="SemiBold" Margin="0,4,0,4"/><TextBlock x:Name="Help" TextWrapping="Wrap" Foreground="#AEB8D0" FontSize="12"/></StackPanel>
<TextBlock Grid.Row="1" Text="CUSTOM PARAMETERS" Foreground="#B18CFF" FontSize="10" FontWeight="Bold" Margin="0,0,0,6"/>
<TextBox x:Name="Fields" Grid.Row="2" AcceptsReturn="True" AcceptsTab="True" TextWrapping="NoWrap" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Auto" FontFamily="Consolas" FontSize="13" Padding="12" Background="#202A40" Foreground="#F3F5FF" BorderBrush="#596584" CaretBrush="#B18CFF"/>
<Grid Grid.Row="3" Margin="0,12,0,0"><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="125"/><ColumnDefinition Width="100"/></Grid.ColumnDefinitions><TextBlock Text="LORA  •  PARAMETER AUTOMATION" Foreground="#7886A5" FontSize="10" VerticalAlignment="Center"/><Button x:Name="Cancel" Grid.Column="1" Content="Cancel" Height="36" Margin="0,0,8,0" Background="#202A40" Foreground="White" BorderBrush="#596584"/><Button x:Name="Continue" Grid.Column="2" Content="Continue" Height="36" Background="#8057E8" Foreground="White" BorderBrush="#A783FF" FontWeight="SemiBold"/></Grid></Grid></Window>'''
    try:
        window = XamlReader.Parse(xaml)
        window.Title = title
        window.FindName("Heading").Text = title
        window.FindName("Help").Text = help_text
        window.FindName("Fields").Text = sample
        result = {"text": None}
        def accept(sender, args):
            result["text"] = window.FindName("Fields").Text
            window.DialogResult = True
            window.Close()
        def cancel(sender, args):
            window.DialogResult = False
            window.Close()
        window.FindName("Continue").Click += accept
        window.FindName("Cancel").Click += cancel
        window.ShowDialog()
        return result["text"]
    except Exception as ex:
        logger.exception("Field editor failed")
        lora_alert("Could not open the parameter editor:\n{}".format(ex), title="Lora Tag Studio", warn_icon=True)
        return None


def category_by_display(name, categories):
    for display, bic, cat in categories:
        if display == name:
            return cat
    return None


def build_definition():
    advanced = lora_select_from_list(["Simple mode · one parameter name per line", "Advanced mode · choose type, binding and group"], title="Lora Tag Studio · Editing Mode", button_name="Continue")
    if not advanced:
        return
    is_advanced = advanced.startswith("Advanced")
    category_options = [x[0] for x in available_categories()]
    if not category_options:
        lora_alert("No supported project categories were found in this Revit project.", title="Lora Tag Studio", warn_icon=True)
        return
    category_name = lora_select_from_list(category_options, title="Choose Tag Category", button_name="Next")
    if not category_name:
        return
    raw = show_field_editor(is_advanced, category_name)
    if raw is None:
        return
    fields, parse_errors = parse_field_lines(raw, is_advanced)
    if parse_errors:
        lora_alert("Please correct these entries before continuing:\n\n" + "\n".join(parse_errors), title="Lora Parameter Validation", warn_icon=True)
        return
    if not fields:
        lora_alert("Enter at least one custom parameter name.", title="Lora Parameter Setup", warn_icon=True)
        return
    file_path = shared_parameter_path()
    if not file_path:
        return
    old_path = app.SharedParametersFilename
    try:
        app.SharedParametersFilename = file_path
        shared_file = app.OpenSharedParameterFile()
    except Exception as ex:
        shared_file = None
        lora_alert("Revit could not open the shared parameter file:\n{}".format(ex), title="Lora Parameter Setup", warn_icon=True)
    if shared_file is None:
        try:
            app.SharedParametersFilename = old_path
        except Exception:
            pass
        return

    category = category_by_display(category_name, available_categories())
    if category is None:
        lora_alert("The selected category is no longer available.", title="Lora Parameter Setup", warn_icon=True)
        return

    # Read-only status preview: no project changes yet.
    preview_lines = ["Category: {}".format(category_name), "Shared parameter file: {}".format(file_path), "", "Parameter setup plan:"]
    for field in fields:
        status = inspect_parameter(doc, shared_file, field["name"], field["type"])
        preview_lines.append("• {} ({}, {}) — {}".format(field["name"], field["type"], field["binding"].title(), status["status"]))
    preview_lines += ["", "Lora will reuse compatible definitions, create missing shared definitions, and add/update category bindings. Existing binding type is preserved when updating an existing binding.", "", "Continue and apply the parameter setup?"]
    if not lora_confirm("\n".join(preview_lines), title="Lora · Review Parameter Setup", yes_text="Apply Setup", no_text="Cancel"):
        return

    created_defs, reused_defs, bound_actions, errors = [], [], [], []
    transaction = DB.Transaction(doc, "Lora — Annotation Tag Parameter Setup")
    transaction.Start()
    try:
        for field in fields:
            try:
                definition, definition_action = ensure_shared_definition(
                    app, shared_file, "Lora Annotation Tag Parameters", field["name"],
                    field["type"], field.get("description", ""))
                if definition_action == "created definition":
                    created_defs.append(field["name"])
                else:
                    reused_defs.append(field["name"])
                action = ensure_project_binding(
                    app, doc, definition, [category],
                    is_instance=(field["binding"] == "instance"), group_name=field["group"])
                bound_actions.append("{} — {}".format(field["name"], action))
            except Exception as ex:
                errors.append("{}: {}".format(field["name"], ex))
        if errors and len(errors) == len(fields):
            transaction.RollBack()
        else:
            transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise
    finally:
        # Keep the selected file configured when successful; restore the prior path only if none was selected.
        try:
            if not file_path:
                app.SharedParametersFilename = old_path
        except Exception:
            pass

    summary = "Parameter setup completed.\n\nCreated definitions: {}\nReused definitions: {}\nBindings updated/created: {}\nIssues: {}".format(
        len(created_defs), len(reused_defs), len(bound_actions), len(errors))
    if created_defs:
        summary += "\n\nCreated:\n" + "\n".join(created_defs)
    if reused_defs:
        summary += "\n\nReused:\n" + "\n".join(reused_defs)
    if bound_actions:
        summary += "\n\nProject bindings:\n" + "\n".join(bound_actions)
    if errors:
        summary += "\n\nIssues:\n" + "\n".join(errors)
    summary += "\n\nNext development stage: create the native RFA family, construct labels and graphics, and load the generated family into the project. This foundation stage does not yet author the tag family itself."
    lora_alert(summary, title="Lora · Parameter Setup Result", warn_icon=bool(errors))

    if not errors or created_defs or reused_defs:
        save = lora_confirm("Would you like to save a Lora tag design definition (.json) for this category and parameter selection?", title="Lora · Save Design Definition", yes_text="Save Definition", no_text="Not Now")
        if save:
            save_design_definition(category_name, fields, file_path, is_advanced)


def save_design_definition(category_name, fields, shared_file_path, advanced):
    data = {
        "schema": "lora.annotation-tag-definition",
        "schema_version": 1,
        "created_by": "Lora Annotation Tag Studio",
        "created_at": datetime.datetime.now().isoformat(),
        "category": category_name,
        "mode": "advanced" if advanced else "simple",
        "shared_parameter_file": shared_file_path,
        "family_generation_status": "configuration_only",
        "fields": fields,
        "notes": ["This file stores the design/parameter configuration.", "Native RFA family authoring is not implemented in this foundation release."]
    }
    path = forms.save_file(file_ext="json", default_name="Lora_{}_TagDefinition.json".format(category_name.replace(" ", "")), title="Save Lora Tag Definition")
    if not path:
        return
    try:
        with open(path, "w") as stream:
            json.dump(data, stream, indent=2)
        lora_alert("Saved tag design definition:\n{}".format(path), title="Lora Tag Studio")
    except Exception as ex:
        lora_alert("Could not save the design definition:\n{}".format(ex), title="Lora Tag Studio", warn_icon=True)


def inspect_external_family():
    path = forms.pick_file(file_ext="rfa", multi_file=False, title="Choose an existing Revit family to inspect")
    if not path:
        return
    family_doc = None
    try:
        family_doc = app.OpenDocumentFile(path)
        family_name = "(unknown)"
        category_name = "(unknown)"
        try:
            family_name = family_doc.OwnerFamily.Name
            category_name = family_doc.OwnerFamily.FamilyCategory.Name
        except Exception:
            pass
        rows = []
        try:
            for param in family_doc.FamilyManager.Parameters:
                try:
                    definition = param.Definition
                    pname = definition.Name
                    ptype = str(definition.ParameterType) if hasattr(definition, "ParameterType") else str(definition.GetDataType().TypeId)
                    shared = "Shared" if param.IsShared else "Family"
                    rows.append("{} | {} | {}".format(pname, ptype, shared))
                except Exception:
                    pass
        except Exception:
            pass
        message = "Family: {}\nCategory: {}\nFile: {}\n\nDetected family parameters: {}\n{}\n\nThis inspection is read-only. External-family editing will be enabled through the compatibility adapter in a later stage.".format(
            family_name, category_name, path, len(rows), "\n".join(rows[:60]) if rows else "No readable family parameters were found.")
        lora_alert(message, title="Lora · Family Compatibility Inspector")
    except Exception as ex:
        logger.exception("External family inspection failed")
        lora_alert("Lora could not inspect this family. It may be incompatible, already open, or require a different Revit version.\n\n{}".format(ex), title="Lora · Family Inspector", warn_icon=True)
    finally:
        if family_doc is not None:
            try:
                family_doc.Close(False)
            except Exception:
                pass


def main():
    if doc is None:
        lora_alert("Open a Revit project before using Lora Annotation Tag Studio.", title="Lora Tag Studio", warn_icon=True)
        return
    if doc.IsReadOnly:
        lora_alert("The active project is read-only. Open an editable project to set up parameters.", title="Lora Tag Studio", warn_icon=True)
        return
    action = lora_select_from_list([
        "Create tag design · automatic parameter setup",
        "Inspect existing family · compatibility report"
    ], title="Lora · Annotation Tag Studio", button_name="Continue")
    if not action:
        return
    try:
        if action.startswith("Create tag design"):
            build_definition()
        else:
            inspect_external_family()
    except Exception as ex:
        logger.exception("Lora Annotation Tag Studio failed")
        lora_alert("Lora could not complete the operation:\n\n{}\n\nSee the pyRevit output panel for the full traceback.".format(ex), title="Lora Tag Studio Error", warn_icon=True)


if __name__ == "__main__":
    main()
