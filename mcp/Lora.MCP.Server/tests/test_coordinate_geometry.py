import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROVIDER_LIB = (Path(__file__).resolve().parents[1] / "revit_identity" /
                "LoraIdentity.extension" / "lib")
if str(PROVIDER_LIB) not in sys.path:
    sys.path.insert(0, str(PROVIDER_LIB))

from lora_identity_provider import coordinate_geometry


class FakeId:
    def __init__(self, value):
        self.Value = value


class FakeXYZ:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


FakeXYZ.Zero = FakeXYZ(0, 0, 0)


class FakeTransform:
    Origin = FakeXYZ(0, 0, 0)
    BasisX = FakeXYZ(1, 0, 0)
    BasisY = FakeXYZ(0, 1, 0)
    BasisZ = FakeXYZ(0, 0, 1)

    @property
    def Inverse(self):
        return self

    def OfPoint(self, point):
        return point


class AffineTransform:
    def __init__(self, angle=0.0, translation=(0.0, 0.0, 0.0), matrix=None):
        import math
        if matrix is None:
            self.a, self.b, self.c, self.d = math.cos(angle), math.sin(angle), -math.sin(angle), math.cos(angle)
            self.tx, self.ty, self.tz = translation
        else:
            self.a, self.b, self.c, self.d, self.tx, self.ty, self.tz = matrix
        self.BasisX = FakeXYZ(self.a, self.b, 0)
        self.BasisY = FakeXYZ(self.c, self.d, 0)
        self.BasisZ = FakeXYZ(0, 0, 1)
        self.Origin = FakeXYZ(self.tx, self.ty, self.tz)

    @property
    def Inverse(self):
        det = self.a * self.d - self.b * self.c
        a, b, c, d = self.d / det, -self.b / det, -self.c / det, self.a / det
        return AffineTransform(matrix=(a, b, c, d,
            -(a * self.tx + c * self.ty), -(b * self.tx + d * self.ty), -self.tz))

    def OfPoint(self, point):
        return FakeXYZ(self.a * point.X + self.c * point.Y + self.tx,
                       self.b * point.X + self.d * point.Y + self.ty,
                       point.Z + self.tz)


class FakeProjectPosition:
    def __init__(self, point):
        self.EastWest, self.NorthSouth, self.Elevation = point.X, point.Y, point.Z
        self.Angle = 0


class FakeProjectLocation:
    Id = FakeId(11)
    Name = "Internal"

    def GetProjectPosition(self, point):
        return FakeProjectPosition(point)

    def GetTransform(self):
        return FakeTransform()


def coordinate_db(transform, independent_transform):
    class Location(FakeProjectLocation):
        def GetTransform(self):
            return transform

        def GetProjectPosition(self, point):
            return FakeProjectPosition(independent_transform.OfPoint(point))

    class BasePoint(FakeBasePoint):
        def __init__(self, element_id):
            super().__init__(element_id)
            self.SharedPosition = independent_transform.OfPoint(self.Position)

        @staticmethod
        def GetProjectBasePoint(doc):
            return BasePoint(12)

        @staticmethod
        def GetSurveyPoint(doc):
            return BasePoint(13)

    class InternalOrigin(FakeInternalOrigin):
        SharedPosition = independent_transform.OfPoint(FakeXYZ.Zero)

    class Doc(FakeDoc):
        ActiveProjectLocation = Location()

    db = fake_db()
    db["Autodesk.Revit.DB"].BasePoint = BasePoint
    db["Autodesk.Revit.DB"].InternalOrigin = InternalOrigin
    return db, Doc()


class FakeBasePoint:
    def __init__(self, element_id):
        self.Id = FakeId(element_id)
        self.Position = FakeXYZ(1, 2, 3)
        self.SharedPosition = FakeXYZ(1, 2, 3)
        self.Clipped = False

    @staticmethod
    def GetProjectBasePoint(doc):
        return FakeBasePoint(12)

    @staticmethod
    def GetSurveyPoint(doc):
        return FakeBasePoint(13)


class FakeInternalOrigin:
    Id = FakeId(10)
    Position = FakeXYZ(0, 0, 0)
    SharedPosition = FakeXYZ(0, 0, 0)

    @staticmethod
    def Get(doc):
        return FakeInternalOrigin()


class FakeRuntime:
    def identity_response(self, uiapp, doc):
        return {"status": "success", "identity": {
            "document_title": "Test", "document_path": "fixture://Test.rvt", "revit_version": "2024",
            "revit_process_id": 1, "process_session_id": "p", "application_session_id": "a",
            "document_session_id": "d"}}


class FakeCategory:
    def __init__(self, name, category_type="Model"):
        self.Name, self.CategoryType = name, category_type


class FakeLocationCurve:
    def __init__(self, curve):
        self.Curve = curve


class FakeLocationPoint:
    def __init__(self, point):
        self.Point = point


class FakeCurve:
    IsBound = True
    Length = 10.0

    def GetEndPoint(self, index):
        return FakeXYZ(index * 10, 0, 0)


class FakeBox:
    Min = FakeXYZ(0, 0, 0)
    Max = FakeXYZ(10, 1, 3)


class FakeElementType:
    Name = "Basic Type"
    FamilyName = "Basic Family"


class FakeLinkType:
    def __init__(self, status):
        self.status = status

    def GetLinkedFileStatus(self):
        return self.status


