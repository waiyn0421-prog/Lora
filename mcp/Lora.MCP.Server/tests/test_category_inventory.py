import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROVIDER_LIB = (Path(__file__).resolve().parents[1] / "revit_identity" /
                "LoraIdentity.extension" / "lib")
if str(PROVIDER_LIB) not in sys.path:
    sys.path.insert(0, str(PROVIDER_LIB))

from lora_identity_provider import category_inventory


class Id:
    def __init__(self, value): self.Value = value


class EnumValue:
    def __init__(self, value, name): self.value, self.name = value, name
    def __int__(self): return self.value
    def __str__(self): return self.name


class Category:
    def __init__(self, name, identifier, kind="Model"):
        self.Name, self.Id, self.CategoryType = name, Id(identifier), kind


class Point:
    X, Y, Z = 1.0, 2.0, 3.0


class LocationPoint:
    def __init__(self): self.Point = Point()


class StructuralParameter:
    def __init__(self, value): self.value = value
    def AsInteger(self): return int(self.value)


class Element:
    def __init__(self, identifier, category, view_specific=False):
        self.Id, self.Category, self.ViewSpecific = Id(identifier), category, view_specific
        self.Location, self.LevelId = LocationPoint(), Id(-1)
        self.StructuralUsage = "NonBearing"
        self.structural_value = False
    class RuntimeType:
        Name = "FamilyInstance"
    def GetType(self): return self.RuntimeType()
    def GetTypeId(self): return Id(800)
    def get_BoundingBox(self, view): return None
    def get_Parameter(self, parameter_id):
        if getattr(self, "missing_structural_parameter", False): return None
        return StructuralParameter(self.structural_value)


class BrokenCategoryElement(Element):
    @property
    def Category(self): raise RuntimeError("category API fault")
    @Category.setter
    def Category(self, value): pass


class ElementType:
    def __init__(self, identifier, category): self.Id, self.Category = Id(identifier), category


class Collector:
    def __init__(self, elements, types=None): self.instances, self.types = elements, types or []
    @property
    def elements(self): return self._elements
    @elements.setter
    def elements(self, value): self._elements = value
    def WhereElementIsNotElementType(self): self.elements = self.instances; return self
    def WhereElementIsElementType(self): self.elements = self.types; return self
    def GetElementCount(self): return len(self.elements)
    def __iter__(self): return iter(self.elements)


class Doc:
    def __init__(self, elements, types=None): self.elements, self.types = elements, types or []
    def GetElement(self, element_id): return None


class Runtime:
    def identity_response(self, uiapp, doc):
        return {"status": "success", "identity": {"process_id": 1, "document_session_id": "d"}}


def patched_modules(elements, extra_enum_names=None):
    db = types.ModuleType("Autodesk.Revit.DB")
    db.BuiltInCategory = type("BuiltInCategory", (), {})
    db.BuiltInParameter = type("BuiltInParameter", (), {"FLOOR_PARAM_IS_STRUCTURAL": "floor_structural"})
    db.FilteredElementCollector = lambda doc: Collector(doc.elements, doc.types)
    db.LocationCurve = type("LocationCurve", (), {})
    db.LocationPoint = LocationPoint
    system = types.ModuleType("System")
    enum_values = [EnumValue(-2000011, "OST_Walls"), EnumValue(-2000023, "OST_Doors"),
        EnumValue(-2008044, "OST_PipeCurves"), EnumValue(-2001330, "OST_StructuralFraming"),
        EnumValue(-2000032, "OST_Floors")]
    enum_values.extend(EnumValue(-3000000 - index, name)
        for index, name in enumerate(extra_enum_names or []))
    system.Enum = types.SimpleNamespace(GetValues=lambda enum_type: enum_values)
    autodesk = types.ModuleType("Autodesk")
    revit = types.ModuleType("Autodesk.Revit")
    revit.DB = db
    autodesk.Revit = revit
    return {"System": system, "Autodesk": autodesk, "Autodesk.Revit": revit,
            "Autodesk.Revit.DB": db}


