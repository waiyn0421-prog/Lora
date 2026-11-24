"""Build a clean, uninstalled Lora source review candidate with hashes."""

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path


VERSION_PROPOSAL = "0.1.0-rc.7"
ARTIFACT_STEM = "Lora-{}-source-candidate".format(VERSION_PROPOSAL)
EXCLUDED_PARTS = {".git", ".venv", "__pycache__", "tests", "tools",
                  "read_only_release_candidate"}
ALLOWED_MCP_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".txt"}


def build_candidate(repository_root=None, output_dir=None):
    """Build into a new directory; refuse every pre-existing output path."""
    root = Path(repository_root or Path(__file__).resolve().parents[3]).resolve()
    destination = Path(output_dir or root / "mcp" / ARTIFACT_STEM).resolve()
    archive = Path(str(destination) + ".zip")
    checksum_sidecar = Path(str(archive) + ".sha256")
    for target in (destination, archive, checksum_sidecar):
        if target.exists():
            raise FileExistsError("Refusing to overwrite existing candidate artifact: {}".format(target))

    files = collect_payload_files(root)
    if not files:
        raise ValueError("Release candidate payload is empty")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="lora-release-stage-", dir=str(parent)))
    staged_zip_fd, staged_zip_name = tempfile.mkstemp(prefix="lora-release-", suffix=".zip",
                                                       dir=str(parent))
    os.close(staged_zip_fd)
    staged_zip = Path(staged_zip_name)
    try:
        payload = stage / ARTIFACT_STEM
        payload.mkdir()
        entries = []
        for source, relative in files:
            target = payload / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(source), str(target))
            entries.append({"path": relative.as_posix(), "size_bytes": target.stat().st_size,
                            "sha256": _sha256(target)})

        manifest = {
            "artifact_type": "uninstalled source review candidate",
            "version_proposal": VERSION_PROPOSAL,
            "release_status": "not release-qualified",
            "created_date": "2026-11-22",
            "file_count": len(entries),
            "files": entries,
            "excluded": [
                "test suites and development tools",
                "virtual environments, Python bytecode and caches",
                "drawing inbox contents and user data",
                "distribution-review.txt and all unrelated user files",
                "the separate read-only sidecar candidate",
                "installer binaries and deployment configuration",
                "third-party wheels and native dependency binaries",
                "development-only requirements-test.txt",
            ],
            "limitations": [
                "No live Revit host compatibility verification has been performed for this candidate.",
                "Transitive dependency licensing and optional PyMuPDF distribution obligations remain under review.",
                "The read-only sidecar candidate does not establish process isolation.",
                "No installer is included.",
            ],
        }
        manifest_path = payload / "manifest.json"
        _write_json(manifest_path, manifest)
        sums = []
        for entry in entries:
            sums.append("{}  {}".format(entry["sha256"], entry["path"]))
        manifest_hash = _sha256(manifest_path)
        sums.append("{}  manifest.json".format(manifest_hash))
        _write_text(payload / "SHA256SUMS.txt", "\n".join(sums) + "\n")

        _write_archive(staged_zip, payload, ARTIFACT_STEM)
        archive_hash = _sha256(staged_zip)
        _write_text(Path(str(staged_zip) + ".sha256"),
                    "{}  {}\n".format(archive_hash, archive.name))
        staged_checksum = Path(str(staged_zip) + ".sha256")

        os.rename(str(payload), str(destination))
        os.rename(str(staged_zip), str(archive))
        os.rename(str(staged_checksum), str(checksum_sidecar))
        try:
            stage.rmdir()
        except OSError:
            pass
        verify_candidate(destination)
        verify_archive(archive)
        return {"directory": str(destination), "archive": str(archive),
                "archive_sha256": archive_hash, "manifest_sha256": manifest_hash,
                "payload_files": len(entries)}
    except Exception:
        # Keep any partial artifacts for inspection; never delete user data.
        raise
    finally:
        if staged_zip.exists():
            try:
                staged_zip.unlink()
            except OSError:
                pass
        staged_checksum = Path(str(staged_zip) + ".sha256")
        if staged_checksum.exists():
            try:
                staged_checksum.unlink()
            except OSError:
                pass


