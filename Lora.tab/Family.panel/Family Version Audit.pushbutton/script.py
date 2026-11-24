# -*- coding: utf-8 -*-
"""Read-only RFA version and family-content audit; never saves a family."""

import os
import sys

from pyrevit import DB, forms, script

_LIB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from LoraFamilyVersionAudit import (inspect_family_file, scan_family_folder,
                                    scan_family_paths, sha256_file)
from LoraFamilyCompatibility import analyze_family_document

logger = script.get_logger()
mode = forms.CommandSwitchWindow.show(
    ["Select family files", "Scan a family folder", "Analyze family compatibility"],
    message="Choose a read-only family audit workflow")
if not mode:
    script.exit()

if mode == "Analyze family compatibility":
    family_path = forms.pick_file(file_ext="rfa", multi_file=False,
                                  title="Select a family to inspect (read-only)")
    if not family_path:
        script.exit()
    target_version = forms.SelectFromList.show(
        ["2023", "2024", "2025", "2026"],
        title="Select target Revit version",
        button_name="Analyze")
    if not target_version:
        script.exit()
    header = inspect_family_file(family_path, DB.BasicFileInfo,
                                 __revit__.Application.VersionNumber)
    if header.get("status") != "success":
        forms.alert("Family header metadata is unavailable. Content analysis was not started.\n\n"
                    "Warnings: {}\n\nNo family was opened or modified.".format(
                        "; ".join(item.get("message", "unknown error")
                                  for item in header.get("warnings", []))),
                    title="Lora · Family Compatibility Audit", warn_icon=True)
        script.exit()
    host_version = __revit__.Application.VersionNumber
    saved_version = header.get("saved_format_version")
    if saved_version is None or saved_version > int(host_version):
        classification = ("incompatible" if saved_version is not None and
                          saved_version > int(target_version) else "requires_manual_review")
        forms.alert("Saved format: {}\nTarget Revit: {}\nRunning Revit: {}\n\n"
                    "Compatibility: {}\nContent: not_inspected\n\n"
                    "The running Revit cannot safely inspect this newer or unknown family format."
                    .format(header.get("saved_format") or "unknown", target_version,
                            host_version, classification),
                    title="Lora · Family Compatibility Audit", warn_icon=True)
        script.exit()

    requested_path = os.path.normcase(os.path.abspath(family_path))
    existing_family_open = False
    try:
        for open_document in __revit__.Application.Documents:
            open_path = open_document.PathName
            if open_path and os.path.normcase(os.path.abspath(open_path)) == requested_path:
                existing_family_open = True
                break
    except Exception as exc:
        logger.exception("Could not verify open Revit documents before family inspection")
        forms.alert("Could not verify whether the selected family is already open. "
                    "Content analysis was not started.\n\n{}: {}".format(
                        type(exc).__name__, exc),
                    title="Lora · Family Compatibility Audit", warn_icon=True)
        script.exit()
    if existing_family_open:
        forms.alert("This family is already open in Revit. Close it before running the audit; "
                    "the audit will not inspect or close an existing document.",
                    title="Lora · Family Compatibility Audit", warn_icon=True)
        script.exit()

    try:
        source_hash_before = sha256_file(family_path)
    except Exception as exc:
        logger.exception("Could not hash family before compatibility inspection")
        forms.alert("Could not verify the source family before inspection; analysis was not started.\n\n"
                    "{}: {}".format(type(exc).__name__, exc),
                    title="Lora · Family Compatibility Audit", warn_icon=True)
        script.exit()

    family_document = None
    result = None
    close_warning = None
    try:
        family_document = __revit__.Application.OpenDocumentFile(family_path)
        result = analyze_family_document(
            family_document, DB, target_version, host_version, saved_version)
    except Exception as exc:
        logger.exception("Family Compatibility Audit failed")
        result = {"status": "failed", "compatibility_classification": "requires_manual_review",
                  "target_revit_version": target_version,
                  "saved_format_version": saved_version,
                  "fields": {}, "warnings": [{"field": "OpenDocumentFile",
                                                "status": "failed",
                                                "message": "{}: {}".format(type(exc).__name__, exc)}],
                  "source_modified": False}
    finally:
        if family_document is not None:
            try:
                if not family_document.Close(False):
                    close_warning = "Revit reported that the temporary family document did not close."
            except Exception as exc:
                close_warning = "Could not close temporary family document: {}: {}".format(
                    type(exc).__name__, exc)
    try:
        source_hash_after = sha256_file(family_path)
    except Exception as exc:
        source_hash_after = None
        close_warning = "Could not verify the family after inspection: {}: {}".format(
            type(exc).__name__, exc)
    if source_hash_after != source_hash_before:
        close_warning = "The source file hash changed during inspection; do not treat this audit as read-only verified."
        if result is not None:
            result["source_modified"] = True
            result["status"] = "partial"

    field_lines = []
    for name, item in sorted((result or {}).get("fields", {}).items()):
        field_lines.append("{}: {} ({} records)".format(
            name, item.get("status", "unknown"), len(item.get("records", []))))
    warning_lines = [item.get("field", "warning") + ": " + item.get("message", "")
                     for item in (result or {}).get("warnings", [])[:12]]
    if close_warning:
        warning_lines.append(close_warning)
    strategy = forms.SelectFromList.show(
        ["Manual reconstruction review (not automated)", "Retain source family; no migration"],
        title="Choose a non-executing strategy for review",
        button_name="Preview")
    if strategy:
        forms.alert("Proposed strategy: {}\n\nPreview only: no output family was created and no changes were made.\n"
                    "Any future reconstruction requires explicit execution approval and isolated test validation."
                    .format(strategy), title="Lora · Family Compatibility Preview", warn_icon=True)
    forms.alert("Saved format: {}\nTarget Revit: {}\nRunning Revit: {}\n"
                "Analysis: {}\nCompatibility: {}\n\n{}\n\nWarnings:\n{}\n\n"
                "Source SHA256 before: {}\nSource SHA256 after: {}\n"
                "The family was closed without saving."
                .format(header.get("saved_format") or "unknown", target_version, host_version,
                        (result or {}).get("status", "failed"),
                        (result or {}).get("compatibility_classification", "requires_manual_review"),
                        "\n".join(field_lines) or "Content not inspected",
                        "\n".join(warning_lines) or "none",
                        source_hash_before, source_hash_after or "unavailable"),
                title="Lora · Family Compatibility Audit",
                warn_icon=(not result or result.get("status") != "success" or
                           result.get("compatibility_classification") != "compatible" or
                           bool(close_warning)))
    script.exit()

