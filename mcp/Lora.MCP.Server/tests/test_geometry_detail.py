import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROVIDER_LIB = (Path(__file__).resolve().parents[1] / "revit_identity" /
                "LoraIdentity.extension" / "lib")
if str(PROVIDER_LIB) not in sys.path:
    sys.path.insert(0, str(PROVIDER_LIB))

from lora_identity_provider import geometry_detail


class RevitId:
    def __init__(self, value): self.Value = value


class XYZ:
    def __init__(self, x, y, z): self.X, self.Y, self.Z = x, y, z


class Shift:
    Origin = XYZ(10, 0, 0)
    BasisX, BasisY, BasisZ = XYZ(1, 0, 0), XYZ(0, 1, 0), XYZ(0, 0, 1)
    def OfPoint(self, point): return XYZ(point.X + 10, point.Y, point.Z)


class Rotate:
    Origin = XYZ(0, 0, 0)
    BasisX, BasisY, BasisZ = XYZ(0, 1, 0), XYZ(-1, 0, 0), XYZ(0, 0, 1)
    def OfPoint(self, point): return XYZ(-point.Y, point.X, point.Z)


class Curve:
    IsBound = True
    Length = 2.0
    def GetEndPoint(self, index):
        if getattr(self, "broken", False): raise AttributeError("endpoint getter fault")
        return XYZ(index + 1, 2, 3)


class Edge:
    def AsCurve(self): return Curve()


class Face:
    Area = 5.0


class Solid:
    Volume, SurfaceArea = 4.0, 8.0
    Faces = [Face()]
    Edges = [Edge()]


class Triangle:
    def get_Vertex(self, index): return XYZ(index, index + 1, 0)


class Mesh:
    NumTriangles = 2
    def get_Triangle(self, index): return Triangle()


class GeometryInstance:
    def __init__(self, geometry, transform=None):
        self.geometry = geometry
        self.Transform = transform or Shift()
    def GetSymbolGeometry(self): return self.geometry


class LocationPoint:
    Point = XYZ(3, 4, 5)


class LocationCurve:
    Curve = Curve()


class Options:
    pass


class ElementId:
    def __init__(self, value): self.Value = value


class Element:
    Id, UniqueId = RevitId(21), "uid-21"
    Category = type("Cat", (), {"Name": "Generic Models"})()
    Location, LevelId = LocationPoint(), RevitId(-1)
    class RuntimeType: Name = "FamilyInstance"
    def __init__(self, geometry): self.geometry = geometry
    def GetType(self): return self.RuntimeType()
    def get_Geometry(self, options): return self.geometry
    def get_BoundingBox(self, view): return None


class Doc:
    def __init__(self, element): self.element = element
    def GetElement(self, element_id): return self.element if element_id.Value == 21 else None


class Runtime:
    def identity_response(self, uiapp, doc):
        return {"status": "success", "identity": {"process_id": 1, "document_session_id": "d"}}


def patched_modules():
    db = types.ModuleType("Autodesk.Revit.DB")
    db.ElementId, db.GeometryInstance, db.Mesh, db.Options, db.Solid = ElementId, GeometryInstance, Mesh, Options, Solid
    db.Curve = Curve
    db.ViewDetailLevel = types.SimpleNamespace(Fine="Fine")
    db.LocationCurve, db.LocationPoint = LocationCurve, LocationPoint
    revit = types.ModuleType("Autodesk.Revit")
    revit.DB = db
    autodesk = types.ModuleType("Autodesk")
    autodesk.Revit = revit
    return {"Autodesk": autodesk, "Autodesk.Revit": revit, "Autodesk.Revit.DB": db}


