# -*- coding: utf-8 -*-


# Lora shared extension library (single source of truth)
import os as _lora_os, sys as _lora_sys
_lora_lib = _lora_os.path.abspath(_lora_os.path.join(_lora_os.path.dirname(__file__), "..", "..", "..", "lib"))
if _lora_lib not in _lora_sys.path:
    _lora_sys.path.insert(0, _lora_lib)
from LoraDialogTheme import lora_select_from_list, lora_alert
from LoraCore import element_id_value, ids_equal, safe_text

"""Lora - Refresh (Revit 2023–2026 / pyRevit; runtime validation required)
Calculates bounding-box top/bottom relative to the highest upward-facing planar
floor face beneath the element location. Review reported failures before issue.
"""
from pyrevit import revit, DB, forms, script
import re

uidoc = revit.uidoc
doc = revit.doc


PARAM_BOTTOM = 'Lora_Relative_Bottom_Elevation'
PARAM_TOP = 'Lora_Relative_Top_Elevation'
PARAM_RELATIVE = 'Lora_Relative_Elevation'
PARAM_TYPE = 'Lora_Elevation_Type'
PARAM_STATUS = 'Lora_Elevation_Status'

CATEGORY_NAMES = {
    DB.BuiltInCategory.OST_PipeCurves: 'Pipe',
    DB.BuiltInCategory.OST_DuctCurves: 'Duct',
    DB.BuiltInCategory.OST_CableTray: 'Cable Tray',
    DB.BuiltInCategory.OST_Conduit: 'Conduit'
}

# Select elements, or use current selection.
selected_ids = list(uidoc.Selection.GetElementIds())
if not selected_ids:
    try:
        refs = uidoc.Selection.PickObjects(DB.Selection.ObjectType.Element,
            'Select pipes, ducts, cable trays and conduits; press Finish when done')
        selected_ids = [r.ElementId for r in refs]
    except Exception:
        script.exit()

valid_category_ids = set()
for _bic in CATEGORY_NAMES.keys():
    try:
        valid_category_ids.add(int(_bic))
    except Exception:
        pass

els = []
for eid in selected_ids:
    el = doc.GetElement(eid)
    if el and el.Category:
        try:
            if element_id_value(el.Category.Id) in valid_category_ids:
                els.append(el)
        except Exception:
            pass
if not els:
    lora_alert('Select at least one Pipe, Duct, Cable Tray or Conduit.', title='Lora MEP Elevations')
    script.exit()

# Gather floor top faces. Top planar faces are used; complex/sloped floors may need review.
floors = list(DB.FilteredElementCollector(doc).OfClass(DB.Floor).WhereElementIsNotElementType())

def get_location_xy(el):
    try:
        loc = el.Location
        if isinstance(loc, DB.LocationCurve):
            crv = loc.Curve
            return crv.Evaluate(0.5, True)
        if isinstance(loc, DB.LocationPoint):
            return loc.Point
    except Exception:
        pass
    bb = el.get_BoundingBox(None)
    if bb:
        return DB.XYZ((bb.Min.X + bb.Max.X) / 2.0,
                      (bb.Min.Y + bb.Max.Y) / 2.0,
                      (bb.Min.Z + bb.Max.Z) / 2.0)
    return None

def get_floor_z(x, y, z_hint):
    """Return highest upward-facing floor face at XY, using planar projection.
    Avoids Face.Intersect overload issues in IronPython/pyRevit.
    """
    hits = []
    for fl in floors:
        try:
            opt = DB.Options()
            opt.ComputeReferences = False
            opt.IncludeNonVisibleObjects = True
            geom = fl.get_Geometry(opt)
            for obj in geom:
                solids = []
                if isinstance(obj, DB.Solid) and obj.Volume > 1e-9:
                    solids.append(obj)
                elif isinstance(obj, DB.GeometryInstance):
                    try:
                        inst = obj.GetInstanceGeometry()
                        for io in inst:
                            if isinstance(io, DB.Solid) and io.Volume > 1e-9:
                                solids.append(io)
                    except Exception:
                        pass
                for solid in solids:
                    for face in solid.Faces:
                        if not isinstance(face, DB.PlanarFace):
                            continue
                        pf = face
                        n = pf.FaceNormal
                        # Consider upward-facing top surfaces, including mildly sloped floors.
                        if n.Z < 0.5:
                            continue
                        # Solve the plane equation for Z at the target XY.
                        try:
                            origin = pf.Origin
                            z = origin.Z - (n.X * (x - origin.X) + n.Y * (y - origin.Y)) / n.Z
                            if z > z_hint + 1e-5:
                                continue
                            test_pt = DB.XYZ(x, y, z)
                            proj = pf.Project(test_pt)
                            if proj is None:
                                continue
                            # Require the projected point to lie within the bounded face.
                            if not pf.IsInside(proj.UVPoint):
                                continue
                            hits.append(z)
                        except Exception:
                            continue
        except Exception:
            continue
    if not hits:
        return None
    return max(hits)