def collect_payload_files(root):
    """Return an explicit source/package allowlist with stable destination paths."""
    root = Path(root).resolve()
    candidates = []

    def add(path, relative):
        if path.is_symlink() or not path.is_file():
            return
        if any(part in EXCLUDED_PARTS for part in path.parts):
            return
        if path.suffix.lower() in (".pyc", ".pyo", ".bak", ".tmp"):
            return
        candidates.append((path, Path(relative)))

    for name in ("README.md", "LICENSE"):
        add(root / name, name)
    extension_root = root
    add(extension_root / "extension.yaml", "Lora.extension/extension.yaml")
    for directory in ("Lora.tab", "lib"):
        source_dir = extension_root / directory
        for path in source_dir.rglob("*"):
            relative = Path("Lora.extension") / path.relative_to(extension_root)
            if directory == "lib" and path.suffix.lower() != ".py":
                continue
            add(path, relative)

    server = root / "mcp" / "Lora.MCP.Server"
    for path in server.iterdir():
        if path.name == "requirements-test.txt":
            continue
        if path.is_file() and (path.suffix.lower() in (".md", ".txt") or path.name == ".gitignore"):
            add(path, Path("mcp/Lora.MCP.Server") / path.name)
    for package_name in ("lora_calibration", "lora_context", "lora_drawing",
                         "lora_execution", "lora_generation", "lora_mcp",
                         "lora_recognition", "lora_review"):
        package_dir = server / package_name
        if package_dir.exists():
            for path in package_dir.rglob("*.py"):
                add(path, Path("mcp/Lora.MCP.Server") / path.relative_to(server))
    identity_root = server / "revit_identity" / "LoraIdentity.extension"
    if identity_root.exists():
        for path in identity_root.rglob("*"):
            if path.suffix.lower() in (".py", ".json", ".yaml", ".yml"):
                add(path, Path("mcp/Lora.MCP.Server") / path.relative_to(server))
    requirements = server / "requirements.txt"
    add(requirements, "mcp/Lora.MCP.Server/requirements.txt")
    inbox_placeholder = server / "drawing_inbox" / ".gitkeep"
    add(inbox_placeholder, "mcp/Lora.MCP.Server/drawing_inbox/.gitkeep")

    # De-duplicate any overlapping glob roots and sort paths for stable manifests.
    unique = {relative.as_posix(): (source, relative) for source, relative in candidates}
    return [unique[key] for key in sorted(unique)]


def verify_candidate(candidate_dir):
    candidate = Path(candidate_dir).resolve()
    manifest_path = candidate / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = {item["path"]: item["sha256"] for item in manifest.get("files", [])}
    actual = {path.relative_to(candidate).as_posix() for path in candidate.rglob("*")
              if path.is_file() and path.name not in ("manifest.json", "SHA256SUMS.txt")}
    if actual != set(declared):
        raise ValueError("Candidate file set differs from manifest")
    for relative, digest in declared.items():
        if _sha256(candidate / relative) != digest:
            raise ValueError("Candidate payload hash mismatch: {}".format(relative))
    manifest_hash = _sha256(manifest_path)
    sums = (candidate / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    expected = {item.split("  ", 1)[1]: item.split("  ", 1)[0]
                for item in sums if "  " in item}
    if expected.get("manifest.json") != manifest_hash:
        raise ValueError("Candidate manifest hash mismatch")
    if any(expected.get(name) != digest for name, digest in declared.items()):
        raise ValueError("Candidate checksum list mismatch")
    return manifest


def verify_archive(archive_path, checksum_path=None):
    archive = Path(archive_path).resolve()
    checksum = Path(checksum_path or (str(archive) + ".sha256"))
    expected = checksum.read_text(encoding="utf-8").split()[0]
    actual = _sha256(archive)
    if expected != actual:
        raise ValueError("Archive SHA256 mismatch")
    with zipfile.ZipFile(str(archive), "r") as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Archive contains duplicate file names")
        if any(name.startswith("../") or "/../" in name for name in names):
            raise ValueError("Archive contains a path traversal entry")
        manifest_members = [name for name in names if name.endswith("/manifest.json")]
        if len(manifest_members) != 1:
            raise ValueError("Archive must contain exactly one manifest")
        manifest_member = manifest_members[0]
        prefix = manifest_member[:-len("manifest.json")]
        manifest = json.loads(bundle.read(manifest_member).decode("utf-8"))
        declared = {item["path"]: item["sha256"] for item in manifest.get("files", [])}
        sums_member = prefix + "SHA256SUMS.txt"
        sums_text = bundle.read(sums_member).decode("utf-8")
        sums = {line.split("  ", 1)[1]: line.split("  ", 1)[0]
                for line in sums_text.splitlines() if "  " in line}
        manifest_digest = hashlib.sha256(bundle.read(manifest_member)).hexdigest()
        if sums.get("manifest.json") != manifest_digest:
            raise ValueError("Archive manifest checksum mismatch")
        if any(sums.get(name) != digest for name, digest in declared.items()):
            raise ValueError("Archive checksum list differs from manifest")
        expected_names = {prefix + relative for relative in declared}
        expected_names.update({manifest_member, prefix + "SHA256SUMS.txt"})
        if set(names) != expected_names:
            raise ValueError("Archive file set differs from embedded manifest")
        for relative, digest in declared.items():
            if hashlib.sha256(bundle.read(prefix + relative)).hexdigest() != digest:
                raise ValueError("Archive payload hash mismatch: {}".format(relative))
    return actual


def _write_archive(archive_path, payload_root, prefix):
    with zipfile.ZipFile(str(archive_path), "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as bundle:
        for path in sorted(payload_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(payload_root).as_posix()
            info = zipfile.ZipInfo(prefix + "/" + relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                            compresslevel=9)


def _sha256(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_text(path, value):
    path.write_text(value, encoding="utf-8", newline="\n")


def _write_json(path, value):
    _write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    result = build_candidate()
    print(json.dumps(result, indent=2, sort_keys=True))
