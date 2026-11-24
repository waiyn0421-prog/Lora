# GitHub Publishing and Deployment Checklist

This checklist describes required review gates. It does not authorize Git, publication, installation or Revit operations.

## Source branch review

- [ ] Confirm target repository, base branch and current remote branch state when network access is available.
- [ ] Review exact included/excluded manifests, source diff, untracked files and file hashes. Preserve user review data, backups and installed extensions.
- [ ] Review the portable README, documentation index, compatibility matrix and known limitations.
- [ ] Complete manual credential, privacy, project-data and dependency/license review; heuristic scans alone are insufficient.
- [ ] Run the full test suite, compile/parser checks and manifest/hash verification from a fresh source copy.
- [ ] Review packaging/dependency licenses, especially optional PDF/OCR libraries, before distributing bundled environments.
- [ ] Approve version and commit boundaries.
- [ ] Obtain explicit approval before staging, committing, creating/pushing a branch, opening a PR, tagging or publishing a release.

## Revit deployment

- [ ] Review the shared pyRevit Routes security boundary. A route allowlist is not process isolation from co-loaded extensions.
- [ ] Obtain current-source host evidence in an isolated host for every Revit/pyRevit version claimed.
- [ ] Test install, update, rollback and uninstall in a disposable environment.
- [ ] Verify listener binding/owner, route allowlist, identity lifecycle, startup/shutdown, ribbon loading and representative commands.
- [ ] Obtain explicit approval before changing any Revit installation or user configuration.

## Model or family writes

- [ ] Use a disposable, separately identified model/family and record input hashes and expected outputs.
- [ ] Review exact write scope, preview, consent, transaction, verification and rollback behavior.
- [ ] Obtain explicit approval before each requested model/family write.
- [ ] Keep coordinate placement blocked until independent surveyed asymmetric controls validate transform direction. Clearance remains unimplemented.

The current branch proposal is source-review preparation only. It is not an installer, production deployment or release.
