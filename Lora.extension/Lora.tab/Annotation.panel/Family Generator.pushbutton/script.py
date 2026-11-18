# -*- coding: utf-8 -*-
"""Lora Family Generator — template-based native family creation stage.

Creates a native .RFA from a selected .RFT, provisions custom shared parameter
identities in the family, and optionally binds them to the selected project
category. It preserves template label/graphic content; it does not claim to
compose arbitrary new label elements (not generally exposed by public Revit API).
"""
import os
import sys
from pyrevit import revit, DB, forms, script

LIB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'lib'))
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from LoraDialogTheme import lora_alert, lora_confirm, lora_select_from_list
from LoraParameterEngine import (
    ensure_shared_definition, ensure_project_binding, group_type_id,
    spec_for, find_shared_definition
)
from LoraFamilyGenerationEngine import create_family_from_template, load_family_into_project, FamilyGenerationError

app = __revit__.Application
doc = revit.doc
logger = script.get_logger()

CATEGORIES = [
    ('Doors', 'OST_Doors', 'Door Tags'), ('Windows', 'OST_Windows', 'Window Tags'),
    ('Rooms', 'OST_Rooms', 'Room Tags'), ('Spaces', 'OST_MEPSpaces', 'Space Tags'),
    ('Walls', 'OST_Walls', 'Wall Tags'), ('Floors', 'OST_Floors', 'Material Tags'),
    ('Structural Columns', 'OST_StructuralColumns', 'Structural Column Tags'),
    ('Structural Framing', 'OST_StructuralFraming', 'Structural Framing Tags'),
    ('Pipes', 'OST_PipeCurves', 'Pipe Tags'), ('Ducts', 'OST_DuctCurves', 'Duct Tags'),
    ('Cable Trays', 'OST_CableTray', 'Cable Tray Tags'), ('Conduits', 'OST_Conduit', 'Conduit Tags'),
    ('Mechanical Equipment', 'OST_MechanicalEquipment', 'Mechanical Equipment Tags'),
    ('Electrical Fixtures', 'OST_ElectricalFixtures', 'Electrical Fixture Tags'),
    ('Generic Models', 'OST_GenericModel', 'Generic Model Tags')
]
KINDS = ['Text', 'Length', 'Integer', 'Number', 'Yes/No']
GROUPS = ['Identity Data', 'Constraints', 'Dimensions', 'Data', 'Text', 'Geometry', 'Graphics', 'Materials and Finishes', 'Phasing', 'Other']


def pick_category():
    labels = [x[0] for x in CATEGORIES]
    selected = lora_select_from_list(labels, title='Lora · Target Element Category', button_name='Continue')
    if not selected:
        return None
    for row in CATEGORIES:
        if row[0] == selected:
            try:
                cat = doc.Settings.Categories.get_Item(getattr(DB.BuiltInCategory, row[1]))
            except Exception:
                cat = None
            if cat is None:
                raise FamilyGenerationError('The selected category is not available in this project.')
            return row, cat
    return None


def get_revit_family_template_root():
    """Return the family template folder configured in Revit's application settings.

    Do not guess a ProgramData path or silently fall back to an arbitrary folder:
    the user's Revit configuration is the source of truth.
    """
    try:
        configured = app.FamilyTemplatePath
    except Exception:
        configured = None
    if not configured:
        raise FamilyGenerationError(
            'Revit did not expose a Family Template Path. Open Revit Options and configure '
            'the Family Template File Location, then restart Lora Family Generator.')
    configured = os.path.abspath(str(configured))
    if not os.path.isdir(configured):
        raise FamilyGenerationError(
            'The Family Template Path configured in Revit does not exist or is not accessible:\n{}\n\n'
            'Check Revit Options > File Locations > Family Template Files.'.format(configured))
    return configured


def _template_category_keywords(category_row):
    """Keywords used to rank template filenames without assuming one language."""
    display = category_row[0].lower()
    tag_label = category_row[2].lower()
    aliases = {
        'doors': ['door'], 'windows': ['window'], 'rooms': ['room'], 'spaces': ['space', 'mep space'],
        'walls': ['wall'], 'floors': ['floor', 'material'],
        'structural columns': ['structural column', 'column'],
        'structural framing': ['structural framing', 'framing', 'beam'],
        'pipes': ['pipe'], 'ducts': ['duct'], 'cable trays': ['cable tray', 'cabletray'],
        'conduits': ['conduit'], 'mechanical equipment': ['mechanical equipment'],
        'electrical fixtures': ['electrical fixture'], 'generic models': ['generic model', 'multi-category']
    }
    return aliases.get(display, [tag_label.replace(' tags', '').replace(' tag', ''), display])


