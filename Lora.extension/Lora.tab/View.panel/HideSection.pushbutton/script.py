# -*- coding: utf-8 -*-
"""Keep section marker symbols on the active view's sheet; hide the other section markers.

Revit draws the section line/head as a Viewer element (OST_Viewers), not as the
ViewSection element itself. The previous implementation attempted to hide ViewSection
ids, which are not the actual visible marker and therefore reported them as unsupported.
"""
import os as _os, sys as _sys
_lib = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..', '..', 'lib'))
if _lib not in _sys.path:
    _sys.path.insert(0, _lib)

from pyrevit import revit, DB, script
from LoraDialogTheme import lora_alert, lora_confirm
from LoraCore import element_id_value

uidoc = revit.uidoc
doc = revit.doc
active_view = uidoc.ActiveView
if active_view is None or active_view.IsTemplate:
    lora_alert('Open the plan view containing the section symbols before running Hide Section.', title='Lora - Hide Section')
    script.exit()

# Determine which sheet contains the active parent view.
active_view_id = element_id_value(active_view.Id)
sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).WhereElementIsNotElementType().ToElements())
active_sheet = None
for sheet in sheets:
    try:
        if any(element_id_value(vid) == active_view_id for vid in sheet.GetAllPlacedViews()):
            active_sheet = sheet
            break
    except Exception:
        pass
if active_sheet is None:
    lora_alert(
        'The active view "{}" is not placed on a sheet.\n\nOpen the plan from its sheet or place it on a sheet first. The tool needs the active view sheet to decide which section symbols should remain visible.'.format(active_view.Name),
        title='Lora - Hide Section')
    script.exit()
active_sheet_id = element_id_value(active_sheet.Id)

# Build a broad view lookup. Revit's OST_Viewers elements do not expose a
# consistent public typed class across all marker kinds / versions. Include all
# ViewSection-derived views (sections, details and callouts), then identify the
# target from reference APIs, view-name parameters, or a guarded adjacent-id fallback.
view_lookup = {}
section_views = {}
all_view_sections = {}
try:
    all_sections = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSection).WhereElementIsNotElementType().ToElements())
except Exception as exc:
    script.get_logger().exception('Hide Section: failed to collect section views')
    lora_alert('Revit could not read section/detail views.\n\n{}'.format(exc), title='Lora - Hide Section Error', warn_icon=True)
    script.exit()
for view in all_sections:
    try:
        if not view.IsTemplate:
            vid = element_id_value(view.Id)
            view_lookup[vid] = view
            all_view_sections[vid] = view
            # Keep actual sections as targets; all ViewSection objects remain available
            # for resolving marker relationships, since some Revit marker classes expose
            # an associated view whose ViewType is not populated as expected.
            if view.ViewType == DB.ViewType.Section:
                section_views[vid] = view
    except Exception:
        continue

placed_on_sheets = {}
for sheet in sheets:
    try:
        sid = element_id_value(sheet.Id)
        for view_id in sheet.GetAllPlacedViews():
            vid = element_id_value(view_id)
            if vid in view_lookup:
                placed_on_sheets.setdefault(vid, set()).add(sid)
    except Exception:
        continue

# Scan visible viewer graphics and match each to its referenced view.
try:
    viewers = list(DB.FilteredElementCollector(doc, active_view.Id)
                   .OfCategory(DB.BuiltInCategory.OST_Viewers)
                   .WhereElementIsNotElementType().ToElements())
except Exception as exc:
    script.get_logger().exception('Hide Section: failed to collect OST_Viewers marker elements')
    lora_alert('Could not scan section marker symbols in this view.\n\n{}'.format(exc), title='Lora - Hide Section Error', warn_icon=True)
    script.exit()

def _id_value(eid):
    try:
        return element_id_value(eid)
    except Exception:
        return -1

def _param_text(element, names):
    for pname in names:
        try:
            bip = getattr(DB.BuiltInParameter, pname, None)
            if bip is None:
                continue
            param = element.get_Parameter(bip)
            if param and param.StorageType == DB.StorageType.String:
                value = param.AsString()
                if value and value.strip():
                    return value.strip()
        except Exception:
            pass
    return None

