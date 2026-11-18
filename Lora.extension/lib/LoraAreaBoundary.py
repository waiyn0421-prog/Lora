# -*- coding: utf-8 -*-
"""Lora Draw Area Boundary - Revit API workflow.
IronPython 2.7-compatible implementation for pyRevit.
"""
import Autodesk.Revit.DB as DB
from pyrevit import revit, forms, script
import importlib


def _load_theme():
    """Load Lora theme without imp (removed in CPython 3.12)."""
    try:
        theme = importlib.import_module('LoraDialogTheme')
        try:
            importlib.reload(theme)
        except Exception:
            pass
        return theme
    except Exception:
        return None


def _id_value(eid):
    try: return int(eid.Value)
    except Exception:
        try: return int(eid.IntegerValue)
        except Exception: return -1


def _is_id(a, b):
    return _id_value(a) == _id_value(b)


def _name(el):
    try: return el.Name
    except Exception: return '<unnamed>'


def get_level(view):
    try:
        level = view.GenLevel
        if level: return level
    except Exception: pass
    try:
        p = view.get_Parameter(DB.BuiltInParameter.PLAN_VIEW_LEVEL)
        if p:
            level = revit.doc.GetElement(p.AsElementId())
            if isinstance(level, DB.Level): return level
    except Exception: pass
    return None


def get_area_schemes(doc):
    try:
        return list(DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme).ToElements())
    except Exception:
        return []


def get_area_plans(doc):
    result = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan).ToElements():
        try:
            if view.ViewType == DB.ViewType.AreaPlan and not view.IsTemplate:
                result.append(view)
        except Exception: pass
    return result


def get_plan_scheme_id(doc, view):
    # Area Plan stores its scheme association in the Area Scheme parameter.
    for bip_name in ('AREA_SCHEME_ID', 'AREA_SCHEME'):
        try:
            bip = getattr(DB.BuiltInParameter, bip_name)
            p = view.get_Parameter(bip)
            if p: return p.AsElementId()
        except Exception: pass
    # Some API builds expose AreaScheme directly on the view.
    try:
        scheme = view.AreaScheme
        if scheme: return scheme.Id
    except Exception: pass
    return DB.ElementId.InvalidElementId


def matching_plans(doc, level):
    matches = []
    for plan in get_area_plans(doc):
        try:
            if _is_id(plan.GenLevel.Id, level.Id):
                scheme_id = get_plan_scheme_id(doc, plan)
                scheme = doc.GetElement(scheme_id) if scheme_id else None
                matches.append((plan, scheme))
        except Exception: pass
    return matches


def choose_scheme(doc, schemes):
    if not schemes:
        forms.alert('This project has no Area Schemes. Create a scheme using Revit: Architecture > Room & Area > Area and Volume Computations > Area Schemes > New. Then run Lora again.',
                    title='Lora | Draw Area Boundary', warn_icon=True)
        return None
    labels = []
    lookup = {}
    for scheme in schemes:
        label = _name(scheme)
        try:
            if scheme.IsGrossBuildingArea: label += '  [System / protected]'
        except Exception: pass
        labels.append(label)
        lookup[label] = scheme
    # Prefer Rentable where available, otherwise first scheme.
    preferred = None
    for label in labels:
        if label.lower().startswith('rentable'):
            preferred = label
            break
    if preferred:
        labels.remove(preferred)
        labels.insert(0, preferred)
    from pyrevit import forms
    theme = _load_theme()
    try:
        if not theme: raise Exception('Lora theme unavailable')
        picked = theme.lora_select_from_list(labels, title='Lora | Select Area Scheme', button_name='Continue', width=520, height=380)
    except Exception:
        picked = forms.SelectFromList.show(labels, title='Lora | Select Area Scheme', multiselect=False)
    return lookup.get(picked)


def choose_area_plan(doc, level, plans):
    if not plans:
        forms.alert('No Area Plans were found for {}.'.format(_name(level)), title='Lora | Draw Area Boundary')
        return None
    labels, lookup = [], {}
    for plan, scheme in plans:
        scheme_name = _name(scheme) if scheme else 'Unknown scheme'
        label = '{}  |  {}'.format(_name(plan), scheme_name)
        labels.append(label); lookup[label] = plan
    theme = _load_theme()
    try:
        if not theme: raise Exception('Lora theme unavailable')
        picked = theme.lora_select_from_list(labels, title='Lora | Use Existing Area Plan', button_name='Use Selected', width=560, height=390)
    except Exception:
        picked = forms.SelectFromList.show(labels, title='Lora | Use Existing Area Plan', multiselect=False)
    return lookup.get(picked)


