# Development Branch Change Summary

This is a source change summary for review, not a published release note. No production installer or Revit deployment is included.

## Revit context and geometry evidence

- Added identity-guarded level catalog, category inventory and selected-element geometry tools with independent pagination, explicit field statuses, bounded output and failure reporting.
- Category inventory maps supported BuiltInCategory values to broad architectural, structural and MEP disciplines. Unmapped/ambiguous categories remain explicit; category membership is not a physical-clearance claim.
- Selected geometry extraction requires explicit element IDs and returns bounded solids, meshes, faces, edges, curves, location/bounding-box status and nested transform provenance. Linked-model geometry is not traversed.
- Coordinate transform remains **UNVERIFIED** pending independent surveyed controls. Geometry APIs do not implement clash or clearance analysis.

## Drawing, recognition and review

- Added bounded PDF/raster drawing processing, vector/raster geometry and text candidates, and deterministic architectural recognition hypotheses.
- Added page-local calibration from explicit scales or user-confirmed references, local review/approval persistence, and drawing render retrieval.
- OCR and drawing libraries are optional dependencies. Native DWG extraction is not supported. Recognition output requires human review and does not create BIM elements.

## Planning and execution

- Added Revit context routing, generation planning and stable preview snapshots with live-inventory and identity provenance.
- Controlled MCP execution remains limited to Basic Walls and is guarded by plan validation, explicit human approval, identity checks and transaction/result reporting. This capability has not been validated in a live Revit host.

## Family audit

- Added Family Version Audit for RFA metadata batches/folder scans and a bounded content inventory for manual compatibility review.
- Content analysis does not emulate a target Revit version and does not downgrade/reconstruct families. It temporarily opens and closes an inspected family without saving; this behavior requires isolated host validation.

## Verification and limits

- Current proposal validation: 303 CPython tests passed with no skips, CPython compilation passed, and 31 Revit-side Python files parsed under IronPython 2.7.12PR and 3.4.2.
- These are source/mock/parser checks. Revit 2023–2026 host compatibility remains unverified; Revit 2024 API metadata inspection is not a host test.
- The MCP server is not read-only as a whole, the read-only artifact does not isolate against co-loaded extensions, coordinate placement remains unverified, and clearance is not implemented.
