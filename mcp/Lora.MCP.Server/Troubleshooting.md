# Troubleshooting

| Symptom | What to check |
|---|---|
| Revit connection unavailable | Confirm Revit and the existing RevitMCP Routes extension are running. Check the configured loopback ports and inspect `lora_diagnose_revit_connection`. |
| Identity sidecar tools unavailable | Confirm the sidecar is installed and its Routes host is exactly `127.0.0.1`; the sidecar fails closed for other bindings. See [RevitIdentityDeployment.md](RevitIdentityDeployment.md). |
| `document_changed` | The active document, process/session identity, or endpoint changed during a read. Re-query the active identity and repeat only against the intended document. |
| Inventory marked incomplete | Inspect totals, page offsets, failure pages, per-field statuses, and warnings. A successful response can still represent an incomplete scan. |
| Geometry status failed | Read the field-specific warnings. Location, bounding-box, and geometry statuses are independent; geometry extraction has request-wide caps and does not imply clearance. |
| Drawing filename rejected | Move the file into the configured inbox and pass only its filename. Confirm supported PDF/JPG/JPEG/PNG format and file-size limits. |
| OCR unavailable | OCR is optional and depends on Tesseract being installed and discoverable. Results without OCR remain text/geometry-limited. |
| Review database unavailable or corrupt | Stop the server and preserve the original database. Check file permissions and free space; restore a known backup only after inspection. The server reports corruption rather than silently replacing the database. |
| Category/type mapping unavailable | Inventory catalogs can be truncated or limited to placed/observed types. See warnings in the response; do not infer an exact type match from a display name alone. |
| Preview coordinates unresolved | Provide a user-confirmed coordinate mapping and validate it. Coordinate diagnostics currently remain unverified in this source state, so do not execute from an unresolved mapping. |
| Execution rejected or indeterminate | Do not retry blindly. Read the execution status/result first; requests are single-use and transaction outcomes can be explicitly uncertain. Follow [ControlledRevitExecution.md](ControlledRevitExecution.md). |

For scope and evidence, consult [MCPTools.md](MCPTools.md), [Security.md](Security.md), and [CompatibilityMatrix.md](CompatibilityMatrix.md).