def _marker_family_text(marker):
    try:
        typ = doc.GetElement(marker.GetTypeId())
        bits = []
        if typ:
            for attr in ('FamilyName', 'Name'):
                try:
                    val = getattr(typ, attr, None)
                    if val:
                        bits.append(str(val))
                except Exception:
                    pass
        return ' '.join(bits).lower()
    except Exception:
        return ''

def _normal_name(value):
    try:
        return ' '.join((value or '').strip().lower().split())
    except Exception:
        return ''

def _match_marker(marker):
    """Resolve the referenced view using all collected ViewSection objects.

    Earlier builds searched only the subset classified as ViewType.Section. That
    restriction can prevent a match when Revit exposes the marker's associated view
    through a different ViewType or when the visible marker's Name is not the view name.
    Resolve broadly first, then the caller safely limits visibility changes to true
    section views only.
    """
    mid = _id_value(marker.Id)
    try:
        marker_name = _normal_name(marker.Name)
    except Exception:
        marker_name = ''

    # IMPORTANT: OST_Viewers also includes elevation markers and other view graphics.
    # ReferenceableViewUtils may resolve a viewer to a non-section view. Never return
    # that result early: only a true ViewType.Section is a valid target for this tool.
    # Otherwise the caller rejects it and the correct section-name match is never tried.
    try:
        rid = DB.ReferenceableViewUtils.GetReferencedViewId(doc, marker.Id)
        rv = view_lookup.get(_id_value(rid))
        if rv is not None and rv.ViewType == DB.ViewType.Section:
            return rv, 'ReferenceableViewUtils (section view)'
    except Exception:
        pass

    # 2) Exact normalized name against actual section views only. In this project,
    # the viewer element names are the same as the corresponding section view names.
    if marker_name:
        named = []
        for candidate in section_views.values():
            try:
                if _normal_name(candidate.Name) == marker_name:
                    named.append(candidate)
            except Exception:
                pass
        if len(named) == 1:
            return named[0], 'normalized exact section name'

    # 3) Use the adjacent-ID relationship observed in this project, but only accept
    # candidates that are actual section views. Prefer an exact-name agreement.
    adjacent = []
    for candidate_id in (mid + 1, mid - 1):
        candidate = section_views.get(candidate_id)
        if candidate is not None:
            adjacent.append(candidate)
    if marker_name and adjacent:
        for candidate in adjacent:
            try:
                if _normal_name(candidate.Name) == marker_name:
                    return candidate, 'adjacent ID and exact section name'
            except Exception:
                pass
    if len(adjacent) == 1:
        return adjacent[0], 'adjacent element id (unique section view)'

    # 4) Read parameters by their visible definition names as well as built-in names.
    # ElementId parameters can directly identify a view without string-name matching.
    try:
        for param in marker.Parameters:
            try:
                pname = _normal_name(param.Definition.Name if param.Definition else '')
                if not any(token in pname for token in ('view', 'section', 'referenc')):
                    continue
                if param.StorageType == DB.StorageType.ElementId:
                    pid = _id_value(param.AsElementId())
                    candidate = section_views.get(pid)
                    if candidate is not None:
                        return candidate, 'view-related ElementId parameter (section view)'
                elif param.StorageType == DB.StorageType.String:
                    target_name = _normal_name(param.AsString())
                    if target_name:
                        matches = [v for v in section_views.values()
                                   if _normal_name(v.Name) == target_name]
                        if len(matches) == 1:
                            return matches[0], 'view-related string parameter'
            except Exception:
                pass
    except Exception:
        pass

    # 5) Known built-in name parameters, for API versions where they are exposed.
    target_name = _normal_name(_param_text(marker, (
        'VIEW_NAME', 'VIEWER_VIEW_NAME', 'VIEWER_REFERENCE_VIEW_NAME',
        'VIEWER_NAME', 'SECTION_NAME'
    )))
    if target_name:
        matches = [v for v in section_views.values()
                   if _normal_name(v.Name) == target_name]
        if len(matches) == 1:
            return matches[0], 'view-name parameter'
    return None, None

