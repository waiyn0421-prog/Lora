# Milestone 4C: controlled Basic Wall execution

## Status and scope

This milestone adds a source-level execution path for **Basic Walls only**, including explicit user geometry as a reviewed source. The 4C.2 sidecar source was deployed to the Revit 2024 test environment for read-only verification; no Revit write operation or live wall creation was performed. The current 4C.2A WallType name-resolution correction is source-only and awaits separate deployment approval.

The path is:

```text
User-reviewed drawing item
  -> approved/current generation plan
  -> live-verified generation preview
  -> one-time execution request
  -> MCP client elicitation
  -> loopback LoraIdentity Routes handler
  -> Revit UI confirmation dialog
  -> one Revit Transaction
  -> post-transaction verification and local history
```

The external CPython MCP process does not call the Revit API. The wall execution endpoint is in `revit_identity/LoraIdentity.extension`, whose Routes handlers declare injected `doc`/`uiapp` arguments. Installed pyRevit Routes source documents that these handlers are dispatched through `IExternalEventHandler.Execute(uiapp)`. Autodesk documents the eight-argument `Wall.Create(Document, Curve, ElementId, ElementId, Double, Double, Boolean, Boolean)` overload in the [Revit 2024 developer guide](https://help.autodesk.com/cloudhelp/2024/ENU/Revit-API/files/Revit_API_Developers_Guide/Revit_Geometric_Elements/Walls_Floors_Ceilings_Roofs_and_Openings/Revit_API_Revit_API_Developers_Guide_Revit_Geometric_Elements_Walls_Floors_Ceilings_Roofs_and_Openings_Walls_html.html) and the [Revit 2026 API reference](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/0ce4c555-4cee-f5fd-2e84-43cacf34ac5c.htm).

## MCP tools

The server registers 59 tools. All previous 56 names remain; 3 tools were added:

| Tool | Purpose | Revit write? |
|---|---|---:|
| `lora_prepare_execution` | Revalidates the preview and stores a short-lived request; resolves wall types and levels through the sidecar | No |
| `lora_get_execution_preview` | Retrieves the exact prepared snapshot and confirmation requirements | No |
| `lora_validate_execution` | Rechecks plan, preview, identity, wall type and level | No |
| `lora_execute_walls` | Requests client elicitation and calls the sidecar write endpoint | **Yes; only destructive tool** |
| `lora_get_execution_result` | Retrieves durable execution history and may reconcile a timed-out request from the Revit-process receipt | No |
| `lora_verify_created_elements` | Reads created walls back from the same Revit document session | No |
| `lora_get_wall_types` | Reads actual `WallType` IDs, names, kinds, widths, functions, and compound layers through the sidecar | No |
| `lora_create_explicit_wall_review` | Persists one user supplied straight wall geometry as pending review | No Revit write |
| `lora_approve_explicit_wall_geometry` | Records exact geometry confirmation, fingerprint, reviewer, and approval history | No Revit write |

Only `lora_execute_walls` has `destructiveHint=true`. Existing generation preview item fields and schema version are unchanged. Explicit geometry uses the same item shape and has no drawing filename or page number.

### WallType discovery name resolution

The read-only `/wall_types` route uses `FilteredElementCollector.OfClass(WallType)` and preserves actual `WallType` IDs. Type names are resolved from `BuiltInParameter.SYMBOL_NAME_PARAM` first, with a guarded `Element.Name` fallback. `UniqueId`, kind, width, function, and compound-structure values are reported when available; individual property failures are returned as field warnings instead of failing the full inventory. The repository implementation has mocked coverage. The 4C.2A source correction has not yet been deployed or verified against live Revit.

The old RevitMCP read-only route allowlist is unchanged. `lora_rollback_execution` is not exposed: after a transaction commits, a separate rollback tool would need a new delete operation and separate safeguards. A failure before commit rolls back the whole transaction. A post-commit verification failure is recorded as committed but unverified; it is not described as a rollback.

## Approval and execution gates

`lora_prepare_execution` rejects a preview unless all of these conditions hold:

- The preview is current, not stale, and was created from live Revit inventory.
- Its document session matches the current process, application, document session, endpoint, and lifecycle identity.
- Its items match the current plan’s review-item set exactly; duplicates, omissions, extra items, non-wall categories, and blocked items are rejected.
- Each included review item remains approved and calibrated against the current calibration fingerprint. Explicitly rejected review candidates may remain excluded; unreviewed or stale candidates block execution.
- Explicit geometry instead requires a persistent approval whose fingerprint matches its current coordinates, units, coordinate system, origin description, and declared length. A separate exact confirmation statement must be approved. No drawing or calibration record is synthesized.
- Plan and preview validation errors are absent. Warnings are shown to the user for review.
- Wall types come from actual `Autodesk.Revit.DB.WallType` elements via GET `/lora-identity-v1/wall_types`; `FamilySymbol` is not used. The response includes ID, name, kind, internal-foot width, function, and compound layers where available. Plans store the exact type ID; the sidecar rechecks both ID and name.
- Base and optional top constraints are explicit Level IDs and names. The sidecar resolves them against `Level`. Top-constrained walls use `WALL_HEIGHT_TYPE` and `WALL_TOP_OFFSET`. Unconnected height is allowed only if top constraint is null. Base and top offsets are separate. These execution inputs do not alter the existing preview response schema.
- Geometry is a finite, nonzero, straight horizontal centerline in internal feet; declared explicit length is checked against the line. Its Z must equal the base Level elevation plus base offset.
- Explicit geometry requires coordinate system (`revit_internal` or `user_local`), source units (mm, cm, m, in, ft), start/end XYZ, declared length, and a nonempty origin description. User local coordinates require a separate user-confirmed project mapping. No origin or coordinates are created automatically.
- Height mode, offsets, structural setting, and flip/orientation are explicit for every wall. A top constraint is never converted to unconnected height.
- Only `Mark` and `Comments` string instance assignments are supported. Any other assignment fails validation rather than being silently ignored.
- Invalid/zero-length curves, non-Basic wall kinds, invalid IDs, mismatched level names, and invalid vertical constraints are rejected. Existing and same-batch wall footprints are checked for centerline intersection and thickness overlap when vertical spans overlap. Unknown existing vertical spans are treated conservatively as overlapping; connected/touching walls may be rejected for manual review.

Each request has a random nonce, exact approval statement, and five-minute expiry. The nonce hash and request state are persisted; the nonce itself is held only in MCP process memory and is removed when the request is consumed or expires. A process restart invalidates every still-prepared request (`approval_lost`); the nonce is never reconstructed from its hash, and a new request must be prepared. `lora_execute_walls` has MCP `destructiveHint=true`, then uses MCP elicitation requiring the exact statement and nonce. The Revit side independently presents a default-No TaskDialog showing the document, source references, type/thickness, level, centerline, height, offset, orientation, structural setting, parameters, and preview warnings. Declining either confirmation performs no creation.

The sidecar endpoint independently rechecks process/application/document session identity, WallType and Level IDs, wall kind, geometry, dimensions, and overlap conditions. It repeats these checks after the Revit dialog and immediately before the transaction, then rechecks identity before commit and after commit.

## Transaction and history

- A batch is limited to 25 walls and uses one Revit `Transaction`.
- All type/level mappings and duplicate checks occur before the transaction. Every wall is created and verified inside the same transaction.
- A creation, parameter assignment, in-transaction verification, or identity error triggers `RollBack`; the response only says rollback succeeded if Revit reports `RolledBack`.
- If Revit reports a pending transaction outcome or rollback cannot be confirmed, the request is recorded as indeterminate and is not reported as successful.
- Immediately after `Wall.Create` returns a wall with a valid `ElementId`, the sidecar records the element ID and `UniqueId` when accessible, before top constraint, parameter, or verification work. Failure records therefore distinguish `created_in_transaction` from `committed`, `rolled_back`, and `uncertain`; a returned ID is not itself evidence of commit. Confirmed rollback records retain IDs with `transaction_outcome=rolled_back`; pending/unknown outcomes retain IDs with `transaction_outcome=uncertain`.
- Post-commit identity or element verification failures retain the created IDs and are recorded as `committed_unverified`.
- The sidecar keeps an in-memory request receipt for duplicate prevention during that Revit process. The MCP-side SQLite history is stored at `%LOCALAPPDATA%\Lora\MCP\executions.sqlite3`, or at `LORA_EXECUTION_DB` when set. It stores prepared request details, state, result, source review links, and failures.
- Each result links its Revit element ID to the source review item, drawing, page, and original geometry references in local history. These trace links are not written into Revit element parameters.
- If a request times out, it is recorded as indeterminate. The result tool queries the sidecar receipt. If Revit has restarted and the receipt is gone, the status stays indeterminate; do not retry as a new request against another session.

## Security boundary and limitations

The existing Routes listener must remain bound to `127.0.0.1`; the sidecar startup refuses other bindings. No listener, firewall, installed RevitMCP, or global Codex configuration was changed. The Routes server has no request authentication middleware. A process on the same computer can reach the local endpoint and could cause a Revit confirmation dialog to appear. The default-No in-Revit confirmation prevents a silent write, but this source does not authenticate the calling process or independently query the MCP review database. Therefore, the machine-local route must be treated as a sensitive write surface, and the Revit TaskDialog is an essential second confirmation layer. Remote access is unsupported.

The MCP execution path verifies review approval and calibration immediately before execution. A direct local HTTP caller bypassing the MCP layer cannot prove that its supplied source review IDs came from an approved review; it can only be gated by the Revit dialog and sidecar’s Revit/document/type/level/geometry checks. This is a known local trust limitation, not a guarantee that the sidecar alone enforces review-database approval.

Other limitations:

- Only straight Basic Walls are supported. Doors, windows, columns, rooms, curtain walls, stacked walls, and profile walls are unsupported.
- The new WallType route is repository source and requires separate approved deployment before live catalog use. Until then, planning can use legacy observed type names, but execution fails closed without a verified WallType element ID.
- No shared coordinates are inferred. Calibrated drawings need a user-confirmed mapping. Explicit Revit internal geometry must be entered and approved as such; user-local coordinates need a complete explicit mapping.
- Levels and wall types are re-resolved by element ID and checked against mapped names immediately before transaction; replaced, missing, or ambiguous IDs fail closed.
- Source drawing information is kept in the local execution history; it is not embedded into a Revit parameter.
- Revit API source-level compatibility is targeted for Revit 2023–2026, but no wall-creation runtime test has been completed in those versions. The user reports that Revit 2024 identity lifecycle tests passed. That does not verify this new wall operation. Revit 2023, 2025, and 2026 wall execution remain unverified.
- Mock tests cannot verify pyRevit event dispatch, TaskDialog behavior, Revit transaction failure processing, or geometry placement in a live model.
- Wall interaction checks classify exact duplicates, collinear overlap, unintended interior crossings, L/T junctions, endpoint connections, and thickness-only proximity. Duplicates, overlaps, and crossings are rejected. Junction contacts are classified but return a manual-review-required validation error; this release does not auto-approve, trim, extend, join, or modify connected wall geometry. The geometry representation cannot establish design intent, so even geometrically clear L/T/end connections require review before execution.

## Disposable Revit 2024 test procedure (future; not run)

Do not use a client or production project. Do not deploy the sidecar until separately approved.

1. Create a separate disposable Revit 2024 project, save it, and confirm it is active. Use an existing Basic Wall type and Levels.
2. For drawing-based geometry, process/calibrate a source and explicitly confirm its mapping. For explicit geometry, call `lora_create_explicit_wall_review` with the coordinate system, units, endpoints, declared length, and origin description.
3. Inspect the explicit confirmation statement and approve it with `lora_approve_explicit_wall_geometry`; for drawing-based geometry approve through the existing review workflow. No approval is automatic.
4. Read WallTypes with `lora_get_wall_types`. Create a generation plan using exact WallType ID/name and base Level ID/name; include top Level ID/name for top-constrained walls. Validate and create a live preview. Preview schema remains unchanged.
5. Confirm exactly one wall is in the approved plan and preview. Provide either a top constraint with top offset or null top constraint with unconnected height, plus explicit base offset, structural, and flip values.
6. Call `lora_prepare_execution`, inspect the full execution preview, and call `lora_validate_execution`. Stop on any identity, mapping, geometry, overlap, or constraint discrepancy.
7. Call `lora_execute_walls`; enter the exact approval statement and nonce in MCP elicitation. Review the default-No Revit dialog and accept only for the one intended wall in the disposable document.
8. Verify the committed result and element ID with `lora_verify_created_elements`; inspect category, WallType ID/name, base/top Level IDs/names, height, offsets, length, thickness, location, parameter values, and source review linkage.
9. Claim no rollback unless Revit reports `RolledBack`. If outcome is indeterminate or committed-unverified, inspect manually and do not retry. Close the disposable file without saving if discarding the test wall.

This is a procedure only. No copy to `%APPDATA%\pyRevit\Extensions\LoraIdentity.extension`, Revit restart, endpoint deployment, or live wall test was performed for this source-level verification.
