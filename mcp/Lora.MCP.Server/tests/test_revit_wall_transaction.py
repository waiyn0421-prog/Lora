"""Mocked Revit API tests for the sidecar's all-or-nothing wall transaction."""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_LIB = SERVER_ROOT / "revit_identity" / "LoraIdentity.extension" / "lib"
if str(PROVIDER_LIB) not in sys.path:
    sys.path.insert(0, str(PROVIDER_LIB))

from lora_identity_provider import wall_execution


class FakeId:
    def __init__(self, value):
        self.Value = int(value)
        self.IntegerValue = int(value)
    def __int__(self):
        return self.Value


class FakeXYZ:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class FakeLine:
    def __init__(self, start, end):
        self.points = [start, end]
        self.Length = ((end.X-start.X)**2+(end.Y-start.Y)**2+(end.Z-start.Z)**2)**0.5
    @classmethod
    def CreateBound(cls, start, end):
        return cls(start, end)
    def GetEndPoint(self, index):
        return self.points[index]


class FakeParameter:
    def __init__(self, value=None, storage="Double"):
        self.value, self.StorageType, self.IsReadOnly = value, storage, False
        self.HasValue = value is not None
    def AsDouble(self): return self.value
    def AsInteger(self): return int(self.value)
    def AsString(self): return self.value
    def AsElementId(self): return self.value
    def Set(self, value): self.value, self.HasValue = value, True; return True


class FakeCategory:
    def __init__(self): self.Id, self.Name = FakeId(-2000011), "Walls"


class FakeType:
    def __init__(self, name, element_id, width):
        self.Name, self.Id, self.Width = name, FakeId(element_id), width
        self.UniqueId = "type-{}".format(element_id)
        self.Kind, self.Function = "Basic", "Interior"
        self.structure = None
    def GetCompoundStructure(self): return self.structure
    def get_Parameter(self, key):
        if key == "type_name": return FakeParameter(self.Name, "String")
        return None


class FakeTypeWithBrokenName(FakeType):
    @property
    def Name(self):
        raise AttributeError("Name")
    @Name.setter
    def Name(self, _value):
        pass
    def __init__(self, name, element_id, width):
        self.type_name = name
        self.Id, self.Width = FakeId(element_id), width
        self.UniqueId, self.Kind, self.Function = "type-{}".format(element_id), "Basic", "Interior"
        self.structure = None
    def get_Parameter(self, key):
        if key == "type_name": return FakeParameter(self.type_name, "String")
        return None


class FakeTypeWithOptionalFailures:
    Id = FakeId(21)
    def get_Parameter(self, _key): return FakeParameter("Optional Test Wall", "String")
    @property
    def UniqueId(self): raise AttributeError("UniqueId")
    @property
    def Kind(self): raise AttributeError("Kind")
    @property
    def Width(self): raise AttributeError("Width")
    @property
    def Function(self): raise AttributeError("Function")
    def GetCompoundStructure(self): raise AttributeError("GetCompoundStructure")


class FakeLayer:
    Function, Width, MaterialId = "Finish1", 0.1, FakeId(88)


class FakeCompound:
    def GetLayers(self): return [FakeLayer()]
    def GetWidth(self): return 0.1
    def GetFirstCoreLayerIndex(self): return 0
    def GetLastCoreLayerIndex(self): return 0


class FakeLevel:
    def __init__(self, name, element_id, elevation=0.0):
        self.Name, self.Id, self.Elevation = name, FakeId(element_id), elevation


class FakeWall:
    next_id = 100
    def __init__(self, doc, type_id, level_id, curve, height, offset, flip, structural):
        self.Id = FakeId(FakeWall.next_id); FakeWall.next_id += 1
        self.UniqueId = "uid-{}".format(self.Id.Value)
        self._type_id, self.LevelId = FakeId(type_id), FakeId(level_id)
        self.Location = types.SimpleNamespace(Curve=curve)
        self.Category, self.Flipped = FakeCategory(), bool(flip)
        self.parameters = {
            "height": FakeParameter(height), "offset": FakeParameter(offset),
            "top_level": FakeParameter(None), "top_offset": FakeParameter(0.0),
            "structural": FakeParameter(1 if structural else 0, "Integer"),
            "Mark": FakeParameter(None, "String"), "Comments": FakeParameter(None, "String")}
        doc.walls.append(self)
    def GetTypeId(self): return self._type_id
    def get_Parameter(self, key): return self.parameters.get(key)
    def LookupParameter(self, name): return self.parameters.get(name)


