# -*- coding: utf-8 -*-
"""Lora Auto Dim - simplified, preset-driven workflow.
Compatible syntax target: IronPython 2.7 / pyRevit, Revit 2023-2026.
This release dimensiones supported straight walls and compatible hosted openings.
Other scanned categories are explicitly report-only.
"""
import os
import sys
import imp

from pyrevit import revit, forms, script
import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import XYZ, Line, ViewType, BuiltInCategory

# Always load the helper shipped with this command, avoiding stale cached modules.
_lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'lib'))
_helper_path = os.path.join(_lib_path, 'LoraAutoDim.py')
if not os.path.isfile(_helper_path):
    forms.alert('The Auto Dim helper is missing:\n{}'.format(_helper_path), title='Lora Auto Dim | Installation Error')
    script.exit()
if _lib_path in sys.path:
    try:
        sys.path.remove(_lib_path)
    except Exception:
        pass
sys.path.insert(0, _lib_path)

try:
    _helpers = imp.load_source('LoraAutoDim_SmartWorkflow', _helper_path)
    get_view_elements = _helpers.get_view_elements
    category_counts = _helpers.category_counts
    get_dimension_types = _helpers.get_dimension_types
    visible_wall_elements = _helpers.visible_wall_elements
    wall_planar_refs = _helpers.wall_planar_refs
    make_dimension = _helpers.make_dimension
    mm_to_ft = _helpers.mm_to_ft
    reference_key = _helpers.reference_key
    existing_dimension_keys = _helpers.existing_dimension_keys
    is_straight_wall = _helpers.is_straight_wall
    id_value = _helpers.id_value
except Exception as ex:
    forms.alert('Could not load the matching Auto Dim helper.\n\n{}\n\nRestore the complete Auto Dim package and reload pyRevit.'.format(ex), title='Lora Auto Dim | Helper Error')
    script.exit()

doc, uidoc = revit.doc, revit.uidoc
view = doc.ActiveView
PLAN_TYPES = [ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan]
if view is None or view.ViewType not in PLAN_TYPES:
    forms.alert('Open a Floor Plan, Ceiling Plan, or Engineering Plan before running Auto Dim.', title='Lora Auto Dim')
    script.exit()

# Scan once; counts are informational and drive the simple profile picker.
visible = get_view_elements(doc, view)
if not visible:
    forms.alert('No visible model elements were found in the active view.', title='Lora Auto Dim')
    script.exit()
counts = category_counts(visible)

profile_map = {
    'Quick - Exterior dimensions': ['overall', 'openings'],
    'Quick - Interior / working dimensions': ['overall', 'openings', 'thickness'],
    'Quick - Doors & Windows': ['openings'],
    'Quick - Wall thickness': ['thickness'],
    'Custom - Choose dimension rules': None,
}
profile = forms.SelectFromList.show(list(profile_map.keys()), title='Lora Auto Dim | 1 of 3 - Choose intent',
    prompt='Choose what you want to dimension. Lora scans the active view automatically.\nVisible walls: {} | Doors: {} | Windows: {} | Columns: {} | Beams: {} | Grids: {}'.format(
        counts.get('Walls', 0), counts.get('Doors', 0), counts.get('Windows', 0), counts.get('Structural Columns', 0), counts.get('Structural Framing', 0), counts.get('Grids', 0)), multiselect=False)
if not profile:
    script.exit()

if profile_map[profile] is None:
    rule_labels = [
        ('overall', 'Overall straight-wall dimensions'),
        ('openings', 'Opening widths + wall gaps (compatible hosted doors/windows)'),
        ('thickness', 'Wall thickness dimensions'),
    ]
    chosen = forms.SelectFromList.show([label for key, label in rule_labels], title='Lora Auto Dim | Custom rules',
        prompt='Select the dimension rules to run.', multiselect=True)
    if not chosen:
        script.exit()
    rules = set([key for key, label in rule_labels if label in chosen])
else:
    rules = set(profile_map[profile])

# Dimension type list is filtered to actual linear dimension types by the helper.
types = get_dimension_types(doc, view)
if not types:
    diag = getattr(get_dimension_types, 'last_diagnostics', {})
    detail = '\n'.join(diag.get('collector_errors', [])) or 'No collector exception was reported.'
    forms.alert('No usable linear DimensionType was found.\nScanned: {}\nUsable linear styles: {}\n{}'.format(
        diag.get('candidate_count', 0), diag.get('result_count', 0), detail), title='Lora Auto Dim | Dimension type')
    script.exit()
labels = sorted(types.keys(), key=lambda x: x.lower())
# Prefer a type containing a common architectural/linear term as a convenience default.
default_label = labels[0]
for label in labels:
    low = label.lower()
    if 'linear' in low or 'architect' in low or 'default' in low:
        default_label = label
        break
