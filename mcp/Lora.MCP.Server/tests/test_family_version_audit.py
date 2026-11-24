import ast
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "lib"))
sys.path.insert(0, str(REPOSITORY_ROOT / "mcp" / "Lora.MCP.Server"))

from LoraFamilyVersionAudit import (inspect_family_file, scan_family_folder,
                                    scan_family_paths, sha256_file)
from lora_context.registry import COMMANDS, find_command


class FakeBasicFileInfo(object):
    extracted_path = None
    instance = None

    @classmethod
    def Extract(cls, path):
        cls.extracted_path = path
        if isinstance(cls.instance, Exception):
            raise cls.instance
        return cls.instance


class FakeInfo(object):
    def __init__(self, file_format="2024", is_later=False):
        self.Format = file_format
        self.IsSavedInLaterVersion = is_later
        self.disposed = False

    def Dispose(self):
        self.disposed = True


class FamilyVersionAuditTests(unittest.TestCase):
    def setUp(self):
        FakeBasicFileInfo.extracted_path = None
        FakeBasicFileInfo.instance = None

    def test_newer_family_format_is_identified_without_downgrade_claim(self):
        info = FakeInfo("2025", True)
        FakeBasicFileInfo.instance = info
        result = inspect_family_file("C:/families/new.rfa", FakeBasicFileInfo, "2024")
        self.assertEqual(result["format_relation"], "newer_than_host")
        self.assertEqual(result["openability_assessment"], "newer_format_not_supported_by_older_host")
        self.assertFalse(result["native_downgrade_supported"])
        self.assertFalse(result["source_modified"])
        self.assertTrue(info.disposed)

    def test_older_and_same_major_formats_are_reported_without_content_guarantees(self):
        FakeBasicFileInfo.instance = FakeInfo("2023", False)
        older = inspect_family_file("C:/families/old.rfa", FakeBasicFileInfo, 2024)
        self.assertEqual(older["format_relation"], "older_than_host")
        self.assertEqual(older["openability_assessment"], "older_format_may_be_upgraded_when_opened")
        FakeBasicFileInfo.instance = FakeInfo("2024", False)
        same = inspect_family_file("C:/families/same.rfa", FakeBasicFileInfo, 2024)
        self.assertEqual(same["format_relation"], "same_major_version")
        self.assertIn("content_compatibility_not_assessed", same["openability_assessment"])

    def test_invalid_extension_never_calls_revit_metadata_api(self):
        result = inspect_family_file("C:/families/project.rvt", FakeBasicFileInfo, 2024)
        self.assertEqual(result["error"], "invalid_family_path")
        self.assertIsNone(FakeBasicFileInfo.extracted_path)
        self.assertFalse(result["source_modified"])

    def test_extract_failure_returns_unknown_with_api_warning(self):
        FakeBasicFileInfo.instance = RuntimeError("unsupported newer metadata")
        result = inspect_family_file("C:/families/new.rfa", FakeBasicFileInfo, 2024)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["warnings"][0]["field"], "BasicFileInfo.Extract")
        self.assertEqual(result["warnings"][0]["exception_type"], "RuntimeError")

    def test_conflicting_version_indicators_remain_unknown(self):
        FakeBasicFileInfo.instance = FakeInfo("2025", False)
        result = inspect_family_file("C:/families/conflict.rfa", FakeBasicFileInfo, 2024)
        self.assertEqual(result["format_relation"], "inconsistent_metadata")
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["source_modified"])

    def test_invalid_or_unavailable_version_data_is_not_guessed(self):
        FakeBasicFileInfo.instance = FakeInfo("format-x", True)
        result = inspect_family_file("C:/families/unknown.rfa", FakeBasicFileInfo, 2024)
        self.assertEqual(result["format_relation"], "unknown")
        self.assertEqual(result["status"], "unknown")
        FakeBasicFileInfo.instance = FakeInfo("2025", True)
        host_unknown = inspect_family_file("C:/families/new.rfa", FakeBasicFileInfo, "unknown")
        self.assertEqual(host_unknown["format_relation"], "unknown")

    def test_family_version_audit_is_registered_as_read_only_command(self):
        command = find_command("Family Version Audit")
        self.assertIsNotNone(command)
        self.assertTrue(command["read_only"])
        self.assertEqual(command["path"],
            "Lora.tab/Family.panel/Family Version Audit.pushbutton/script.py")
        self.assertEqual(len([item for item in COMMANDS if item["name"] == "Family Version Audit"]), 1)

    def test_ui_entrypoint_has_no_document_writes_or_transactions(self):
        script_path = (REPOSITORY_ROOT / "Lora.tab" / "Family.panel" /
                       "Family Version Audit.pushbutton" / "script.py")
        tree = ast.parse(script_path.read_text(encoding="utf-8"))
        calls = [node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        call_names = [node.func.id for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        self.assertIn("pick_file", calls)
        self.assertIn("inspect_family_file", call_names)
        forbidden = {"NewFamilyDocument", "Save", "SaveAs", "LoadFamily",
                     "Transaction", "SubTransaction", "TransactionGroup", "Delete", "Commit", "RollBack"}
        self.assertFalse(forbidden.intersection(calls))
        self.assertFalse(forbidden.intersection(call_names))

    def test_compatibility_path_closes_temporary_document_without_saving(self):
        script_path = (REPOSITORY_ROOT / "Lora.tab" / "Family.panel" /
                       "Family Version Audit.pushbutton" / "script.py")
        tree = ast.parse(script_path.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertTrue(any(node.func.attr == "OpenDocumentFile" for node in calls))
        self.assertTrue(any(node.func.attr == "Close" and len(node.args) == 1 and
                            isinstance(node.args[0], ast.Constant) and node.args[0].value is False
                            for node in calls))
        forbidden = {"Save", "SaveAs", "Transaction", "SubTransaction", "TransactionGroup",
                     "LoadFamily", "Delete", "Commit"}
        self.assertFalse(forbidden.intersection(node.func.attr for node in calls))

    def test_batch_scan_counts_each_result_and_bounds_warning_examples(self):
        FakeBasicFileInfo.instance = FakeInfo("2024", False)
        results = scan_family_paths(
            ["C:/families/a.rfa", "C:/families/b.rfa"],
            FakeBasicFileInfo, 2024, sample_limit=1)
        self.assertEqual(results["status"], "success")
        self.assertEqual(results["files_examined"], 2)
        self.assertEqual(results["status_counts"], {"success": 2})
        self.assertEqual(results["format_relation_counts"], {"same_major_version": 2})
        self.assertTrue(results["complete"])
        self.assertEqual(results["examples"], [])

    def test_batch_scan_retains_only_bounded_unknown_examples(self):
        FakeBasicFileInfo.instance = RuntimeError("invalid family header")
        results = scan_family_paths(
            ["C:/families/a.rfa", "C:/families/b.rfa", "C:/families/c.rfa"],
            FakeBasicFileInfo, 2024, sample_limit=1)
        self.assertEqual(results["status"], "partial")
        self.assertFalse(results["complete"])
        self.assertEqual(results["files_examined"], 3)
        self.assertEqual(results["status_counts"], {"unknown": 3})
        self.assertEqual(results["warning_count"], 3)
        self.assertEqual(len(results["examples"]), 1)

    def test_folder_scan_is_recursive_case_insensitive_and_reports_progress(self):
        FakeBasicFileInfo.instance = FakeInfo("2024", False)
        progress = []
        with tempfile.TemporaryDirectory() as folder:
            nested = os.path.join(folder, "nested")
            os.makedirs(nested)
            for path in (os.path.join(folder, "one.rfa"),
                         os.path.join(nested, "two.RFA"),
                         os.path.join(folder, "ignore.rvt")):
                with open(path, "wb"):
                    pass
            results = scan_family_folder(
                folder, FakeBasicFileInfo, 2024,
                progress_callback=lambda count, item: progress.append((count, item["status"])))
        self.assertEqual(results["status"], "success")
        self.assertEqual(results["files_examined"], 2)
        self.assertEqual(results["status_counts"], {"success": 2})
        self.assertEqual(progress, [(1, "success"), (2, "success")])

    def test_folder_scan_large_result_keeps_bounded_memory_samples(self):
        FakeBasicFileInfo.instance = RuntimeError("unsupported family format")
        with tempfile.TemporaryDirectory() as folder:
            for index in range(250):
                with open(os.path.join(folder, "family-{}.rfa".format(index)), "wb"):
                    pass
            results = scan_family_folder(folder, FakeBasicFileInfo, 2024, sample_limit=5)
        self.assertEqual(results["files_examined"], 250)
        self.assertEqual(results["status_counts"], {"unknown": 250})
        self.assertEqual(results["warning_count"], 250)
        self.assertEqual(len(results["examples"]), 5)
        self.assertFalse(results["complete"])

    def test_folder_scan_rejects_missing_folder_and_invalid_sample_limit(self):
        missing = scan_family_folder("C:/no-such-lora-folder", FakeBasicFileInfo, 2024)
        self.assertEqual(missing["error"], "invalid_folder")
        self.assertFalse(missing["complete"])
        with tempfile.TemporaryDirectory() as folder:
            invalid_limit = scan_family_folder(
                folder, FakeBasicFileInfo, 2024, sample_limit=-1)
        self.assertEqual(invalid_limit["error"], "invalid_sample_limit")

    def test_sha256_file_streams_source_without_modifying_it(self):
        payload = b"family-audit-read-only"
        with tempfile.NamedTemporaryFile(delete=False) as stream:
            stream.write(payload)
            path = stream.name
        try:
            self.assertEqual(sha256_file(path, chunk_size=4),
                             hashlib.sha256(payload).hexdigest())
            with open(path, "rb") as stream:
                self.assertEqual(stream.read(), payload)
        finally:
            os.remove(path)

    def test_sha256_file_rejects_non_positive_chunk_size(self):
        with self.assertRaises(ValueError):
            sha256_file("unused.rfa", chunk_size=0)

    def test_scan_ui_exposes_batch_and_folder_modes_read_only(self):
        script_path = (REPOSITORY_ROOT / "Lora.tab" / "Family.panel" /
                       "Family Version Audit.pushbutton" / "script.py")
        tree = ast.parse(script_path.read_text(encoding="utf-8"))
        calls = [node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertIn("CommandSwitchWindow", script_path.read_text(encoding="utf-8"))
        self.assertIn("pick_folder", calls)
        self.assertIn("pick_file", calls)
        self.assertNotIn("Transaction", calls)


if __name__ == "__main__":
    unittest.main()
