# Lora User Manual

## Revit ribbon

Lora's pyRevit ribbon is organized into Annotation, Family, MEP, Documentation and View panels. Each button's supported input and model/file effects are listed in [LoraCommandRegistry.md](LoraCommandRegistry.md). Read the command's preview and warnings before confirming any model-writing operation.

### Family Version Audit

Use this command to select multiple RFA files, scan a folder, or run a single-family compatibility inventory.

- Header batch/folder scans read `BasicFileInfo` only. Unknown/corrupt/newer metadata is reported as unknown when the API cannot distinguish it.
- Compatibility inventory asks for a target Revit year. It hashes the RFA, checks that the family is not already open, temporarily opens it in the running host, inventories bounded content, closes it without saving, and hashes it again.
- The analyzer can classify a family as incompatible when its saved major format is newer than the target. It reports all other content as requiring manual review because it does not emulate a target Revit release.
- Strategy selection is a non-executing preview: manual reconstruction review or retain the source. No family conversion/reconstruction is performed.

The command is not live-host validated. Use only disposable family files until the read-only open/close/hash behavior is tested in Revit 2024.

## MCP client

The MCP server runs locally over stdio and reads Revit through existing loopback integrations. Configure it using [Installation.md](Installation.md). Review [Security.md](Security.md) before configuring or deploying any Revit listener.

The MCP server includes controlled Basic Wall execution tools. It is not a read-only service as a whole. The separate sidecar candidate contains only allowlisted reads but does not provide process isolation from co-loaded pyRevit extensions.

The base Python environment does not install PDF, raster or OCR extras. Install `requirements-drawing.txt` to enable PDF/raster handling, and `requirements-ocr.txt` for the OCR wrapper; OCR also needs an independently installed Tesseract executable. Missing extras are reported as unavailable. Check [Installation.md](Installation.md) and [ThirdPartyNotices.md](ThirdPartyNotices.md) before installing optional packages.

## Current limitations

- Current source has no verified live Revit-host test.
- Coordinate transforms remain unverified until independent surveyed controls are supplied.
- Geometry clearance/clash analysis is not implemented.
- Family compatibility inventory is informational and does not certify target-version compatibility.
- No installer or published release is provided.
