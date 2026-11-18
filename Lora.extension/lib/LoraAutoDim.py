# -*- coding: utf-8 -*-
"""Lora Auto Dim helpers. Revit 2023-2026 / IronPython-compatible."""
from Autodesk.Revit.DB import (FilteredElementCollector, BuiltInCategory, BuiltInParameter,
    DimensionType, Wall, Options, PlanarFace, XYZ, Line, ReferenceArray, ViewType)

MM_PER_FOOT = 304.8

# Use BuiltInCategory IDs rather than localized Category.Name values.
CATEGORY_IDS = {
    'Walls': int(BuiltInCategory.OST_Walls),
    'Doors': int(BuiltInCategory.OST_Doors),
    'Windows': int(BuiltInCategory.OST_Windows),
    'Structural Columns': int(BuiltInCategory.OST_StructuralColumns),
    'Structural Framing': int(BuiltInCategory.OST_StructuralFraming),
    'Grids': int(BuiltInCategory.OST_Grids),
    'Stairs': int(BuiltInCategory.OST_Stairs),
    'Floors': int(BuiltInCategory.OST_Floors),
}


def mm_to_ft(value):
    return float(value) / MM_PER_FOOT


def id_value(eid):
    try:
        return int(eid.Value)
    except Exception:
        try:
            return int(eid.IntegerValue)
        except Exception:
            return -1


def get_view_elements(doc, view):
    return list(FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType().ToElements())


def category_counts(elements):
    counts = dict((name, 0) for name in CATEGORY_IDS)
    for el in elements:
        try:
            cat = el.Category
            if cat is None:
                continue
            cid = id_value(cat.Id)
            for name, target_id in CATEGORY_IDS.items():
                if cid == target_id:
                    counts[name] += 1
                    break
        except Exception:
            pass
    return counts


def get_dimension_types(doc, view=None):
    """Return actual linear DimensionType objects only; spot/angular styles are excluded."""
    candidates = []
    collector_errors = []
    try:
        candidates = list(FilteredElementCollector(doc).OfClass(DimensionType).WhereElementIsElementType().ToElements())
    except Exception as ex:
        collector_errors.append('DimensionType collector: {}'.format(ex))
        try:
            for item in FilteredElementCollector(doc).WhereElementIsElementType().ToElements():
                try:
                    if item.GetType().Name == 'DimensionType':
                        candidates.append(item)
                except Exception:
                    pass
        except Exception as ex2:
            collector_errors.append('Fallback type scan: {}'.format(ex2))

    results = {}
    for item in candidates:
        try:
            if item is None or item.GetType().Name != 'DimensionType':
                continue
            if 'linear' not in str(item.StyleType).lower():
                continue
            name = ''
            try:
                name = item.Name
            except Exception:
                pass
            if not name:
                try:
                    p = item.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                    name = p.AsString() if p else ''
                except Exception:
                    name = ''
            if not name:
                continue
            label = '{} [Id {}] — Linear'.format(name, id_value(item.Id))
            results[label] = item
        except Exception:
            continue
    get_dimension_types.last_diagnostics = {
        'candidate_count': len(candidates),
        'result_count': len(results),
        'collector_errors': collector_errors,
    }
    return results


def _iter_solids(geometry):
    if geometry is None:
        return
    for obj in geometry:
        try:
            if obj.Faces is not None and obj.Volume > 1e-9:
                yield obj
        except Exception:
            try:
                for nested in _iter_solids(obj.GetInstanceGeometry()):
                    yield nested
            except Exception:
                pass


def wall_planar_refs(wall, view):
    """Return sorted end-face and side-face references. Curved walls are rejected by caller."""
    try:
        curve = wall.Location.Curve
        start, end = curve.GetEndPoint(0), curve.GetEndPoint(1)
        delta = XYZ(end.X - start.X, end.Y - start.Y, 0)
        if delta.GetLength() < 1e-8:
            return [], []
        direction = delta.Normalize()
        perp = XYZ(-direction.Y, direction.X, 0)
    except Exception:
        return [], []
    opts = Options()
    opts.View = view
    opts.ComputeReferences = True
    opts.IncludeNonVisibleObjects = False
    ends, sides = [], []
    try:
        geom = wall.get_Geometry(opts)
        for solid in _iter_solids(geom):
            for face in solid.Faces:
                try:
                    if not isinstance(face, PlanarFace) or face.Reference is None:
                        continue
                    normal = face.FaceNormal
                    origin = face.Origin
                    if abs(normal.DotProduct(direction)) > 0.90:
                        ends.append(((origin - start).DotProduct(direction), face.Reference))
                    elif abs(normal.DotProduct(perp)) > 0.90:
                        sides.append(((origin - start).DotProduct(perp), face.Reference))
                except Exception:
                    continue
    except Exception:
        pass

    def unique_sorted(items):
        out, seen = [], set()
        for pos, ref in sorted(items, key=lambda x: x[0]):
            try:
                key = ref.ConvertToStableRepresentation(wall.Document)
            except Exception:
                key = str(ref)
            if key not in seen:
                seen.add(key)
                out.append((pos, ref))
        return out
    return unique_sorted(ends), unique_sorted(sides)


def reference_key(doc, refs):
    """Stable, order-independent key for duplicate dimension prevention."""
    keys = []
    for ref in refs:
        if ref is None:
            continue
        try:
            key = ref.ConvertToStableRepresentation(doc)
        except Exception:
            key = str(ref)
        if key not in keys:
            keys.append(key)
    if len(keys) < 2:
        return None
    return tuple(sorted(keys))


def existing_dimension_keys(doc, view, dim_type):
    """Collect signatures of existing linear dimensions in this view; failures are non-fatal."""
    signatures = set()
    scan_errors = []
    try:
        elements = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType().ToElements()
    except Exception as ex:
        return signatures, ['Could not scan existing dimensions: {}'.format(ex)]
    wanted_type_id = id_value(dim_type.Id)
    for element in elements:
        try:
            if element.GetType().Name != 'Dimension':
                continue
            try:
                if id_value(element.GetTypeId()) != wanted_type_id:
                    continue
            except Exception:
                continue
            refs = element.References
            key = reference_key(doc, [r for r in refs])
            if key:
                signatures.add(key)
        except Exception:
            continue
    return signatures, scan_errors


def make_dimension(doc, view, refs, line, dim_type):
    ra = ReferenceArray()
    seen = set()
    for ref in refs:
        if ref is None:
            continue
        try:
            key = ref.ConvertToStableRepresentation(doc)
        except Exception:
            key = str(ref)
        if key in seen:
            continue
        seen.add(key)
        ra.Append(ref)
    if ra.Size < 2:
        return None
    try:
        result = doc.Create.NewDimension(view, line, ra, dim_type)
        make_dimension.last_error = None
        return result
    except Exception as ex:
        make_dimension.last_error = str(ex)
        return None


def visible_wall_elements(doc, view):
    try:
        return list(FilteredElementCollector(doc, view.Id).OfClass(Wall).WhereElementIsNotElementType().ToElements())
    except Exception:
        return []


def is_straight_wall(wall):
    try:
        return wall.Location.Curve.GetType().Name == 'Line'
    except Exception:
        return False
