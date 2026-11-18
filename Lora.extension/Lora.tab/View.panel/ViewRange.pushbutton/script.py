# -*- coding: utf-8 -*-
from __future__ import print_function



# Lora shared extension library (single source of truth)
import os as _lora_os, sys as _lora_sys
_lora_lib = _lora_os.path.abspath(_lora_os.path.join(_lora_os.path.dirname(__file__), "..", "..", "..", "lib"))
if _lora_lib not in _lora_sys.path:
    _lora_sys.path.insert(0, _lora_lib)
from LoraDialogTheme import lora_select_from_list, lora_alert
from LoraCore import element_id_value, ids_equal, safe_text

"""Lora View Range Editor for Revit 2023 / pyRevit 4.8.12.

- Edits the native PlanViewRange object.
- Displays length values using the active Revit project's length units,
  matching Revit's View Range dialog formatting.
- Shows the four View Range planes in the active 3D view using a
  DirectContext3D server implemented from pyRevit's DC3D pattern.

The DC3D server is deliberately kept in this one script; no custom external
service module is required from the user's pyRevit installation.
"""

import os
import traceback
from collections import namedtuple

from System import Guid
from System.Collections.Generic import List

from pyrevit import forms, revit, DB, UI, script

# Revit 2023 exposes these namespaces from RevitAPI.dll.  They are used here
# instead of pyrevit.api because pyRevit 4.8.12 does not expose these aliases.
from Autodesk.Revit.DB import ExternalService as es
from Autodesk.Revit.DB import DirectContext3D as dc3d


WINDOW_KEY = 'LORA_VIEWRANGE_EDITOR_WINDOW'

PLANES = [
    (DB.PlanViewPlane.TopClipPlane, 'Top Plane', (0, 255, 0)),
    (DB.PlanViewPlane.CutPlane, 'Cut Plane', (255, 0, 0)),
    (DB.PlanViewPlane.BottomClipPlane, 'Bottom Plane', (0, 0, 255)),
    (DB.PlanViewPlane.ViewDepthPlane, 'View Depth', (255, 127, 0)),
]

TOLERANCE = 1e-9


# -----------------------------------------------------------------------------
# DirectContext3D support - based on pyRevit's dc3dserver pattern
# -----------------------------------------------------------------------------
Edge = namedtuple('Edge', ['a', 'b', 'color'])
Triangle = namedtuple('Triangle', ['a', 'b', 'c', 'normal', 'color'])


class Mesh(object):
    def __init__(self, edges, triangles):
        self.edges = edges or []
        self.triangles = triangles or []
        self.vertices = []
        for edge in self.edges:
            self.vertices.extend([edge.a, edge.b])
        for triangle in self.triangles:
            self.vertices.extend([triangle.a, triangle.b, triangle.c])

    @staticmethod
    def calculate_triangle_normal(a, b, c):
        return (c - a).CrossProduct(b - a)