def choose_family_template(category_row):
    """List RFT templates from Revit's configured FamilyTemplatePath.

    Matching templates are shown first. If Revit's localized template names do not
    match the category keywords, all templates beneath the configured root are
    offered with their relative paths so the user can select the correct one.
    """
    root = get_revit_family_template_root()
    templates = []
    for current_root, dirs, files in os.walk(root):
        # Skip hidden/system folders and keep discovery deterministic.
        dirs[:] = sorted([d for d in dirs if not d.startswith('.')])
        for filename in files:
            if filename.lower().endswith('.rft'):
                full_path = os.path.join(current_root, filename)
                rel_path = os.path.relpath(full_path, root)
                templates.append((filename, rel_path, full_path))
    templates.sort(key=lambda x: (x[1].lower(), x[0].lower()))
    if not templates:
        raise FamilyGenerationError(
            'No Revit family templates (.rft) were found under the folder configured in Revit:\n{}\n\n'
            'Check Revit Options > File Locations > Family Template Files.'.format(root))

    keywords = _template_category_keywords(category_row)
    def score(item):
        filename, rel_path, _ = item
        text = (filename + ' ' + rel_path).lower().replace('_', ' ').replace('-', ' ')
        points = 0
        for keyword in keywords:
            key = keyword.lower()
            if key and key in text:
                points += 10 if 'tag' in text or 'annotation' in text else 5
        if 'tag' in text:
            points += 4
        if 'metric' in filename.lower():
            points += 1
        return points

    ranked = sorted(templates, key=lambda x: (-score(x), x[1].lower()))
    matches = [x for x in ranked if score(x) > 0]
    use_matches = bool(matches)
    shown = matches if use_matches else ranked
    labels = []
    lookup = {}
    for filename, rel_path, full_path in shown:
        prefix = 'Likely match' if use_matches and score((filename, rel_path, full_path)) > 0 else 'Template'
        label = '{} · {}  [{}]'.format(prefix, filename, rel_path)
        labels.append(label)
        lookup[label] = full_path

    note = ('Templates are read from Revit Family Template Path:\n{}\n\n'
            'Likely category matches are listed first. Confirm the template category before generating.').format(root)
    if not use_matches:
        note += ('\n\nNo filename matched the selected category (template names may be localized). '
                 'All .rft files under the configured folder are shown.')
    lora_alert(note, title='Lora · Revit Template Location')
    selected = lora_select_from_list(labels, title='Lora · Choose Family Template', button_name='Use Template')
    if not selected:
        return None
    return lookup.get(selected)


def shared_file_path():
    current = app.SharedParametersFilename
    options = ['Use current shared parameter file', 'Select existing shared parameter file', 'Create a new Lora shared parameter file']
    choice = lora_select_from_list(options, title='Lora · Shared Parameter File', button_name='Continue')
    if not choice:
        return None
    if choice == options[0]:
        if current and os.path.isfile(current):
            return current
        lora_alert('Revit does not have an accessible shared parameter file configured.', title='Lora Family Generator', warn_icon=True)
        return None
    if choice == options[1]:
        return forms.pick_file(file_ext='txt', multi_file=False, title='Select shared parameter file')
    path = forms.save_file(file_ext='txt', default_name='Lora_SharedParameters.txt', title='Create shared parameter file')
    if path and not os.path.exists(path):
        with open(path, 'w') as f:
            f.write('# Revit shared parameter file created by Lora.\n# Do not edit manually.\n')
            f.write('*META\tVERSION\tMINVERSION\nMETA\t2\t1\n*GROUP\tID\tNAME\n')
            f.write('*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\n')
    return path