def resolve_destination(doc, uidoc, active_view):
    if active_view.ViewType == DB.ViewType.AreaPlan:
        return active_view
    allowed = [DB.ViewType.FloorPlan, DB.ViewType.EngineeringPlan]
    if active_view.ViewType not in allowed:
        forms.alert('Open an Architectural Floor Plan, Structural Plan, or Area Plan before running this tool.',
                    title='Lora | Draw Area Boundary', warn_icon=True)
        return None
    level = get_level(active_view)
    if level is None:
        forms.alert('Lora could not identify the Level associated with the active plan view.', title='Lora | Draw Area Boundary', warn_icon=True)
        return None
    existing = matching_plans(doc, level)
    actions = ['Create New Area Plan', 'Use Existing Area Plan']
    theme = _load_theme()
    try:
        if not theme: raise Exception('Lora theme unavailable')
        action = theme.lora_select_from_list(actions, title='Lora | Area Plan Destination', button_name='Continue', width=500, height=300)
    except Exception:
        action = forms.CommandSwitchWindow.show(actions, message='Active view: {}\nLevel: {}'.format(_name(active_view), _name(level)))
    if not action: return None
    if action == actions[1]:
        # Offer same-level plans first. If none exist, show all project plans only as an explicit fallback.
        choices = existing
        if not choices:
            all_plans = get_area_plans(doc)
            if not all_plans:
                forms.alert('No Area Plans exist in this project. Choose Create New Area Plan.', title='Lora | Draw Area Boundary')
                return None
            other = []
            for plan in all_plans:
                try:
                    scheme = doc.GetElement(get_plan_scheme_id(doc, plan))
                    other.append((plan, scheme))
                except Exception: pass
            forms.alert('No Area Plan exists for {}. Lora will show other project Area Plans; choose carefully.'.format(_name(level)), title='Lora | Draw Area Boundary', warn_icon=True)
            choices = other
        target = choose_area_plan(doc, level, choices)
        if target is None: return None
        if not _is_id(target.GenLevel.Id, level.Id):
            if not forms.alert('The selected Area Plan belongs to a different Level. Continue and project geometry to that Level?', title='Lora | Different Level', yes=True, no=True):
                return None
        return target

    schemes = get_area_schemes(doc)
    scheme = choose_scheme(doc, schemes)
    if scheme is None: return None
    # Reuse an existing plan rather than attempting a duplicate for same level + scheme.
    for plan, plan_scheme in existing:
        if plan_scheme and _is_id(plan_scheme.Id, scheme.Id):
            theme = _load_theme()
            try:
                if not theme: raise Exception('Lora theme unavailable')
                use_existing = theme.lora_confirm('An Area Plan already exists for this Level and scheme:\n\n{}\n\nUse the existing view instead of creating a duplicate?'.format(_name(plan)), title='Lora | Existing Area Plan', yes_text='Use Existing', no_text='Cancel')
            except Exception:
                use_existing = forms.alert('An Area Plan already exists for this Level and scheme. Use it?', title='Lora | Existing Area Plan', yes=True, no=True)
            return plan if use_existing else None
    new_plan = None
    with revit.Transaction('Lora - Create Area Plan'):
        new_plan = DB.ViewPlan.CreateAreaPlan(doc, scheme.Id, level.Id)
        try: new_plan.Name = '{} - {}'.format(_name(scheme), _name(level))
        except Exception: pass
    return new_plan


def selected_source_curves(doc, uidoc):
    ids = list(uidoc.Selection.GetElementIds())
    if not ids:
        forms.alert('Select Detail Lines and/or Model Lines first, then run Draw Area Boundary.', title='Lora | Draw Area Boundary')
        return []
    curves = []
    ignored = 0
    for eid in ids:
        el = doc.GetElement(eid)
        if el is None: continue
        try:
            if el.Category and _id_value(el.Category.Id) == int(DB.BuiltInCategory.OST_AreaBoundaryLines):
                ignored += 1; continue
        except Exception: pass
        if isinstance(el, (DB.DetailCurve, DB.ModelCurve)):
            try:
                c = el.GeometryCurve
                if c and c.IsBound:
                    curves.append({'source_id': el.Id, 'curve': c, 'source': el})
            except Exception: pass
    if not curves:
        forms.alert('The current selection contains no supported Detail Lines or Model Lines. Select drafting/model curves and try again.', title='Lora | Draw Area Boundary')
        return []
    return curves


def mm_to_ft(value):
    return float(value) / 304.8


def _xyz_at_level(point, z):
    return DB.XYZ(point.X, point.Y, z)


