# -*- coding: utf-8 -*-
"""Read-only inspection of the saved Revit format version in an RFA header."""

import hashlib
import os


def inspect_family_file(file_path, basic_file_info_api, running_version):
    """Inspect BasicFileInfo only; never open, upgrade, save, or modify the RFA."""
    path = str(file_path or "")
    if not path or os.path.splitext(path)[1].lower() != ".rfa":
        return {"status": "error", "error": "invalid_family_path",
                "message": "Select a Revit family file with the .rfa extension.",
                "source_modified": False}

    host_version = _major_version(running_version)
    result = {"status": "unknown", "file_path": path,
              "running_revit_version": host_version,
              "saved_format": None, "saved_format_version": None,
              "is_saved_in_later_version": None, "format_relation": "unknown",
              "openability_assessment": "unknown",
              "native_downgrade_supported": False,
              "source_modified": False, "warnings": []}
    info = None
    try:
        info = basic_file_info_api.Extract(path)
        if info is None:
            raise ValueError("BasicFileInfo.Extract returned no metadata")
    except Exception as exc:
        result["warnings"].append(_warning("BasicFileInfo.Extract", exc))
        return result

    try:
        saved_format = info.Format
        result["saved_format"] = str(saved_format) if saved_format is not None else None
        result["saved_format_version"] = _major_version(saved_format)
    except Exception as exc:
        result["warnings"].append(_warning("BasicFileInfo.Format", exc))

    try:
        result["is_saved_in_later_version"] = bool(info.IsSavedInLaterVersion)
    except Exception as exc:
        result["warnings"].append(_warning("BasicFileInfo.IsSavedInLaterVersion", exc))

    saved_version = result["saved_format_version"]
    later_flag = result["is_saved_in_later_version"]
    if saved_version is not None and host_version is not None:
        if saved_version > host_version:
            result["format_relation"] = "newer_than_host"
            result["openability_assessment"] = "newer_format_not_supported_by_older_host"
        elif saved_version == host_version:
            result["format_relation"] = "same_major_version"
            result["openability_assessment"] = "format_matches_host; content_compatibility_not_assessed"
        else:
            result["format_relation"] = "older_than_host"
            result["openability_assessment"] = "older_format_may_be_upgraded_when_opened"

        expected_later = saved_version > host_version
        if later_flag is not None and later_flag != expected_later:
            result["format_relation"] = "inconsistent_metadata"
            result["openability_assessment"] = "unknown_due_to_inconsistent_metadata"
            result["warnings"].append({"field": "version_metadata",
                "code": "inconsistent_version_indicators",
                "message": "BasicFileInfo.Format and IsSavedInLaterVersion disagree."})
        else:
            result["status"] = "success"
    else:
        result["warnings"].append({"field": "version_metadata", "code": "version_unavailable",
            "message": "A valid saved format version and running Revit version are required."})

    try:
        dispose = getattr(info, "Dispose", None)
        if callable(dispose):
            dispose()
    except Exception as exc:
        result["warnings"].append(_warning("BasicFileInfo.Dispose", exc))
    return result


def scan_family_paths(paths, basic_file_info_api, running_version,
                      sample_limit=20, progress_callback=None):
    """Inspect a batch of paths without opening family documents.

    The result retains aggregate counts and a bounded sample of non-success
    records, so folder scans do not keep an unbounded result list in memory.
    """
    result = _empty_scan_result()
    if sample_limit < 0:
        result["status"] = "error"
        result["error"] = "invalid_sample_limit"
        return result

    try:
        iterator = iter(paths)
    except Exception as exc:
        result["status"] = "error"
        result["error"] = "invalid_paths"
        result["warnings"].append(_warning("paths", exc))
        return result

    for path in iterator:
        item = inspect_family_file(path, basic_file_info_api, running_version)
        result["files_examined"] += 1
        status = item.get("status", "unknown")
        result["status_counts"][status] = result["status_counts"].get(status, 0) + 1
        relation = item.get("format_relation", "unknown")
        result["format_relation_counts"][relation] = (
            result["format_relation_counts"].get(relation, 0) + 1)
        if item.get("warnings") or status != "success":
            if len(result["examples"]) < sample_limit:
                result["examples"].append(item)
            result["warning_count"] += max(1, len(item.get("warnings", [])))
        if progress_callback is not None:
            try:
                progress_callback(result["files_examined"], item)
            except Exception as exc:
                result["warnings"].append(_warning("progress_callback", exc))

    result["complete"] = not result["warnings"] and result["warning_count"] == 0
    if result["warning_count"] or result["warnings"]:
        result["status"] = "partial"
    else:
        result["status"] = "success"
    return result


def scan_family_folder(folder_path, basic_file_info_api, running_version,
                       sample_limit=20, progress_callback=None):
    """Recursively audit RFA headers under a folder; never open or write files."""
    folder = str(folder_path or "")
    if not folder or not os.path.isdir(folder):
        result = _empty_scan_result()
        result.update({"status": "error", "error": "invalid_folder"})
        result["warnings"].append({"field": "folder_path", "code": "invalid_folder",
                                   "message": "Select an accessible folder to scan."})
        return result

    result = _empty_scan_result()
    if sample_limit < 0:
        result.update({"status": "error", "error": "invalid_sample_limit"})
        return result

    for current, directories, filenames in os.walk(folder, onerror=lambda exc: _record_walk_error(result, exc)):
        directories.sort()
        for filename in sorted(filenames):
            if os.path.splitext(filename)[1].lower() != ".rfa":
                continue
            path = os.path.join(current, filename)
            item = inspect_family_file(path, basic_file_info_api, running_version)
            result["files_examined"] += 1
            status = item.get("status", "unknown")
            result["status_counts"][status] = result["status_counts"].get(status, 0) + 1
            relation = item.get("format_relation", "unknown")
            result["format_relation_counts"][relation] = (
                result["format_relation_counts"].get(relation, 0) + 1)
            if item.get("warnings") or status != "success":
                if len(result["examples"]) < sample_limit:
                    result["examples"].append(item)
                result["warning_count"] += max(1, len(item.get("warnings", [])))
            if progress_callback is not None:
                try:
                    progress_callback(result["files_examined"], item)
                except Exception as exc:
                    result["warnings"].append(_warning("progress_callback", exc))

    result["complete"] = not result["warnings"] and result["warning_count"] == 0
    result["status"] = "success" if result["complete"] else "partial"
    return result


def sha256_file(file_path, chunk_size=1024 * 1024):
    """Return a streaming SHA256 digest without writing or buffering the file."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with open(file_path, "rb") as stream:
        while True:
            block = stream.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _empty_scan_result():
    return {"status": "not_started", "files_examined": 0,
            "status_counts": {}, "format_relation_counts": {},
            "warning_count": 0, "examples": [], "warnings": [],
            "complete": False}


def _record_walk_error(result, exc):
    result["warnings"].append(_warning("folder_scan", exc))


def _major_version(value):
    try:
        parsed = int(str(value).strip())
        return parsed if 2000 <= parsed <= 2100 else None
    except Exception:
        return None


def _warning(field, exc):
    return {"field": field, "code": "api_access_failed",
            "exception_type": type(exc).__name__, "message": str(exc)[:240]}
