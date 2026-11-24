import ast
import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))

from LoraFamilyCompatibility import analyze_family_document


class FakeDefinition(object):
    def __init__(self, name):
        self.Name = name


class FakeFamilyParameter(object):
    def __init__(self, name, storage="Double", shared=False, formula=None,
                 associated=None):
        self.Definition = FakeDefinition(name)
        self.IsShared = shared
        self.GUID = "11111111-2222-3333-4444-555555555555" if shared else None
        self.IsInstance = False
        self.IsReporting = False
        self.StorageType = storage
        self.Formula = formula
        self.IsDeterminedByFormula = bool(formula)
        self.CanAssignFormula = True
        self.AssociatedParameters = associated or []


class ThrowingFormulaParameter(FakeFamilyParameter):
    @property
    def Formula(self):
        raise RuntimeError("formula getter failed")

    @Formula.setter
    def Formula(self, value):
        pass


class FakeFamilyType(object):
    def __init__(self, name, values):
        self.Name = name
        self._values = values

    def HasValue(self, parameter):
        return parameter.Definition.Name in self._values

    def AsDouble(self, parameter):
        return self._values[parameter.Definition.Name]

    def AsInteger(self, parameter):
        return self._values[parameter.Definition.Name]

    def AsString(self, parameter):
        return self._values[parameter.Definition.Name]

    def AsElementId(self, parameter):
        return self._values[parameter.Definition.Name]


class FakeFamilyManager(object):
    def __init__(self, types, parameters):
        self.Types = types
        self.Parameters = parameters


class FakeCategory(object):
    def __init__(self, name="Generic Models"):
        self.Name = name
        self.Id = 42
        self.BuiltInCategory = "OST_GenericModel"
        self.SubCategories = [FakeCategoryChild("Nested Lines")]


class FakeCategoryChild(object):
    def __init__(self, name):
        self.Name = name


class FakeOwnerFamily(object):
    def __init__(self):
        self.FamilyCategory = FakeCategory()
        self.FamilyPlacementType = "WorkPlaneBased"


class FakeVisibility(object):
    IsShownInFine = True
    IsShownInMedium = True
    IsShownInCoarse = False
    IsShownInLeftRight = True
    IsShownInFrontBack = True
    IsShownInTopBottom = True
    IsShownInPlanRCPCut = True
    IsShownOnlyWhenCut = False


class FakeFace(object):
    MaterialElementId = "mat-1"


class FakeSolid(object):
    Volume = 100.0
    Faces = [FakeFace()]


class FakeMesh(object):
    pass


class FakeGeometryInstance(object):
    def GetSymbolGeometry(self):
        return [FakeSolid()]


class FakeForm(object):
    Name = "Extrusion"
    IsSolid = True
    Visible = True
    Subcategory = FakeCategoryChild("Furniture")

    def GetVisibility(self):
        return FakeVisibility()

    def get_Geometry(self, options):
        return [FakeSolid(), FakeMesh(), FakeGeometryInstance()]


class FakeReferencePlane(object):
    Name = "Center (Left/Right)"


class FakeDimension(object):
    IsLocked = True
    FamilyLabel = FakeFamilyParameter("Width")


class FakeNestedFamily(object):
    Name = "Nested Chair"
    IsShared = True


class FakeSymbol(object):
    Family = FakeNestedFamily()


class FakeFamilyInstance(object):
    Symbol = FakeSymbol()
    Host = object()


class FakeConnectorElement(object):
    Domain = "Piping"
    SystemClassification = "SupplyHydronic"
    Shape = "Round"
    IsPrimary = True
    Radius = 0.5
    Width = 1.0
    Height = 1.0


class FakeMaterial(object):
    Name = "Concrete"


class FakeOptions(object):
    IncludeNonVisibleObjects = False


class FakeCollector(object):
    def __init__(self, document):
        self.document = document

    def OfClass(self, element_class):
        return [item for item in self.document.elements if isinstance(item, element_class)]


class FakeViewDetailLevel(object):
    Fine = "Fine"


class FakeDB(object):
    GenericForm = FakeForm
    Solid = FakeSolid
    Mesh = FakeMesh
    GeometryInstance = FakeGeometryInstance
    ReferencePlane = FakeReferencePlane
    Dimension = FakeDimension
    FamilyInstance = FakeFamilyInstance
    ConnectorElement = FakeConnectorElement
    FilteredElementCollector = FakeCollector
    Options = FakeOptions
    ViewDetailLevel = FakeViewDetailLevel


class FakeDocument(object):
    IsFamilyDocument = True
    IsModified = False

    def __init__(self, parameters=None):
        self.OwnerFamily = FakeOwnerFamily()
        self.FamilyManager = FakeFamilyManager(
            [FakeFamilyType("Default", {"Width": 12.5})],
            parameters or [FakeFamilyParameter("Width", formula="Height * 2", shared=True),
                           FakeFamilyParameter("Height")])
        self.elements = [FakeForm(), FakeReferencePlane(), FakeDimension(),
                         FakeFamilyInstance(), FakeConnectorElement()]

    def GetElement(self, element_id):
        if element_id == "mat-1":
            return FakeMaterial()
        return None