style_options = ['Use recommended: {}'.format(default_label)] + [x for x in labels if x != default_label]
style_choice = forms.SelectFromList.show(style_options, title='Lora Auto Dim | 2 of 3 - Dimension style',
    prompt='Only linear dimension types are listed. Choose the type for this run.', multiselect=False)
if not style_choice:
    script.exit()
if style_choice.startswith('Use recommended: '):
    type_label = default_label
else:
    type_label = style_choice
dim_type = types[type_label]

# Keep the common workflow short; only ask for numeric settings if the user opts in.
offset_mm = 600.0
spacing_mm = 250.0
advanced = forms.alert('Use the recommended layout settings?\n\nFirst dimension offset: 600 mm\nChain spacing: 250 mm\n\nChoose No to customize these values.', title='Lora Auto Dim | Placement', yes=True, no=True)
if not advanced:
    try:
        offset_text = forms.ask_for_string(default='600', prompt='First dimension-chain offset from the wall reference (mm).', title='Lora Auto Dim | Advanced placement')
        if offset_text is None:
            script.exit()
        spacing_text = forms.ask_for_string(default='250', prompt='Spacing between dimension chains (mm).', title='Lora Auto Dim | Advanced placement')
        if spacing_text is None:
            script.exit()
        offset_mm = float(offset_text)
        spacing_mm = float(spacing_text)
        if offset_mm <= 0 or spacing_mm <= 0:
            raise ValueError('Offset and spacing must be greater than zero.')
    except Exception as ex:
        forms.alert('Invalid placement values. Enter positive numbers in millimetres.\n{}'.format(ex), title='Lora Auto Dim')
        script.exit()

walls = visible_wall_elements(doc, view)
if not walls:
    forms.alert('No visible walls were found in this view. This release creates wall-based dimensions only.', title='Lora Auto Dim')
    script.exit()

base_offset = mm_to_ft(offset_mm)
spacing = mm_to_ft(spacing_mm)
proposals = []
failed_geometry = 0
skipped_curved = 0
skipped_refs = 0

for wall in walls:
    try:
        if not is_straight_wall(wall):
            skipped_curved += 1
            continue
        curve = wall.Location.Curve
        start, end = curve.GetEndPoint(0), curve.GetEndPoint(1)
        delta = XYZ(end.X - start.X, end.Y - start.Y, 0)
        if delta.GetLength() < 1e-8:
            skipped_refs += 1
            continue
        direction = delta.Normalize()
        perp = XYZ(-direction.Y, direction.X, 0)
        mid = XYZ((start.X + end.X) / 2.0, (start.Y + end.Y) / 2.0, (start.Z + end.Z) / 2.0)
        end_items, side_items = wall_planar_refs(wall, view)
        if len(end_items) < 2:
            skipped_refs += 1
            continue

        if 'overall' in rules:
            a = XYZ(start.X + perp.X * base_offset, start.Y + perp.Y * base_offset, start.Z)
            b = XYZ(end.X + perp.X * base_offset, end.Y + perp.Y * base_offset, end.Z)
            refs = [end_items[0][1], end_items[-1][1]]
            key = reference_key(doc, refs)
            if key:
                proposals.append({'wall_id': id_value(wall.Id), 'rule': 'Overall wall length', 'refs': refs, 'line': Line.CreateBound(a, b), 'key': key})

        if 'openings' in rules:
            try:
                insert_ids = wall.FindInserts(True, False, True, True)
            except Exception:
                insert_ids = []
            inserts = []
            for eid in insert_ids:
                element = doc.GetElement(eid)
                if element is None or element.Category is None:
                    continue
                try:
                    cat_id = id_value(element.Category.Id)
                    if cat_id not in [int(BuiltInCategory.OST_Doors), int(BuiltInCategory.OST_Windows)]:
                        continue
                    loc = element.Location
                    if not hasattr(loc, 'Point'):
                        continue
                    dist = (loc.Point - start).DotProduct(direction)
                    left = element.GetReferenceByName('Left')
                    right = element.GetReferenceByName('Right')
                    if left:
                        inserts.append((dist, left))
                    if right:
                        inserts.append((dist + 0.0001, right))
                except Exception:
                    continue
            inserts.sort(key=lambda x: x[0])
            refs = [end_items[0][1]] + [r for _, r in inserts] + [end_items[-1][1]]
            ordered_refs, seen = [], set()
            for ref in refs:
                try:
                    rkey = ref.ConvertToStableRepresentation(doc)
                except Exception:
                    rkey = str(ref)
                if rkey not in seen:
                    seen.add(rkey)
                    ordered_refs.append(ref)
            key = reference_key(doc, ordered_refs)
            # Do not create an ordinary wall-end dimension under an opening-only rule.
            if inserts and key and len(ordered_refs) >= 3:
                opening_offset = max(mm_to_ft(50), base_offset - spacing)
                a = XYZ(start.X + perp.X * opening_offset, start.Y + perp.Y * opening_offset, start.Z)
                b = XYZ(end.X + perp.X * opening_offset, end.Y + perp.Y * opening_offset, end.Z)
                proposals.append({'wall_id': id_value(wall.Id), 'rule': 'Opening widths + wall gaps', 'refs': ordered_refs, 'line': Line.CreateBound(a, b), 'key': key})

        if 'thickness' in rules and len(side_items) >= 2:
            p1 = XYZ(mid.X - perp.X * base_offset, mid.Y - perp.Y * base_offset, mid.Z)
            p2 = XYZ(mid.X + perp.X * base_offset, mid.Y + perp.Y * base_offset, mid.Z)
            refs = [side_items[0][1], side_items[-1][1]]
            key = reference_key(doc, refs)
            if key:
                proposals.append({'wall_id': id_value(wall.Id), 'rule': 'Wall thickness', 'refs': refs, 'line': Line.CreateBound(p1, p2), 'key': key})
    except Exception as ex:
        failed_geometry += 1
        print('Auto Dim preflight skipped wall {}: {}'.format(id_value(wall.Id), ex))

