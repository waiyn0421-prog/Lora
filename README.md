# Lora for Revit

Lora is an existing pyRevit extension for Revit productivity, documentation, family workflows, MEP data updates and model inspection. This development branch also contains a companion CPython MCP server for Revit context, drawing analysis, review, planning and a guarded Basic Wall execution workflow.

This is development source, not a release package or installer. The declared Revit target is 2023–2026. **No current-source live Revit host verification is recorded for those versions.** Revit 2024 API metadata has been inspected; this is not runtime verification. See [the compatibility matrix](mcp/Lora.MCP.Server/CompatibilityMatrix.md).

## Features and commands

| Ribbon panel | Command | Summary |
|---|---|---|
| Annotation | Family Generator | Creates a family using a template or source family and can bind parameters/load it into a project. |
| Annotation | Tag Studio | Builds a tag design and parameter setup workflow; setup can change project bindings. |
| Family | Family Version Audit | Reads family version metadata and provides a bounded, read-only family content inventory for manual compatibility review. |
| Family | InPlace to Family | Converts supported in-place content to a family; the workflow can load/place it and offers an option to delete the original. |
| MEP | Tag Creation | Provides separate Parameter Generator and Tag Generator readiness flows. Parameter Generator changes project parameter bindings; readiness does not author a tag family. |
| MEP | Refresh | Updates selected pipe, duct, cable tray or conduit elevation-related values using project parameters. |
| Documentation | Auto Dim | Previews and creates supported dimensions for straight walls and compatible hosted doors/windows. |
| Documentation | Data Sync | Exports schedules to workbooks and imports supported edited values from Lora workbooks. |
| Documentation | Draw Area Boundary | Converts supported detail/model lines to area boundaries; it does not place Area elements. |
| Documentation | TableGen | Imports CSV cells into a Legend or Drafting View using text and detail lines. |
| View | Hide Section | Hides or shows matching section marker symbols in a plan view; it does not delete views. |
| View | View Range | Previews and applies plan view range settings. |

Commands that write to Revit use their own confirmation and transaction workflows. Consult the [command registry](mcp/Lora.MCP.Server/LoraCommandRegistry.md) and [user manual](mcp/Lora.MCP.Server/UserManual.md) for inputs, effects and limitations. Source inspection does not establish live UI or model behavior.

## Family Version Audit

The audit can scan multiple RFA headers or recursively scan a folder with `BasicFileInfo`. Its content review temporarily opens a family in the running Revit host, inventories supported categories, types, parameters and selected geometry/material/constraint information, then closes without saving. It does not downgrade, reconstruct, load or certify compatibility with another Revit release. Unsupported or uninspected fields remain explicit. See [Family Compatibility](mcp/Lora.MCP.Server/FamilyCompatibility.md).

## Drawing and BIM workflows

The MCP server supports bounded PDF/raster intake from a configured local inbox, geometry/text candidates, deterministic recognition hypotheses, page-local drawing calibration, local review records, Revit context queries, generation planning and preview. Recognition results are hypotheses for human review. Drawing calibration does not establish a drawing-to-Revit transform. Native DWG extraction is unsupported, OCR is optional, and physical clearance is **not implemented**.

Selected Revit geometry is returned as bounded evidence for explicitly requested element IDs. It does not prove collision-free placement. Controlled MCP execution currently supports Basic Walls only, and the main MCP server is not a read-only service. See [the MCP architecture](mcp/Lora.MCP.Server/Architecture.md), [known limitations](mcp/Lora.MCP.Server/KnownLimitations.md) and [security notes](mcp/Lora.MCP.Server/Security.md).

## Revit and pyRevit compatibility

| Version | Evidence |
|---|---|
| Revit 2023 | Target only; no local API assembly or current-source live host test. |
| Revit 2024 | API metadata inspected; current-source live host behavior unverified. |
| Revit 2025 | Target only; no local API assembly or current-source live host test. |
| Revit 2026 | Target only; no local API assembly or current-source live host test. |

The ribbon extension requires pyRevit in the intended Revit host. The sidecar source follows pyRevit/IronPython-compatible conventions; the MCP server is separate and runs under CPython. The recorded development test environment is CPython 3.12.7. Parser checks do not prove pyRevit or Revit runtime compatibility. No minimum pyRevit version is asserted by this source review. See [the compatibility matrix](mcp/Lora.MCP.Server/CompatibilityMatrix.md).

## Installation overview

This repository is source, not an installer. Do not copy it into a production Revit installation as an unreviewed upgrade. The ribbon extension is discovered through a pyRevit extension search path. The optional MCP server uses its own CPython virtual environment and connects to existing RevitMCP routes and, for guarded diagnostics, the Lora identity sidecar. Setup examples and rollback considerations are in [Installation](mcp/Lora.MCP.Server/Installation.md). Installation/deployment requires a separate review and approval.

The extension layout is:

```text
extension.yaml                      extension metadata at repository root
Lora.tab/<Panel>.panel/              Revit ribbon panels
  <Command>.pushbutton/              command bundle, script and icon
lib/                                 shared pyRevit command helpers
mcp/Lora.MCP.Server/                 CPython MCP server, tests and documentation
  revit_identity/                    separate pyRevit identity sidecar source
docs/                                documentation index
```

## Development and tests

From `mcp/Lora.MCP.Server`, create a CPython virtual environment and install `requirements-test.txt` to include optional drawing/OCR test dependencies. Run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q .
```

The current source suite has 303 passing tests in the documented CPython environment. Most Revit behavior is tested with fakes/mocks; the suite is not a Revit host test. See [the test report](mcp/Lora.MCP.Server/TestReport.md) and [developer guide](mcp/Lora.MCP.Server/DeveloperGuide.md).

## Security and known limitations

The MCP process uses stdio for its client connection and contacts Revit Routes through loopback HTTP with endpoint allowlists and document-identity checks. Loopback is not authentication against other local processes. pyRevit Routes uses a shared registry, so an allowlisted sidecar is not process isolation from co-loaded extensions. Keep the listener local and review its binding before use. Local drawing/review databases may contain project data.

Coordinate transform status remains **UNVERIFIED** pending independent surveyed control points. Clearance remains **NOT IMPLEMENTED**. Revit 2023–2026 host compatibility, ribbon loading, command transactions, and current-source end-to-end MCP deployment remain unverified. Read [Known Limitations](mcp/Lora.MCP.Server/KnownLimitations.md) before use.

## Troubleshooting and documentation

See [Troubleshooting](mcp/Lora.MCP.Server/Troubleshooting.md) for connection, dependency and test issues. The [documentation index](docs/README.md) links user, developer, architecture, compatibility, installation and security guides.