class Buffer(object):
    def __init__(self, display_style, vertex_buffer, vertex_count,
                 index_buffer, index_count, vertex_format, effect_instance,
                 primitive_type, start, primitive_count):
        self.display_style = display_style
        self.vertex_buffer = vertex_buffer
        self.vertex_count = vertex_count
        self.index_buffer = index_buffer
        self.index_count = index_count
        self.vertex_format = vertex_format
        self.effect_instance = effect_instance
        self.primitive_type = primitive_type
        self.start = start
        self.primitive_count = primitive_count

    def is_valid(self, display_style):
        try:
            return all([
                self.display_style == display_style,
                self.vertex_buffer.IsValid,
                self.index_buffer.IsValid,
                self.vertex_format.IsValid,
                self.effect_instance.IsValid,
            ])
        except Exception:
            return False

    @classmethod
    def build_edge_buffer(cls, display_style, meshes):
        try:
            edges = []
            for mesh in meshes or []:
                edges.extend(mesh.edges)
            if not edges:
                return None

            format_bits = dc3d.VertexFormatBits.PositionColored
            vertex_format = dc3d.VertexFormat(format_bits)
            effect_instance = dc3d.EffectInstance(format_bits)

            vertex_count = dc3d.VertexPositionColored.GetSizeInFloats() * len(edges) * 2
            vertex_buffer = dc3d.VertexBuffer(vertex_count)
            index_count = dc3d.IndexLine.GetSizeInShortInts() * len(edges)
            index_buffer = dc3d.IndexBuffer(index_count)

            vertex_buffer.Map(vertex_count)
            index_buffer.Map(index_count)
            vertex_stream = vertex_buffer.GetVertexStreamPositionColored()
            index_stream = index_buffer.GetIndexStreamLine()

            for idx, edge in enumerate(edges):
                first = idx * 2
                vertex_stream.AddVertex(dc3d.VertexPositionColored(edge.a, edge.color))
                vertex_stream.AddVertex(dc3d.VertexPositionColored(edge.b, edge.color))
                index_stream.AddLine(dc3d.IndexLine(first, first + 1))

            vertex_buffer.Unmap()
            index_buffer.Unmap()

            return cls(display_style, vertex_buffer, vertex_count,
                       index_buffer, index_count, vertex_format,
                       effect_instance, dc3d.PrimitiveType.LineList, 0,
                       len(edges))
        except Exception:
            return None

    @classmethod
    def build_triangle_buffer(cls, display_style, meshes, transparent=False):
        try:
            triangles = []
            for mesh in meshes or []:
                for tri in mesh.triangles:
                    try:
                        is_transparent = tri.color.GetTransparency() > 0
                    except Exception:
                        is_transparent = False
                    if transparent == is_transparent:
                        triangles.append(tri)

            if not triangles:
                return None

            shaded = display_style in (
                DB.DisplayStyle.Shading,
                DB.DisplayStyle.ShadingWithEdges,
            )
            format_bits = dc3d.VertexFormatBits.PositionNormalColored
            vertex_format = dc3d.VertexFormat(format_bits)
            if shaded:
                effect_instance = dc3d.EffectInstance(
                    dc3d.VertexFormatBits.PositionNormalColored)
            else:
                effect_instance = dc3d.EffectInstance(
                    dc3d.VertexFormatBits.PositionColored)

            vertex_count = (
                dc3d.VertexPositionNormalColored.GetSizeInFloats()
                * len(triangles) * 3
            )
            vertex_buffer = dc3d.VertexBuffer(vertex_count)
            index_count = dc3d.IndexTriangle.GetSizeInShortInts() * len(triangles)
            index_buffer = dc3d.IndexBuffer(index_count)

            vertex_buffer.Map(vertex_count)
            index_buffer.Map(index_count)
            vertex_stream = vertex_buffer.GetVertexStreamPositionNormalColored()
            index_stream = index_buffer.GetIndexStreamTriangle()

            for idx, tri in enumerate(triangles):
                color = tri.color
                if display_style == DB.DisplayStyle.HLR:
                    color = DB.ColorWithTransparency(
                        255, 255, 255, tri.color.GetTransparency())
                first = idx * 3
                vertex_stream.AddVertex(
                    dc3d.VertexPositionNormalColored(tri.a, tri.normal, color))
                vertex_stream.AddVertex(
                    dc3d.VertexPositionNormalColored(tri.b, tri.normal, color))
                vertex_stream.AddVertex(
                    dc3d.VertexPositionNormalColored(tri.c, tri.normal, color))
                index_stream.AddTriangle(
                    dc3d.IndexTriangle(first, first + 1, first + 2))

            vertex_buffer.Unmap()
            index_buffer.Unmap()

            return cls(display_style, vertex_buffer, vertex_count,
                       index_buffer, index_count, vertex_format,
                       effect_instance, dc3d.PrimitiveType.TriangleList, 0,
                       len(triangles))
        except Exception:
            return None

    def flush(self):
        dc3d.DrawContext.FlushBuffer(
            self.vertex_buffer,
            self.vertex_count,
            self.index_buffer,
            self.index_count,
            self.vertex_format,
            self.effect_instance,
            self.primitive_type,
            self.start,
            self.primitive_count,
        )