if mode == "Select family files":
    family_paths = forms.pick_file(file_ext="rfa", multi_file=True,
                                   title="Select Revit family files (read-only)")
    if not family_paths:
        script.exit()
    if isinstance(family_paths, basestring):
        family_paths = [family_paths]
    if len(family_paths) == 1:
        result = inspect_family_file(family_paths[0], DB.BasicFileInfo,
                                     __revit__.Application.VersionNumber)
    else:
        result = scan_family_paths(family_paths, DB.BasicFileInfo,
                                   __revit__.Application.VersionNumber)
else:
    folder_path = forms.pick_folder(title="Select a folder of Revit families (read-only)")
    if not folder_path:
        script.exit()
    result = scan_family_folder(folder_path, DB.BasicFileInfo,
                                __revit__.Application.VersionNumber)

if "files_examined" in result:
    statuses = ", ".join("{}: {}".format(key, value)
                          for key, value in sorted(result.get("status_counts", {}).items()))
    relations = ", ".join("{}: {}".format(key, value)
                           for key, value in sorted(result.get("format_relation_counts", {}).items()))
    examples = "\n".join(os.path.basename(item.get("file_path") or "")
                         for item in result.get("examples", []))
    forms.alert("Status: {}\nFamilies examined: {}\nInspection results: {}\n"
                "Format relations: {}\nWarnings: {}\n\n"
                "{}\n\nOnly family file headers were read; no family was opened or modified."
                .format(result.get("status"), result.get("files_examined"),
                        statuses or "none", relations or "none",
                        result.get("warning_count"),
                        "Warning examples:\n" + examples if examples else ""),
                title="Lora · Family Version Audit",
                warn_icon=(result.get("status") != "success"))
    script.exit()

if result.get("status") != "success":
    detail = "\n".join("{}: {}".format(item.get("field"), item.get("message"))
                      for item in result.get("warnings", []))
    forms.alert("Saved format: {}\nRunning Revit: {}\nAssessment: {}\n\n{}\n\n"
                "No family was opened or modified.".format(
                    result.get("saved_format") or "unknown",
                    result.get("running_revit_version") or "unknown",
                    result.get("openability_assessment") or "unknown", detail),
                title="Lora · Family Version Audit", warn_icon=True)
else:
    saved = result.get("saved_format") or "unknown"
    host = result.get("running_revit_version") or "unknown"
    relation = result.get("format_relation")
    if relation == "newer_than_host":
        assessment = ("This file is saved in a newer Revit format. Native Revit does not save "
                      "families back to older formats; this command does not downgrade it.")
    elif relation == "older_than_host":
        assessment = ("This file is saved in an older format. Revit may upgrade it when opened; "
                      "this command did not open it.")
    elif relation == "same_major_version":
        assessment = "The saved major format matches this Revit version; family content was not validated."
    else:
        assessment = "Version metadata is inconsistent; compatibility is unknown."
    forms.alert("Family: {}\nSaved format: {}\nRunning Revit: {}\n\n{}\n\n"
                "Read-only header inspection only. No family was opened, upgraded, saved, or modified.".format(
                    os.path.basename(family_path), saved, host, assessment),
                title="Lora · Family Version Audit",
                warn_icon=(relation in ("newer_than_host", "inconsistent_metadata")))