class GeometryDetailTests(unittest.TestCase):
    def test_solid_mesh_face_edge_curve_instance_and_transform_evidence(self):
        geometry = [Solid(), Mesh(), GeometryInstance([Curve()])]
        with patch.dict(sys.modules, patched_modules()):
            result = geometry_detail.geometry_detail(Runtime(), object(), Doc(Element(geometry)), ["21"])
        self.assertEqual(result["status"], "success")
        item = result["items"][0]
        self.assertEqual(item["geometry_counts"], {"solids": 1, "meshes": 1, "faces": 1,
            "edges": 1, "curves": 1, "geometry_instances": 1, "other_objects": 0})
        self.assertEqual(item["geometry_status"], "inspected")
        self.assertEqual(result["geometry_entities"][0]["kind"], "solid")
        mesh = next(entity for entity in result["geometry_entities"] if entity["kind"] == "mesh")
        self.assertEqual(mesh["triangle_count"], 2)
        curve = next(entity for entity in result["geometry_entities"] if entity["kind"] == "curve")
        self.assertEqual(curve["start"]["raw_feet"]["x"], 11)
        self.assertEqual(curve["instance_transform_chain"][0]["direction"],
                         "symbol-local to parent instance coordinates")
        self.assertEqual(result["clearance_status"], "unverified")

    def test_entity_page_is_bounded_and_independent_of_requested_elements(self):
        with patch.dict(sys.modules, patched_modules()):
            result = geometry_detail.geometry_detail(Runtime(), object(), Doc(Element([Solid(), Mesh()])),
                ["21"], entity_offset=1, entity_limit=2)
        self.assertEqual(result["total_entities_observed"], 4)
        self.assertEqual(len(result["geometry_entities"]), 2)
        self.assertTrue(result["entity_has_more"])

    def test_curve_endpoint_error_fails_geometry_and_keeps_location_result(self):
        curve = Curve()
        curve.broken = True
        with patch.dict(sys.modules, patched_modules()):
            result = geometry_detail.geometry_detail(Runtime(), object(), Doc(Element([curve])), ["21"])
        item = result["items"][0]
        self.assertEqual(item["geometry_status"], "failed")
        self.assertEqual(item["location_status"], "inspected")
        self.assertTrue(any(warning["field"] == "geometry.curve.endpoints" for warning in item["warnings"]))

    def test_nested_instance_transform_chain_is_applied_inside_out(self):
        nested = GeometryInstance([Curve()], Rotate())
        outer = GeometryInstance([nested], Shift())
        with patch.dict(sys.modules, patched_modules()):
            result = geometry_detail.geometry_detail(Runtime(), object(), Doc(Element([outer])), ["21"])
        curve = next(entity for entity in result["geometry_entities"] if entity["kind"] == "curve")
        self.assertEqual(curve["start"]["raw_feet"], {"x": 8, "y": 1, "z": 3})
        self.assertEqual(len(curve["instance_transform_chain"]), 2)

    def test_curve_length_failure_marks_curve_descriptor_failed(self):
        class LengthThrows(Curve):
            @property
            def Length(self): raise RuntimeError("curve length failed")
        with patch.dict(sys.modules, patched_modules()):
            result = geometry_detail.geometry_detail(Runtime(), object(), Doc(Element([LengthThrows()])), ["21"])
        self.assertEqual(result["items"][0]["geometry_status"], "failed")
        self.assertEqual(result["geometry_entities"][0]["inspection_status"], "failed")
        self.assertTrue(any(warning["field"] == "geometry.curve.length"
                            for warning in result["items"][0]["warnings"]))

    def test_bounding_box_failure_does_not_fail_independent_geometry_inspection(self):
        class BoundingBoxThrows(Element):
            def get_BoundingBox(self, view): raise RuntimeError("bbox getter failed")
        with patch.dict(sys.modules, patched_modules()):
            result = geometry_detail.geometry_detail(Runtime(), object(),
                Doc(BoundingBoxThrows([Solid()])), ["21"])
        item = result["items"][0]
        self.assertEqual(item["bounding_box_status"], "failed")
        self.assertEqual(item["geometry_status"], "inspected")
        self.assertTrue(item["geometry_complete"])
        self.assertTrue(any(warning["field"] == "bounding_box" and
                            warning["message"] == "bbox getter failed"
                            for warning in item["warnings"]))

    def test_missing_element_and_invalid_inputs_fail_closed(self):
        with patch.dict(sys.modules, patched_modules()):
            missing = geometry_detail.geometry_detail(Runtime(), object(), Doc(None), ["22"])
        self.assertEqual(missing["failure_count"], 1)
        self.assertEqual(missing["failures"][0]["error"]["code"], "element_not_found")
        self.assertEqual(geometry_detail.geometry_detail(Runtime(), object(), Doc(None), ["x"])["error"],
                         "invalid_element_id")


if __name__ == "__main__":
    unittest.main()