hide_ids = []
unhide_ids = []
skipped = []
matched = []
match_methods = {}
for marker in viewers:
    try:
        related_view, method = _match_marker(marker)
        if related_view is None:
            skipped.append('Viewer {}'.format(_id_value(marker.Id)))
            continue
        # This tool specifically governs section views. Don't accidentally hide an
        # elevation marker or unrelated viewer just because it is in OST_Viewers.
        if related_view.ViewType != DB.ViewType.Section:
            skipped.append('{} (resolved view type: {}; not a section target)'.format(related_view.Name, related_view.ViewType))
            continue
        related_id = _id_value(related_view.Id)
        same_sheet = active_sheet_id in placed_on_sheets.get(related_id, set())
        if not active_view.CanElementBeHidden(marker.Id):
            skipped.append('{} (marker cannot be hidden)'.format(related_view.Name))
            continue
        hidden = active_view.IsHidden(marker.Id)
        if same_sheet and hidden:
            unhide_ids.append(marker.Id)
            matched.append(related_view.Name)
        elif not same_sheet and not hidden:
            hide_ids.append(marker.Id)
            matched.append(related_view.Name)
        else:
            # It is still a successful match, even if no visibility change is needed.
            matched.append(related_view.Name)
        match_methods[method] = match_methods.get(method, 0) + 1
    except Exception:
        try:
            skipped.append('Viewer {}'.format(_id_value(marker.Id)))
        except Exception:
            skipped.append('Unknown marker')

if not hide_ids and not unhide_ids:
    if not matched:
        # Write detailed diagnostics so the actual Revit marker classes, types, and
        # exposed parameters can be inspected instead of guessing at ID relationships.
        def _safe_text(value):
            try:
                return str(value).replace('\r', ' ').replace('\n', ' ')
            except Exception:
                return '<unreadable>'
        lines = []
        lines.append('LORA HIDE SECTION - MARKER DIAGNOSTICS')
        lines.append('View: {} (Id={})'.format(_safe_text(active_view.Name), active_view_id))
        lines.append('Active sheet: {} (Id={})'.format(_safe_text(active_sheet.Name), active_sheet_id))
        lines.append('Viewer elements scanned: {}'.format(len(viewers)))
        lines.append('Matching algorithm: section-only ReferenceableViewUtils, exact section name, adjacent section ID, section-view parameters')
        lines.append('')
        for index, marker in enumerate(viewers):
            lines.append('=== VIEWER {} ==='.format(index + 1))
            lines.append('Id: {}'.format(_id_value(marker.Id)))
            try: lines.append('Class: {}'.format(_safe_text(marker.GetType().FullName)))
            except Exception: pass
            try: lines.append('Category: {}'.format(_safe_text(marker.Category.Name if marker.Category else None)))
            except Exception: pass
            try: lines.append('Name: {}'.format(_safe_text(marker.Name)))
            except Exception: pass
            try:
                typ = doc.GetElement(marker.GetTypeId())
                lines.append('TypeId: {}'.format(_id_value(marker.GetTypeId())))
                if typ:
                    try: lines.append('Type class: {}'.format(_safe_text(typ.GetType().FullName)))
                    except Exception: pass
                    try: lines.append('Type name: {}'.format(_safe_text(typ.Name)))
                    except Exception: pass
                    try: lines.append('Family name: {}'.format(_safe_text(typ.FamilyName)))
                    except Exception: pass
            except Exception: pass
            try:
                bb = marker.get_BoundingBox(active_view)
                if bb: lines.append('BoundingBox: Min=({}, {}, {}) Max=({}, {}, {})'.format(bb.Min.X, bb.Min.Y, bb.Min.Z, bb.Max.X, bb.Max.Y, bb.Max.Z))
            except Exception: pass
            try:
                for param in marker.Parameters:
                    try:
                        pname = param.Definition.Name if param.Definition else '<no definition>'
                        st = str(param.StorageType)
                        if param.StorageType == DB.StorageType.String: val = param.AsString() or param.AsValueString()
                        elif param.StorageType == DB.StorageType.ElementId: val = 'ElementId({})'.format(_id_value(param.AsElementId()))
                        elif param.StorageType == DB.StorageType.Integer: val = param.AsInteger()
                        elif param.StorageType == DB.StorageType.Double: val = param.AsDouble()
                        else: val = param.AsValueString()
                        lines.append('PARAM | {} | {} | {}'.format(_safe_text(pname), st, _safe_text(val)))
                    except Exception: pass
            except Exception: pass
            lines.append('')
        lines.append('=== VIEWSECTION VIEWS ===')
        for vid, view in sorted(all_view_sections.items()):
            lines.append('ViewId={} | Name={} | ViewType={} | Sheets={}'.format(vid, _safe_text(view.Name), _safe_text(view.ViewType), ','.join(str(x) for x in sorted(placed_on_sheets.get(vid, set())))))
        report_path = _os.path.join(_os.path.expanduser('~'), 'Desktop', 'Lora_HideSection_Diagnostic.txt')
        try:
            import io
            with io.open(report_path, 'w', encoding='utf-8') as f:
                f.write(u'\n'.join(lines))
        except Exception:
            report_path = _os.path.join(_os.environ.get('TEMP', _os.path.expanduser('~')), 'Lora_HideSection_Diagnostic.txt')
            import io
            with io.open(report_path, 'w', encoding='utf-8') as f:
                f.write(u'\n'.join(lines))
        lora_alert(
            'MARKER DETECTION FAILED - no changes were made.\n\nView: {}\nVisible Viewer elements scanned: {}\nSection markers matched: 0\nUnmatched/unsupported markers: {}\n\nThe matching methods were attempted but none resolved a section view. A diagnostic report was written to:\n{}\n\nDo not treat this run as successful.'.format(
                active_view.Name, len(viewers), len(skipped), report_path),
            title='Lora - Hide Section: Detection Failed', warn_icon=True)
    else:
        lora_alert(
            'Markers were detected, but no visibility changes were required.\n\nView: {}\nSection markers matched: {}\nMarkers to hide: 0\nMarkers to show: 0\nUnmatched/unsupported markers: {}\nMatching methods: {}'.format(
                active_view.Name, len(matched), len(skipped), ', '.join('{}: {}'.format(k, v) for k, v in match_methods.items())),
            title='Lora - Hide Section')
    script.exit()