def _distance_xy(a, b):
    dx = a.X-b.X; dy = a.Y-b.Y
    return (dx*dx + dy*dy) ** 0.5


def prepare_curves(items, target_level, tolerance_mm):
    """Project horizontal curves to level; snap line endpoints within tolerance."""
    z = target_level.Elevation
    tol = mm_to_ft(tolerance_mm)
    prepared = []
    skipped = []
    for item in items:
        c = item['curve']
        try:
            p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
            if abs(p0.Z-p1.Z) > tol:
                skipped.append((item, 'Non-horizontal/sloped source curve'))
                continue
            # Source curve must be effectively horizontal; project it to target level.
            mid = c.Evaluate(0.5, True)
            if abs(p0.Z-mid.Z) > tol or abs(p1.Z-mid.Z) > tol:
                skipped.append((item, 'Non-planar source curve'))
                continue
            if not isinstance(c, (DB.Line, DB.Arc)):
                skipped.append((item, 'Unsupported curve type; only straight lines and circular arcs are supported'))
                continue
            dz = z - mid.Z
            transformed = c.CreateTransformed(DB.Transform.CreateTranslation(DB.XYZ(0, 0, dz)))
            prepared.append({'source_id': item['source_id'], 'curve': transformed, 'is_line': isinstance(transformed, DB.Line)})
        except Exception as ex:
            skipped.append((item, 'Could not read/project curve: {}'.format(ex)))

    # Cluster endpoints only for straight lines. Arc geometry is preserved as-is.
    endpoints = []
    for idx, item in enumerate(prepared):
        if item['is_line']:
            for end_index in (0, 1):
                endpoints.append((idx, end_index, item['curve'].GetEndPoint(end_index)))
    clusters = []
    endpoint_cluster = {}
    for idx, end_index, point in endpoints:
        found = None
        for ci, cluster in enumerate(clusters):
            if _distance_xy(point, cluster['point']) <= tol:
                found = ci; break
        if found is None:
            clusters.append({'point': point, 'members': [(idx, end_index)]})
            found = len(clusters)-1
        else:
            cluster = clusters[found]
            cluster['members'].append((idx, end_index))
            # Mean coordinate gives a neutral shared connection point.
            pts = [prepared[a]['curve'].GetEndPoint(b) for a,b in cluster['members']]
            cluster['point'] = DB.XYZ(sum(p.X for p in pts)/len(pts), sum(p.Y for p in pts)/len(pts), z)
        endpoint_cluster[(idx, end_index)] = found

    repaired = 0
    for idx, item in enumerate(prepared):
        if not item['is_line']: continue
        c = item['curve']
        p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
        c0 = endpoint_cluster.get((idx,0)); c1 = endpoint_cluster.get((idx,1))
        if c0 is not None: p0 = clusters[c0]['point']
        if c1 is not None: p1 = clusters[c1]['point']
        if p0.DistanceTo(c.GetEndPoint(0)) > 1e-9 or p1.DistanceTo(c.GetEndPoint(1)) > 1e-9:
            try:
                item['curve'] = DB.Line.CreateBound(p0, p1)
                repaired += 1
            except Exception:
                skipped.append(({'source_id': item['source_id']}, 'Endpoint repair would create a zero-length line'))
                item['skip'] = True
    prepared = [x for x in prepared if not x.get('skip')]

    # Remove exact/reversed duplicate line segments.
    unique = []
    keys = set()
    duplicates = 0
    quant = max(mm_to_ft(0.1), 1e-7)  # precision independent of endpoint gap tolerance
    for item in prepared:
        c = item['curve']
        try:
            a, b = c.GetEndPoint(0), c.GetEndPoint(1)
            ka = (round(a.X/quant), round(a.Y/quant))
            kb = (round(b.X/quant), round(b.Y/quant))
            key = tuple(sorted((ka,kb)))
            if key in keys:
                duplicates += 1
                continue
            keys.add(key)
        except Exception: pass
        unique.append(item)
    return unique, skipped, repaired, duplicates


def count_closed_components(items, tolerance_mm):
    """Approximate closed-loop count from endpoint graph; informational only."""
    tol = mm_to_ft(tolerance_mm)
    points, edges = [], []
    def node_for(p):
        for i, q in enumerate(points):
            if _distance_xy(p,q) <= tol:
                return i
        points.append(p); return len(points)-1
    for item in items:
        try:
            a = node_for(item['curve'].GetEndPoint(0))
            b = node_for(item['curve'].GetEndPoint(1))
            edges.append((a,b))
        except Exception: pass
    adjacency = {}
    for a,b in edges:
        adjacency.setdefault(a,[]).append(b); adjacency.setdefault(b,[]).append(a)
    seen=set(); loops=0
    for node in adjacency:
        if node in seen: continue
        stack=[node]; component=[]; seen.add(node)
        while stack:
            cur=stack.pop(); component.append(cur)
            for nxt in adjacency.get(cur,[]):
                if nxt not in seen: seen.add(nxt); stack.append(nxt)
        if component and all(len(adjacency.get(n,[])) == 2 for n in component):
            loops += 1
    return loops


