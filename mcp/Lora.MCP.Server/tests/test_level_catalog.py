import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROVIDER_LIB = (Path(__file__).resolve().parents[1] / "revit_identity" /
                "LoraIdentity.extension" / "lib")
if str(PROVIDER_LIB) not in sys.path:
    sys.path.insert(0, str(PROVIDER_LIB))

from lora_identity_provider import level_catalog


class FakeId:
    def __init__(self, value): self.Value = value


class FakeLevel:
    def __init__(self, identifier, name, elevation):
        self.Id, self.Name, self.Elevation = FakeId(identifier), name, elevation


class BrokenLevel(FakeLevel):
    @property
    def Name(self): raise RuntimeError("Level.Name getter failed")
    @Name.setter
    def Name(self, value): pass


class BrokenElevation(FakeLevel):
    @property
    def Elevation(self): raise AttributeError("Level.Elevation getter failed")
    @Elevation.setter
    def Elevation(self, value): pass


class FakeCollector:
    def __init__(self, levels): self.levels = levels
    def OfClass(self, level_type): return self
    def WhereElementIsNotElementType(self): return self
    def GetElementCount(self): return len(self.levels)
    def __iter__(self): return iter(self.levels)


class FakeDoc:
    def __init__(self, levels): self.levels = levels


class FakeRuntime:
    def __init__(self, status="success"): self.status = status
    def identity_response(self, uiapp, doc):
        return {"status": self.status, "identity": {"process_id": 7,
            "document_title": "Sample", "process_session_id": "p",
            "application_session_id": "a", "document_session_id": "d"}}


def patched_revit(levels):
    db = types.ModuleType("Autodesk.Revit.DB")
    db.FilteredElementCollector = lambda doc: FakeCollector(doc.levels)
    db.Level = FakeLevel
    autodesk = types.ModuleType("Autodesk")
    revit = types.ModuleType("Autodesk.Revit")
    revit.DB = db
    autodesk.Revit = revit
    return {"Autodesk": autodesk, "Autodesk.Revit": revit, "Autodesk.Revit.DB": db}


class LevelCatalogTests(unittest.TestCase):
    def test_returns_element_ids_raw_and_converted_elevations_and_pagination(self):
        levels = [FakeLevel(10, "L1", 0.0), FakeLevel(11, "L2", 10.0)]
        with patch.dict(sys.modules, patched_revit(levels)):
            result = level_catalog.level_catalog(FakeRuntime(), object(), FakeDoc(levels),
                offset=1, limit=1)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["levels"][0]["element_id"], "11")
        self.assertEqual(result["levels"][0]["level_name"], "L2")
        self.assertEqual(result["levels"][0]["elevation_internal_feet"], 10.0)
        self.assertEqual(result["levels"][0]["elevation_mm"], 3048.0)
        self.assertTrue(result["has_more"] is False)
        self.assertEqual(result["total_levels"], 2)
        self.assertTrue(result["inventory_complete"])
        self.assertEqual(result["clearance_status"], "unverified")

    def test_field_failures_preserve_other_fields_and_use_independent_failure_page(self):
        levels = [BrokenLevel(10, "ignored", 5.0), BrokenElevation(11, "L2", 0.0)]
        with patch.dict(sys.modules, patched_revit(levels)):
            result = level_catalog.level_catalog(FakeRuntime(), object(), FakeDoc(levels),
                offset=0, limit=2, failure_offset=1, failure_limit=1)
        first = result["levels"][0]
        self.assertEqual(first["element_id_status"], "inspected")
        self.assertEqual(first["name_status"], "failed")
        self.assertEqual(first["elevation_status"], "inspected")
        self.assertEqual(first["warnings"][0]["field"], "level_name")
        self.assertIn("Level.Name getter failed", first["warnings"][0]["message"])
        second = result["levels"][1]
        self.assertEqual(second["element_id_status"], "inspected")
        self.assertEqual(second["name_status"], "inspected")
        self.assertEqual(second["elevation_status"], "failed")
        self.assertEqual(second["warnings"][0]["field"], "elevation")
        self.assertEqual(result["total_failures"], 2)
        self.assertEqual(result["failures"][0]["failure_index"], 1)
        self.assertEqual(result["failures"][0]["field"], "elevation")
        self.assertFalse(result["failure_has_more"])
        self.assertFalse(result["has_more"])
        self.assertFalse(result["inventory_complete"])

    def test_identity_failure_prevents_level_collection(self):
        db = types.ModuleType("Autodesk.Revit.DB")
        db.FilteredElementCollector = lambda doc: self.fail("collector must not run")
        db.Level = FakeLevel
        autodesk = types.ModuleType("Autodesk")
        revit = types.ModuleType("Autodesk.Revit")
        revit.DB = db
        autodesk.Revit = revit
        with patch.dict(sys.modules, {"Autodesk": autodesk, "Autodesk.Revit": revit,
                                      "Autodesk.Revit.DB": db}):
            result = level_catalog.level_catalog(FakeRuntime("error"), object(), FakeDoc([]))
        self.assertEqual(result["error"], "document_identity_unavailable")

    def test_invalid_element_and_failure_pagination_are_rejected(self):
        with patch.dict(sys.modules, patched_revit([])):
            self.assertEqual(level_catalog.level_catalog(FakeRuntime(), object(), FakeDoc([]),
                offset=-1)["error"], "invalid_pagination")
            self.assertEqual(level_catalog.level_catalog(FakeRuntime(), object(), FakeDoc([]),
                failure_limit=101)["error"], "invalid_pagination")

    def test_empty_inventory_and_element_offset_beyond_total(self):
        levels = []
        with patch.dict(sys.modules, patched_revit(levels)):
            empty = level_catalog.level_catalog(FakeRuntime(), object(), FakeDoc(levels))
            beyond = level_catalog.level_catalog(FakeRuntime(), object(), FakeDoc(levels),
                                                 offset=20)
        for result in (empty, beyond):
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["total_levels"], 0)
            self.assertEqual(result["levels"], [])
            self.assertFalse(result["has_more"])
            self.assertTrue(result["inventory_complete"])

    def test_zero_failures_and_failure_offset_beyond_total(self):
        levels = [FakeLevel(10, "L1", 0.0)]
        with patch.dict(sys.modules, patched_revit(levels)):
            result = level_catalog.level_catalog(FakeRuntime(), object(), FakeDoc(levels),
                failure_offset=1, failure_limit=1)
        self.assertEqual(result["total_failures"], 0)
        self.assertEqual(result["failures"], [])
        self.assertFalse(result["failure_has_more"])

    def test_failure_offset_and_limit_validation(self):
        with patch.dict(sys.modules, patched_revit([])):
            for kwargs in ({"failure_offset": -1}, {"failure_offset": 10000001},
                           {"failure_limit": 0}, {"failure_limit": 101}):
                self.assertEqual(level_catalog.level_catalog(FakeRuntime(), object(),
                    FakeDoc([]), **kwargs)["error"], "invalid_pagination")


if __name__ == "__main__":
    unittest.main()