summary = ('Active view: {}\nReference sheet: {}\n\nHide section symbols whose views are not placed on this sheet: {}\nShow section symbols whose views are placed on this sheet: {}\nUnmatched/unsupported markers: {}\nMatching methods: {}\n\nOnly marker symbols in the active view will be hidden/shown. Section views will not be deleted.').format(
    active_view.Name, active_sheet.Name, len(hide_ids), len(unhide_ids), len(skipped), ', '.join('{}: {}'.format(k, v) for k, v in match_methods.items()))
if not lora_confirm(summary, title='Lora - Hide Unnecessary Sections', yes_text='Apply', no_text='Cancel'):
    script.exit()

try:
    with revit.Transaction('Lora - Keep Same-Sheet Section Markers'):
        if hide_ids:
            active_view.HideElements(hide_ids)
        if unhide_ids:
            active_view.UnhideElements(unhide_ids)
except Exception as exc:
    script.get_logger().exception('Hide Section marker visibility update failed')
    lora_alert('Revit could not update section marker visibility.\n\n{}'.format(exc), title='Lora - Hide Section Error', warn_icon=True)
    script.exit()

lora_alert(
    'Section marker visibility updated.\n\nView: {}\nHidden (not on reference sheet): {}\nShown (on reference sheet): {}\nUnmatched/unsupported markers: {}\nMatching methods: {}\n\nOnly marker symbols in the active view were changed; section views were not deleted.'.format(
        active_view.Name, len(hide_ids), len(unhide_ids), len(skipped), ', '.join('{}: {}'.format(k, v) for k, v in match_methods.items())),
    title='Lora - Hide Section')