class ViewRangeServer(dc3d.IDirectContext3DServer):
    def __init__(self, uidoc):
        self.guid = Guid.NewGuid()
        self.uidoc = uidoc
        self.name = 'Lora View Range'
        self.description = 'Lora View Range coloured planes'
        self.vendor_id = 'Lora'
        self._meshes = []
        self._line_buffer = None
        self._opaque_buffer = None
        self._transparent_buffer = None

    def CanExecute(self, view):
        try:
            return view.ViewType == DB.ViewType.ThreeD and (
                self.uidoc is None or self.uidoc.Document.Equals(view.Document))
        except Exception:
            return False

    def GetApplicationId(self):
        return ''

    def GetDescription(self):
        return self.description

    def GetName(self):
        return self.name

    def GetServerId(self):
        return self.guid

    def GetServiceId(self):
        return es.ExternalServices.BuiltInExternalServices.DirectContext3DService

    def GetSourceId(self):
        return ''

    def GetVendorId(self):
        return self.vendor_id

    def UsesHandles(self):
        return False

    def UseInTransparentPass(self, view):
        return True

    def GetBoundingBox(self, view):
        try:
            if not self._meshes:
                return None
            vertices = []
            for mesh in self._meshes:
                vertices.extend(mesh.vertices)
            if len(vertices) < 2:
                return None
            outline = DB.Outline(vertices[0], vertices[1])
            for vertex in vertices[2:]:
                outline.AddPoint(vertex)
            return outline
        except Exception:
            return None

    def RenderScene(self, view, display_style):
        try:
            if not self._meshes:
                return

            buffers_missing = (
                self._line_buffer is None and
                self._opaque_buffer is None and
                self._transparent_buffer is None)
            buffers_invalid = any([
                self._line_buffer is not None and not self._line_buffer.is_valid(display_style),
                self._opaque_buffer is not None and not self._opaque_buffer.is_valid(display_style),
                self._transparent_buffer is not None and not self._transparent_buffer.is_valid(display_style),
            ])

            if buffers_missing or buffers_invalid:
                self._line_buffer = Buffer.build_edge_buffer(display_style, self._meshes)
                self._opaque_buffer = Buffer.build_triangle_buffer(
                    display_style, self._meshes, transparent=False)
                self._transparent_buffer = Buffer.build_triangle_buffer(
                    display_style, self._meshes, transparent=True)

            if dc3d.DrawContext.IsTransparentPass():
                if self._transparent_buffer is not None and display_style != DB.DisplayStyle.Wireframe:
                    self._transparent_buffer.flush()
            else:
                if self._opaque_buffer is not None and display_style != DB.DisplayStyle.Wireframe:
                    self._opaque_buffer.flush()
                if self._line_buffer is not None and display_style not in (
                        DB.DisplayStyle.Rendering, DB.DisplayStyle.Shading):
                    self._line_buffer.flush()
        except Exception:
            # Never allow a DC3D callback exception to terminate Revit.
            pass

    @property
    def meshes(self):
        return self._meshes

    @meshes.setter
    def meshes(self, value):
        self._meshes = value or []
        self._line_buffer = None
        self._opaque_buffer = None
        self._transparent_buffer = None

    def add_server(self):
        service = es.ExternalServiceRegistry.GetService(self.GetServiceId())
        if service.IsRegisteredServerId(self.GetServerId()):
            self.remove_server()
        service.AddServer(self)
        active = service.GetActiveServerIds()
        if not active.Contains(self.GetServerId()):
            active.Add(self.GetServerId())
        service.SetActiveServers(active)

    def remove_server(self):
        try:
            service = es.ExternalServiceRegistry.GetService(self.GetServiceId())
            if service.IsRegisteredServerId(self.GetServerId()):
                service.RemoveServer(self.GetServerId())
        except Exception:
            pass
        self._meshes = []
        self._line_buffer = None
        self._opaque_buffer = None
        self._transparent_buffer = None


