# Revit Generation Planning (Milestone 4A)

The generation planner converts explicitly approved drawing review records into a versioned, deterministic proposal. It does not execute the proposed operations. No Revit create, update, delete, selection, or transaction route is registered or called.

## Flow and traceability

`approved review item + current calibration + explicit mapping + read-only active-document inventory → persisted plan`

Each plan stores its source review and drawing/page references, original review item IDs and geometry references, calibration ID/fingerprint, recognition evidence, target document identity, inventory snapshot, operation, dimensions, mapping, dependencies, validation results, warnings and timestamps. Non-approved, stale, missing-calibration and incomplete-calibration records remain listed as unresolved and block readiness. Openings remain unsupported until a reviewer explicitly classifies them as Doors or Windows.

## Planned categories

| Category | Planned operation | Key requirements |
|---|---|---|
| Walls | `create_wall` | Current approved calibration, centerline, positive thickness, explicit level and exact observed system type |
| Doors | `create_hosted_door` | Explicit category, unique loaded family symbol ID, level, host planned Wall, positive width and height |
| Windows | `create_hosted_window` | Same explicit symbol, level, host and dimension requirements as Doors |
| Columns | `create_column` | Unique loaded structural-column symbol ID, level, calibrated position and dimensions where available |
| Rooms | `place_room` | Closed calibrated boundary polygon and explicit level; boundary planning only |
| Levels | `create_level` | Explicit elevation with explicit supported unit |
| Grids | `create_grid` | Reviewed nonzero line and explicit grid name; always warns that recognition is not confirmed |

No arbitrary Revit type is selected. Names are matched exactly. Family symbols require a unique symbol ID if family/type names are ambiguous. Type catalog results can be incomplete. The identity-guarded `lora_get_level_catalog` sidecar route reads exact Level element IDs, names and elevations using independent element and failure pages. Generation inventory walks all Level pages and failure pages and requires stable totals and identity across reads before setting `level_ids_available`; incomplete or unavailable catalogs retain the older statistics result and cannot certify IDs as complete. This remains read-only discovery and does not make plans executable.

## Units and coordinates

Calibrated geometry keeps its source coordinate references and is converted to Revit internal feet only when its source unit is explicitly supported (`mm`, `cm`, `m`, `in`, `ft`). No default unit is inferred. User-confirmed placement mapping accepts an internal-feet origin and rotation; it produces a 4×4 rigid XY rotation/translation matrix. Without it, geometry remains drawing-local and Revit placement coordinates are null with a warning. Project Base Point and Survey Point values remain unavailable because no confirmed read-only route supplies them. No shared coordinates are invented.

Dimensions supplied as reviewer mappings are converted only when the unit is explicit; otherwise an existing calibrated source unit is required. A wall centerline can be derived by pairing two calibrated line endpoints only when both lines exist; unmatched or uncertain geometry is blocked. Broad architectural generation beyond the separately guarded Basic Wall execution workflow is not supported.

## Validation and state

Validation records errors separately from warnings: missing approval/calibration, changed document identity, missing levels/types/symbols, invalid geometry or dimensions, missing host dependencies, duplicate placements, unconfirmed coordinates and inventory limitations. Dependency ordering is deterministic: Levels, Grids, Walls, Columns, Doors/Windows, Rooms. A plan can be `blocked`, `planned`, `planned_with_warnings`, or `ready_for_review`; the last state is not permission or support to execute.

Type/instance parameter scope and writability are not available from the schema-name snapshot. User parameter assignments are retained as proposed data, with a warning when the name is absent from the snapshot or scope cannot be verified.

## MCP tools

- `lora_create_generation_plan`
- `lora_get_generation_plan`
- `lora_validate_generation_plan`
- `lora_get_generation_plan_items`
- `lora_update_generation_mapping`
- `lora_get_generation_plan_summary`

Only the create, validate and update-mapping tools call confirmed RevitMCP read routes. The mapping update is a local planning-data update. All six tools are non-executing and cannot write to Revit.

## Persistence and recovery

Plans and append-only mapping/revalidation events share the versioned review database at `%LOCALAPPDATA%\Lora\MCP\reviews.sqlite3` (or `LORA_REVIEW_DB`). Schema version 3 adds `generation_plans` and `generation_plan_history`; v1/v2 databases migrate transactionally. Unsupported newer versions and corrupt databases are reported without replacing the original database. Test runs use temporary databases.

## Known limits

- No general-purpose Revit write path exists. The separate Milestone 4C workflow supports guarded Basic Wall creation only; planning tools do not execute it.
- The live RevitMCP inventory can fail or change documents during its multi-route read; identity mismatch aborts inventory collection.
- Revit application version, complete wall type catalogs, Project Base Point and Survey Point are not reported by confirmed available routes in the current installation. Level IDs can be read only when the identity sidecar is installed and its full paginated inventory completes; no host test of this route has been performed in this work period.
- Family symbol results may be paginated/truncated; completeness is surfaced and blocks certainty.
- Room placement is only closed-boundary planning; no room or boundary creation is performed.
- This milestone does not establish Revit-version runtime compatibility for Revit 2023–2026.
