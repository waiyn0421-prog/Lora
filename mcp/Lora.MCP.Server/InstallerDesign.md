# Windows Installer Design (Not Implemented)

## Decision

No installer was built. No Inno Setup Compiler, WiX compiler or other installer tool was found on the development machine. The repository has no tested installer framework. Creating an executable package before the distribution allowlist, Revit host matrix, pyRevit behavior and rollback procedure are qualified would create avoidable deployment risk.

## Proposed smallest safe design

- Use a signed Windows installer only after selecting a maintained packaging tool and reviewing its license.
- Install to a new, versioned Lora extension directory rather than overwriting an existing installation.
- Discover Revit and pyRevit installations read-only; display the detected versions and let the user choose supported targets.
- Reject unsupported or unverified hosts by default; allow any experimental override only as an explicit test-only action.
- Preserve existing user configuration and MCP databases. Never migrate or delete them automatically.
- Copy a manifest-verified package to a temporary target, verify all hashes, then activate it through a separate pyRevit custom-extension path.
- Record exactly which files/configuration entries the installer created. Uninstall removes only those owned entries and leaves user files, backups and existing extensions untouched.
- Maintain a backup/rollback record and restore the previous custom-extension configuration if installation or startup validation fails.
- Do not deploy or test the installer on a production machine. Verify install, upgrade, repair and uninstall in disposable VMs for Revit 2023, 2024, 2025 and 2026 separately.

## Blocking prerequisites

1. Current-source Revit/pyRevit host evidence for each claimed version.
2. Approved full-product package allowlist and dependency/license notices.
3. Stable release identity/version and signed artifact policy.
4. Disposable Windows test VMs, unattended rollback tests and explicit installer approval.

Until these prerequisites are met, distribution is a source archive/review candidate only and should not install itself.

## Isolated install/rollback test matrix

No installer currently exists, no disposable Windows profile/VM was available, and no Revit installation was modified in this phase. These cases are acceptance tests for a future installer, not completed tests:

| Case | Setup and action | Required result | Status |
|---|---|---|---|
| Fresh install | Clean disposable Windows VM/profile with one supported Revit/pyRevit version | Installs only to a new Lora-owned extension directory; verifies package hashes; records every file/config change | Not run; installer absent |
| Existing installation detection | Place a synthetic prior Lora version in an isolated custom-extension path | Detects and reports version/path; does not overwrite automatically | Not run; installer absent |
| Upgrade | Install a prior test version, then upgrade | Backs up owned files; preserves user data/config/review DB; refuses unexpected conflicts | Not run; installer absent |
| Configuration preservation | Seed unrelated pyRevit paths/settings and MCP/local data | Leaves unrelated settings and user data byte-for-byte unchanged | Not run; installer absent |
| Backup | Install/upgrade in disposable VM | Produces timestamped, manifest-verified backup and rollback metadata before activation | Not run; installer absent |
| Uninstall | Remove the test installation | Removes only paths recorded as owned by Lora; preserves shared extensions and user data | Not run; installer absent |
| Rollback | Inject copy/startup/hash failure during test install | Restores previous extension selection and files; reports failure; does not leave mixed versions | Not run; installer absent |
| Failed-install recovery | Terminate/cancel at each install phase | Next run detects incomplete state and offers safe recovery without deleting files | Not run; installer absent |

Execution requires a disposable profile/VM and an installer design/tool approval. Do not point these procedures at the production profile or installation.