def set_param(el, name, value):
    p = el.LookupParameter(name)
    if p is None or p.IsReadOnly:
        return False
    try:
        if p.StorageType == DB.StorageType.Double:
            p.Set(float(value))
        elif p.StorageType == DB.StorageType.String:
            p.Set(str(value))
        else:
            return False
        return True
    except Exception:
        return False

def read_param_text(el, names):
    """Read the first non-empty parameter value from a list of possible names."""
    for name in names:
        try:
            p = el.LookupParameter(name)
            if p is None:
                continue
            val = p.AsString()
            if val:
                return val.strip()
            val = p.AsValueString()
            if val:
                return val.strip()
        except Exception:
            pass
    return ''

def get_service_abbreviation(el):
    # System Abbreviation is normally supplied by the assigned MEP system type.
    # Prefer the built-in parameter when available, then localized/display names.
    names = []
    try:
        bip = getattr(DB.BuiltInParameter, 'RBS_SYSTEM_ABBREVIATION_PARAM', None)
        if bip is not None:
            p = el.get_Parameter(bip)
            if p:
                val = p.AsString() or p.AsValueString()
                if val and val.strip():
                    return val.strip()
    except Exception:
        pass
    names.extend(['System Abbreviation', 'Abbreviation'])
    value = read_param_text(el, names)
    if value:
        return value
    # Fallback: read the abbreviation from the assigned MEP system type.
    try:
        mep_system = getattr(el, 'MEPSystem', None)
        if mep_system:
            sys_type = doc.GetElement(mep_system.GetTypeId())
            if sys_type:
                return read_param_text(sys_type, ['System Abbreviation', 'Abbreviation'])
    except Exception:
        pass
    return ''

def clean_dimension_text(value):
    """Remove display units such as mm and diameter symbols from size text."""
    if not value:
        return ''
    text = str(value).strip()
    # Remove common metric/imperial unit suffixes; preserve numeric dimensions.
    text = re.sub(r'\s*(mm|millimeters?|cm|meters?|m|inches|inch|in)\s*$', '', text, flags=re.IGNORECASE)
    text = text.replace('Ø', '').replace('ø', '').strip()
    text = re.sub(r'\s+', ' ', text)
    return text

def get_dimension_value(el, bip_names, param_names):
    for bip_name in bip_names:
        try:
            bip = getattr(DB.BuiltInParameter, bip_name, None)
            if bip is not None:
                p = el.get_Parameter(bip)
                if p:
                    if p.StorageType == DB.StorageType.Double:
                        val = DB.UnitUtils.ConvertFromInternalUnits(p.AsDouble(), DB.UnitTypeId.Millimeters)
                        return '{:.0f}'.format(val)
                    val = p.AsValueString() or p.AsString()
                    if val:
                        return clean_dimension_text(val)
        except Exception:
            pass
    val = read_param_text(el, param_names)
    return clean_dimension_text(val)