class FakeCollector:
    def __init__(self, doc): self.doc, self.kind = doc, None
    def OfClass(self, kind): self.kind = kind; return self
    def WhereElementIsElementType(self): return self
    def WhereElementIsNotElementType(self): return self
    def __iter__(self):
        if self.kind is FakeType: return iter(self.doc.types)
        if self.kind is FakeLevel: return iter(self.doc.levels)
        if self.kind is FakeWall: return iter(self.doc.walls)
        return iter([])


class FakeTransactionStatus:
    Started, Committed = "Started", "Committed"


class FakeTransaction:
    def __init__(self, doc, name): self.doc, self.name, self.status = doc, name, None
    def Start(self): self.snapshot = list(self.doc.walls); self.status = FakeTransactionStatus.Started
    def Commit(self):
        if self.doc.commit_status == "Pending": self.status = "Pending"
        else: self.status = FakeTransactionStatus.Committed
        return self.status
    def RollBack(self):
        if self.doc.rollback_error: raise RuntimeError("synthetic rollback failure")
        if not self.doc.rollback_unconfirmed: self.doc.walls[:] = self.snapshot; self.status = "RolledBack"
    def GetStatus(self): return self.status


class FakeDocument:
    IsFamilyDocument = False
    def __init__(self, fail_at=None, rollback_error=False, rollback_unconfirmed=False, commit_status=None):
        self.types = [FakeType("Basic 200", 20, 200.0 / 304.8)]
        self.levels = [FakeLevel("Level 01", 1, 0.0), FakeLevel("Level 02", 2, 10.0)]
        self.walls, self.fail_at, self.create_count = [], fail_at, 0
        self.rollback_error, self.rollback_unconfirmed = rollback_error, rollback_unconfirmed
        self.commit_status = commit_status
    def GetElement(self, element_id):
        key = int(element_id)
        for value in self.types + self.levels + self.walls:
            if int(value.Id) == key: return value
        return None


class FakeWallApi(FakeWall):
    @staticmethod
    def Create(doc, curve, type_id, level_id, height, offset, flip, structural):
        doc.create_count += 1
        if doc.fail_at == doc.create_count:
            raise RuntimeError("synthetic wall creation failure")
        return FakeWall(doc, type_id, level_id, curve, height, offset, flip, structural)


FakeWall.Create = staticmethod(FakeWallApi.Create)


class FakeRuntime:
    def __init__(self):
        self.receipts = {}
        self.identity = {"process_id": 1, "process_session_id": "proc", "application_session_id": "app",
                         "document_session_id": "doc", "document_title": "Disposable",
                         "document_path": "fixture://Disposable.rvt", "document_type": "project",
                         "revit_version": "2024", "identity_provider_version": 1,
                         "document_lifecycle_sequence": 1, "document_lifecycle_generation": 1,
                         "active_document_switch_counter": 0}
    def identity_response(self, _uiapp, _doc): return {"status": "success", "identity": self.identity}
    def get_execution_receipt(self, request_id): return self.receipts.get(request_id)
    def store_execution_receipt(self, request_id, request_hash, state, response):
        self.receipts[request_id] = {"request_hash": request_hash, "state": state, "response": response}


class FakeTaskDialog:
    answer = "Yes"
    def __init__(self, title): self.title = title
    def Show(self): return self.answer


def install_fake_revit():
    db = types.ModuleType("Autodesk.Revit.DB")
    db.BuiltInCategory = types.SimpleNamespace(OST_Walls=-2000011)
    db.BuiltInParameter = types.SimpleNamespace(WALL_USER_HEIGHT_PARAM="height", WALL_BASE_OFFSET="offset",
        WALL_STRUCTURAL_SIGNIFICANT="structural", WALL_HEIGHT_TYPE="top_level", WALL_TOP_OFFSET="top_offset",
        SYMBOL_NAME_PARAM="type_name")
    db.FilteredElementCollector, db.Level, db.Line = FakeCollector, FakeLevel, FakeLine
    db.Transaction, db.TransactionStatus, db.Wall, db.WallType = FakeTransaction, FakeTransactionStatus, FakeWall, FakeType
    db.XYZ, db.ElementId = FakeXYZ, FakeId
    ui = types.ModuleType("Autodesk.Revit.UI")
    ui.TaskDialog, ui.TaskDialogCommonButtons = FakeTaskDialog, types.SimpleNamespace(Yes=1, No=2)
    ui.TaskDialogResult = types.SimpleNamespace(Yes="Yes", No="No")
    revit = types.ModuleType("Autodesk.Revit")
    revit.DB, revit.UI = db, ui
    autodesk = types.ModuleType("Autodesk")
    autodesk.Revit = revit
    return {"Autodesk": autodesk, "Autodesk.Revit": revit,
            "Autodesk.Revit.DB": db, "Autodesk.Revit.UI": ui}


