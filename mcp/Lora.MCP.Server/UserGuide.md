# Lora MCP User Guide

## Read the active model

Start with `lora_diagnose_revit_connection` or `lora_get_active_document_identity` to check whether the active Revit document is available and identity-guarded. Use `lora_get_bim_context` for a bounded overview, `lora_analyze_model` for RevitMCP category statistics, and `lora_get_category_inventory` for category-ID-based instance/type counts and paged details. The natural-language router can also answer category inventory/discipline summary questions using one bounded page.

`lora_get_geometry_inventory` returns a paged read-only inventory. `lora_get_element_geometry` extracts geometry only for explicitly supplied element IDs (1–25 per call); it does not scan the model. Both keep clearance unverified. See [CategoryInventory.md](CategoryInventory.md) and [GeometryExtraction.md](GeometryExtraction.md).

## Process and review a drawing

1. Put a supported PDF or raster image in the configured drawing inbox and pass only its filename to the processing tool.
2. Inspect returned pages, warnings, and drawing geometry. Recognition output is a hypothesis, not an approved BIM element.
3. Supply user-confirmed scale and coordinate references through calibration. Calibration is page-local and does not independently verify the drawing's real-world coordinates.
4. Create a review, correct ambiguous candidates, and approve only items reviewed by a person. Review state is local SQLite data.
5. Create a plan and preview. Check document identity, mappings, warnings, coordinates and stale state before considering any execution request.

The pipeline's plan/preview does not automatically consume the category inventory or selected-element geometry routes. A drawing-to-Revit spatial check is not safe while the required coordinate mapping is unverified. No clearance result is claimed.

## Model-writing boundary

Only the separately guarded Basic Wall execution workflow can write through the Lora AI MCP workflow. It requires an approved/current preview, validated mappings and document identity, exact approval text, a single-use nonce, and explicit human approval. Structural and MEP creation are not implemented. Other existing Lora ribbon commands have their own effects; see [LoraCommandRegistry.md](LoraCommandRegistry.md). Never treat a tool recommendation or preview as execution approval.

For command inputs and output fields, use [MCPTools.md](MCPTools.md). For current limitations, see [KnownLimitations.md](KnownLimitations.md).