def convert(items, target_plan, tolerance_mm):
    doc = revit.doc
    level = target_plan.GenLevel
    prepared, skipped, repaired, duplicates = prepare_curves(items, level, tolerance_mm)
    if not prepared:
        return {'created':0, 'skipped':skipped, 'repaired':repaired, 'duplicates':duplicates, 'loops':0, 'errors':['No valid horizontal curves remain after geometry checks.']}
    loops = count_closed_components(prepared, tolerance_mm)
    created, errors = [], []
    plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ(0,0,level.Elevation))
    with revit.Transaction('Lora - Draw Area Boundary'):
        sketch_plane = DB.SketchPlane.Create(doc, plane)
        for item in prepared:
            try:
                boundary = doc.Create.NewAreaBoundaryLine(sketch_plane, item['curve'], target_plan)
                if boundary: created.append(boundary.Id)
                else: errors.append('Revit returned no element for source {}'.format(_id_value(item['source_id'])))
            except Exception as ex:
                errors.append('Source {}: {}'.format(_id_value(item['source_id']), ex))
    return {'created':len(created), 'created_ids':created, 'skipped':skipped, 'repaired':repaired,
            'duplicates':duplicates, 'loops':loops, 'errors':errors}


def manage_schemes(doc):
    schemes = get_area_schemes(doc)
    if not schemes:
        forms.alert('No Area Schemes were found in this project.', title='Lora | Manage Area Schemes')
        return
    labels=[]; lookup={}
    for s in schemes:
        try: protected = bool(s.IsGrossBuildingArea)
        except Exception: protected = False
        label = '{}{}'.format(_name(s), '  [Gross Building - protected]' if protected else '')
        labels.append(label); lookup[label]=s
    actions=['Review / Rename Existing Scheme', 'Create New Scheme in Revit', 'Delete Scheme in Revit']
    theme = _load_theme()
    try:
        if not theme: raise Exception('Lora theme unavailable')
        action=theme.lora_select_from_list(actions, title='Lora | Manage Area Schemes', button_name='Continue', width=520, height=320)
    except Exception:
        action=forms.CommandSwitchWindow.show(actions, message='Area Scheme management')
    if not action: return
    if action == actions[0]:
        theme = _load_theme()
        try:
            if not theme: raise Exception('Lora theme unavailable')
            selected=theme.lora_select_from_list(labels, title='Lora | Select Scheme to Rename', button_name='Continue', width=560, height=390)
        except Exception:
            selected=forms.SelectFromList.show(labels, title='Select Area Scheme', multiselect=False)
        scheme=lookup.get(selected)
        if not scheme: return
        try:
            if scheme.IsGrossBuildingArea:
                forms.alert('Gross Building is a protected system scheme and cannot be renamed or deleted.', title='Lora | Protected Scheme', warn_icon=True); return
        except Exception: pass
        new_name=forms.ask_for_string(default=_name(scheme), prompt='Enter the new Area Scheme name:', title='Lora | Rename Area Scheme')
        if not new_name: return
        if any(_name(s).lower()==new_name.strip().lower() and not _is_id(s.Id, scheme.Id) for s in schemes):
            forms.alert('An Area Scheme with this name already exists.', title='Lora | Duplicate Name', warn_icon=True); return
        with revit.Transaction('Lora - Rename Area Scheme'):
            scheme.Name = new_name.strip()
        forms.alert('Area Scheme renamed to: {}'.format(new_name.strip()), title='Lora | Manage Area Schemes')
        return
    if action == actions[1]:
        forms.alert('To create a custom Area Scheme, use Revit native command:\n\nArchitecture > Room & Area panel > Area and Volume Computations > Area Schemes > New.\n\nAfter creating it, run Lora again; the new scheme will appear in the list.', title='Lora | Create Area Scheme')
        return
    if action == actions[2]:
        theme = _load_theme()
        try:
            if not theme: raise Exception('Lora theme unavailable')
            selected=theme.lora_select_from_list(labels, title='Lora | Select Scheme to Delete', button_name='Review', width=560, height=390)
        except Exception:
            selected=forms.SelectFromList.show(labels, title='Select Area Scheme', multiselect=False)
        scheme=lookup.get(selected)
        if not scheme: return
        try:
            if scheme.IsGrossBuildingArea:
                forms.alert('Gross Building is a protected system scheme and cannot be deleted.', title='Lora | Protected Scheme', warn_icon=True); return
        except Exception: pass
        associated=[]
        for plan in get_area_plans(doc):
            if _is_id(get_plan_scheme_id(doc, plan), scheme.Id): associated.append(plan)
        msg='Deleting this scheme will also delete its associated Area Plans.\n\nScheme: {}\nAssociated Area Plans: {}\n\nUse Revit native Area and Volume Computations to review/delete this scheme safely.'.format(_name(scheme), len(associated))
        forms.alert(msg, title='Lora | Delete Area Scheme', warn_icon=True)
        return


