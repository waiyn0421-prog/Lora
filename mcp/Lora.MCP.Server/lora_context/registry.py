"""Verified, descriptive registry of the current Lora ribbon commands.

This registry describes capabilities for recommendations only. It never imports or
executes a ribbon command.
"""

COMMANDS = (
    {
        "name": "Family Generator",
        "path": "Lora.tab/Annotation.panel/Family Generator.pushbutton/script.py",
        "purpose": "Create a category-specific family from a Revit family template or a copy of an existing tag family, add/reuse chosen shared parameters, and optionally bind/load it into the project.",
        "required_input": "Editable project document; category; parameter definitions/binding/group; shared-parameter file; family template or source RFA; output folder/name. Project binding and loading are separately confirmed.",
        "supported_context": "Project document plus a compatible family template/source family; category is chosen from the supported category list in the script.",
        "limitations": "Does not compose new tag label graphics automatically; labels must already exist in the chosen template/family and should be checked against parameters.",
        "model_effect": "Modifies the project if parameter binding or family loading is confirmed; also creates an external RFA and may create shared-parameter definitions.",
        "read_only": False,
    },
    {
        "name": "Tag Studio",
        "path": "Lora.tab/Annotation.panel/TagStudio.pushbutton/script.py",
        "purpose": "Create a tag design definition with a parameter setup preview, create/reuse shared definitions and project bindings after confirmation, or inspect a selected external family and save a JSON design definition.",
        "required_input": "Editable project, selected category, simple or advanced parameter fields, shared-parameter file; or an external RFA for inspection.",
        "supported_context": "Editable Revit project for setup; external RFA file for family inspection.",
        "limitations": "Inspection is a read-only subflow; setup commits a project transaction. A saved JSON design is not itself a Revit tag family. Supported categories are script-defined.",
        "model_effect": "Setup can create/reuse shared parameters and add/update category bindings in the project; inspection only reads an external family and may write a JSON file.",
        "read_only": False,
    },
    {
        "name": "Auto Dim",
        "path": "Lora.tab/Documentation.panel/Auto Dim.pushbutton/script.py",
        "purpose": "Preview and create duplicate-aware linear dimension chains using selected dimension rules and style.",
        "required_input": "Active Floor Plan, Ceiling Plan, or Engineering Plan; dimension profile/rules, linear dimension type, offset/spacing, and confirmation.",
        "supported_context": "Active plan view; straight walls and compatible hosted door/window references.",
        "limitations": "Curved walls and unusable references can be skipped. Columns, beams, grids, floors, and stairs are scan-only, not dimensioned by this release.",
        "model_effect": "Creates Revit dimensions in a transaction after preview confirmation.",
        "read_only": False,
    },
    {
        "name": "Data Sync",
        "path": "Lora.tab/Documentation.panel/Data Sync.pushbutton/script.py",
        "purpose": "Export an element schedule to an XLSX workbook with matching IDs and original-value baselines, or import changed cells back to matching Revit element parameters.",
        "required_input": "Element schedule; export destination for export, or a Lora-exported/compatible XLSX with hidden identifier and baseline columns for import.",
        "supported_context": "Revit project document with eligible element schedules and accessible workbook file.",
        "limitations": "Import only applies values differing from the baseline; blank cells are no-ops; read-only, formula/calculated, unsupported, unmatched and ElementId values are skipped. Keep hidden IDs/headings intact.",
        "model_effect": "Export reads Revit and writes an XLSX file. Import modifies writable Revit parameter values in a transaction.",
        "read_only": False,
    },
    {
        "name": "Draw Area Boundary",
        "path": "Lora.tab/Documentation.panel/Draw Area Boundary.pushbutton/script.py",
        "purpose": "Convert selected Detail Lines/Model Lines into Area Boundary Lines, choosing or creating an Area Plan and managing Area Scheme options.",
        "required_input": "Selected supported curves and an active floor/engineering/area plan; target level/Area Plan and Area Scheme choices; endpoint tolerance.",
        "supported_context": "Floor Plan, Engineering Plan, or Area Plan with a resolvable level and applicable Area Scheme.",
        "limitations": "Only bound horizontal lines and circular arcs are supported. Sloped/non-planar/unsupported curves can be skipped. It does not place Area elements. New Area Plan creation occurs in Revit; custom Area Scheme creation/deletion directs users to native Revit workflows.",
        "model_effect": "Creates Area Plans and Area Boundary Lines in transactions; the scheme workflow may rename an existing non-protected scheme.",
        "read_only": False,
    },
    {
        "name": "TableGen",
        "path": "Lora.tab/Documentation.panel/TableGen.pushbutton/script.py",
        "purpose": "Import CSV rows as Revit text notes and detail-line grid elements in a Legend or Drafting View.",
        "required_input": "CSV path, destination choice (Legend or Drafting View), title/layout settings, and available TextNoteType.",
        "supported_context": "Revit project document; destination is a Legend or Drafting View selected/created by the command.",
        "limitations": "Imports CSV cells as-is; layout uses text notes and detail curves. Requires a usable text-note type and a valid CSV.",
        "model_effect": "Creates or duplicates a view and creates text/detail-line elements in a transaction; activates the destination view.",
        "read_only": False,
    },
    {
        "name": "InPlace to Family",
        "path": "Lora.tab/Family.panel/InPlace to Family.pushbutton/script.py",
        "purpose": "Convert a selected in-place model family into a loadable RFA, load it into the project, and place a family instance.",
        "required_input": "Selected in-place family; output family name/path, compatible RFT template, category, and conversion/material/solid options. The UI includes a delete-original option.",
        "supported_context": "Revit project with a selected or pickable in-place model family and a compatible family template.",
        "limitations": "Geometry/material conversion can produce warnings; unsupported solids or template/category mismatch can fail. Review the option to delete the original carefully.",
        "model_effect": "Creates an external RFA, loads it, places a new project instance, and may delete the original if selected.",
        "read_only": False,
    },
    {
        "name": "Family Version Audit",
        "path": "Lora.tab/Family.panel/Family Version Audit.pushbutton/script.py",
        "purpose": "Audit saved RFA format in a batch/folder or inspect a selected family document read-only for compatibility review.",
        "required_input": "Readable .rfa files; compatibility review also requires a selected target Revit version.",
        "supported_context": "Uses BasicFileInfo for header scans. Compatibility review temporarily opens a family in the running Revit API, inventories family content, and closes the document without saving.",
        "limitations": "Content inventory is bounded and incomplete for geometry/features not explicitly listed. Only a saved format newer than the target is automatically classified incompatible; other content remains requires_manual_review. Target-version API behavior is not simulated. Revit does not natively save back to an older format; this command does not downgrade or reconstruct.",
        "model_effect": "Reads RFA metadata and family content. The compatibility flow does not start a transaction, save, load, create, or modify a family/project; temporary documents are closed with Close(False).",
        "read_only": True,
    },
    {
        "name": "Tag Creation",
        "path": "Lora.tab/MEP.panel/TagCreation.pushbutton/script.py",
        "purpose": "Open either the MEP Parameter Generator or Tag Generator readiness flow.",
        "required_input": "Editable project and chosen flow; Parameter Generator additionally needs MEP categories, shared parameter file and definitions; Tag Generator checks expected bindings.",
        "supported_context": "Editable Revit project and script-supported MEP tag categories.",
        "limitations": "Parameter Generator creates/binds shared parameters. Tag Generator checks readiness but explicitly does not automatically author or load a new tag-family RFA.",
        "model_effect": "Parameter Generator modifies shared-parameter/project binding data. Tag readiness flow reads project data; it does not create tags or tag-family geometry.",
        "read_only": False,
    },
    {
        "name": "Refresh",
        "path": "Lora.tab/MEP.panel/UpdateMEPElevations.pushbutton/script.py",
        "purpose": "Calculate floor-relative top/bottom/relative elevations and service-size/type/status text for selected or picked MEP elements.",
        "required_input": "Selected or interactively picked pipes, ducts, cable trays, and conduits; required target parameters must be bound/writable.",
        "supported_context": "Revit project with selected/picked supported MEP curves and usable floor/element geometry.",
        "limitations": "Elements without geometry or a floor below are skipped or marked for manual review; results require parameter validation. Source identifies Revit 2023–2026 as targets, not runtime-tested here.",
        "model_effect": "Writes elevation/service/size/status parameters in a Revit transaction.",
        "read_only": False,
    },
    {
        "name": "Hide Section",
        "path": "Lora.tab/View.panel/HideSection.pushbutton/script.py",
        "purpose": "Keep section marker symbols associated with the active view's sheet visible and hide other matched section marker symbols.",
        "required_input": "Active non-template plan view placed on a sheet and confirmation of the hide operation.",
        "supported_context": "A sheet-placed plan view containing section symbols.",
        "limitations": "Uses the actual viewer marker symbols and sheet/view matching; active view must be placed on a sheet. Does not delete section views.",
        "model_effect": "Changes hidden state of marker elements in the active view in a transaction.",
        "read_only": False,
    },
    {
        "name": "View Range",
        "path": "Lora.tab/View.panel/ViewRange.pushbutton/script.py",
        "purpose": "Display a visual editor for a selected plan view's Top, Cut, Bottom, and View Depth settings.",
        "required_input": "An active 3D view for the preview and a selected/picked plan view; new plane levels/offsets and apply confirmation.",
        "supported_context": "3D active view used for preview plus a target plan view whose view range can be edited.",
        "limitations": "Not a 3D view-range editor. Template-controlled target views and invalid/unlimited plane combinations may restrict edits; no runtime validation is claimed here.",
        "model_effect": "Edits the target plan view range in a Revit transaction; preview uses temporary DirectContext3D graphics.",
        "read_only": False,
    },
)


def list_commands():
    """Return copies so consumers cannot mutate the registry accidentally."""
    return [dict(command) for command in COMMANDS]


def find_command(name):
    wanted = (name or "").strip().casefold()
    for command in COMMANDS:
        if command["name"].casefold() == wanted:
            return dict(command)
    return None