def get_service_size(el, catname):
    """Return sizes without units: Pipe -> Ø200; rectangular services -> 600 x 300."""
    if catname == 'Pipe':
        diameter = get_dimension_value(
            el,
            ['RBS_PIPE_DIAMETER_PARAM', 'RBS_PIPE_OUTER_DIAMETER'],
            ['Diameter', 'Nominal Diameter', 'Outside Diameter']
        )
        return 'Ø{}'.format(diameter) if diameter else ''
    if catname == 'Duct':
        width = get_dimension_value(el, ['RBS_CURVE_WIDTH_PARAM'], ['Width'])
        height = get_dimension_value(el, ['RBS_CURVE_HEIGHT_PARAM'], ['Height'])
        if width and height:
            return '{} x {}'.format(width, height)
        return width or height or ''
    if catname == 'Cable Tray':
        width = get_dimension_value(el, ['RBS_CABLETRAY_WIDTH_PARAM'], ['Width'])
        height = get_dimension_value(el, ['RBS_CABLETRAY_HEIGHT_PARAM'], ['Height'])
        if width and height:
            return '{} x {}'.format(width, height)
        return width or height or ''
    if catname == 'Conduit':
        diameter = get_dimension_value(
            el,
            ['RBS_CONDUIT_DIAMETER_PARAM'],
            ['Diameter', 'Nominal Diameter', 'Outside Diameter']
        )
        return 'Ø{}'.format(diameter) if diameter else ''
    return ''

updated = 0
skipped = []
notes = []
category_names_by_id = {}
for _bic, _name in CATEGORY_NAMES.items():
    try:
        category_names_by_id[int(_bic)] = _name
    except Exception:
        pass

with revit.Transaction('Lora - Refresh'):
    for el in els:
        catname = category_names_by_id.get(element_id_value(el.Category.Id), 'MEP')
        bb = el.get_BoundingBox(None)
        pt = get_location_xy(el)
        if not bb or not pt:
            skipped.append('{} ({}): no usable geometry'.format(element_id_value(el.Id), catname))
            continue
        floor_z = get_floor_z(pt.X, pt.Y, bb.Min.Z)
        if floor_z is None:
            # Do not write a guessed elevation if no floor is found.
            set_param(el, PARAM_STATUS, 'NO FLOOR FOUND - CHECK MANUALLY')
            skipped.append('{} ({}): no floor surface found below element'.format(element_id_value(el.Id), catname))
            continue
        bottom_rel = bb.Min.Z - floor_z
        top_rel = bb.Max.Z - floor_z
        # Assign the display reference automatically by MEP category.
        if catname == 'Pipe':
            default_type = 'BOP'
        elif catname == 'Duct':
            default_type = 'BOD'
        else:
            # Cable tray/trunking and conduit default to bottom of tray/conduit.
            default_type = 'BOT'

        # Populate the display fields from the element/system properties.
        abbreviation = get_service_abbreviation(el)
        service_size = get_service_size(el, catname)
        okb = set_param(el, PARAM_BOTTOM, bottom_rel)
        okt = set_param(el, PARAM_TOP, top_rel)
        okr = set_param(el, PARAM_RELATIVE, bottom_rel)
        oktype = set_param(el, PARAM_TYPE, default_type)
        okabbr = set_param(el, 'Lora_Service_Abbreviation', abbreviation) if abbreviation else True
        oksize = set_param(el, 'Lora_Service_Size', service_size) if service_size else True
        status_ok = set_param(el, PARAM_STATUS, 'OK - FLOOR INTERSECTION')
        if okb and okt and okr and oktype and okabbr and oksize:
            updated += 1
        else:
            missing = []
            for nm, ok in [(PARAM_BOTTOM,okb),(PARAM_TOP,okt),(PARAM_RELATIVE,okr),
                           (PARAM_TYPE,oktype),('Lora_Service_Abbreviation',okabbr),
                           ('Lora_Service_Size',oksize)]:
                if not ok: missing.append(nm)
            notes.append('{}: could not write {}'.format(element_id_value(el.Id), ', '.join(missing)))

msg = 'Lora MEP Elevations\n\nUpdated: {} of {} element(s).\n'.format(updated, len(els))
msg += '\nDefault displayed references: BOP (pipes), BOD (ducts), BOT (cable trays/conduits).'
msg += '\nValues are relative to the highest upward-facing floor face intersected at the element location.'
msg += '\n\nReview the results: bounding-box elevations are used in this first version; sloped/curved services and complex floor geometry may need checking.'
if skipped:
    msg += '\n\nSkipped / needs manual review ({}):\n{}'.format(len(skipped), '\n'.join(skipped[:15]))
if notes:
    msg += '\n\nParameter write issues ({}):\n{}'.format(len(notes), '\n'.join(notes[:10]))
lora_alert(msg, title='Lora - Refresh', warn_icon=bool(skipped or notes))
