# Revit Extension Installation and Rollback

## Status

There is no Lora Windows installer in this development checkout. This guide describes a controlled **test-only** manual procedure; it is not authorization to alter a production Revit installation. Revit 2023–2026 compatibility is not qualified by this document.

## Isolated test installation

1. Use a disposable Windows VM or dedicated Revit test account with a supported pyRevit build and one Revit version installed.
2. Record the Revit and pyRevit versions and current extension configuration.
3. Back up any pre-existing test extension directory and its configuration. Do not overwrite an existing `Lora.extension`.
4. Use a clean, hash-verified release candidate directory. Confirm the package manifest before extraction.
5. Register a new test-only pyRevit custom extension directory through pyRevit's supported configuration UI. Keep it separate from the existing installed Lora and RevitMCP locations.
6. Start Revit with the test extension directory. Check pyRevit logs and verify the expected ribbon tab/panels before running commands.
7. Run read-only commands first on disposable files/models. Test any write-capable commands only in a disposable model with explicit test authorization.
8. Record command outcomes, logs, file hashes and rollback results. A successful ribbon load is not full compatibility evidence.

The Family Version Audit metadata modes read RFA headers. Its compatibility mode opens a selected family temporarily, hashes the file before/after, performs a read-only inventory and closes with `Close(False)`. Test this only with disposable families until a Revit host test confirms those behaviors.

## Uninstall and rollback in a test environment

1. Close Revit.
2. Remove only the test-only extension-directory registration created for that test.
3. Preserve the test package, manifest, hashes and logs for diagnosis.
4. Restore the backed-up test configuration if needed.
5. Restart Revit and verify the original test extensions load as before.

Do not delete or replace existing Lora/RevitMCP files as part of rollback. No automated uninstaller is supplied. A future installer must record only files and settings it created, refuse to overwrite user data, and have a tested rollback path.

## Deployment gate

Before production deployment, require a separately approved package, verified hashes, backup, test-environment loading and rollback evidence, current-source host testing for each claimed Revit version, and explicit user approval. Current source is not release-qualified.