if not proposals:
    forms.alert('No dimension proposals could be prepared.\n\nCurved walls skipped: {}\nWalls without usable references: {}\nGeometry errors: {}\n\nThis release supports straight-wall dimensions and compatible hosted door/window references only.'.format(
        skipped_curved, skipped_refs, failed_geometry), title='Lora Auto Dim | Preflight')
    script.exit()

existing_keys, scan_errors = existing_dimension_keys(doc, view, dim_type)
new_keys, filtered = set(), []
duplicates_existing = duplicates_proposed = 0
for item in proposals:
    key = item['key']
    if key in existing_keys:
        duplicates_existing += 1
        continue
    if key in new_keys:
        duplicates_proposed += 1
        continue
    new_keys.add(key)
    filtered.append(item)

rule_counts = {}
for item in filtered:
    rule_counts[item['rule']] = rule_counts.get(item['rule'], 0) + 1
rule_summary = '\n'.join(['  - {}: {}'.format(k, v) for k, v in sorted(rule_counts.items())]) or '  - None'
preview = ('LORA AUTO DIM - REVIEW\n\nProfile: {}\nView: {}\nDimension type: {}\nOffset / spacing: {} / {} mm\n\n'
    'Proposed dimensions: {}\n{}\n\nCurved walls skipped: {}\nWalls without usable references: {}\nGeometry errors: {}\n'
    'Existing duplicates skipped: {}\nDuplicate proposals removed: {}\n\n'
    'This release creates straight-wall dimensions and compatible hosted door/window chains. Columns, beams, grids, floors and stairs are scan-only.\n\nCreate these dimensions?').format(
        profile, view.Name, type_label, offset_mm, spacing_mm, len(filtered), rule_summary,
        skipped_curved, skipped_refs, failed_geometry, duplicates_existing, duplicates_proposed)
if not filtered:
    forms.alert(preview.replace('Create these dimensions?', 'No new dimensions remain after duplicate checks.'), title='Lora Auto Dim | Review')
    script.exit()
if not forms.alert(preview, title='Lora Auto Dim | Review', yes=True, no=True):
    script.exit()

created, failed_create, creation_errors = 0, 0, []
try:
    with revit.Transaction('Lora Auto Dim - {}'.format(profile)):
        for item in filtered:
            try:
                dimension = make_dimension(doc, view, item['refs'], item['line'], dim_type)
                if dimension:
                    created += 1
                else:
                    failed_create += 1
                    if len(creation_errors) < 8:
                        detail = getattr(make_dimension, 'last_error', None)
                        creation_errors.append('Wall {} - {}{}'.format(item['wall_id'], item['rule'], ': ' + str(detail) if detail else ''))
            except Exception as ex:
                failed_create += 1
                if len(creation_errors) < 8:
                    creation_errors.append('Wall {} - {}: {}'.format(item['wall_id'], item['rule'], ex))
except Exception as ex:
    script.get_logger().exception('Auto Dim transaction failed')
    forms.alert('Auto Dim transaction failed. Revit should roll back the transaction.\n\n{}'.format(ex), title='Lora Auto Dim | Error')
    script.exit()

result = ('Auto Dim finished.\n\nProfile: {}\nView: {}\nDimension type: {}\n\nCreated: {}\nCreation failures: {}\n'
    'Curved walls skipped: {}\nWalls without usable references: {}\nGeometry errors: {}\nExisting duplicates skipped: {}\nDuplicate proposals skipped: {}').format(
        profile, view.Name, type_label, created, failed_create, skipped_curved, skipped_refs, failed_geometry, duplicates_existing, duplicates_proposed)
if creation_errors:
    result += '\n\nFailure examples:\n' + '\n'.join(creation_errors)
if scan_errors:
    result += '\n\nExisting dimension scan notes:\n' + '\n'.join(scan_errors)
result += '\n\nNo dimension was counted as created unless Revit returned a Dimension element. Structural categories are scan-only in this release.'
forms.alert(result, title='Lora Auto Dim | Result')