def build_request(runtime, doc, count=1):
    expected = {key: runtime.identity[key] for key in (
        "process_id", "process_session_id", "application_session_id", "document_session_id",
        "document_title", "document_path", "document_type", "revit_version",
        "identity_provider_version", "document_lifecycle_sequence",
        "document_lifecycle_generation", "active_document_switch_counter")}
    walls = []
    for index in range(count):
        walls.append({"planned_element_id": "planned-{}".format(index),
                      "source_review_item_id": "review-{}".format(index),
                      "wall_type_name": "Basic 200", "wall_type_id": "20", "base_level_name": "Level 01",
                      "base_constraint": {"element_id": "1", "name": "Level 01"},
                      "expected_thickness_internal_feet": 200.0/304.8,
                      "centerline_internal_feet": [[float(index*3), 0.0, 0.0], [float(index*3+2), 0.0, 0.0]],
                      "unconnected_height_internal_feet": 10.0, "top_constraint": None,
                      "top_offset_internal_feet": 0.0, "base_offset_internal_feet": 0.0,
                      "structural": False, "flip": False, "parameter_assignments": {"Mark": "W-{}".format(index)}})
    resolved = wall_execution._resolve_walls(doc, walls)
    return {"request_id": "request-1", "preview_id": "preview-1", "approval_text": "approved",
            "validation_warnings": [], "expected_identity": expected, "walls": walls,
            "validated_walls": resolved}


