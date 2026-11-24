# Installation and MCP Configuration

This guide configures the CPython MCP server. It does not install or update the Lora ribbon extension, RevitMCP, or the Revit-side identity sidecar. Revit-side setup has separate prerequisites and requires explicit review.

## Requirements

- Windows and CPython. CPython 3.12.7 is the recorded development/test environment; other versions are not established by this test result.
- An existing RevitMCP pyRevit extension for Revit data routes.
- The identity sidecar only for operations that require its guarded diagnostics. Its route registration shares the pyRevit Routes listener.
- PDF/raster and OCR packages are optional. OCR additionally requires a separately installed Tesseract executable.

## Create the server environment

Run from the repository's `mcp/Lora.MCP.Server` directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

To enable PDF and raster processing, install `requirements-drawing.txt`. For OCR, install `requirements-ocr.txt` and Tesseract. For the full development test dependencies, use `requirements-test.txt`. Review [ThirdPartyNotices.md](ThirdPartyNotices.md) before redistributing optional packages; PyMuPDF's AGPL/commercial licensing requires a separate decision.

Start the server in stdio mode:

```powershell
.\.venv\Scripts\python.exe -m lora_mcp.server
```

## MCP client configuration

Configure the intended client to run the virtual environment's Python with `-m lora_mcp.server`, using `mcp/Lora.MCP.Server` as its working directory. Client formats vary. If a client requires absolute paths, substitute the actual local repository path at configuration time; do not commit user-specific paths or credentials.

The MCP server uses stdio to communicate with its client. Revit requests go only to loopback routes. `LORA_REVITMCP_PORTS` can set the permitted loopback port candidates; it cannot specify a remote host. Do not expose Routes on a network interface as part of this setup.

## Local inputs and persisted data

- Drawing files must be placed in the configured inbox. Tools accept a filename, not an arbitrary filesystem path. `LORA_DRAWING_INBOX` can select the inbox before startup.
- Review, plan and preview records use a local SQLite database by default under the current user's local application-data directory. `LORA_REVIEW_DB` can override its location.
- Execution receipts use a separate local database; `LORA_EXECUTION_DB` can override its location.

These databases can contain drawing-derived geometry, review annotations, user-supplied labels and execution history. Protect and back them up according to the project data policy. Do not add them to source control.

## Revit extension structure and test setup

Lora is a pyRevit extension with `extension.yaml` at the repository root, ribbon bundles under `Lora.tab/`, and shared helpers under `lib/`. Test it only in an isolated development extension search path for the target Revit version. Do not overwrite an existing production extension. Record a backup and rollback plan before any installation change. No installer is included.

The identity sidecar source is under `revit_identity/`, but the Routes registry is shared with co-loaded extensions. Verify loopback listener binding and process ownership before registering it. See [RevitIdentityDeployment.md](RevitIdentityDeployment.md) and [GitHubPublishingChecklist.md](GitHubPublishingChecklist.md). This development source has not been qualified for production deployment.
