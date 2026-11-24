# Family Version and Compatibility Audit

## Scope

The Family Version Audit command has three read-only modes:

1. Select multiple `.rfa` files and compare header format metadata.
2. Recursively scan a folder for `.rfa` headers.
3. Select one family, choose a target Revit year (2023–2026), and inspect selected family-document content for manual review.

The metadata modes use `Autodesk.Revit.DB.BasicFileInfo`. The content mode opens a temporary family document using the running Revit process, runs a bounded inventory, then calls `Document.Close(False)`. The command rejects a file whose header is newer than the running Revit host. It also refuses to inspect a file that is already open in the current process. The current Revit API may upgrade an older family in memory while opening it; the command never saves that document. The content report therefore describes the running host's in-memory API representation and cannot prove how the same content behaves in the selected target version.

No command path starts a `Transaction`, saves or loads a family, creates an output family, or writes to a project document. The preview choices are descriptive only: manual reconstruction review or retain the source family. Neither choice executes.

## Reported content

The content inventory hashes the selected file before and after opening it and shows both SHA256 values. A hash mismatch marks the result partial and warns the user not to treat the audit as read-only verified. The content inventory reports:

- Family category name, element ID and BuiltInCategory.
- Family type names and values for supported parameter storage types, subject to count limits.
- Parameter names, shared GUIDs, instance/reporting flags, storage type, formulas, formula-driven state, formula assignment capability and associated parameter names where exposed by the API.
- `GenericForm` geometry summaries: solid/mesh/geometry-instance counts and positive-volume solid counts. Symbol geometry is traversed to a bounded depth; the report does not certify family geometry completeness.
- Family-category subcategories and material element IDs/names referenced by inspected solid faces.
- `GenericForm` visibility flags for detail level, orientation and cut display.
- `ReferencePlane` names and `Dimension` lock/family-label information.
- Nested `FamilyInstance` family names, shared status where exposed, family placement type and nested instance host presence.
- `ConnectorElement` domain, system classification, shape, primary status and dimensional fields where available.

Each group has an inspection status and warnings. Missing API members are represented as unavailable where detected; getter/enumeration failures are reported separately. Record, geometry-depth and geometry-object limits are explicit in warnings. If a category is not part of the defined inspection scope, it is not claimed complete.

## Compatibility classification rules

Allowed overall classifications are `compatible`, `incompatible`, `requires_reconstruction`, `requires_manual_review` and `not_inspected`.

- `incompatible` is used only when `BasicFileInfo.Format` proves that the saved family major format is newer than the selected target major version.
- `requires_manual_review` is used for all other cases. This includes matching/older headers, missing target API assemblies, partial content inventory, unenumerated feature classes and any unvalidated content behavior.
- `compatible` is not currently emitted by the analyzer. A matching major version is not proof that all family content or behavior is compatible.
- `requires_reconstruction` is reserved for a future analysis that identifies specific content with a tested reconstruction method. No such reconstruction is implemented.
- `not_inspected` applies when header/API preflight prevents content inspection.

The selected target year is a reporting input only. The current host does not emulate another Revit release. No API member-presence table exists for 2023, 2025 or 2026, and no representative target-version fixture matrix has been run.

## Revit API evidence

Reflection against the locally installed Revit 2024 `RevitAPI.dll` found the API members used by this source, including `Family.FamilyCategory`, `Family.FamilyPlacementType`, `Document.FamilyManager`, `FamilyManager.Types/Parameters`, `FamilyParameter.GUID/Formula/AssociatedParameters`, `FamilyType.AsDouble/AsInteger/AsString/AsElementId/HasValue`, `GenericForm.GetVisibility`, `GenericForm.get_Geometry`, `ReferencePlane`, `Dimension.IsLocked/FamilyLabel`, `ConnectorElement` domain/shape/system fields, `Application.OpenDocumentFile`, and `Document.Close(Boolean)`. This is API metadata verification only. It does not establish that the content collectors return expected results or that `OpenDocumentFile` behaves correctly in a pyRevit command. No live Revit test was performed.

## Migration and reconstruction decision

Native backward saving of a newer `.rfa` into an older Revit format is unsupported. `Document.SaveAs` does not select a previous major family format. The command does not attempt this.

Potential future strategies are separate workflows with different loss profiles:

- **Manual rebuild in the target Revit version:** create a new family from a target-version template and recreate geometry, parameters, formulas, constraints, hosting, nested families, materials, visibility and connectors. This is not an automatic conversion and needs explicit preservation comparisons.
- **Geometry transfer (SAT or another neutral format):** may transfer selected geometry but loses or changes parametric behavior, reference planes, constraints, hosting, nested-family semantics, connectors, formulas, materials/subcategories and visibility. It cannot be called family-compatible without extensive reconstruction and comparison.
- **IFC/DWG transfer:** useful for limited geometry exchange, but it does not preserve a native family definition or the full Revit family behavior required here.

No strategy is implemented because there is no complete target-version Revit environment, accepted loss policy, representative fixture family set, output/rollback design or live comparison evidence. Any future conversion must use a copy in an isolated test environment, preserve and hash the source, write to a new non-existing output path, validate in the target Revit version and never overwrite the source. User approval is required before family-writing execution is introduced.

## Required validation before H2/H3 completion

1. Test the command in Revit 2024 against disposable families covering types/values, shared parameters/GUIDs, formulas/associations, nested/shared families, hosted/workplane families, geometry forms, material/subcategory assignments, visibility, reference planes/dimensions and MEP connectors.
2. Confirm each API property/collector result and ensure the family document closes successfully with `Close(False)` after normal, partial and exceptional scans. Hash input files before/after these tests.
3. Add fixture families for corrupt/unsupported headers and geometry/collector failures. Verify errors remain field-local and never become a compatibility pass.
4. Obtain Revit 2023, 2025 and 2026 APIs/hosts and repeat parser, API, UI and representative behavior tests separately.
5. Before reconstruction work, approve a field-by-field preservation/loss matrix and require target-host reopen/validation plus a complete output rollback procedure.