class WallTransactionTests(unittest.TestCase):
    def test_exactly_one_wall_commits_and_is_verified(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules):
            runtime, doc = FakeRuntime(), FakeDocument()
            uiapp = object()
            response = wall_execution.execute_walls(runtime, uiapp, doc, build_request(runtime, doc))
        self.assertEqual(response["status"], "success")
        self.assertTrue(response["committed"])
        self.assertEqual(len(doc.walls), 1)
        self.assertEqual(response["created_elements"][0]["category"], "Walls")
        self.assertEqual(response["created_elements"][0]["wall_type"], "Basic 200")
        self.assertEqual(response["created_elements"][0]["base_level"], "Level 01")
        self.assertTrue(response["created_elements"][0]["verified"])
        self.assertEqual(response["created_elements"][0]["unique_id"],
                         "uid-{}".format(response["created_elements"][0]["element_id"]))

    def test_top_constraint_and_offsets_are_assigned_and_verified(self):
        modules = install_fake_revit()
        payload = None
        with patch.dict(sys.modules, modules):
            runtime, doc = FakeRuntime(), FakeDocument()
            payload = build_request(runtime, doc)
            wall = payload["walls"][0]
            wall.pop("unconnected_height_internal_feet")
            wall.update(top_constraint={"element_id": "2", "name": "Level 02"},
                        top_offset_internal_feet=1.0, base_offset_internal_feet=0.5,
                        centerline_internal_feet=[[0, 0, 0.5], [2, 0, 0.5]])
            payload["validated_walls"] = wall_execution._resolve_walls(doc, payload["walls"])
            response = wall_execution.execute_walls(runtime, object(), doc, payload)
        self.assertEqual(response["status"], "success")
        record = response["created_elements"][0]
        self.assertEqual(record["top_constraint_level_id"], "2")
        self.assertEqual(record["top_offset_internal_feet"], 1.0)
        self.assertAlmostEqual(record["height_internal_feet"], 10.5)

    def test_wall_type_discovery_uses_walltype_elements(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules):
            doc = FakeDocument()
            doc.types[0].structure = FakeCompound()
            result = wall_execution.list_wall_types(FakeRuntime(), object(), doc)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["wall_types"][0]["element_id"], "20")
        self.assertEqual(result["wall_types"][0]["name"], "Basic 200")
        self.assertEqual(result["wall_types"][0]["wall_kind"], "Basic")
        self.assertAlmostEqual(result["wall_types"][0]["width_internal_feet"], 200.0/304.8)
        self.assertEqual(result["wall_types"][0]["function"], "Interior")
        self.assertEqual(result["wall_types"][0]["compound_structure"]["layers"][0]["function"], "Finish1")

    def test_wall_type_name_uses_symbol_name_parameter_when_element_name_throws(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules):
            doc = FakeDocument()
            doc.types = [FakeTypeWithBrokenName("Basic Wall 250", 23, 250.0 / 304.8)]
            with self.assertRaises(AttributeError):
                _ = doc.types[0].Name
            result = wall_execution.list_wall_types(FakeRuntime(), object(), doc)
        self.assertEqual(result["status"], "success")
        record = result["wall_types"][0]
        self.assertEqual(record["element_id"], "23")
        self.assertEqual(record["name"], "Basic Wall 250")
        self.assertEqual(record["name_source"], "SYMBOL_NAME_PARAM")
        self.assertEqual(record["unique_id"], "type-23")

    def test_wall_type_optional_property_failures_are_field_warnings(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules):
            doc = FakeDocument()
            doc.types = [FakeTypeWithOptionalFailures()]
            result = wall_execution.list_wall_types(FakeRuntime(), object(), doc)
        self.assertEqual(result["status"], "success")
        record = result["wall_types"][0]
        self.assertEqual(record["name"], "Optional Test Wall")
        self.assertIsNone(record["unique_id"])
        self.assertIsNone(record["wall_kind"])
        self.assertIsNone(record["width_internal_feet"])
        self.assertIsNone(record["function"])
        self.assertEqual(record["compound_structure"], {"status": "unavailable"})
        warning_fields = {warning["field"] for warning in record["field_warnings"]}
        self.assertTrue({"uniqueid", "kind", "width", "function", "compound_structure"}.issubset(warning_fields))
        self.assertEqual(len(result["warnings"]), len(record["field_warnings"]))

    def test_overlapping_existing_wall_is_rejected(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules):
            doc = FakeDocument()
            existing = FakeWall(doc, 20, 1, FakeLine(FakeXYZ(0, 0, 0), FakeXYZ(2, 0, 0)), 10, 0, False, False)
            existing.Id = FakeId(100)
            with self.assertRaises(ValueError) as caught:
                wall_execution._reject_existing_duplicates(doc, build_request(FakeRuntime(), doc)["walls"],
                                                           wall_execution._resolve_walls(doc, build_request(FakeRuntime(), doc)["walls"]))
        self.assertIn("duplicate", str(caught.exception))

    def test_invalid_wall_kind_level_id_and_planned_overlap_are_rejected(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules):
            doc = FakeDocument()
            walls = build_request(FakeRuntime(), doc)["walls"]
            doc.types[0].Kind = "Curtain"
            with self.assertRaises(ValueError) as caught:
                wall_execution._resolve_walls(doc, walls)
            self.assertEqual(str(caught.exception), "unsupported_wall_type_kind")
            doc.types[0].Kind = "Basic"
            walls[0]["base_constraint"]["element_id"] = "999"
            with self.assertRaises(ValueError):
                wall_execution._resolve_walls(doc, walls)
            walls[0]["base_constraint"]["element_id"] = "1"
            duplicate = dict(walls[0], planned_element_id="planned-other", source_review_item_id="review-other")
            duplicate["centerline_internal_feet"] = list(walls[0]["centerline_internal_feet"])
            with self.assertRaises(ValueError) as caught:
                wall_execution._reject_existing_duplicates(doc, [walls[0], duplicate],
                    wall_execution._resolve_walls(doc, [walls[0], duplicate]))
        self.assertEqual(str(caught.exception), "planned_wall_duplicate")

    def test_failure_in_batch_rolls_back_all_walls(self):
        modules = install_fake_revit()
        FakeTaskDialog.answer = "Yes"
        with patch.dict(sys.modules, modules):
            runtime, doc = FakeRuntime(), FakeDocument(fail_at=2)
            response = wall_execution.execute_walls(runtime, object(), doc, build_request(runtime, doc, count=2))
        self.assertEqual(response["status"], "error")
        self.assertFalse(response["committed"])
        self.assertEqual(doc.walls, [])
        self.assertEqual(runtime.receipts["request-1"]["state"], "complete")
        self.assertEqual(response["transaction"]["status"], "rolled_back")
        self.assertEqual(len(response["created_elements"]), 1)
        self.assertEqual(response["created_elements"][0]["transaction_outcome"], "rolled_back")
        self.assertTrue(response["created_elements"][0]["element_id"])

    def test_pending_commit_retains_created_ids_and_is_indeterminate(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules):
            runtime, doc = FakeRuntime(), FakeDocument(commit_status="Pending")
            response = wall_execution.execute_walls(runtime, object(), doc, build_request(runtime, doc))
        self.assertEqual(response["status"], "commit_pending")
        self.assertIsNone(response["committed"])
        self.assertEqual(len(response["created_elements"]), 1)
        self.assertTrue(response["created_elements"][0]["element_id"])

    def test_unconfirmed_rollback_retains_ids_and_does_not_claim_rollback(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules):
            runtime, doc = FakeRuntime(), FakeDocument(fail_at=2, rollback_unconfirmed=True)
            response = wall_execution.execute_walls(runtime, object(), doc,
                                                    build_request(runtime, doc, count=2))
        self.assertEqual(response["status"], "rollback_failed")
        self.assertIsNone(response["committed"])
        self.assertEqual(response["transaction"]["status"], "Started")
        self.assertEqual(len(response["created_elements"]), 1)
        self.assertTrue(response["created_elements"][0]["element_id"])
        self.assertEqual(response["created_elements"][0]["transaction_outcome"], "uncertain")

    def test_id_is_captured_before_parameter_assignment_failure(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules), patch.object(wall_execution, "_apply_parameters",
                                                            side_effect=RuntimeError("parameter failure")):
            runtime, doc = FakeRuntime(), FakeDocument()
            response = wall_execution.execute_walls(runtime, object(), doc, build_request(runtime, doc))
        self.assertEqual(response["transaction"]["status"], "rolled_back")
        self.assertEqual(len(response["created_elements"]), 1)
        self.assertEqual(response["created_elements"][0]["creation_state"], "rolled_back")
        self.assertEqual(response["created_elements"][0]["unique_id"], "uid-{}".format(response["created_elements"][0]["element_id"]))

    def test_id_is_captured_before_first_post_create_constraint_operation(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules), patch.object(FakeWall, "get_Parameter",
                                                            side_effect=RuntimeError("first post-create failure")):
            runtime, doc = FakeRuntime(), FakeDocument()
            payload = build_request(runtime, doc)
            payload["walls"][0].pop("unconnected_height_internal_feet")
            payload["walls"][0]["top_constraint"] = {"element_id": "2", "name": "Level 02"}
            payload["walls"][0]["top_offset_internal_feet"] = 0.0
            payload["validated_walls"] = wall_execution._resolve_walls(doc, payload["walls"])
            response = wall_execution.execute_walls(runtime, object(), doc, payload)
        self.assertEqual(response["transaction"]["status"], "rolled_back")
        self.assertEqual(len(response["created_elements"]), 1)
        self.assertTrue(response["created_elements"][0]["element_id"])

    def test_later_wall_creation_failure_preserves_only_returned_wall_ids(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules):
            runtime, doc = FakeRuntime(), FakeDocument(fail_at=2)
            response = wall_execution.execute_walls(runtime, object(), doc, build_request(runtime, doc, count=2))
        self.assertEqual(response["transaction"]["status"], "rolled_back")
        self.assertEqual(len(response["created_elements"]), 1)
        self.assertEqual(response["created_elements"][0]["planned_element_id"], "planned-0")

    def test_id_is_preserved_when_in_transaction_verification_fails(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules), patch.object(wall_execution, "_wall_record",
                return_value={"verified": False, "error": "synthetic verification failure"}):
            runtime, doc = FakeRuntime(), FakeDocument()
            response = wall_execution.execute_walls(runtime, object(), doc, build_request(runtime, doc))
        self.assertEqual(response["transaction"]["status"], "rolled_back")
        self.assertEqual(len(response["created_elements"]), 1)
        self.assertTrue(response["created_elements"][0]["element_id"])

    def test_id_is_preserved_when_post_commit_verification_fails(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules), patch.object(wall_execution, "_wall_record",
                side_effect=[{"verified": True, "element_id": "100"},
                             RuntimeError("post commit verification failed")]):
            runtime, doc = FakeRuntime(), FakeDocument()
            response = wall_execution.execute_walls(runtime, object(), doc, build_request(runtime, doc))
        self.assertEqual(response["status"], "verification_failed")
        self.assertTrue(response["committed"])
        self.assertEqual(response["created_elements"][0]["transaction_outcome"], "committed")
        self.assertTrue(response["created_elements"][0]["element_id"])

    def test_verification_endpoint_imports_wall_and_returns_actual_result(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules), patch.object(wall_execution, "_wall_record",
                return_value={"verified": True, "element_id": "100"}):
            runtime, doc = FakeRuntime(), FakeDocument()
            wall = FakeWall(doc, 20, 1, FakeLine(FakeXYZ(0, 0, 0), FakeXYZ(2, 0, 0)), 10, 0, False, False)
            wall.Id = FakeId(100)
            payload = {"expected_identity": build_request(runtime, doc)["expected_identity"],
                       "elements": [{"element_id": "100", "source_review_item_id": "review-0"}]}
            response = wall_execution.verify_walls(runtime, object(), doc, payload)
        self.assertEqual(response["status"], "success")
        self.assertTrue(response["elements"][0]["verified"])
        self.assertEqual(response["verified_count"], 1)

    def test_verification_endpoint_returns_structured_unexpected_error(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules), patch.object(wall_execution, "_wall_record",
                side_effect=RuntimeError("synthetic read failure")):
            runtime, doc = FakeRuntime(), FakeDocument()
            wall = FakeWall(doc, 20, 1, FakeLine(FakeXYZ(0, 0, 0), FakeXYZ(2, 0, 0)), 10, 0, False, False)
            wall.Id = FakeId(100)
            payload = {"expected_identity": build_request(runtime, doc)["expected_identity"],
                       "elements": [{"element_id": "100"}]}
            response = wall_execution.verify_walls(runtime, object(), doc, payload)
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "synthetic read failure")

    def test_wall_interaction_classification_and_manual_review(self):
        def line(a, b): return (FakeXYZ(a[0], a[1], 0), FakeXYZ(b[0], b[1], 0))
        classify = wall_execution._classify_wall_interaction
        self.assertEqual(classify(line((0, 0), (2, 0)), line((0, 0), (2, 0)), .2, .2), "duplicate")
        self.assertEqual(classify(line((0, 0), (3, 0)), line((1, 0), (4, 0)), .2, .2), "collinear_overlap")
        self.assertEqual(classify(line((0, 0), (2, 0)), line((2, 0), (2, 2)), .2, .2), "l_junction_manual_review")
        self.assertEqual(classify(line((0, 0), (2, 0)), line((1, 0), (1, 2)), .2, .2), "t_junction_manual_review")
        self.assertEqual(classify(line((0, 0), (2, 0)), line((2, 0), (4, 0)), .2, .2), "endpoint_to_endpoint_junction_manual_review")
        self.assertEqual(classify(line((0, 0), (2, 2)), line((0, 2), (2, 0)), .2, .2), "unintended_intersection")
        self.assertEqual(classify(line((0, 0), (2, 0)), line((0, 2), (2, 2)), .2, .2), "separate")
        with self.assertRaisesRegex(ValueError, "manual_review_required"):
            wall_execution._raise_for_wall_interaction("l_junction_manual_review", "planned")

    def test_revit_dialog_decline_creates_no_wall(self):
        modules = install_fake_revit()
        FakeTaskDialog.answer = "No"
        try:
            with patch.dict(sys.modules, modules):
                runtime, doc = FakeRuntime(), FakeDocument()
                response = wall_execution.execute_walls(runtime, object(), doc, build_request(runtime, doc))
            self.assertEqual(response["status"], "cancelled")
            self.assertEqual(doc.walls, [])
        finally:
            FakeTaskDialog.answer = "Yes"

    def test_same_request_is_idempotent_within_revit_session(self):
        modules = install_fake_revit()
        with patch.dict(sys.modules, modules):
            runtime, doc = FakeRuntime(), FakeDocument()
            payload = build_request(runtime, doc)
            first = wall_execution.execute_walls(runtime, object(), doc, payload)
            second = wall_execution.execute_walls(runtime, object(), doc, payload)
        self.assertEqual(first, second)
        self.assertEqual(len(doc.walls), 1)


if __name__ == "__main__":
    unittest.main()
