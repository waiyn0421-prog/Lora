# Lora Revit Extension Development Instructions

## Project identity

- Lora is an existing pyRevit extension. Continue development in this repository; never rebuild it from scratch.
- The supported Autodesk Revit versions are 2023, 2024, 2025, and 2026. Verify each command against the versions it claims to support.
- The existing GitHub repository, `https://github.com/waiyn0421-prog/Lora`, is the source of truth. Do not replace it with a new project or repository.
- The project root is the extension root. It contains `extension.yaml`, shared code in `lib/`, and UI commands under `Lora.tab/`.

## Development rules

- Preserve all existing working features and project conventions.
- Inspect the relevant existing code and its callers before modifying anything.
- Do not delete or rename existing files without explicit user approval.
- Avoid duplicated functions and unnecessary dependencies; reuse shared helpers where appropriate.
- Maintain the existing project architecture unless the user approves a change.
- Keep changes focused and reviewable.

## Compatibility

- Consider Revit API differences across Revit 2023–2026 before changing API calls.
- Check the pyRevit engine/runtime compatibility of scripts and imported libraries.
- Maintain compatibility with existing Python scripts and their supported engine syntax; do not assume CPython-only features are available.
- Isolate version-specific implementations behind clear helpers or modules when needed.
- Never claim successful compatibility for a Revit version or pyRevit engine without actually testing it. State which versions and environments were tested and which were not.

## Existing features verified in this repository

These notes describe the implementations found in the current repository. A command's presence does not establish that it has been runtime-tested in every supported Revit version.

- **MEP Elevation Updater** — `Lora.tab/MEP.panel/UpdateMEPElevations.pushbutton/script.py` updates elevation-related parameter values for selected pipes, ducts, cable trays, and conduits. It includes location/floor lookup, service and size text handling, and parameter-writing helpers. The script identifies Revit 2023–2026 as its target; runtime validation is required.
- **MEP Tag Generator** — `Lora.tab/MEP.panel/TagCreation.pushbutton/script.py` provides a Tag Generator flow for setting up MEP tag families/designs, alongside a Parameter Generator option. It can inspect an external family and interact with shared parameters. Keep the distinction between the two flows clear when changing this combined command.
- **Parameter Generator** — The same `TagCreation` command implements shared-parameter definition creation and project binding for MEP categories. Shared parameter utilities also exist in `lib/LoraParameterEngine.py`. Do not assume parameter changes are limited to tag-family content.
- **Hide Section Annotations** — `Lora.tab/View.panel/HideSection.pushbutton/script.py` implements Hide Section. It finds section/detail-view marker symbols in the active plan view, matches them to views placed on a chosen sheet, and hides or shows markers accordingly. It does not delete section views. The feature is present under the UI name “Hide Section.”
- **Auto Dimension** — `Lora.tab/Documentation.panel/Auto Dim.pushbutton/script.py` uses helpers in `lib/LoraAutoDim.py` to prepare and create dimensions. The current implementation targets straight walls and compatible hosted door/window references; curved walls and elements without usable references can be skipped. Its source targets IronPython 2.7 / pyRevit and Revit 2023–2026; verify actual host behavior before making broader claims.
- **Table Generator** — `Lora.tab/Documentation.panel/TableGen.pushbutton/script.py` imports CSV data and creates a text-and-grid table in a Legend or Drafting View. It may duplicate an existing Legend or create/use a Drafting View according to the selected workflow.
- **Data Sync** — `Lora.tab/Documentation.panel/Data Sync.pushbutton/script.py` exports Revit schedule data to `.xlsx` and imports edited workbook values back to a selected schedule. Preserve its workbook identifiers and schedule/parameter matching safeguards when modifying import/export behavior.
- **Boundary Converter** — No command with the exact name “Boundary Converter” was found. The related implemented command is **Draw Area Boundary**, at `Lora.tab/Documentation.panel/Draw Area Boundary.pushbutton/script.py`, with implementation in `lib/LoraAreaBoundary.py`. It converts selected Detail Lines and Model Lines into Area Boundary Lines for an Area Plan and includes Area Scheme management. It does not automatically place Area elements. Refer to this as Draw Area Boundary unless the user confirms a separate Boundary Converter feature is intended.

## Development workflow

1. Analyze the existing implementation and repository state before proposing or making changes.
2. Explain the proposed change and identify the affected files before implementation.
3. Implement changes as isolated, focused tasks that preserve unrelated behavior.
4. Run available relevant checks and report exactly what was run and its result. Do not imply a Revit runtime check was run when it was not.
5. Review the Git diff for scope, unintended changes, and compatibility concerns.
6. Provide clear instructions for the user to test the change in Revit, including relevant Revit and pyRevit versions.
7. Request user approval before creating commits or pushing changes.

## GitHub protection

- Never force push.
- Never push directly to `main` without explicit approval.
- Preserve the stable release and avoid changes that replace or destabilize it without approval.
- Use feature branches for significant development; keep `main` stable.

## Documentation

- Document new functions and non-obvious behavior in the code or appropriate project documentation.
- Maintain accurate installation instructions when installation behavior changes.
- Record Revit-version and pyRevit-engine compatibility, distinguishing targets from versions actually tested.
- Document known issues, limitations, and workarounds in the appropriate documentation.
