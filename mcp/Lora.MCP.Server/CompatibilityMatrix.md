# Revit, pyRevit and Python Compatibility Evidence

The extension declares Revit 2023–2026 as its target range. A target declaration, parser check, mocked test, API metadata inspection and live host test are different evidence classes. Only a live test in the corresponding host can establish that version's runtime behavior.

## Revit versions

| Revit | Target declaration | API assembly evidence | Current-source live host test | Status |
|---|---|---|---|---|
| 2023 | Target | No usable local Revit API assembly found in the checked standard installation location. | Not performed. | Unverified. |
| 2024 | Target | Local `RevitAPI.dll` metadata inspected: assembly identity 24.3.60.0, file version 24.3.60.12. Selected enum names and API members were checked. This is metadata only. | Not performed for current source. | Metadata inspected; runtime unverified. |
| 2025 | Target | No local API assembly found in the checked standard installation location. | Not performed. | Unverified. |
| 2026 | Target | No local API assembly found in the checked standard installation location. | Not performed. | Unverified. |

## Python evidence

| Runtime/check | Result | Limit |
|---|---|---|
| CPython 3.12.7 | Full suite: 303 passed, 0 failed, 0 skipped; source compilation passed. | Mocks and fakes do not exercise Revit CLR objects or UI. Other CPython versions are not established by this test result. |
| IronPython 2.7.12PR parser | Syntax-only compilation passed for 31 Revit-side Python files in this proposal copy. | Does not import or run commands in pyRevit. |
| IronPython 3.4.2 parser | Syntax-only compilation passed for the same 31 Revit-side Python files. | Does not prove that the selected engine is used by a given Revit host or validate API calls. |
| pyRevit host | Not tested in a current-source Revit process. | Parser success is not extension-load or UI verification. |

The parser set covers Python under `lib/`, `Lora.tab/` and the Lora identity extension's startup/provider modules. It does not test runtime API behavior.

## Required qualification

For each claimed Revit version, use an isolated supported host and record the Revit build, pyRevit/runtime, source hashes, process/document identity, UI load, representative reads, transaction behavior where applicable, and warnings. Verify enum conversion, element IDs, collectors, geometry wrappers, nested/link transforms and shutdown lifecycle against that host's API. Do not infer compatibility across releases from one API assembly or mocked tests.