# -----------------------------------------------------------------------------
# Native View Range data
# -----------------------------------------------------------------------------
class LevelItem(object):
    def __init__(self, level_id, name, elevation):
        self.Id = level_id
        self.Name = name
        self.Elevation = elevation

    def __str__(self):
        return self.Name


class PlaneState(object):
    def __init__(self, plane, name, level_id, offset, level_name,
                 elevation, unlimited):
        self.Plane = plane
        self.Name = name
        self.LevelId = level_id
        self.Offset = offset
        self.LevelName = level_name
        self.Elevation = elevation
        self.Unlimited = unlimited




def get_levels(doc):
    levels = []
    for level in DB.FilteredElementCollector(doc).OfClass(DB.Level):
        try:
            levels.append(LevelItem(level.Id, level.Name, level.ProjectElevation))
        except Exception:
            try:
                levels.append(LevelItem(level.Id, level.Name, level.Elevation))
            except Exception:
                pass
    levels.sort(key=lambda x: (x.Elevation, x.Name.lower()))
    return levels


def get_plan_views(doc):
    plans = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        try:
            if view.IsTemplate:
                continue
            if view.ViewType in (
                    DB.ViewType.FloorPlan,
                    DB.ViewType.CeilingPlan,
                    DB.ViewType.AreaPlan,
                    DB.ViewType.EngineeringPlan):
                plans.append(view)
        except Exception:
            pass
    plans.sort(key=lambda x: x.Name.lower())
    return plans


def get_selected_plan(doc):
    try:
        selected_ids = list(revit.uidoc.Selection.GetElementIds())
    except Exception:
        selected_ids = []
    for element_id in selected_ids:
        try:
            element = doc.GetElement(element_id)
            if isinstance(element, DB.ViewPlan) and not element.IsTemplate:
                return element
        except Exception:
            pass
    try:
        active = revit.uidoc.ActiveView
        if isinstance(active, DB.ViewPlan) and not active.IsTemplate:
            return active
    except Exception:
        pass
    return None


def choose_plan(doc):
    plans = get_plan_views(doc)
    if not plans:
        lora_alert('No plan views were found in this project.',
                    title='Lora - View Range Editor')
        return None

    class PlanItem(object):
        def __init__(self, view):
            self.Id = view.Id
            self.Name = view.Name
            self.TypeName = str(view.ViewType)

        def __str__(self):
            return '{} | {}'.format(self.Name, self.TypeName)

    selected = lora_select_from_list(
        [PlanItem(v) for v in plans],
        title='Select Plan View',
        button_name='Open View Range Editor',
        multiselect=False,
        width=520,
        height=520,
    )
    if not selected:
        return None
    return doc.GetElement(selected.Id)


def get_view_range(plan):
    try:
        return plan.GetViewRange()
    except Exception as exc:
        lora_alert('Unable to read the View Range for "{}".\n\n{}'.format(
            plan.Name, exc), title='Lora - View Range Editor')
        return None


def is_unlimited(level_id):
    return (ids_equal(level_id, DB.ElementId.InvalidElementId) or
            ids_equal(level_id, DB.PlanViewRange.Unlimited))


def get_plane_state(doc, view_range, plane, name):
    try:
        level_id = view_range.GetLevelId(plane)
        offset = view_range.GetOffset(plane)
    except Exception:
        return PlaneState(plane, name, DB.ElementId.InvalidElementId,
                          0.0, 'N/A', None, False)

    if is_unlimited(level_id):
        return PlaneState(plane, name, DB.PlanViewRange.Unlimited,
                          offset, 'Unlimited', None, True)

    level = doc.GetElement(level_id)
    if level is None:
        return PlaneState(plane, name, level_id, offset,
                          'Unknown', None, False)
    try:
        base = level.ProjectElevation
    except Exception:
        base = level.Elevation
    return PlaneState(plane, name, level_id, offset,
                      level.Name, base + offset, False)


