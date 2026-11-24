# Lora MCP Server

The Lora MCP server is a separate CPython process using stdio transport. It provides Revit context queries through existing RevitMCP routes, guarded diagnostics through the Lora identity sidecar, local drawing processing/review, deterministic recognition, calibration, generation planning/preview, and a guarded Basic Wall execution workflow.

The main server is **not read-only as a whole**: its Basic Wall execution tools can write to Revit after their explicit guarded approval flow. Drawing review and generation planning persist local SQLite records. Ribbon commands also have model-writing workflows. Read each tool/command contract before use.

Revit geometry inventory and selected geometry tools return bounded evidence. Category membership or a bounding box is not clearance evidence. Clearance is not implemented; coordinate transform verification remains unverified. Drawing recognition returns hypotheses and requires human review. Native DWG extraction is unsupported.

## Setup and use

Use the [Installation guide](Installation.md) to create an isolated CPython environment and configure an MCP client. The service communicates with its MCP client over stdio and contacts Revit routes only over loopback. It does not install the Revit-side extension.

Ribbon and MCP command behavior is listed in [LoraCommandRegistry.md](LoraCommandRegistry.md). See [Architecture.md](Architecture.md), [Security.md](Security.md), [CompatibilityMatrix.md](CompatibilityMatrix.md), [KnownLimitations.md](KnownLimitations.md) and [UserGuide.md](UserGuide.md) before connecting to a live Revit process.

Run the full test suite from this directory with `python -m unittest discover -s tests -v`. These tests use mocks/fakes and do not establish live Revit compatibility. See [TestReport.md](TestReport.md) for the current evidence and limitations.