class FakeElement:
    def __init__(self, element_id, location=None, bbox=None, category=None, view_specific=False):
        self.Id, self.UniqueId = FakeId(element_id), "uid-{}".format(element_id)
        self.Category = category or FakeCategory("Walls")
        self.ViewSpecific = view_specific
        self.Location = location
        self.LevelId = FakeId(-1)
        self._bbox = bbox

    class _RuntimeType:
        Name = "FakeElement"

    def GetType(self):
        return self._RuntimeType()

    def GetTypeId(self):
        return FakeId(100)

    def get_BoundingBox(self, view):
        return self._bbox


class FakeLink(FakeElement):
    class _RuntimeType:
        Name = "RevitLinkInstance"

    def __init__(self, element_id, linked_doc=None, **kwargs):
        super().__init__(element_id, **kwargs)
        self._linked_doc = linked_doc

    def GetLinkDocument(self):
        return self._linked_doc

    def GetTypeId(self):
        return FakeId(200)


class FakeInternalOriginElement(FakeElement):
    class _RuntimeType:
        Name = "InternalOrigin"


class FakeCollector:
    def __init__(self, elements):
        self.elements = elements

    def WhereElementIsNotElementType(self):
        return self

    def GetElementCount(self):
        return len(self.elements)

    def __iter__(self):
        return iter(self.elements)


class FakeDoc:
    ActiveProjectLocation = FakeProjectLocation()

    def __init__(self, elements=None):
        self.elements = elements or []

    def GetElement(self, element_id):
        if str(element_id.Value) == "100":
            return FakeElementType()
        if str(element_id.Value) == "200":
            return FakeLinkType("Unloaded")
        return None


def fake_db(elements=None):
    db = types.ModuleType("Autodesk.Revit.DB")
    db.BasePoint = FakeBasePoint
    db.InternalOrigin = FakeInternalOrigin
    db.XYZ = FakeXYZ
    db.FilteredElementCollector = lambda doc: FakeCollector(doc.elements)
    db.LocationCurve = FakeLocationCurve
    db.LocationPoint = FakeLocationPoint
    db.RevitLinkInstance = FakeLink
    db.Options = type("Options", (), {})
    db.ViewDetailLevel = types.SimpleNamespace(Fine="Fine")
    revit = types.ModuleType("Autodesk.Revit")
    revit.DB = db
    autodesk = types.ModuleType("Autodesk")
    autodesk.Revit = revit
    return {"Autodesk": autodesk, "Autodesk.Revit": revit, "Autodesk.Revit.DB": db}