def capture_states(doc, plan):
    vr = get_view_range(plan)
    if vr is None:
        return None
    return [get_plane_state(doc, vr, plane, name)
            for plane, name, _ in PLANES]


# -----------------------------------------------------------------------------
# Project-unit formatting and parsing
# -----------------------------------------------------------------------------
def get_length_units(doc):
    return doc.GetUnits()


def format_length(doc, internal_value):
    if internal_value is None:
        return 'N/A'
    try:
        # This is the same Revit unit formatting service used by the native UI.
        return DB.UnitFormatUtils.Format(
            get_length_units(doc),
            DB.SpecTypeId.Length,
            internal_value,
            True)
    except Exception:
        try:
            unit_id = get_length_units(doc).GetFormatOptions(
                DB.SpecTypeId.Length).GetUnitTypeId()
            display_value = DB.UnitUtils.ConvertFromInternalUnits(
                internal_value, unit_id)
            return str(round(display_value, 6)).rstrip('0').rstrip('.')
        except Exception:
            return str(internal_value)


def parse_length(doc, text):
    if text is None or not str(text).strip():
        raise ValueError('Offset is empty.')
    text = str(text).strip()

    # Prefer Revit's own parser so fractional imperial input and project
    # units are accepted in the same manner as Revit's native dialogs.
    try:
        result = DB.UnitFormatUtils.TryParse(
            get_length_units(doc), DB.SpecTypeId.Length, text, 0.0)
        if isinstance(result, tuple):
            ok = result[0]
            value = result[1] if len(result) > 1 else None
            if ok:
                return float(value)
        elif result:
            # IronPython can expose a by-ref value differently between API builds.
            # Fall through to the deterministic display-unit conversion below.
            pass
    except Exception:
        pass

    try:
        unit_id = get_length_units(doc).GetFormatOptions(
            DB.SpecTypeId.Length).GetUnitTypeId()
        display_value = float(text.replace(',', ''))
        return DB.UnitUtils.ConvertToInternalUnits(display_value, unit_id)
    except Exception:
        raise ValueError('Invalid length value: {}'.format(text))


# -----------------------------------------------------------------------------
# 3D plane geometry
# -----------------------------------------------------------------------------
def corners_from_bbox(bbox):
    if bbox is None:
        return []
    try:
        transform = bbox.Transform
    except Exception:
        transform = DB.Transform.Identity
    d = bbox.Max - bbox.Min
    # Match pyRevit's View Range corner ordering so the plane normals
    # face upward.  Correct winding is important for shaded DirectContext3D
    # surfaces; the previous ordering produced downward normals and could
    # leave the filled planes invisible while their edges remained visible.
    pts = [
        bbox.Min,
        bbox.Min + DB.XYZ.BasisY * d.Y,
        bbox.Min + DB.XYZ.BasisX * d.X + DB.XYZ.BasisY * d.Y,
        bbox.Min + DB.XYZ.BasisX * d.X,
    ]
    return [transform.OfPoint(p) for p in pts]


def make_plane_mesh(corners, z, rgb):
    if len(corners) != 4:
        return None
    pts = [DB.XYZ(p.X, p.Y, z) for p in corners]
    # Revit/pyRevit uses a transparent DirectContext3D pass.  100 gives
    # a clearly visible translucent plane while still showing the model.
    color = DB.ColorWithTransparency(rgb[0], rgb[1], rgb[2], 100)
    edge_color = DB.ColorWithTransparency(rgb[0], rgb[1], rgb[2], 0)

    edges = [
        Edge(pts[0], pts[1], edge_color),
        Edge(pts[1], pts[2], edge_color),
        Edge(pts[2], pts[3], edge_color),
        Edge(pts[3], pts[0], edge_color),
    ]
    normal1 = Mesh.calculate_triangle_normal(pts[0], pts[1], pts[2])
    normal2 = Mesh.calculate_triangle_normal(pts[2], pts[3], pts[0])
    triangles = [
        Triangle(pts[0], pts[1], pts[2], normal1, color),
        Triangle(pts[2], pts[3], pts[0], normal2, color),
    ]
    return Mesh(edges, triangles)


