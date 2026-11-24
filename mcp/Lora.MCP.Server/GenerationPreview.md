# Generation Preview

The generation preview presents persisted Milestone 4A plans for review. It does not create Revit elements, transact, select elements, save the document, or create a native Revit 3D view.

## Snapshot contents

Each snapshot stores `preview_id`, source plan ID/version, review ID, target document identity (only if read inventory returned it), inventory source/status/verification time, creation time, classified validation findings, preview items, a structured 2D feature list, differences from the prior snapshot and staleness state. No original drawing bytes are stored.

Each item carries planned element ID, source review item ID, category/operation, proposed family/type/symbol and level, live or fixture match status, calibrated/internal-foot geometry, location, orientation, dimensions, proposed parameter assignments, dependencies, and errors/warnings. Proposed parameters are not represented as verified values. Visual features support wall centrelines and thickness, door/window locations, column footprints, room boundaries and level annotations where such geometry exists. Geometry that cannot be rendered is counted as omitted. Coordinates are explicitly drawing-local or Revit internal feet. `native_revit_3d` is always false.

## Validation states

- **Error** blocks preview readiness: unapproved or stale source items, unsupported categories, missing/invalid geometry, unavailable type/level, invalid host, duplicate placements, or unresolved placement coordinates.
- **Warning** requests review: incomplete inventory, possible column footprint overlap, incomplete catalog, or parameter name not found in the schema snapshot.
- **Information** records non-blocking context, including live inventory verification and unavailable type/instance parameter scope.

Every preview has `ready_for_execution: false` and `execution_supported: false`. Even if checks pass, the plan is only reviewable; there is no generation operation in this milestone.

## Live inventory and disconnection

Lora uses only the existing identity-checked RevitMCP client and allowlisted read routes. When it cannot connect, a preview may still be persisted for geometry/review, but inventory status is unavailable and levels/types are unverified. Caller/test fixture data is explicitly labeled non-live. Successful live inventory results carry a verification timestamp and active document identity. A revalidation creates a new snapshot; it does not overwrite prior evidence. Mapping or validation changes increase the plan version, causing older snapshots to be marked stale on retrieval.

The inventory source and active document identity are recorded per preview when available. A live inventory is evidence about the identified document at the recorded time; missing or stale identity/inventory information remains unavailable or unverified. No host observation is embedded in this feature reference.

## Persistence

Preview JSON is stored in `generation_previews` in the review SQLite database at `%LOCALAPPDATA%\Lora\MCP\reviews.sqlite3`, or `LORA_REVIEW_DB` when set. Schema version 4 adds the snapshot table and plan index transactionally. Preview IDs are immutable; new validations create new IDs. A changed plan version marks previous previews stale. The `differences_from_previous` field summarizes item additions, removals and changed values; `lora_compare_generation_previews` compares any two snapshots.

## Limitations

- Live inventory availability depends on the configured RevitMCP routes and identity guard.
- No matching Revit 3D geometry, view rendering or Revit element preview is provided.
- Overlap checks are conservative and limited to column footprints; legitimate connections and touching geometry need human review.
- RevitMCP schema does not expose full parameter scope/compatibility or writability.
- Revit element creation remains unsupported.
