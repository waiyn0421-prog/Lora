# Revit Identity Sidecar Deployment Prerequisites

This document describes prerequisites for a future controlled test; it is not an instruction to deploy this source. The sidecar is separate from the CPython MCP server and registers routes with the shared pyRevit Routes service.

## Before a test

1. Use a disposable Revit model and isolated extension search path for the exact Revit/pyRevit version under test.
2. Record the source revision and file hashes. Back up the test extension and define rollback before changing the test installation.
3. Verify that the Routes listener is bound only to `127.0.0.1` and is owned by the intended Revit process. The sidecar does not set the listener host, firewall policy, or authentication.
4. Ensure no conflicting identity API or unexpected route registrations exist. Startup should fail closed if the route registry or listener state is not as expected.
5. Capture Revit version, process/document session identity, startup log, request result and warnings without including private model names or paths in public reports.

## Expected source behavior

The provider checks the active/configured Routes host and registers the identity route only when the host is exactly the required IPv4 loopback address. Missing, wildcard, alternate-interface or uncertain bindings are rejected. This prevents remote-network binding when enforced, but does not authenticate other local processes and does not isolate the shared registry from co-loaded extensions.

The identity contract reports available process/application/document session evidence and refuses guarded operations when required identity is uncertain. It does not modify the Revit document. No current-source live-host test is claimed for Revit 2023–2026.

## Verification and rollback

In a disposable test installation, verify startup registration, loopback ownership, same-document identity across repeated reads, process/document switch rejection, shutdown cleanup and restoration of the pre-test extension. Close Revit before restoring the test backup so the pyRevit runtime and event subscriptions are released. Do not change RevitMCP, production installations, firewall configuration or user-wide pyRevit settings during a source qualification test.

Any deployment outside an isolated disposable test installation requires separate explicit approval after a reviewed plan and test evidence. See [Security.md](Security.md), [RevitConnection.md](RevitConnection.md) and [GitHubPublishingChecklist.md](GitHubPublishingChecklist.md).