def parse_custom_fields(raw, advanced):
    fields = []
    errors = []
    seen = set()
    for line_no, raw_line in enumerate((raw or '').splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [x.strip() for x in line.split('|')]
        name = parts[0]
        if not name:
            continue
        if name.lower() in seen:
            errors.append('Line {}: duplicate parameter name {}'.format(line_no, name))
            continue
        seen.add(name.lower())
        kind = parts[1] if advanced and len(parts) > 1 and parts[1] else 'Text'
        binding = parts[2].lower() if advanced and len(parts) > 2 and parts[2] else 'instance'
        group = parts[3] if advanced and len(parts) > 3 and parts[3] else 'Identity Data'
        description = parts[4] if advanced and len(parts) > 4 else 'Created by Lora Annotation Tag Studio'
        if kind.lower() not in [k.lower() for k in KINDS]:
            errors.append('Line {}: unsupported type {}'.format(line_no, kind)); continue
        kind = [k for k in KINDS if k.lower() == kind.lower()][0]
        if binding not in ('instance', 'type'):
            errors.append('Line {}: binding must be Instance or Type'.format(line_no)); continue
        if group not in GROUPS:
            errors.append('Line {}: invalid parameter group {}'.format(line_no, group)); continue
        if not name.replace('_', '').replace(' ', '').isalnum() or name[0].isdigit():
            errors.append('Line {}: parameter name contains unsupported characters'.format(line_no)); continue
        fields.append({'name': name, 'type': kind, 'binding': binding, 'group': group, 'description': description})
    return fields, errors


def choose_fields(category_name):
    advanced = lora_confirm(
        'Choose parameter setup mode.\n\nYes = Advanced (configure type, binding and group for each field).\nNo = Simple (Text, Instance, Identity Data defaults).',
        title='Lora · Parameter Designer', yes_text='Advanced', no_text='Simple')
    fields = []
    default_name = 'Lora_{}_Tag_Description'.format(category_name.replace(' ', '_'))
    while True:
        name = forms.ask_for_string(
            default=default_name if not fields else '',
            prompt='Enter a custom shared parameter name. Leave blank to finish.',
            title='Lora · Custom Parameter {}'.format(len(fields) + 1))
        if name is None:
            return None, advanced
        name = name.strip()
        if not name:
            break
        if any(x['name'].lower() == name.lower() for x in fields):
            lora_alert("'{}' is already in this parameter list.".format(name), title='Lora Parameter Designer', warn_icon=True)
            continue
        kind, binding, group, description = 'Text', 'instance', 'Identity Data', 'Created by Lora Annotation Tag Studio'
        if advanced:
            kind = lora_select_from_list(KINDS, title='Data type · {}'.format(name), button_name='Continue')
            if not kind:
                return None, advanced
            binding_choice = lora_select_from_list(['Instance', 'Type'], title='Binding · {}'.format(name), button_name='Continue')
            if not binding_choice:
                return None, advanced
            binding = binding_choice.lower()
            group = lora_select_from_list(GROUPS, title='Parameter group · {}'.format(name), button_name='Continue')
            if not group:
                return None, advanced
            description = forms.ask_for_string(default=description, prompt='Optional description', title='Description · {}'.format(name)) or description
        fields.append({'name': name, 'type': kind, 'binding': binding, 'group': group, 'description': description})
        if not lora_confirm('Add another custom parameter?', title='Lora Parameter Designer', yes_text='Add Another', no_text='Finish'):
            break
    return fields, advanced


def get_or_create_definitions(shared_path, fields):
    old_path = app.SharedParametersFilename
    app.SharedParametersFilename = shared_path
    try:
        shared_file = app.OpenSharedParameterFile()
        if shared_file is None:
            raise FamilyGenerationError('Revit could not open the selected shared parameter file.')
        definitions = []
        actions = []
        for field in fields:
            group = shared_file.Groups.get_Item('Lora Annotation Tagging')
            if group is None:
                group = shared_file.Groups.Create('Lora Annotation Tagging')
            existing_group, existing = find_shared_definition(shared_file, field['name'])
            if existing is not None:
                # Validate type where the API exposes a comparable ForgeTypeId.
                try:
                    expected = spec_for(field['type']).TypeId
                    actual = existing.GetDataType().TypeId
                    if expected and actual and expected != actual:
                        raise FamilyGenerationError("Shared parameter '{}' exists with a different data type.".format(field['name']))
                except FamilyGenerationError:
                    raise
                except Exception:
                    pass
                definition = existing
                action = 'reused definition'
            else:
                opts = DB.ExternalDefinitionCreationOptions(field['name'], spec_for(field['type']))
                opts.Visible = True
                try: opts.Description = field['description']
                except Exception: pass
                definition = group.Definitions.Create(opts)
                action = 'created definition'
            definitions.append(definition)
            actions.append({'name': field['name'], 'action': action, 'type': field['type'], 'binding': field['binding']})
        return definitions, actions
    finally:
        # Keep Revit's selected shared parameter file on the file the user chose,
        # as definitions have just been added there; restore only if the operation failed.
        pass


def main():
    if doc is None or doc.IsFamilyDocument:
        lora_alert('Open a Revit project before running Family Generator.', title='Lora Family Generator', warn_icon=True)
        return
    try:
        cat_data = pick_category()
        if not cat_data: return
        category_row, project_category = cat_data
        source_mode = lora_select_from_list(
            ['Create new family from Revit template', 'Edit a copy of an existing family'],
            title='Lora · Family Workflow', button_name='Continue')
        if not source_mode: return

        if source_mode == 'Edit a copy of an existing family':
            template_path = forms.pick_file(file_ext='rfa', multi_file=False,
                                            title='Select existing tag family (.rfa) to edit as a new copy')
            if not template_path: return
            try:
                source_name = os.path.basename(template_path)
                if not source_name.lower().endswith('.rfa'):
                    raise FamilyGenerationError('Choose an existing Revit family file (.rfa).')
            except Exception:
                raise
        else:
            template_path = None

        fields, advanced = choose_fields(category_row[0])
        if fields is None: return
        shared_path = shared_file_path()
        if not shared_path: return
        if source_mode == 'Create new family from Revit template':
            template_path = choose_family_template(category_row)
            if not template_path: return
        output_folder = forms.pick_folder(title='Choose output folder for generated .RFA family')
        if not output_folder: return
        default_name = 'Lora_{}_Tag'.format(category_row[0].replace(' ', '_'))
        if source_mode == 'Edit a copy of an existing family':
            default_name = 'Lora_Edit_' + os.path.splitext(os.path.basename(template_path))[0]
        family_name = forms.ask_for_string(default=default_name, prompt='Enter the output family filename (without .rfa).', title='Lora Family Generator')
        if not family_name: return
        family_name = family_name.strip()
        if family_name.lower().endswith('.rfa'):
            family_name = family_name[:-4]
        if not family_name:
            lora_alert('Enter a valid family filename.', title='Lora Family Generator', warn_icon=True)
            return
        output_path = os.path.join(output_folder, family_name + '.rfa')
        if os.path.exists(output_path):
            lora_alert('The output file already exists. Choose another family name; Lora will not overwrite it.', title='Lora Family Generator', warn_icon=True)
            return

        definitions, definition_actions = get_or_create_definitions(shared_path, fields)
        bind = lora_confirm(
            'Also bind these shared parameters to the selected project category ({})?\n\nThis makes the parameters available on matching project elements where applicable. It does not create or populate values automatically.'.format(category_row[0]),
            title='Lora · Project Binding', yes_text='Bind Parameters', no_text='Family Only')
        if bind and definitions:
            tx = DB.Transaction(doc, 'Lora - Bind annotation tag parameters')
            tx.Start()
            try:
                for definition, field in zip(definitions, fields):
                    ensure_project_binding(app, doc, definition, [project_category], field['binding'] == 'instance', field['group'])
                tx.Commit()
            except Exception:
                tx.RollBack()
                raise

        parameter_specs = []
        for definition, field in zip(definitions, fields):
            parameter_specs.append({
                'definition': definition,
                'group_id': group_type_id(field['group']),
                'is_instance': field['binding'] == 'instance'
            })
        report = create_family_from_template(app, template_path, output_path, family_name,
                                             parameter_specs, category_row[2])
        summary = [
            'Family saved: {}'.format(report['output_path']),
            'Family category: {}'.format(report.get('family_category')),
            'Shared definitions: {}'.format(len(definitions)),
            'Family parameters added: {}'.format(len(report['parameters_added'])),
            'Family parameters reused: {}'.format(len(report['parameters_reused'])),
            '',
            'LABEL AUTHORING STATUS: this build adds/reuses the selected shared parameters but does not compose new label graphics automatically. Existing labels and graphics are preserved. For a new tag, use a category-specific template with prebuilt labels; for an edited family, verify that its existing labels reference the shared parameters you need.'
        ]
        if report.get('warnings'):
            summary += ['', 'Warnings:'] + ['• ' + w for w in report['warnings']]
        lora_alert('\n'.join(summary), title='Lora Family Generator', warn_icon=bool(report.get('warnings')))
        if lora_confirm('Load the generated family into the active project now?', title='Lora Family Generator', yes_text='Load Family', no_text='Not Now'):
            family = load_family_into_project(doc, output_path)
            lora_alert('Family loaded into the current project.\n\nFamily: {}'.format(family.Name), title='Lora Family Generator')
    except FamilyGenerationError as ex:
        lora_alert(str(ex), title='Lora Family Generator', warn_icon=True)
    except Exception as ex:
        logger.exception('Lora Family Generator failed')
        lora_alert('Family generation failed:\n\n{}\n\nSee pyRevit output for details.'.format(ex), title='Lora Family Generator', warn_icon=True)

if __name__ == '__main__':
    main()
