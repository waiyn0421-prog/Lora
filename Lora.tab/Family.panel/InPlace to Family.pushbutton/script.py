# -*- coding: utf-8 -*-
"""Lora InPlace to Family — convert an in-place model element into a loadable RFA.

Target: pyRevit / Revit 2023–2026 (runtime validation required).
Uses the native Revit API. No Dynamo, Python.NET Reflection.Emit proxy, or
custom IWin32Window implementation is used.
"""
from __future__ import print_function

import os
import re
import sys
import traceback

from pyrevit import revit, DB, forms, script

_LIB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'lib'))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from LoraDialogTheme import lora_alert, lora_confirm, lora_apply_winforms_theme
from LoraCore import element_id_value

import clr
clr.AddReference('System')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')

from System import String
from System.IO import Path, Directory
from System.Windows.Forms import (
    Form, Label, TextBox, ComboBox, ComboBoxStyle, Button, CheckBox,
    OpenFileDialog, DialogResult, FormBorderStyle, FormStartPosition,
    AnchorStyles, MessageBox, MessageBoxButtons, MessageBoxIcon,
    AutoCompleteStringCollection, AutoCompleteMode, AutoCompleteSource
)
from System.Drawing import Point, Size
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.UI.Selection import ObjectType

doc = revit.doc
uidoc = revit.uidoc
app = __revit__.Application
logger = script.get_logger()

# Revit 2023-2026 compatibility gate. The command uses stable Revit DB APIs
# shared by these releases; ElementId access is handled by LoraCore.
try:
    REVIT_VERSION = int(app.VersionNumber)
except Exception:
    REVIT_VERSION = 0

if REVIT_VERSION and REVIT_VERSION not in (2023, 2024, 2025, 2026):
    lora_alert(
        'InPlace to Family supports Revit 2023, 2024, 2025 and 2026. '
        'Detected Revit version: {}.'.format(REVIT_VERSION),
        title='Lora | Unsupported Revit Version',
        warn_icon=True,
        exitscript=True
    )

INVALID_CHARS = set(Path.GetInvalidFileNameChars())


def _element_id_value(eid):
    return element_id_value(eid)


def _selected_inplace_element():
    ids = list(uidoc.Selection.GetElementIds())
    for eid in ids:
        element = doc.GetElement(eid)
        if _is_inplace(element):
            return element
    return None


def _is_inplace(element):
    try:
        return isinstance(element, DB.FamilyInstance) and element.Symbol.Family.IsInPlace
    except Exception:
        return False


def _get_model_categories():
    names = []
    for category in doc.Settings.Categories:
        try:
            if (category.CategoryType == DB.CategoryType.Model
                    and category.BuiltInCategory != DB.BuiltInCategory.INVALID
                    and category.CanAddSubcategory
                    and category.Name):
                names.append(category.Name)
        except Exception:
            pass
    return sorted(set(names))


def _get_material_names():
    result = []
    for material in DB.FilteredElementCollector(doc).OfClass(DB.Material):
        try:
            result.append(material.Name)
        except Exception:
            pass
    return sorted(set(result))


def _default_template_path():
    try:
        root = app.FamilyTemplatePath
        if root and os.path.isdir(root):
            candidate = os.path.join(root, 'Metric Generic Model.rft')
            if os.path.isfile(candidate):
                return candidate
            # Try common metric generic-model template naming.
            for name in os.listdir(root):
                if name.lower().endswith('.rft') and 'generic' in name.lower() and 'metric' in name.lower():
                    return os.path.join(root, name)
    except Exception:
        pass
    return ''


def _collect_solids(element):
    options = DB.Options()
    options.DetailLevel = DB.ViewDetailLevel.Fine
    options.ComputeReferences = False
    options.IncludeNonVisibleObjects = False
    geometry = element.get_Geometry(options)
    solids = []
    if geometry is None:
        return solids

    def walk(geometry_objects):
        for geometry_object in geometry_objects:
            if isinstance(geometry_object, DB.Solid):
                try:
                    if geometry_object.Faces.Size > 0 and geometry_object.Volume > 1e-9:
                        solids.append(geometry_object)
                except Exception:
                    pass
            elif isinstance(geometry_object, DB.GeometryInstance):
                try:
                    walk(geometry_object.GetInstanceGeometry())
                except Exception:
                    # Some nested instances expose symbol geometry only.
                    try:
                        walk(geometry_object.GetSymbolGeometry())
                    except Exception:
                        pass
    walk(geometry)
    return solids


