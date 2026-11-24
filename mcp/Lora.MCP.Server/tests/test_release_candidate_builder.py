import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
import build_full_release_candidate as builder


class ReleaseCandidateBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.output = Path(self.temp.name) / "candidate"
        self._write("README.md", "readme\n")
        self._write("LICENSE", "license\n")
        self._write("extension.yaml", "name: Lora\n")
        self._write("Lora.tab/Test.pushbutton/script.py", "VALUE = 1\n")
        self._write("lib/helper.py", "VALUE = 2\n")
        self._write("mcp/Lora.MCP.Server/README.md", "server\n")
        self._write("mcp/Lora.MCP.Server/requirements.txt", "requests\n")
        self._write("mcp/Lora.MCP.Server/requirements-test.txt", "test-only dependency\n")
        self._write("mcp/Lora.MCP.Server/lora_mcp/__init__.py", "\n")
        self._write("mcp/Lora.MCP.Server/tests/test_private.py", "PRIVATE = 1\n")
        self._write("mcp/Lora.MCP.Server/.venv/secret.txt", "PRIVATE\n")
        self._write("distribution-review.txt", "PRIVATE\n")

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_build_has_exact_manifest_hashes_and_allowlisted_payload(self):
        result = builder.build_candidate(self.root, self.output)
        self.assertEqual(result["payload_files"], 8)
        manifest = builder.verify_candidate(self.output)
        archive = Path(result["archive"])
        self.assertEqual(builder.verify_archive(archive), result["archive_sha256"])
        included = {item["path"] for item in manifest["files"]}
        self.assertIn("Lora.extension/Lora.tab/Test.pushbutton/script.py", included)
        self.assertIn("mcp/Lora.MCP.Server/lora_mcp/__init__.py", included)
        self.assertFalse(any("tests/" in item or ".venv/" in item for item in included))
        self.assertNotIn("mcp/Lora.MCP.Server/requirements-test.txt", included)
        self.assertFalse((self.output / "distribution-review.txt").exists())
        self.assertTrue(Path(str(archive) + ".sha256").exists())

    def test_refuses_to_overwrite_existing_outputs(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            builder.build_candidate(self.root, self.output)
        self.assertTrue(self.output.is_dir())

    def test_archive_verifier_rejects_changed_archive_bytes(self):
        result = builder.build_candidate(self.root, self.output)
        archive = Path(result["archive"])
        with archive.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "Archive SHA256 mismatch"):
            builder.verify_archive(archive)

    def test_archive_verifier_checks_embedded_payload_hashes(self):
        result = builder.build_candidate(self.root, self.output)
        archive = Path(result["archive"])
        rewritten = archive.with_name("altered.zip")
        with zipfile.ZipFile(archive, "r") as source, zipfile.ZipFile(rewritten, "w") as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename.endswith("README.md") and "mcp/" in item.filename:
                    data = b"tampered\n"
                target.writestr(item, data)
        digest = hashlib.sha256(rewritten.read_bytes()).hexdigest()
        checksum = Path(str(rewritten) + ".sha256")
        checksum.write_text(digest + "  altered.zip\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Archive payload hash mismatch"):
            builder.verify_archive(rewritten, checksum)


if __name__ == "__main__":
    unittest.main()
