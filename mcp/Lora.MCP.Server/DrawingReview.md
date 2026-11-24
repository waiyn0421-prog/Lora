# Drawing Review and Approval

Milestone 3E adds a structured review API over existing recognition and calibration records. It does not modify Revit and stores no original PDF or image bytes. The current MCP server has no interactive drawing canvas; clients can call `lora_get_drawing_review` for session/page metadata, use the existing `lora_get_drawing_render(processing_id, page_number)` for the cached image, and overlay review-item source or calibrated geometry.

## Persistence

Review sessions, generation plans and generation previews persist in a schema-versioned SQLite database (database schema version 4; review snapshot format version 1) at:

```text
%LOCALAPPDATA%\Lora\MCP\reviews.sqlite3
```

If `LOCALAPPDATA` is unavailable, the platform's user-local data directory is used. `LORA_REVIEW_DB` can select a different local database file before starting the server. The MCP tool does not accept a path. SQLite transactions and `synchronous=FULL` protect completed changes. Database schema version is stored in `PRAGMA user_version` (currently 4). Newer schemas are rejected without alteration. Corruption is reported and the original file is preserved; the server does not silently recreate or replace it.

Back up the database by stopping the MCP server and copying `reviews.sqlite3` to a protected local backup location. Restore by stopping the server and replacing the file with the backup. Drawings are not included; sessions refer to source filename and processing ID. The drawing render cache remains process-local, so after restart the source must be processed again to display a render; persisted review geometry/history remains available.

## Data and status

Each item has a stable UUID, session/source/page identity, original geometry references, original source geometry, optional calibrated geometry and calibration fingerprint, recognition type/evidence/confidence, current and original properties, current geometry, corrections, warnings, reviewer, status, and persistent event history. Items start `pending`; recognition never approves an item. Supported review categories are Walls, Doors, Windows, Columns, Rooms, Dimensions, Levels, Grids, Openings, and Other. Ambiguous door/window openings remain `Openings` until corrected.

Statuses are `pending`, `approved`, `rejected`, `needs_correction`, and `unresolved`. Approve/reject are explicit operations; rejection requires a reason. Duplicate approval is idempotent and adds no duplicate history event. Rejected or unresolved items must be returned to pending before approval. Generic update can set pending, needs_correction, or unresolved but cannot set approved/rejected. All original properties and geometry remain unchanged by corrections; current corrections are separately recorded in both item data and history.

Only rows whose status is `approved` are in the approved collection. Summary distinguishes approved items with current calibration from those missing, incomplete, or stale. Stale items remain flagged and are excluded from current-calibration readiness. Approval is blocked for stale items; missing/incomplete calibration permits explicit review approval but returns a warning and is not considered ready.

## Calibration invalidation

At session creation, the manager stores per-page calibration IDs and a fingerprint of the transform, scales, target units, references, and completeness. `lora_validate_drawing_review` compares supplied current calibration results. A changed or newly added calibration flags all affected page items as stale, records history, and retains original/current reviewer geometry without rewriting it. The session advances its comparison snapshot to avoid duplicate change events. Stale items cannot be approved again; create a new review session to review changed calibrated geometry. If no current calibration is supplied, validation reports the comparison as unavailable and assumes no change.

## Tools

| Tool | Purpose |
|---|---|
| `lora_create_drawing_review` | Create a persistent session from an existing recognition ID and optional calibration IDs. |
| `lora_get_drawing_review` | Retrieve session summary, pages, and render-tool reference. |
| `lora_get_review_items` | Filter by status/category/page and paginate. |
| `lora_get_review_item` | Retrieve one item and its item-specific history. |
| `lora_update_review_item` | Validate property/geometry corrections or set pending/needs_correction/unresolved. |
| `lora_approve_review_item` | Explicit approval. |
| `lora_reject_review_item` | Rejection with required reason. |
| `lora_get_review_history` | Paginate session or item history. |
| `lora_get_review_summary` | Count by status/category and calibration readiness. |
| `lora_validate_drawing_review` | Compare calibration fingerprints and persist stale flags. |

## Limits

- Reviewer identity is supplied by the caller; the MCP server does not authenticate a human reviewer.
- Review property edits are limited to category, label, name, mark, type name, fire rating, comments, and dimensions. Geometry corrections accept finite source/calibrated points, polylines, explicitly closed polygons, or bounding boxes.
- Calibration and recognition caches are process-local. A session persists after restart, but new calibration comparison requires a current calibration result in the active process. Reprocessing can produce new IDs; users must create a new review session for new recognition data.
- Approval records human intent supplied through the MCP call. It does not certify code/regulatory compliance and does not trigger Revit generation.