def get_plane_meshes(doc, plan, active_3d):
    if not isinstance(plan, DB.ViewPlan):
        return []

    bbox = None
    try:
        if active_3d.IsSectionBoxActive:
            bbox = active_3d.GetSectionBox()
    except Exception:
        bbox = None

    if bbox is None:
        try:
            bbox = plan.CropBox
        except Exception:
            bbox = None

    corners = corners_from_bbox(bbox)
    if len(corners) != 4:
        return []

    vr = plan.GetViewRange()
    meshes = []
    for plane, _, rgb in PLANES:
        level_id = vr.GetLevelId(plane)
        if is_unlimited(level_id):
            # Unlimited Top has no finite graphical elevation.
            continue
        level = doc.GetElement(level_id)
        if level is None:
            continue
        try:
            base = level.ProjectElevation
        except Exception:
            base = level.Elevation
        z = base + vr.GetOffset(plane)
        mesh = make_plane_mesh(corners, z, rgb)
        if mesh is not None:
            meshes.append(mesh)
    return meshes


# -----------------------------------------------------------------------------
# Editor window
# -----------------------------------------------------------------------------
class RowProxy(object):
    def __init__(self, plane_text, elevation_text, offset_text, level_combo):
        self.PlaneText = plane_text
        self.ElevationText = elevation_text
        self.OffsetText = offset_text
        self.LevelCombo = level_combo


