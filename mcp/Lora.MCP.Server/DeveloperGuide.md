# Lora MCP Developer Guide

## Architecture

- `lora_mcp/`: MCP tool registration, local client, allowlisted HTTP access and identity guard.
- `revit_identity/LoraIdentity.extension/`: pyRevit-side identity, read-only diagnostic, category inventory, geometry evidence and separately guarded Basic Wall execution routes.
- `lora_drawing/`, `lora_recognition/`, `lora_calibration/`: bounded drawing processing and unconfirmed geometry hypotheses.
- `lora_review/`, `lora_generation/`, `lora_execution/`: local review/planning persistence, preview validation and guarded wall execution coordination.
- `lora_context/`: deterministic, read-only BIM context and question routing.
- `tests/`: CPython tests with mocked Revit API and HTTP surfaces.

## Local verification

From `mcp/Lora.MCP.Server`:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q .
```

From the repository root, run `git diff --check`. These checks do not replace pyRevit/IronPython parsing, Revit API assembly inspection, or live host validation. See [CompatibilityMatrix.md](CompatibilityMatrix.md).

## Change rules

Keep Revit reads bounded, identity-checked and allowlisted. Treat API getter failures as field-specific evidence, preserve independent field statuses, and report incompleteness rather than infer a success. Geometry routes must be explicitly scoped, capped, read-only and coordinate-provenance-aware. Do not infer clearance from category, location, or bounding-box data. Preserve transaction/approval/nonce protections; only the supported Basic Wall route may invoke its existing write flow.

Any change to category enum mappings, geometry traversal, Revit API overloads, or pyRevit runtime syntax needs version-specific review and host tests before compatibility is claimed. Do not claim a Revit version is verified from mocks alone.

For family-format/content inspection, see [FamilyCompatibility.md](FamilyCompatibility.md). The analyzer is a bounded read-only inventory; it opens only a temporary family document, performs no transaction, and closes with `Close(False)`. Its fake-API tests do not validate Revit geometry collectors or family upgrade behavior.