def _try_union(solids):
    try:
        merged = solids[0]
        for solid in solids[1:]:
            merged = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                merged, solid, DB.BooleanOperationsType.Union)
        return merged
    except Exception:
        return None


def _find_material(family_doc, name):
    for material in DB.FilteredElementCollector(family_doc).OfClass(DB.Material):
        try:
            if material.Name == name:
                return material
        except Exception:
            pass
    return None


def _copy_basic_material(family_doc, project_material_name):
    """Create a family-document material with basic display properties."""
    source = None
    for material in DB.FilteredElementCollector(doc).OfClass(DB.Material):
        try:
            if material.Name == project_material_name:
                source = material
                break
        except Exception:
            pass
    if source is None:
        return None

    target = _find_material(family_doc, project_material_name)
    if target is None:
        target = DB.Material.Create(family_doc, project_material_name)
    try:
        target.Color = source.Color
    except Exception:
        pass
    try:
        target.Transparency = source.Transparency
    except Exception:
        pass
    try:
        target.Shininess = source.Shininess
    except Exception:
        pass
    try:
        target.Smoothness = source.Smoothness
    except Exception:
        pass
    return target


def _convert_to_family(settings):
    element = settings['element']
    if not _is_inplace(element):
        raise Exception('The selected element is not an in-place family instance.')

    bbox = element.get_BoundingBox(None)
    if bbox is None:
        raise Exception('Could not read the selected element bounding box.')
    origin = bbox.Min

    steps = []
    family_doc = None
    family_path = None
    try:
        steps.append('Extracting element geometry')
        solids = _collect_solids(element)
        if not solids:
            raise Exception('No valid solid geometry was found on the selected in-place element.')

        steps.append('Moving geometry to family origin')
        translation = DB.Transform.CreateTranslation(
            DB.XYZ(-origin.X, -origin.Y, -origin.Z))
        local_solids = [DB.SolidUtils.CreateTransformed(solid, translation)
                        for solid in solids]

        target_solids = local_solids
        warnings = []
        if len(local_solids) > 1:
            if settings['combine_solids']:
                merged = _try_union(local_solids)
                if merged is not None:
                    target_solids = [merged]
                else:
                    warnings.append(
                        'Solid union failed; created separate forms to preserve geometry.')
            else:
                warnings.append(
                    'Created {} separate forms to preserve individual solids and cuts.'.format(
                        len(local_solids)))

        steps.append('Creating family document')
        family_doc = app.NewFamilyDocument(settings['template_path'])
        if family_doc is None:
            raise Exception('Revit could not create a family document from the selected template.')

        steps.append('Creating family geometry')
        family_transaction = DB.Transaction(family_doc, 'Lora - Create In-Place Family Geometry')
        family_transaction.Start()
        try:
            try:
                family_category = family_doc.Settings.Categories.get_Item(settings['category'])
                family_doc.OwnerFamily.FamilyCategory = family_category
            except Exception as category_error:
                warnings.append('Could not assign category "{}": {}'.format(
                    settings['category'], category_error))

            freeforms = []
            for index, solid in enumerate(target_solids):
                try:
                    freeforms.append(DB.FreeFormElement.Create(family_doc, solid))
                except Exception as form_error:
                    warnings.append('Solid {} could not be converted: {}'.format(index + 1, form_error))
            if not freeforms:
                raise Exception('Revit could not create a FreeFormElement from any extracted solid.')

            if settings['is_void']:
                for freeform in freeforms:
                    try:
                        parameter = freeform.get_Parameter(DB.BuiltInParameter.ELEMENT_IS_CUTTING)
                        if parameter and not parameter.IsReadOnly:
                            parameter.Set(1)
                    except Exception:
                        pass
                try:
                    allow_cut = family_doc.OwnerFamily.get_Parameter(
                        DB.BuiltInParameter.FAMILY_ALLOW_CUT_WITH_VOIDS)
                    if allow_cut and not allow_cut.IsReadOnly:
                        allow_cut.Set(1)
                except Exception:
                    warnings.append('The family template did not expose the void-cutting setting.')
            else:
                material_name = settings['material_name']
                if material_name:
                    family_material = _copy_basic_material(family_doc, material_name)
                    if family_material:
                        for freeform in freeforms:
                            try:
                                parameter = freeform.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
                                if parameter and not parameter.IsReadOnly:
                                    parameter.Set(family_material.Id)
                            except Exception:
                                pass
                    else:
                        warnings.append('Material "{}" was not found; material assignment was skipped.'.format(
                            material_name))

                subcategory_name = settings['subcategory_name'].strip()
                if subcategory_name:
                    try:
                        parent_category = family_doc.OwnerFamily.FamilyCategory
                        subcategories = parent_category.SubCategories
                        if subcategories.Contains(subcategory_name):
                            subcategory = subcategories.get_Item(subcategory_name)
                        else:
                            subcategory = family_doc.Settings.Categories.NewSubcategory(
                                parent_category, subcategory_name)
                        for freeform in freeforms:
                            freeform.Subcategory = subcategory
                    except Exception as subcategory_error:
                        warnings.append('Could not create/assign subcategory "{}": {}'.format(
                            subcategory_name, subcategory_error))
            family_doc.Regenerate()
            family_transaction.Commit()
        except Exception:
            if family_transaction.HasStarted():
                family_transaction.RollBack()
            raise

        steps.append('Saving family file')
        documents_dir = os.path.join(os.path.expanduser('~'), 'Documents')
        output_dir = os.path.join(documents_dir, 'Lora Families')
        if not os.path.isdir(output_dir):
            try:
                os.makedirs(output_dir)
            except OSError:
                if not os.path.isdir(output_dir):
                    raise
        family_path = os.path.join(output_dir, settings['family_name'] + '.rfa')
        if os.path.isfile(family_path):
            raise Exception('The output family file already exists: {}. Rename the family or move the existing file before converting.'.format(family_path))
        save_options = DB.SaveAsOptions()
        save_options.OverwriteExistingFile = True
        family_doc.SaveAs(family_path, save_options)

        steps.append('Loading family into project')
        # LoadFamily(Document) avoids a custom IFamilyLoadOptions implementation.
        loaded_family = family_doc.LoadFamily(doc)
        if loaded_family is None:
            raise Exception('Revit did not return the newly loaded family.')

        steps.append('Closing temporary family document')
        family_doc.Close(False)
        family_doc = None

        steps.append('Placing family instance')
        symbol_ids = list(loaded_family.GetFamilySymbolIds())
        if not symbol_ids:
            raise Exception('The new family contains no FamilyType.')
        symbol = doc.GetElement(symbol_ids[0])

        placement_transaction = DB.Transaction(doc, 'Lora - Place Converted Family')
        placement_transaction.Start()
        try:
            if not symbol.IsActive:
                symbol.Activate()
                doc.Regenerate()
            instance = doc.Create.NewFamilyInstance(
                origin, symbol, StructuralType.NonStructural)
            if settings['delete_original']:
                doc.Delete(element.Id)
            placement_transaction.Commit()
        except Exception:
            if placement_transaction.HasStarted():
                placement_transaction.RollBack()
            raise

        return loaded_family, instance, warnings, family_path

    except Exception as error:
        raise Exception('Failed during "{}".\n{}\n\n{}'.format(
            steps[-1] if steps else 'Initial validation',
            error, traceback.format_exc()))
    finally:
        if family_doc is not None:
            try:
                family_doc.Close(False)
            except Exception:
                pass