class FamilyCompatibilityTests(unittest.TestCase):
    def test_all_inventory_areas_are_inspected_and_require_manual_review(self):
        width = FakeFamilyParameter("Width", formula="Height * 2", shared=True,
                                    associated=[FakeFamilyParameter("WidthAssoc")])
        height = FakeFamilyParameter("Height")
        doc = FakeDocument([width, height])
        result = analyze_family_document(doc, FakeDB, 2024, 2024, "2024")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["compatibility_classification"], "requires_manual_review")
        self.assertEqual(set(result["fields"]), {
            "category", "types", "parameters", "geometry", "materials_subcategories",
            "visibility", "references_constraints", "nested_families", "hosting", "mep_connectors"})
        self.assertEqual(result["fields"]["category"]["records"][0]["name"], "Generic Models")
        self.assertEqual(result["fields"]["types"]["records"][0]["name"], "Default")
        self.assertEqual(result["fields"]["types"]["records"][0]["parameter_values"][0]["value"], 12.5)
        width_record = next(item for item in result["fields"]["parameters"]["records"]
                            if item.get("name") == "Width")
        self.assertEqual(width_record["guid"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(width_record["formula"], "Height * 2")
        self.assertEqual(width_record["associated_parameter_names"], ["WidthAssoc"])
        self.assertEqual(result["fields"]["geometry"]["summary"]["positive_volume_solids"], 2)
        self.assertEqual(result["fields"]["geometry"]["summary"]["meshes"], 1)
        self.assertTrue(any(item.get("kind") == "material" and item.get("name") == "Concrete"
                            for item in result["fields"]["materials_subcategories"]["records"]))
        self.assertTrue(result["fields"]["visibility"]["records"][0]["IsShownInFine"])
        self.assertEqual(result["fields"]["references_constraints"]["summary"]["locked_dimensions"], 1)
        self.assertTrue(any(item.get("name") == "Nested Chair"
                            for item in result["fields"]["nested_families"]["records"]))
        self.assertEqual(result["fields"]["hosting"]["records"][0]["family_placement_type"], "WorkPlaneBased")
        self.assertEqual(result["fields"]["mep_connectors"]["records"][0]["domain"], "Piping")
        self.assertFalse(result["source_modified"])
        self.assertFalse(doc.IsModified)

    def test_newer_saved_format_is_the_only_automatic_incompatibility_rule(self):
        newer = analyze_family_document(FakeDocument(), FakeDB, 2023, 2024, "2024")
        self.assertEqual(newer["compatibility_classification"], "incompatible")
        self.assertEqual(newer["compatibility_reason"], "saved_format_newer_than_target")
        older = analyze_family_document(FakeDocument(), FakeDB, 2024, 2024, "2023")
        self.assertEqual(older["compatibility_classification"], "requires_manual_review")
        self.assertEqual(older["compatibility_reason"], "content_compatibility_not_proven")

    def test_one_getter_failure_is_field_local_and_does_not_hide_other_inventory(self):
        doc = FakeDocument([ThrowingFormulaParameter("Width"), FakeFamilyParameter("Height")])
        result = analyze_family_document(doc, FakeDB, 2024, 2024, "2024")
        self.assertEqual(result["fields"]["parameters"]["status"], "partial")
        self.assertTrue(any(item["field"] == "FamilyParameter.Formula" and
                            item["status"] == "failed" for item in result["fields"]["parameters"]["warnings"]))
        self.assertEqual(result["fields"]["category"]["status"], "inspected")
        self.assertEqual(result["fields"]["geometry"]["status"], "inspected")
        self.assertEqual(result["fields"]["mep_connectors"]["status"], "inspected")
        self.assertEqual(result["compatibility_classification"], "requires_manual_review")

    def test_non_family_document_fails_closed_without_inspecting_content(self):
        doc = FakeDocument()
        doc.IsFamilyDocument = False
        result = analyze_family_document(doc, FakeDB, 2024, 2024, "2024")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["fields"], {})
        self.assertEqual(result["compatibility_classification"], "requires_manual_review")

    def test_ui_and_analyzer_do_not_create_transactions_or_save_documents(self):
        analyzer_path = REPOSITORY_ROOT / "lib" / "LoraFamilyCompatibility.py"
        tree = ast.parse(analyzer_path.read_text(encoding="utf-8"))
        names = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        forbidden = {"Transaction", "SubTransaction", "TransactionGroup", "Save", "SaveAs",
                     "LoadFamily", "NewFamilyDocument", "Delete", "Commit"}
        self.assertFalse(names.intersection(forbidden))


if __name__ == "__main__":
    unittest.main()
