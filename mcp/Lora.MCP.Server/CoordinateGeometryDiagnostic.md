# Coordinate and Geometry Diagnostic

Milestone 4C.2C adds two read-only MCP tools backed by routes in the Lora identity sidecar. The diagnostic runs in Revit's pyRevit Routes context, uses the active document supplied by the route bridge, and checks the existing Revit-side session identity before inspection. It does not open a transaction or modify, reload, or save anything.

## Tools and routes

| MCP tool | Sidecar route | Behavior |
|---|---|---|
| `lora_get_coordinate_diagnostic` | `GET /lora-identity-v1/coordinate_diagnostic` | Reads Internal Origin, Project Base Point, Survey Point, active ProjectLocation, and transform cross-checks. |
| `lora_get_geometry_inventory(offset=0, limit=50, failure_offset=0, failure_limit=50)` | `POST /lora-identity-v1/geometry_inventory` | Scans non-type host-document elements, returns a bounded element page plus an independently paged failure list, and reports document-wide scan counts. Each page size is at most 100. |

The Lora MCP client sends these only to the already pinned `127.0.0.1` Routes endpoint, brackets the call with active document identity reads, and rejects a response whose process/application/document session does not match. No category names are used for coordinate landmarks.

## Coordinate conventions

- Revit point coordinates are returned in feet in `raw_feet` and converted using exactly 304.8 mm/ft. Each point states its coordinate reference.
- `ProjectPosition` east/west, north/south, and elevation values are returned in feet and millimetres; its angle is returned in radians and degrees.
- Internal Origin uses `InternalOrigin.Get(doc)`, with both its internal position and active-location shared position.
- Project Base Point and Survey Point use `BasePoint.GetProjectBasePoint()` and `BasePoint.GetSurveyPoint()`; position, shared position, element ID, and clipped state are guarded independently.
- The active location is read from `Document.ActiveProjectLocation`. The diagnostic calls `GetProjectPosition(XYZ.Zero)` and inspects `GetTransform()` basis/origin.
- Transform direction remains `unverified` unless applying the transform or its inverse to the internal origin matches `InternalOrigin.SharedPosition`. BasePoint transform results are also compared with each point's `SharedPosition`. Mismatches are warnings; direction is never inferred from a category name or label.
- The `GetProjectPosition(XYZ.Zero)` report is retained as an independent active-location datum. Its EastWest/NorthSouth report fields are not silently equated with XYZ axes.

Autodesk documents InternalOrigin and BasePoint coordinate properties, and documents passing `XYZ.Zero` to `ProjectLocation.GetProjectPosition` to inspect the active location's transformation: [Revit 2024 Place and Locations API guide](https://help.autodesk.com/cloudhelp/2024/ENU/Revit-API/files/Revit_API_Developers_Guide/Advanced_Topics/Place_and_Locations/Revit_API_Revit_API_Developers_Guide_Advanced_Topics_Place_and_Locations_Place_html.html).

## Geometry inventory and completeness

The inventory uses `FilteredElementCollector(document).WhereElementIsNotElementType()` and processes elements individually. Model-category instances are classified separately from view-specific elements, annotation/detail elements, non-model elements, and Revit link instances. Each record has the same type, level, location, bounding-box and geometry-inspection status fields (`not_inspected`, `inspected`, `not_applicable`, `unavailable`, or `failed`), plus an individual warning list. Inspected model instances can include type element ID, family/type names, LevelId/name, LocationCurve endpoints and length, LocationPoint, and model bounding box. API failures become per-element warnings or failure records and do not abort the remainder of the scan.

The collector scan is capped at 100,000 non-type elements per request. Revit link instances use `GetLinkDocument()` plus `RevitLinkType.GetLinkedFileStatus()` for loaded/unloaded/file status. Loaded linked documents are scanned once per link document and summarized in their own internal coordinate systems; their link-instance transform is returned without applying it to each extent. Linked instances beyond the 200 unique-document scan cap, or summaries beyond the 100-record response cap, are marked truncated/uninspected. The result separates:

- Element `offset`/`limit` and failure `failure_offset`/`failure_limit` are independent; each page size is capped at 100. `failure_has_more` is calculated against the failure page.
- `element_scan_complete`: whether the host collector enumeration finished (and agrees with its reported count when available).
- `is_complete`: whether the host scan finished and no truncation, unsupported/failed geometry, or uninspected links remain.
- `truncated`: whether host scanning or linked-document reporting reached a cap.
- Counts for examined, physical model instances, successfully inspected, unsupported, failed, view-specific, annotation/detail, other/nonphysical, and link instances.
- Loaded linked document summaries in linked-document internal coordinates, with the link instance transform and per-link completeness. Unloaded links are reported as unavailable.
- `has_uninspected_links`, `has_unsupported_elements`, and `has_failed_elements`.

Linked-document element locations are extracted for per-document coverage counts but individual linked element records are not included in the host `items` page. The inventory also does not transform every linked extent into host coordinates or run a proposed-wall clash test. A loaded link can have a complete linked-document scan while host-coordinate clearance remains unverified. Missing locations/bounds are unsupported evidence, never empty space.

`clearance_status` is deliberately always `unverified` in this milestone. The inventory is not an extent-specific collision test and does not certify any wall location as clear.

## API compatibility and verification

The implementation uses guarded access for optional properties and is written in the existing IronPython-compatible sidecar style. Reflection against the installed Revit 2024 `RevitAPI.dll` confirmed the exact signatures used for `InternalOrigin.Get(Document)`, `BasePoint.GetProjectBasePoint(Document)`, `BasePoint.GetSurveyPoint(Document)`, `ProjectLocation.GetProjectPosition(XYZ)`, `ProjectLocation.GetTransform()`, and `RevitLinkType.GetLinkedFileStatus()`, plus the LocationCurve/LocationPoint members and collector count. The Revit 2023 directory did not contain a RevitAPI assembly, and Revit 2025/2026 assemblies are not installed here. Autodesk/Revit API references document InternalOrigin and BasePoint accessors as predating these target versions (InternalOrigin since Revit 2021; BasePoint accessors since Revit 2020.2), but automated mocks and reference inspection do not establish host-runtime compatibility. Revit 2024 live verification and Revit 2023/2025/2026 runtime compatibility remain unverified and deployment is pending approval.

## Tests

`tests/test_coordinate_geometry.py` covers Internal Origin/BasePoint access, active location and transform direction, failed transform cross-checks, unit conversions, LocationCurve/LocationPoint/bounding boxes, unsupported and failed elements, loaded/unloaded links, bounded paging, completeness flags, and clearance remaining unverified. `tests/test_read_only.py` and `tests/test_context_engine.py` verify registration and the one destructive tool invariant.