def _show_setup_dialog(state, categories, materials):
    """Lora-themed setup dialog. Selection is performed after the dialog closes."""
    form = Form()
    form.Text = 'Lora | InPlace to Family'
    form.ClientSize = Size(650, 430)
    form.MinimumSize = Size(650, 470)
    form.FormBorderStyle = FormBorderStyle.Sizable
    form.StartPosition = FormStartPosition.CenterScreen
    form.MaximizeBox = False
    form.MinimizeBox = False
    form.TopMost = True

    margin = 18
    label_width = 145
    input_x = margin + label_width + 12
    input_width = 445
    row_height = 39

    def add_label(text, y):
        label = Label()
        label.Text = text
        label.Location = Point(margin, y + 5)
        label.Size = Size(label_width, 24)
        form.Controls.Add(label)
        return label

    def add_textbox(text, y, width=None):
        control = TextBox()
        control.Text = text or ''
        control.Location = Point(input_x, y)
        control.Size = Size(width or input_width, 25)
        form.Controls.Add(control)
        return control

    y = 18
    select_button = Button()
    select_button.Text = 'Select In-Place Element'
    select_button.Location = Point(margin, y)
    select_button.Size = Size(175, 30)
    form.Controls.Add(select_button)

    selected_label = Label()
    selected_label.Location = Point(205, y + 6)
    selected_label.Size = Size(420, 24)
    selected_label.Text = state['selection_label']
    form.Controls.Add(selected_label)
    y += 48

    add_label('Family name', y)
    name_box = add_textbox(state['family_name'], y)
    y += row_height

    add_label('Family template (.rft)', y)
    template_box = add_textbox(state['template_path'], y, 350)
    browse_button = Button()
    browse_button.Text = 'Browse...'
    browse_button.Location = Point(input_x + 358, y - 1)
    browse_button.Size = Size(82, 27)
    form.Controls.Add(browse_button)
    y += row_height

    add_label('Family category', y)
    category_box = ComboBox()
    category_box.DropDownStyle = ComboBoxStyle.DropDownList
    category_box.Location = Point(input_x, y)
    category_box.Size = Size(input_width, 26)
    for category_name in categories:
        category_box.Items.Add(category_name)
    if state['category'] in categories:
        category_box.SelectedItem = state['category']
    elif categories:
        category_box.SelectedIndex = 0
    form.Controls.Add(category_box)
    y += row_height

    add_label('Material', y)
    material_box = ComboBox()
    material_box.DropDownStyle = ComboBoxStyle.DropDown
    material_box.Location = Point(input_x, y)
    material_box.Size = Size(input_width, 26)
    material_box.Items.Add('')
    autocomplete = AutoCompleteStringCollection()
    for material_name in materials:
        material_box.Items.Add(material_name)
        autocomplete.Add(material_name)
    material_box.AutoCompleteMode = AutoCompleteMode.SuggestAppend
    material_box.AutoCompleteSource = AutoCompleteSource.CustomSource
    material_box.AutoCompleteCustomSource = autocomplete
    material_box.Text = state['material_name']
    form.Controls.Add(material_box)
    y += row_height

    add_label('Subcategory', y)
    subcategory_box = add_textbox(state['subcategory_name'], y)
    y += row_height + 2

    void_check = CheckBox()
    void_check.Text = 'Create as void / cutting form'
    void_check.Location = Point(input_x, y)
    void_check.Size = Size(260, 24)
    void_check.Checked = state['is_void']
    form.Controls.Add(void_check)

    union_check = CheckBox()
    union_check.Text = 'Combine solids (may fail on complex geometry)'
    union_check.Location = Point(input_x + 265, y)
    union_check.Size = Size(360, 24)
    union_check.Checked = state['combine_solids']
    form.Controls.Add(union_check)
    y += 32

    delete_check = CheckBox()
    delete_check.Text = 'Delete original in-place element after successful placement'
    delete_check.Location = Point(input_x, y)
    delete_check.Size = Size(430, 24)
    delete_check.Checked = state['delete_original']
    form.Controls.Add(delete_check)

    status_label = Label()
    status_label.Text = 'Output: Documents\\Lora Families  |  Original is kept unless delete is enabled.'
    status_label.Location = Point(margin, y + 30)
    status_label.Size = Size(600, 24)
    form.Controls.Add(status_label)

    cancel_button = Button()
    cancel_button.Text = 'Cancel'
    cancel_button.Location = Point(430, 382)
    cancel_button.Size = Size(90, 30)
    form.Controls.Add(cancel_button)
    create_button = Button()
    create_button.Text = 'Create Family'
    create_button.Location = Point(528, 382)
    create_button.Size = Size(105, 30)
    form.Controls.Add(create_button)

    result = {'action': 'cancel'}

    def capture():
        state['family_name'] = name_box.Text.strip()
        state['template_path'] = template_box.Text.strip()
        state['category'] = (unicode(category_box.SelectedItem) if 'unicode' in globals() else str(category_box.SelectedItem)) if category_box.SelectedItem is not None else ''
        state['material_name'] = material_box.Text.strip()
        state['subcategory_name'] = subcategory_box.Text.strip()
        state['is_void'] = bool(void_check.Checked)
        state['combine_solids'] = bool(union_check.Checked)
        state['delete_original'] = bool(delete_check.Checked)

    def on_select(sender, args):
        capture()
        result['action'] = 'pick'
        form.Close()

    def on_browse(sender, args):
        dialog = OpenFileDialog()
        dialog.Filter = 'Revit Family Template (*.rft)|*.rft'
        dialog.Title = 'Select Revit Family Template'
        try:
            root = app.FamilyTemplatePath
            if root and os.path.isdir(root):
                dialog.InitialDirectory = root
        except Exception:
            pass
        if dialog.ShowDialog() == DialogResult.OK:
            template_box.Text = dialog.FileName

    def on_cancel(sender, args):
        capture()
        result['action'] = 'cancel'
        form.Close()

    def on_create(sender, args):
        capture()
        if state['element'] is None:
            MessageBox.Show('Select an in-place element first.', 'Lora - Missing Element',
                            MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        if not _is_inplace(state['element']):
            MessageBox.Show('The selected element is not an in-place family instance.',
                            'Lora - Invalid Selection', MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        if not state['family_name'] or any(ch in INVALID_CHARS for ch in state['family_name']):
            MessageBox.Show('Enter a valid family name.', 'Lora - Invalid Family Name',
                            MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        if not state['template_path'] or not os.path.isfile(state['template_path']):
            MessageBox.Show('Select a valid .rft family template.', 'Lora - Missing Template',
                            MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        if not state['category']:
            MessageBox.Show('Choose a family category.', 'Lora - Missing Category',
                            MessageBoxButtons.OK, MessageBoxIcon.Warning)
            return
        for family in DB.FilteredElementCollector(doc).OfClass(DB.Family):
            if family.Name.lower() == state['family_name'].lower():
                MessageBox.Show(
                    'A family named "{}" is already loaded. Use a different family name to avoid replacing existing content.'.format(
                        state['family_name']),
                    'Lora - Family Already Exists', MessageBoxButtons.OK, MessageBoxIcon.Warning)
                return
        result['action'] = 'create'
        form.Close()

    select_button.Click += on_select
    browse_button.Click += on_browse
    cancel_button.Click += on_cancel
    create_button.Click += on_create
    form.CancelButton = cancel_button
    form.AcceptButton = create_button

    lora_apply_winforms_theme(form)
    form.ShowDialog()
    return result['action'], state


def main():
    element = _selected_inplace_element()
    if element is not None:
        selection_label = 'Selected: {} (Id {})'.format(
            element.Category.Name if element.Category else 'In-Place Model',
            _element_id_value(element.Id))
    else:
        selection_label = 'No in-place element selected'

    state = {
        'element': element,
        'selection_label': selection_label,
        'family_name': '',
        'template_path': _default_template_path(),
        'category': element.Category.Name if element and element.Category else '',
        'material_name': '',
        'subcategory_name': '',
        'is_void': False,
        'combine_solids': False,
        'delete_original': False,
    }

    categories = _get_model_categories()
    materials = _get_material_names()
    while True:
        action, state = _show_setup_dialog(state, categories, materials)
        if action == 'pick':
            try:
                reference = uidoc.Selection.PickObject(
                    ObjectType.Element,
                    'Select an in-place family element to convert')
                picked = doc.GetElement(reference.ElementId)
                if not _is_inplace(picked):
                    lora_alert('The picked element is not an in-place family. Please select an in-place model element.',
                               title='Lora | InPlace to Family', warn_icon=True)
                else:
                    state['element'] = picked
                    state['selection_label'] = 'Selected: {} (Id {})'.format(
                        picked.Category.Name if picked.Category else 'In-Place Model',
                        _element_id_value(picked.Id))
            except Exception:
                # Escape returns to the setup dialog without losing its values.
                pass
            continue
        if action != 'create':
            return
        break

    try:
        family, instance, warnings, saved_path = _convert_to_family(state)
        message = (
            'Conversion completed successfully.\n\n'
            'Family: {}\n'
            'Category: {}\n'
            'Family file: {}\n'
            'New instance ElementId: {}'
        ).format(family.Name, state['category'], saved_path, _element_id_value(instance.Id))
        if warnings:
            message += '\n\nWarnings:\n- ' + '\n- '.join(warnings)
        lora_alert(message, title='Lora | InPlace to Family')
    except Exception as error:
        logger.error(traceback.format_exc())
        lora_alert(str(error), title='Lora | Conversion Failed', warn_icon=True)


if __name__ == '__main__':
    main()