class ViewRangeWindow(forms.WPFWindow):
    def __init__(self, xaml_path, doc, plan, active_3d, server):
        forms.WPFWindow.__init__(self, xaml_path)
        self.doc = doc
        self.plan = plan
        self.active_3d = active_3d
        self.server = server
        self.levels = get_levels(doc)
        self.original = capture_states(doc, plan)
        self._loading = False

        if self.original is None:
            self.Close()
            return

        self.TitleText.Text = 'Showing View Range of'
        self.ViewNameText.Text = '[ {} ]'.format(plan.Name)

        self.TopRow = RowProxy(self.TopRowPlaneText, self.TopRowElevationText,
                               self.TopRowOffsetText, self.TopRowLevelCombo)
        self.CutRow = RowProxy(self.CutRowPlaneText, self.CutRowElevationText,
                               self.CutRowOffsetText, self.CutRowLevelCombo)
        self.BottomRow = RowProxy(self.BottomRowPlaneText, self.BottomRowElevationText,
                                  self.BottomRowOffsetText, self.BottomRowLevelCombo)
        self.DepthRow = RowProxy(self.DepthRowPlaneText, self.DepthRowElevationText,
                                 self.DepthRowOffsetText, self.DepthRowLevelCombo)

        self._setup_rows()
        self._load_original_values()
        self.Closed += self.on_closed

    def _setup_rows(self):
        self._loading = True
        try:
            rows = [self.TopRow, self.CutRow, self.BottomRow, self.DepthRow]
            for row in rows:
                row.LevelCombo.ItemsSource = self.levels
                row.LevelCombo.DisplayMemberPath = 'Name'
        finally:
            self._loading = False

    def _find_level_item(self, level_id):
        if is_unlimited(level_id):
            return None
        for item in self.levels:
            if ids_equal(item.Id, level_id):
                return item
        return None

    def _set_row(self, row, state, allow_unlimited=False):
        self._loading = True
        try:
            row.PlaneText.Text = state.Name
            row.ElevationText.Text = format_length(self.doc, state.Elevation)
            if allow_unlimited:
                special = LevelItem(DB.PlanViewRange.Unlimited, 'Unlimited', 0.0)
                items = [special] + list(self.levels)
                row.LevelCombo.ItemsSource = items
                row.LevelCombo.DisplayMemberPath = 'Name'
                if state.Unlimited:
                    row.LevelCombo.SelectedIndex = 0
                    row.OffsetText.Text = 'N/A'
                    row.OffsetText.IsEnabled = False
                else:
                    row.LevelCombo.SelectedItem = self._find_level_item(state.LevelId)
                    row.OffsetText.Text = format_length(self.doc, state.Offset)
                    row.OffsetText.IsEnabled = True
            else:
                row.LevelCombo.SelectedItem = self._find_level_item(state.LevelId)
                row.OffsetText.Text = format_length(self.doc, state.Offset)
                row.OffsetText.IsEnabled = True
        finally:
            self._loading = False

    def _load_original_values(self):
        self._set_row(self.TopRow, self.original[0], True)
        self._set_row(self.CutRow, self.original[1])
        self._set_row(self.BottomRow, self.original[2])
        self._set_row(self.DepthRow, self.original[3])
        self.StatusText.Text = 'Values are from the selected plan view.'

    def _refresh_elevation(self, row):
        if self._loading:
            return
        try:
            item = row.LevelCombo.SelectedItem
            if item is None:
                row.ElevationText.Text = 'N/A'
                return
            if is_unlimited(item.Id):
                row.ElevationText.Text = 'N/A'
                row.OffsetText.Text = 'N/A'
                row.OffsetText.IsEnabled = False
                return
            row.OffsetText.IsEnabled = True
            offset = parse_length(self.doc, row.OffsetText.Text)
            row.ElevationText.Text = format_length(
                self.doc, item.Elevation + offset)
        except Exception:
            row.ElevationText.Text = 'N/A'

    def on_top_level_changed(self, sender, args):
        self._refresh_elevation(self.TopRow)

    def on_cut_level_changed(self, sender, args):
        self._refresh_elevation(self.CutRow)

    def on_bottom_level_changed(self, sender, args):
        self._refresh_elevation(self.BottomRow)

    def on_depth_level_changed(self, sender, args):
        self._refresh_elevation(self.DepthRow)

    def on_cut_offset_changed(self, sender, args):
        self._refresh_elevation(self.CutRow)

    def on_bottom_offset_changed(self, sender, args):
        self._refresh_elevation(self.BottomRow)

    def on_depth_offset_changed(self, sender, args):
        self._refresh_elevation(self.DepthRow)

    def _row_values(self, row, plane_name, allow_unlimited=False):
        item = row.LevelCombo.SelectedItem
        if item is None:
            raise ValueError('{}: please select an associated level.'.format(plane_name))
        if allow_unlimited and is_unlimited(item.Id):
            return item.Id, None
        return item.Id, parse_length(self.doc, row.OffsetText.Text)

    def _collect_changes(self):
        return [
            (DB.PlanViewPlane.TopClipPlane,
             self._row_values(self.TopRow, 'Top Plane', True)),
            (DB.PlanViewPlane.CutPlane,
             self._row_values(self.CutRow, 'Cut Plane')),
            (DB.PlanViewPlane.BottomClipPlane,
             self._row_values(self.BottomRow, 'Bottom Plane')),
            (DB.PlanViewPlane.ViewDepthPlane,
             self._row_values(self.DepthRow, 'View Depth')),
        ]

    def _validate_order(self, vr):
        elevations = {}
        for plane, name, _ in PLANES:
            level_id = vr.GetLevelId(plane)
            if is_unlimited(level_id):
                continue
            level = self.doc.GetElement(level_id)
            if level is None:
                raise ValueError('{}: associated level was not found.'.format(name))
            try:
                base = level.ProjectElevation
            except Exception:
                base = level.Elevation
            elevations[plane] = base + vr.GetOffset(plane)

        checks = [
            (DB.PlanViewPlane.TopClipPlane, DB.PlanViewPlane.CutPlane,
             'Top Plane must be at or above Cut Plane.'),
            (DB.PlanViewPlane.CutPlane, DB.PlanViewPlane.BottomClipPlane,
             'Cut Plane must be at or above Bottom Plane.'),
            (DB.PlanViewPlane.BottomClipPlane, DB.PlanViewPlane.ViewDepthPlane,
             'Bottom Plane must be at or above View Depth.'),
        ]
        for higher, lower, message in checks:
            if higher in elevations and lower in elevations:
                if elevations[higher] < elevations[lower] - TOLERANCE:
                    raise ValueError(message)

    def _refresh_3d(self):
        try:
            self.server.remove_server()
            self.server.meshes = get_plane_meshes(
                self.doc, self.plan, self.active_3d)
            self.server.add_server()
            revit.uidoc.RefreshActiveView()
        except Exception as exc:
            self.StatusText.Text = '3D preview could not be refreshed: {}'.format(exc)

    def apply_changes(self):
        try:
            changes = self._collect_changes()
        except Exception as exc:
            lora_alert(str(exc), title='Lora - Invalid View Range Value')
            return

        try:
            with revit.Transaction('Lora - Edit View Range'):
                vr = self.plan.GetViewRange()
                for plane, values in changes:
                    level_id, offset = values
                    vr.SetLevelId(plane, level_id)
                    if offset is not None:
                        vr.SetOffset(plane, offset)
                self._validate_order(vr)
                self.plan.SetViewRange(vr)
        except Exception as exc:
            lora_alert('Revit could not apply the View Range changes.\n\n{}'.format(exc),
                        title='Lora - View Range Editor')
            return

        self.original = capture_states(self.doc, self.plan)
        self._load_original_values()
        self._refresh_3d()
        self.StatusText.Text = 'Changes applied successfully.'

    def reset_original(self):
        self._load_original_values()
        self._refresh_3d()
        self.StatusText.Text = 'Original values restored in the editor. No Revit changes were made.'

    def on_apply(self, sender, args):
        self.apply_changes()

    def on_reset(self, sender, args):
        self.reset_original()

    def on_closed(self, sender, args):
        try:
            self.server.remove_server()
            revit.uidoc.RefreshActiveView()
        except Exception:
            pass
        script.set_envvar(WINDOW_KEY, None)