def run():
    doc, uidoc = revit.doc, revit.uidoc
    if doc.IsFamilyDocument:
        forms.alert('Draw Area Boundary is available only in a project document.', title='Lora | Draw Area Boundary', warn_icon=True)
        return
    actions=['Draw Area Boundary', 'Manage Area Schemes']
    theme = _load_theme()
    try:
        if not theme: raise Exception('Lora theme unavailable')
        action=theme.lora_select_from_list(actions, title='Lora | Area Tools', button_name='Continue', width=480, height=280)
    except Exception:
        action=forms.CommandSwitchWindow.show(actions, message='Choose an area workflow')
    if not action: return
    if action == actions[1]:
        manage_schemes(doc); return

    # Capture selected geometry BEFORE creating/changing views; Detail Curves are view-specific.
    source_items = selected_source_curves(doc, uidoc)
    if not source_items: return
    active_view = doc.ActiveView
    target_plan = resolve_destination(doc, uidoc, active_view)
    if target_plan is None: return

    tolerance = 2.0
    try:
        raw = forms.ask_for_string(default='2.0', prompt='Automatic endpoint connection tolerance (mm):', title='Lora | Geometry Repair')
        if raw is None: return
        tolerance = float(raw.replace(',', '.'))
        if tolerance < 0 or tolerance > 50:
            forms.alert('Tolerance must be between 0 and 50 mm.', title='Lora | Invalid Tolerance', warn_icon=True); return
    except Exception:
        forms.alert('Enter a valid numeric tolerance in millimetres.', title='Lora | Invalid Tolerance', warn_icon=True); return

    # Preview geometry processing before committing boundary elements.
    level = target_plan.GenLevel
    prepared, skipped, repaired, duplicates = prepare_curves(source_items, level, tolerance)
    loops = count_closed_components(prepared, tolerance)
    preview = ('Destination: {}\nLevel: {}\n\nSelected source curves: {}\nUsable curves: {}\nAutomatically repaired line segments: {}\nDuplicate segments: {}\nClosed loops detected: {}\nSkipped curves: {}\n\nOriginal Detail/Model Lines will be retained. Continue?'.format(
        _name(target_plan), _name(level), len(source_items), len(prepared), repaired, duplicates, loops, len(skipped)))
    theme = _load_theme()
    try:
        if not theme: raise Exception('Lora theme unavailable')
        proceed=theme.lora_confirm(preview, title='Lora | Conversion Preview', yes_text='Convert', no_text='Cancel')
    except Exception:
        proceed=forms.alert(preview, title='Lora | Conversion Preview', yes=True, no=True)
    if not proceed: return

    result = convert(source_items, target_plan, tolerance)
    # Switch to the destination after successful conversion; source selection is no longer needed.
    if result['created']:
        try: uidoc.RequestViewChange(target_plan)
        except Exception: pass
    lines=['Area Boundary conversion completed.','', 'Area Plan: {}'.format(_name(target_plan)),
           'Created Area Boundary Lines: {}'.format(result['created']),
           'Automatically repaired line segments: {}'.format(result['repaired']),
           'Duplicate segments removed: {}'.format(result['duplicates']),
           'Closed loops detected: {}'.format(result['loops']),
           'Skipped source curves: {}'.format(len(result['skipped'])),
           'Creation errors: {}'.format(len(result['errors'])), '',
           'Note: Closed-loop detection is informational. Lora creates valid boundary segments but does not automatically place Area elements.']
    if result['skipped']:
        lines.append('\nSkipped details:')
        for item, reason in result['skipped'][:10]: lines.append('- {}: {}'.format(_id_value(item.get('source_id')), reason))
    if result['errors']:
        lines.append('\nCreation errors:')
        for err in result['errors'][:10]: lines.append('- {}'.format(err))
    forms.alert('\n'.join(lines), title='Lora | Draw Area Boundary', warn_icon=bool(result['errors'] or result['skipped']))