class CategoryInventoryTests(unittest.TestCase):
    def test_extended_architectural_structural_and_mep_categories_are_mapped_by_enum_name(self):
        names = ["OST_StairsRuns", "OST_StairsLandings", "OST_StructConnectionPlates",
            "OST_StructSubConnections", "OST_StructuralTendons", "OST_DuctCurvesInsulation",
            "OST_DuctFittingLining", "OST_FlexPipeCurvesInsulation",
            "OST_FabricationDuctwork", "OST_FabricationPipeworkInsulation", "OST_WireInsulations"]
        elements = [Element(index + 1,
            Category(name, -3000000 - index)) for index, name in enumerate(names)]
        with patch.dict(sys.modules, patched_modules(elements, names)):
            result = category_inventory.category_inventory(Runtime(), object(), Doc(elements), limit=20)
        categories = {item["built_in_category"]: item for item in result["categories"]}
        for name in names:
            self.assertIn(name, categories)
            self.assertEqual(categories[name]["discipline"], category_inventory._DISCIPLINE_BY_CATEGORY[name])
        self.assertEqual(categories["OST_StairsRuns"]["discipline"], "architecture")
        self.assertEqual(categories["OST_StructConnectionPlates"]["discipline"], "structural")
        self.assertEqual(categories["OST_DuctCurvesInsulation"]["discipline"], "mep")
        self.assertEqual(result["classification_counts"]["model_instance"], len(names))

    def test_builtin_category_ids_drive_discipline_and_counts(self):
        elements = [Element(1, Category("Walls", -2000011)),
                    Element(2, Category("Doors", -2000023)),
                    Element(3, Category("Pipes", -2008044))]
        with patch.dict(sys.modules, patched_modules(elements)):
            result = category_inventory.category_inventory(Runtime(), object(), Doc(elements))
        categories = {entry["built_in_category"]: entry for entry in result["categories"]}
        self.assertEqual(categories["OST_Walls"]["discipline"], "architecture")
        self.assertEqual(categories["OST_Doors"]["total"], 1)
        self.assertEqual(categories["OST_PipeCurves"]["discipline"], "mep")
        self.assertEqual(result["classification_counts"]["model_instance"], 3)
        self.assertEqual(result["clearance_status"], "unverified")

    def test_discipline_filter_has_bounded_details_and_geometry_evidence(self):
        elements = [Element(index, Category("Walls", -2000011)) for index in range(1, 4)]
        with patch.dict(sys.modules, patched_modules(elements)):
            result = category_inventory.category_inventory(Runtime(), object(), Doc(elements),
                discipline="architecture", offset=1, limit=1)
        self.assertEqual(result["total_matching_elements"], 3)
        self.assertEqual(result["total_categories"], 1)
        self.assertFalse(result["categories_has_more"])
        self.assertEqual([item["element_id"] for item in result["items"]], ["2"])
        self.assertTrue(result["has_more"])
        self.assertEqual(result["items"][0]["location_status"], "inspected")
        self.assertEqual(result["items"][0]["geometry_inspection_status"], "inspected")
        self.assertIn("no solid/mesh extraction", result["items"][0]["geometry_evidence_scope"])

    def test_annotation_view_specific_and_unknown_category_are_not_physical_claims(self):
        elements = [Element(1, Category("Text", -1, "Annotation")),
                    Element(2, Category("View Model", -2000011), True),
                    Element(3, Category("Custom", -999999))]
        with patch.dict(sys.modules, patched_modules(elements)):
            result = category_inventory.category_inventory(Runtime(), object(), Doc(elements))
        by_id = {item["element_id"]: item for item in result["items"]}
        self.assertEqual(by_id["1"]["element_kind"], "annotation")
        self.assertEqual(by_id["2"]["element_kind"], "view_specific")
        self.assertEqual(by_id["3"]["discipline"], "unknown")
        self.assertEqual(by_id["1"]["geometry_inspection_status"], "not_applicable")
        common = {"type_id", "type_id_status", "type_name", "type_name_status", "family_name",
            "family_name_status", "level_id_status", "level_name_status", "level_info_status",
            "location_status", "bounding_box_status", "geometry_inspection_status", "warnings"}
        self.assertTrue(common.issubset(by_id["1"]))
        self.assertTrue(common.issubset(by_id["3"]))
        self.assertEqual(by_id["3"]["exclusion_reason"], "built_in_category_unmapped")

    def test_category_api_failures_are_independently_paginated(self):
        elements = [BrokenCategoryElement(1, None), BrokenCategoryElement(2, None)]
        with patch.dict(sys.modules, patched_modules(elements)):
            result = category_inventory.category_inventory(Runtime(), object(), Doc(elements),
                failure_offset=1, failure_limit=1)
        self.assertEqual(result["total_failures"], 2)
        self.assertEqual(len(result["failures"]), 1)
        self.assertEqual(result["failures"][0]["failure_index"], 1)
        self.assertFalse(result["inventory_complete"])
        self.assertIsNone(result["has_more"])

    def test_category_page_pagination_is_separate_from_element_page(self):
        elements = [Element(1, Category("Walls", -2000011)),
                    Element(2, Category("Doors", -2000023)),
                    Element(3, Category("Pipes", -2008044))]
        with patch.dict(sys.modules, patched_modules(elements)):
            result = category_inventory.category_inventory(Runtime(), object(), Doc(elements),
                offset=0, limit=3, category_offset=1, category_limit=1)
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(len(result["categories"]), 1)
        self.assertEqual(result["category_offset"], 1)
        self.assertEqual(result["category_limit"], 1)
        self.assertEqual(result["total_categories"], 3)
        self.assertTrue(result["category_page_has_more"])

    def test_shared_wall_and_floor_categories_use_structural_instance_properties(self):
        wall = Element(1, Category("Walls", -2000011))
        wall.StructuralUsage = "Bearing"
        floor = Element(2, Category("Floors", -2000032))
        floor.structural_value = True
        with patch.dict(sys.modules, patched_modules([wall, floor])):
            result = category_inventory.category_inventory(Runtime(), object(), Doc([wall, floor]),
                discipline="structural")
        self.assertEqual(result["total_matching_elements"], 2)
        self.assertEqual({item["built_in_category"] for item in result["categories"]},
                         {"OST_Walls", "OST_Floors"})
        self.assertTrue(all(item["discipline"] == "structural" for item in result["categories"]))

    def test_floor_structural_builtin_parameter_missing_or_throwing_stays_unknown(self):
        missing = Element(1, Category("Floors", -2000032))
        missing.missing_structural_parameter = True
        class ParameterThrows(Element):
            def get_Parameter(self, parameter_id): raise RuntimeError("structural parameter getter failed")
        throwing = ParameterThrows(2, Category("Floors", -2000032))
        with patch.dict(sys.modules, patched_modules([])):
            result = category_inventory.category_inventory(Runtime(), object(), Doc([missing, throwing]))
        unknown = [item for item in result["categories"] if item["discipline"] == "unknown"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(result["discipline_inspection_incomplete_elements"], 2)
        self.assertFalse(result["inventory_complete"])
        with patch.dict(sys.modules, patched_modules([])):
            details = category_inventory.category_inventory(Runtime(), object(), Doc([missing, throwing]),
                discipline="unknown", offset=0, limit=2)
        statuses = {item["discipline_resolution_status"] for item in details["items"]}
        self.assertEqual(statuses, {"unavailable", "failed"})
        self.assertTrue(all(item["discipline_warning"] for item in details["items"]))

    def test_element_types_are_counted_separately_from_instances(self):
        instance = Element(1, Category("Walls", -2000011))
        wall_type = ElementType(800, Category("Walls", -2000011))
        with patch.dict(sys.modules, patched_modules([])):
            result = category_inventory.category_inventory(Runtime(), object(), Doc([instance], [wall_type]))
        self.assertEqual(result["elements_examined"], 1)
        self.assertEqual(result["element_types_examined"], 1)
        self.assertEqual(result["classification_counts"]["element_type"], 1)
        self.assertTrue(any(item["element_type_count"] == 1 for item in result["categories"]))

    def test_invalid_pagination_and_discipline_are_rejected(self):
        self.assertEqual(category_inventory.category_inventory(Runtime(), object(), Doc([]),
            offset=-1)["error"], "invalid_pagination")
        self.assertEqual(category_inventory.category_inventory(Runtime(), object(), Doc([]),
            discipline="interiors")["error"], "invalid_discipline")


if __name__ == "__main__":
    unittest.main()