def main():
    existing = script.get_envvar(WINDOW_KEY)
    if existing:
        try:
            if existing.IsVisible:
                script.exit()
        except Exception:
            script.set_envvar(WINDOW_KEY, None)

    doc = revit.doc
    active = revit.uidoc.ActiveGraphicalView
    if not isinstance(active, DB.View3D):
        lora_alert(
            'Please open and activate a 3D View before running Lora > View > View Range.\n\n'
            'The editor uses that 3D view to display the coloured View Range planes.',
            title='Lora - View Range Editor')
        script.exit()

    plan = get_selected_plan(doc)
    if plan is None:
        plan = choose_plan(doc)
    if plan is None:
        script.exit()

    try:
        template_id = plan.ViewTemplateId
    except Exception:
        template_id = DB.ElementId.InvalidElementId

    if template_id != DB.ElementId.InvalidElementId:
        template = doc.GetElement(template_id)
        lora_alert(
            'The selected plan view uses a View Template:\n\n"{}"\n\n'
            'If View Range is controlled by the template, Revit may prevent changes.\n'
            'Edit the template when required.'.format(
                template.Name if template else 'Unknown'),
            title='Lora - View Range Editor')

    server = ViewRangeServer(revit.uidoc)
    try:
        server.meshes = get_plane_meshes(doc, plan, active)
        server.add_server()
    except Exception as exc:
        lora_alert(
            'The View Range editor can open, but the 3D preview could not be initialized.\n\n'
            '{}'.format(exc),
            title='Lora - 3D Preview')
        server.remove_server()

    xaml_path = os.path.join(os.path.dirname(__file__), 'ViewRangeEditor.xaml')
    window = ViewRangeWindow(xaml_path, doc, plan, active, server)
    script.set_envvar(WINDOW_KEY, window)
    window.show_dialog()
    script.set_envvar(WINDOW_KEY, None)


if __name__ == '__main__':
    main()