class CoordinateGeometryTests(unittest.TestCase):
    def test_internal_origin_base_points_and_transform_are_reported(self):
        doc = FakeDoc()
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.coordinate_diagnostic(FakeRuntime(), object(), doc)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["internal_origin"]["position_internal"]["millimetres"], {"x": 0.0, "y": 0.0, "z": 0.0})
        self.assertEqual(result["project_base_point"]["element_id"], "12")
        self.assertEqual(result["survey_point"]["element_id"], "13")
        self.assertEqual(result["active_project_location"]["transform"]["direction"], "unverified")
        self.assertEqual(result["active_project_location"]["transform_cross_check"]["status"], "unverified")
        self.assertEqual(result["active_project_location"]["transform"]["origin"]["coordinate_reference"],
                         "unverified transform output coordinate frame")

    def test_zero_translation_rotation_is_verified_from_non_origin_references(self):
        transform = AffineTransform(angle=0.47)
        db, doc = coordinate_db(transform, transform)
        with patch.dict(sys.modules, db):
            result = coordinate_geometry.coordinate_diagnostic(FakeRuntime(), object(), doc)
        record = result["active_project_location"]
        self.assertEqual(record["transform"]["direction"], "internal_to_shared")
        self.assertEqual(record["transform"]["origin"]["coordinate_reference"], "active ProjectLocation shared coordinates")
        refs = record["transform_cross_check"]["independent_project_position_checks"]
        self.assertEqual(len(refs), 2)
        self.assertTrue(all(item["forward_matches_project_position"] for item in refs))
        self.assertFalse(any(item["inverse_matches_project_position"] for item in refs))

    def test_nonzero_translation_and_rotation_verify_forward_direction(self):
        transform = AffineTransform(angle=0.31, translation=(12.0, -8.0, 2.5))
        db, doc = coordinate_db(transform, transform)
        with patch.dict(sys.modules, db):
            result = coordinate_geometry.coordinate_diagnostic(FakeRuntime(), object(), doc)
        self.assertEqual(result["active_project_location"]["transform"]["direction"], "internal_to_shared")

    def test_inverse_direction_can_be_verified_without_assuming_method_name(self):
        project_mapping = AffineTransform(angle=-0.38, translation=(-4.0, 9.0, 1.0))
        transform_reported = project_mapping.Inverse
        db, doc = coordinate_db(transform_reported, project_mapping)
        with patch.dict(sys.modules, db):
            result = coordinate_geometry.coordinate_diagnostic(FakeRuntime(), object(), doc)
        record = result["active_project_location"]
        self.assertEqual(record["transform"]["direction"], "shared_to_internal")
        self.assertEqual(record["transform"]["origin"]["coordinate_reference"], "Revit internal model coordinates")

    def test_ambiguous_and_failed_transform_checks_remain_unverified(self):
        cases = ((AffineTransform(), AffineTransform()),
                 (AffineTransform(angle=0.2, translation=(5, 0, 0)), AffineTransform()))
        for transform, independent in cases:
            db, doc = coordinate_db(transform, independent)
            with patch.dict(sys.modules, db):
                result = coordinate_geometry.coordinate_diagnostic(FakeRuntime(), object(), doc)
            record = result["active_project_location"]
            self.assertEqual(record["transform"]["direction"], "unverified")
            self.assertEqual(record["transform"]["origin"]["coordinate_reference"],
                             "unverified transform output coordinate frame")

    def test_transform_origin_label_is_unverified_when_base_point_cross_check_missing(self):
        transform = AffineTransform(angle=0.2)
        db, doc = coordinate_db(transform, transform)
        class EmptyBasePoint:
            GetProjectBasePoint = staticmethod(lambda _doc: None)
            GetSurveyPoint = staticmethod(lambda _doc: None)
        db["Autodesk.Revit.DB"].BasePoint = EmptyBasePoint
        with patch.dict(sys.modules, db):
            result = coordinate_geometry.coordinate_diagnostic(FakeRuntime(), object(), doc)
        self.assertEqual(result["active_project_location"]["transform"]["direction"], "unverified")
        self.assertEqual(result["active_project_location"]["transform"]["origin"]["coordinate_reference"],
                         "unverified transform output coordinate frame")

    def test_transform_cross_check_failure_is_reported_without_direction_guess(self):
        class ShiftedTransform(FakeTransform):
            Origin = FakeXYZ(50, 0, 0)

            def OfPoint(self, point):
                return FakeXYZ(point.X + 50, point.Y, point.Z)

            @property
            def Inverse(self):
                class InverseTransform:
                    def OfPoint(self, point):
                        return FakeXYZ(point.X - 50, point.Y, point.Z)
                return InverseTransform()

        class ShiftedLocation(FakeProjectLocation):
            def GetTransform(self):
                return ShiftedTransform()

        class ShiftedDoc(FakeDoc):
            ActiveProjectLocation = ShiftedLocation()

        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.coordinate_diagnostic(FakeRuntime(), object(), ShiftedDoc())
        self.assertEqual(result["active_project_location"]["transform"]["direction"], "unverified")
        self.assertTrue(any(item["code"] == "transform_cross_check_failed" for item in result["warnings"]))

    def test_curve_point_bbox_unsupported_links_and_completeness(self):
        curve = FakeElement(1, location=FakeLocationCurve(FakeCurve()), bbox=FakeBox())
        point = FakeElement(2, location=FakeLocationPoint(FakeXYZ(2, 3, 4)))
        unsupported = FakeElement(3)
        annotation = FakeElement(4, category=FakeCategory("Text Notes", "Annotation"), view_specific=True)
        link = FakeLink(5, category=FakeCategory("RVT Links"))
        internal_origin_marker = FakeInternalOriginElement(6)
        doc = FakeDoc([curve, point, unsupported, annotation, link, internal_origin_marker])
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), doc, 0, 10)
        counts = result["counts"]
        self.assertEqual(counts["total_elements_examined"], 6)
        self.assertEqual(counts["physical_model_instances"], 2)
        self.assertEqual(counts["physical_geometry_candidates"], 2)
        self.assertEqual(counts["successfully_inspected"], 2)
        self.assertEqual(counts["unsupported"], 1)
        self.assertEqual(counts["view_specific_elements"], 1)
        self.assertEqual(counts["revit_link_instances"], 1)
        self.assertEqual(counts["coordinate_reference_elements"], 1)
        self.assertEqual(counts["uninspected_links"], 1)
        self.assertFalse(result["is_complete"])
        self.assertTrue(result["has_unsupported_elements"])
        self.assertEqual(result["clearance_status"], "unverified")
        self.assertEqual(result["items"][0]["location_curve"]["length_mm"], 3048.0)
        self.assertEqual(result["items"][1]["location_point"]["millimetres"]["x"], 609.6)
        self.assertEqual(result["items"][0]["type_id"], "100")
        self.assertEqual(result["items"][0]["type_name"], "Basic Type")
        self.assertEqual(result["items"][0]["family_name"], "Basic Family")
        self.assertEqual(result["items"][0]["type_info_status"], "inspected")
        self.assertEqual(result["items"][0]["level_info_status"], "unavailable")
        self.assertEqual(result["items"][0]["location_status"], "inspected")
        self.assertEqual(result["items"][0]["bounding_box_status"], "inspected")
        self.assertEqual(result["items"][0]["geometry_inspection_status"], "inspected")

    def test_geometry_classifications_separate_candidates_references_and_unknowns(self):
        class ModelCurve(FakeElement):
            class _RuntimeType:
                Name = "ModelCurve"

        class Grid(FakeElement):
            class _RuntimeType:
                Name = "Grid"

        class SketchPlane(FakeElement):
            class _RuntimeType:
                Name = "SketchPlane"

        class UnknownElement(FakeElement):
            pass

        candidate = FakeElement(31, location=FakeLocationPoint(FakeXYZ(1, 2, 3)))
        model_curve = ModelCurve(32, location=FakeLocationCurve(FakeCurve()))
        datum = Grid(33)
        reference = SketchPlane(37, category=FakeCategory("Reference Geometry"))
        unknown = UnknownElement(34, category=FakeCategory("Other", "Annotation"))
        non_model_unknown = UnknownElement(35, category=FakeCategory("Unclassified", "Internal"))
        model_without_supported_geometry = FakeElement(36)
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(),
                FakeDoc([candidate, model_curve, datum, reference, unknown, non_model_unknown,
                         model_without_supported_geometry]), 0, 10)
        classifications = {item["element_id"]: item["classification"] for item in result["items"]}
        self.assertEqual(classifications["31"], "physical_geometry_candidate")
        self.assertEqual(classifications["32"], "model_curve_or_reference_geometry")
        self.assertEqual(classifications["33"], "coordinate_reference")
        self.assertEqual(classifications["37"], "model_curve_or_reference_geometry")
        self.assertEqual(classifications["34"], "annotation_or_detail")
        self.assertEqual(classifications["35"], "unsupported_or_unclassified")
        self.assertEqual(classifications["36"], "unsupported_or_unclassified")
        self.assertEqual(result["counts"]["physical_geometry_candidates"], 1)
        self.assertEqual(result["clearance_status"], "unverified")

    def test_every_classification_has_common_status_schema_and_not_applicable_geometry(self):
        elements = [
            FakeLink(60),
            FakeElement(61, category=FakeCategory("Tags", "Annotation"), view_specific=True),
            FakeElement(62, category=FakeCategory("Text", "Annotation")),
            FakeInternalOriginElement(63),
            type("ModelCurve", (FakeElement,), {"_RuntimeType": type("RuntimeType", (), {"Name": "ModelCurve"})})(64),
            FakeElement(65, category=FakeCategory("Internal", "Internal")),
            FakeElement(66, location=FakeLocationPoint(FakeXYZ(1, 2, 3))),
        ]
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(elements), 0, 20)
        expected = {"classification_status", "classification_warnings", "type_id_status",
                    "type_resolution_status", "type_name_status", "family_name_status",
                    "type_info_status", "level_id_status", "level_name_status",
                    "level_resolution_status", "level_info_status", "location_status", "bounding_box_status",
                    "geometry_inspection_status", "inspection_status", "warnings",
                    "type_id", "type_name", "family_name"}
        valid = set(("not_inspected", "inspected", "not_applicable", "unavailable", "failed"))
        for item in result["items"]:
            self.assertTrue(expected.issubset(set(item)))
            for field in ("type_id_status", "type_resolution_status", "type_name_status",
                          "family_name_status", "type_info_status", "level_id_status",
                          "level_name_status", "level_resolution_status", "level_info_status", "location_status",
                          "bounding_box_status", "geometry_inspection_status", "inspection_status"):
                self.assertIn(item[field], valid)
            self.assertIn(item["classification_status"], ("not_inspected", "inspected", "failed"))
            self.assertIsInstance(item["classification_warnings"], list)
            self.assertIsInstance(item["warnings"], list)
        classifications = {item["element_id"]: item for item in result["items"]}
        self.assertEqual(classifications["60"]["classification"], "link_instance")
        self.assertEqual(classifications["61"]["classification"], "view_specific")
        self.assertEqual(classifications["62"]["classification"], "annotation_or_detail")
        self.assertEqual(classifications["63"]["classification"], "coordinate_reference")
        self.assertEqual(classifications["64"]["classification"], "model_curve_or_reference_geometry")
        self.assertEqual(classifications["65"]["classification"], "unsupported_or_unclassified")
        for key in ("60", "61", "62", "63", "64"):
            self.assertEqual(classifications[key]["geometry_inspection_status"], "not_applicable")
            self.assertEqual(classifications[key]["inspection_status"], "not_applicable")
        self.assertEqual(classifications["65"]["geometry_inspection_status"], "not_inspected")

    def test_type_id_and_level_access_failures_are_reported_independently(self):
        class BrokenTypeElement(FakeElement):
            def GetTypeId(self):
                raise RuntimeError("type id failure")

        elements = [BrokenTypeElement(70, location=FakeLocationPoint(FakeXYZ(0, 0, 0))),
                    FakeElement(71, location=FakeLocationPoint(FakeXYZ(0, 0, 0)))]
        del elements[1].LevelId
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(elements), 0, 10)
        first, second = result["items"]
        self.assertEqual(first["type_info_status"], "failed")
        self.assertEqual(first["geometry_inspection_status"], "inspected")
        self.assertTrue(any(w["field"] == "type_id" for w in first["warnings"]))
        self.assertEqual(second["level_info_status"], "unavailable")
        self.assertEqual(second["level_id_status"], "unavailable")
        self.assertEqual(second["level_resolution_status"], "not_applicable")
        self.assertEqual(second["level_name_status"], "not_applicable")

    def test_type_name_and_family_name_getter_errors_are_reported_independently(self):
        class TypeNameThrows:
            @property
            def Name(self):
                raise RuntimeError("type name getter failed")
            FamilyName = "Family A"

        class FamilyNameThrows:
            Name = "Type B"
            @property
            def FamilyName(self):
                raise RuntimeError("family name getter failed")

        class MappingDoc(FakeDoc):
            def __init__(self, elements, mapping):
                super().__init__(elements)
                self.mapping = mapping

            def GetElement(self, element_id):
                return self.mapping.get(str(element_id.Value))

        elements = [FakeElement(90, location=FakeLocationPoint(FakeXYZ(0, 0, 0))),
                    FakeElement(91, location=FakeLocationPoint(FakeXYZ(0, 0, 0)))]
        doc = MappingDoc(elements, {})
        # Give each instance a distinct type id so both property failures are exercised.
        elements[0].GetTypeId = lambda: FakeId(101)
        elements[1].GetTypeId = lambda: FakeId(102)
        doc.mapping = {"101": TypeNameThrows(), "102": FamilyNameThrows()}
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), doc, 0, 10)
        type_name_failed, family_name_failed = result["items"]
        self.assertEqual(type_name_failed["type_name_status"], "failed")
        self.assertEqual(type_name_failed["family_name_status"], "inspected")
        self.assertEqual(type_name_failed["geometry_inspection_status"], "inspected")
        self.assertEqual(family_name_failed["type_name_status"], "inspected")
        self.assertEqual(family_name_failed["family_name_status"], "failed")
        self.assertEqual(family_name_failed["type_info_status"], "failed")
        self.assertTrue(any(w["field"] == "type_name" and w["message"] == "type name getter failed"
                            for w in type_name_failed["warnings"]))
        self.assertTrue(any(w["field"] == "family_name" and w["message"] == "family name getter failed"
                            for w in family_name_failed["warnings"]))

    def test_level_name_getter_error_does_not_invalidate_geometry(self):
        class LevelNameThrows:
            @property
            def Name(self):
                raise RuntimeError("level name getter failed")

        class MappingDoc(FakeDoc):
            def GetElement(self, element_id):
                if str(element_id.Value) == "500":
                    return LevelNameThrows()
                return super().GetElement(element_id)

        element = FakeElement(92, location=FakeLocationPoint(FakeXYZ(0, 0, 0)))
        element.LevelId = FakeId(500)
        with patch.dict(sys.modules, fake_db()):
            item = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), MappingDoc([element]), 0, 1)["items"][0]
        self.assertEqual(item["level_info_status"], "failed")
        self.assertEqual(item["level_name_status"], "failed")
        self.assertEqual(item["location_status"], "inspected")
        self.assertEqual(item["geometry_inspection_status"], "inspected")
        self.assertTrue(any(w["field"] == "level_name" and w["message"] == "level name getter failed"
                            for w in item["warnings"]))

    def test_family_object_name_fallback_is_preserved_when_family_name_property_is_absent(self):
        family = type("Family", (), {"Name": "Fallback Family"})()
        type_without_family_name = type("TypeWithoutFamilyName", (), {
            "Name": "Fallback Type", "Family": family})

        class MappingDoc(FakeDoc):
            def GetElement(self, element_id):
                return type_without_family_name() if str(element_id.Value) == "100" else None

        with patch.dict(sys.modules, fake_db()):
            item = coordinate_geometry.geometry_inventory(FakeRuntime(), object(),
                MappingDoc([FakeElement(99, location=FakeLocationPoint(FakeXYZ(0, 0, 0)))]), 0, 1)["items"][0]
        self.assertEqual(item["type_name"], "Fallback Type")
        self.assertEqual(item["family_name"], "Fallback Family")
        self.assertEqual(item["family_name_status"], "inspected")

    def test_missing_location_and_bbox_are_unavailable_and_api_errors_are_failed(self):
        class BrokenGeometry(FakeElement):
            @property
            def Location(self):
                raise RuntimeError("location failure")

            @Location.setter
            def Location(self, value):
                pass

            def get_BoundingBox(self, view):
                raise RuntimeError("bbox failure")

        elements = [FakeElement(72), BrokenGeometry(73)]
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(elements), 0, 10)
        missing, broken = result["items"]
        self.assertEqual(missing["location_status"], "unavailable")
        self.assertEqual(missing["bounding_box_status"], "unavailable")
        self.assertEqual(missing["geometry_inspection_status"], "unavailable")
        self.assertEqual(broken["location_status"], "failed")
        self.assertEqual(broken["bounding_box_status"], "failed")
        self.assertEqual(broken["geometry_inspection_status"], "failed")
        self.assertEqual(result["counts"]["failed"], 1)

    def test_location_and_bbox_fail_independently_and_keep_successful_field(self):
        class LocationThrows(FakeElement):
            @property
            def Location(self):
                raise RuntimeError("location getter failed")
            @Location.setter
            def Location(self, value):
                pass

        class BoundingBoxThrows(FakeElement):
            def get_BoundingBox(self, view):
                raise RuntimeError("bbox getter failed")

        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc([
                LocationThrows(93, bbox=FakeBox()),
                BoundingBoxThrows(94, location=FakeLocationPoint(FakeXYZ(1, 2, 3)))
            ]), 0, 10)
        location_failed, bbox_failed = result["items"]
        self.assertEqual((location_failed["location_status"], location_failed["bounding_box_status"],
                          location_failed["geometry_inspection_status"]), ("failed", "inspected", "inspected"))
        self.assertEqual((bbox_failed["location_status"], bbox_failed["bounding_box_status"],
                          bbox_failed["geometry_inspection_status"]), ("inspected", "failed", "inspected"))
        self.assertTrue(any(w["field"] == "location" and w["message"] == "location getter failed"
                            for w in location_failed["warnings"]))
        self.assertTrue(any(w["field"] == "bounding_box" and w["message"] == "bbox getter failed"
                            for w in bbox_failed["warnings"]))

    def test_attribute_error_from_declared_location_is_failed_but_absent_location_is_unavailable(self):
        class AttributeErrorLocation(FakeElement):
            @property
            def Location(self):
                raise AttributeError("location getter raised internally")
            @Location.setter
            def Location(self, value):
                pass

        absent = FakeElement(97)
        del absent.Location
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc([
                AttributeErrorLocation(98), absent
            ]), 0, 2)
        failed, unavailable = result["items"]
        self.assertEqual(failed["location_status"], "failed")
        self.assertTrue(any(w["field"] == "location" and
                            w["exception_type"] == "AttributeError" and
                            w["message"] == "location getter raised internally"
                            for w in failed["warnings"]))
        self.assertEqual(unavailable["location_status"], "unavailable")
        self.assertFalse(any(w["field"] == "location" for w in unavailable["warnings"]))

    def test_early_classification_failure_leaves_fields_not_inspected(self):
        class CategoryThrows(FakeElement):
            @property
            def Category(self):
                raise RuntimeError("category getter failed")
            @Category.setter
            def Category(self, value):
                pass

        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc([CategoryThrows(95)]), 0, 1)
        item = result["items"][0]
        common_fields = ("classification_status", "classification_warnings", "type_id_status",
            "type_resolution_status", "type_name_status", "family_name_status", "type_info_status",
            "level_id_status", "level_name_status", "level_info_status", "location_status",
            "bounding_box_status", "geometry_inspection_status", "inspection_status", "warnings")
        for field in common_fields:
            self.assertIn(field, item)
        self.assertEqual(item["classification_status"], "failed")
        self.assertEqual(item["inspection_status"], "not_inspected")
        for field in ("type_id_status", "type_resolution_status", "type_name_status", "family_name_status",
                      "type_info_status", "level_id_status", "level_resolution_status", "level_name_status", "level_info_status",
                      "location_status", "bounding_box_status", "geometry_inspection_status"):
            self.assertEqual(item[field], "not_inspected", field)
        self.assertEqual(item["classification_warnings"][0]["field"], "classification")
        self.assertEqual(item["classification_warnings"][0]["message"], "category getter failed")

    def test_category_name_and_category_type_failures_are_classification_errors(self):
        class NameThrows(FakeCategory):
            def __init__(self):
                self.CategoryType = "Model"

            @property
            def Name(self):
                raise RuntimeError("category name failed")

        class CategoryTypeThrows(FakeCategory):
            def __init__(self):
                self.Name = "Walls"

            @property
            def CategoryType(self):
                raise RuntimeError("category type failed")

        cases = ((FakeElement(1001, category=NameThrows()), "category name failed"),
                 (FakeElement(1002, category=CategoryTypeThrows()), "category type failed"))
        with patch.dict(sys.modules, fake_db()):
            for element, message in cases:
                item = coordinate_geometry.geometry_inventory(FakeRuntime(), object(),
                    FakeDoc([element]), 0, 1)["items"][0]
                self.assertEqual(item["classification_status"], "failed")
                self.assertEqual(item["classification_warnings"][0]["field"], "classification")
                self.assertEqual(item["classification_warnings"][0]["message"], message)
                self.assertEqual(item["type_info_status"], "not_inspected")
                self.assertEqual(item["level_info_status"], "not_inspected")
                self.assertEqual(item["location_status"], "not_inspected")
                self.assertEqual(item["bounding_box_status"], "not_inspected")

    def test_null_category_is_classified_without_inspecting_fields(self):
        element = FakeElement(1003)
        element.Category = None
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc([element]), 0, 1)
        item = result["items"][0]
        self.assertEqual(item["classification"], "unsupported_or_unclassified")
        self.assertEqual(item["classification_status"], "inspected")
        self.assertEqual(item["classification_warnings"], [])
        self.assertEqual(item["type_info_status"], "not_inspected")
        self.assertEqual(item["level_info_status"], "not_inspected")

    def test_type_and_level_aggregates_share_status_contract(self):
        cases = (
            (("failed", "inspected", "not_inspected"), "failed"),
            (("inspected", "unavailable", "not_applicable"), "unavailable"),
            (("inspected", "inspected", "not_applicable"), "inspected"),
            (("inspected", "inspected", "inspected"), "inspected"),
            (("unavailable", "not_applicable", "not_applicable"), "unavailable"),
            (("unavailable", "unavailable", "unavailable"), "unavailable"),
            (("not_applicable", "not_applicable", "not_applicable"), "not_applicable"),
            (("not_inspected", "not_inspected", "not_inspected"), "not_inspected"),
            (("inspected", "not_inspected", "not_applicable"), "not_inspected"),
        )
        for granular, expected in cases:
            type_status = coordinate_geometry._aggregate_status(granular + ("not_applicable",))
            level_status = coordinate_geometry._aggregate_status(granular)
            self.assertEqual(type_status, expected)
            self.assertEqual(level_status, expected)

    def test_type_and_level_records_aggregate_id_and_name_results(self):
        class TypeIdOnly:
            Name = None
            FamilyName = None

        class LevelNameUnavailable:
            Name = None

        class MappingDoc(FakeDoc):
            def GetElement(self, element_id):
                if str(element_id.Value) == "1010":
                    return TypeIdOnly()
                if str(element_id.Value) == "1011":
                    return LevelNameUnavailable()
                return None

        element = FakeElement(1004, location=FakeLocationPoint(FakeXYZ(0, 0, 0)))
        element.GetTypeId = lambda: FakeId(1010)
        element.LevelId = FakeId(1011)
        with patch.dict(sys.modules, fake_db()):
            item = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), MappingDoc([element]), 0, 1)["items"][0]
        self.assertEqual((item["type_id_status"], item["type_resolution_status"],
                          item["type_name_status"], item["family_name_status"]),
                         ("inspected", "inspected", "unavailable", "unavailable"))
        self.assertEqual(item["type_info_status"], "unavailable")
        self.assertEqual((item["level_id_status"], item["level_resolution_status"],
                          item["level_name_status"]), ("inspected", "inspected", "unavailable"))
        self.assertEqual(item["level_info_status"], "unavailable")

    def test_curve_endpoint_attribute_error_is_location_failure_and_preserves_partial_point(self):
        class Curve:
            IsBound = True
            Length = 1.0

            def GetEndPoint(self, index):
                if index == 1:
                    raise AttributeError("endpoint getter failed")
                return FakeXYZ(2, 3, 4)

        element = FakeElement(1005, location=FakeLocationCurve(Curve()))
        with patch.dict(sys.modules, fake_db()):
            item = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc([element]), 0, 1)["items"][0]
        self.assertEqual(item["location_status"], "failed")
        self.assertEqual(item["geometry_inspection_status"], "failed")
        self.assertEqual(item["inspection_status"], "failed")
        self.assertEqual(item["location_curve"]["start"]["raw_feet"], {"x": 2.0, "y": 3.0, "z": 4.0})
        self.assertIsNone(item["location_curve"]["end"])
        self.assertTrue(any(w["field"] == "location" and
                            w["exception_type"] == "AttributeError" and
                            w["message"] == "endpoint getter failed"
                            for w in item["warnings"]))

    def test_failure_pagination_is_independent_from_element_pagination(self):
        class BrokenElement(FakeElement):
            @property
            def Category(self):
                raise RuntimeError("synthetic error")

            @Category.setter
            def Category(self, value):
                pass

        elements = [FakeElement(80, location=FakeLocationPoint(FakeXYZ(0, 0, 0))),
                    BrokenElement(81), FakeElement(82, location=FakeLocationPoint(FakeXYZ(0, 0, 0))),
                    BrokenElement(83), BrokenElement(84)]
        with patch.dict(sys.modules, fake_db()):
            page = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(elements),
                offset=2, limit=1, failure_offset=0, failure_limit=2)
        self.assertEqual([item["element_id"] for item in page["items"]], ["82"])
        self.assertEqual([item["element_id"] for item in page["element_failures"]], ["81", "83"])
        self.assertEqual(page["failure_offset"], 0)
        self.assertEqual(page["failure_limit"], 2)
        self.assertTrue(page["failure_has_more"])
        self.assertEqual(page["total_failed_elements"], 3)

    def test_empty_inventory_out_of_range_pages_and_zero_failures(self):
        with patch.dict(sys.modules, fake_db()):
            empty = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(),
                offset=0, limit=3, failure_offset=0, failure_limit=2)
            beyond = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc([
                FakeElement(96, location=FakeLocationPoint(FakeXYZ(0, 0, 0)))
            ]), offset=4, limit=2, failure_offset=5, failure_limit=3)
        self.assertEqual(empty["counts"]["total_elements_examined"], 0)
        self.assertEqual(empty["inventory_item_count"], 0)
        self.assertEqual(empty["items"], [])
        self.assertFalse(empty["has_more"])
        self.assertEqual(empty["total_failed_elements"], 0)
        self.assertEqual(empty["element_failures"], [])
        self.assertFalse(empty["failure_has_more"])
        self.assertEqual(beyond["counts"]["total_elements_examined"], 1)
        self.assertEqual(beyond["items"], [])
        self.assertFalse(beyond["has_more"])
        self.assertEqual(beyond["total_failed_elements"], 0)
        self.assertEqual(beyond["element_failures"], [])
        self.assertFalse(beyond["failure_has_more"])

    def test_failure_pagination_rejects_invalid_offsets_and_limits(self):
        with patch.dict(sys.modules, fake_db()):
            bad_offset = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(), failure_offset=-1)
            bad_bool = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(), failure_offset=True)
            bad_zero_limit = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(), failure_limit=0)
            bad_large_limit = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(), failure_limit=101)
        self.assertEqual(bad_offset["error"], "invalid_failure_offset")
        self.assertEqual(bad_bool["error"], "invalid_failure_offset")
        self.assertEqual(bad_zero_limit["error"], "invalid_failure_limit")
        self.assertEqual(bad_large_limit["error"], "invalid_failure_limit")

    def test_large_inventory_page_boundaries_and_statuses(self):
        elements = [FakeElement(index, location=FakeLocationPoint(FakeXYZ(index, 0, 0)))
                    for index in range(205)]
        with patch.dict(sys.modules, fake_db()):
            first = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(elements), 0, 100)
            second = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(elements), 100, 100)
            last = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(elements), 200, 100)
        self.assertEqual(len(first["items"]), 100)
        self.assertEqual(len(second["items"]), 100)
        self.assertEqual(len(last["items"]), 5)
        self.assertEqual(first["items"][0]["element_id"], "0")
        self.assertEqual(second["items"][0]["element_id"], "100")
        self.assertEqual(last["items"][-1]["element_id"], "204")
        self.assertTrue(first["has_more"] and second["has_more"])
        self.assertFalse(last["has_more"])
        self.assertEqual(last["counts"]["total_elements_examined"], 205)

    def test_failure_records_are_retrievable_on_inventory_pages(self):
        class BrokenElement(FakeElement):
            @property
            def Category(self):
                raise RuntimeError("synthetic error")

            @Category.setter
            def Category(self, value):
                pass

        elements = [BrokenElement(41), BrokenElement(42),
                    FakeElement(43, location=FakeLocationPoint(FakeXYZ(1, 2, 3))), BrokenElement(44)]
        pages = []
        with patch.dict(sys.modules, fake_db()):
            for offset in range(4):
                pages.append(coordinate_geometry.geometry_inventory(
                    FakeRuntime(), object(), FakeDoc(elements), offset, 1, offset, 1))
        failures = [failure for page in pages for failure in page["element_failures"]]
        self.assertEqual([failure["element_id"] for failure in failures], ["41", "42", "44"])
        self.assertTrue(all(failure["error"]["code"] == "element_inspection_failed" for failure in failures))
        self.assertTrue(all(failure["category"] is None or isinstance(failure["category"], str) for failure in failures))
        self.assertTrue(all(page["total_failed_elements"] == 3 for page in pages))
        self.assertEqual([page["failure_has_more"] for page in pages], [True, True, False, False])
        self.assertTrue(all(page["failure_offset"] == page["offset"] and page["failure_limit"] == page["limit"] for page in pages))
        self.assertTrue(all(not page["is_complete"] for page in pages))

    def test_failed_element_does_not_abort_inventory(self):
        class BrokenElement(FakeElement):
            @property
            def Category(self):
                raise RuntimeError("synthetic error")

            @Category.setter
            def Category(self, value):
                pass

        doc = FakeDoc([BrokenElement(1), FakeElement(2, location=FakeLocationPoint(FakeXYZ(0, 0, 0)))])
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), doc, 0, 10)
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["counts"]["successfully_inspected"], 1)
        self.assertTrue(result["has_failed_elements"])
        self.assertEqual(result["clearance_status"], "unverified")

    def test_unloaded_link_and_invalid_paging_are_safe(self):
        with patch.dict(sys.modules, fake_db()):
            invalid = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc(), 0, 101)
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), FakeDoc([FakeLink(9)]), 0, 10)
        self.assertEqual(invalid["error"], "invalid_limit")
        self.assertEqual(result["counts"]["linked_documents_available"], 0)
        self.assertTrue(result["has_uninspected_links"])
        self.assertEqual(result["items"][0]["linked_document_load_state"], "unloaded")
        self.assertEqual(result["clearance_status"], "unverified")

    def test_loaded_link_document_geometry_is_inspected_in_link_coordinates(self):
        linked_doc = FakeDoc([FakeElement(22, location=FakeLocationPoint(FakeXYZ(1, 1, 0)))])
        host_doc = FakeDoc([FakeLink(9, linked_doc=linked_doc)])
        with patch.dict(sys.modules, fake_db()):
            result = coordinate_geometry.geometry_inventory(FakeRuntime(), object(), host_doc, 0, 10)
        self.assertEqual(result["counts"]["linked_documents_available"], 1)
        self.assertEqual(result["counts"]["linked_documents_successfully_inspected"], 1)
        self.assertFalse(result["has_uninspected_links"])
        self.assertTrue(result["items"][0]["linked_geometry_inspected"])
        self.assertEqual(result["linked_documents"][0]["coordinate_reference"], "linked document internal coordinates")
        self.assertEqual(result["clearance_status"], "unverified")

    def test_linked_element_failures_are_paged_and_reported_in_link_summary(self):
        class BrokenLinkedElement(FakeElement):
            @property
            def Category(self):
                raise RuntimeError("linked synthetic error")

            @Category.setter
            def Category(self, value):
                pass

        linked_doc = FakeDoc([BrokenLinkedElement(51), BrokenLinkedElement(52)])
        host_doc = FakeDoc([FakeLink(9, linked_doc=linked_doc)])
        pages = []
        with patch.dict(sys.modules, fake_db()):
            for offset in range(2):
                pages.append(coordinate_geometry.geometry_inventory(
                    FakeRuntime(), object(), host_doc, offset, 1, offset, 1))
        summaries = [page["linked_documents"][0] for page in pages]
        failures = [item for summary in summaries for item in summary["failed_element_records"]]
        self.assertEqual([item["element_id"] for item in failures], ["51", "52"])
        self.assertEqual([summary["failure_has_more"] for summary in summaries], [True, False])
        self.assertTrue(all(summary["failed"] == 2 and not summary["geometry_complete"] for summary in summaries))
        self.assertTrue(all(page["clearance_status"] == "unverified" for page in pages))


if __name__ == "__main__":
    unittest.main()
