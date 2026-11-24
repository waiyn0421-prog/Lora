"""Create and verify an isolated read-only pyRevit sidecar candidate."""

import hashlib
import json
import shutil
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parents[1]
SOURCE_EXTENSION = SERVER_ROOT / "revit_identity" / "LoraIdentity.extension"
DEFAULT_OUTPUT = REPO_ROOT / "mcp" / "read_only_release_candidate"

PROVIDER_FILES = (
    "__init__.py",
    "compatibility.py",
    "coordinate_geometry.py",
    "category_inventory.py",
    "geometry_detail.py",
    "level_catalog.py",
    "registry.py",
    "runtime.py",
    "security.py",
)

ROUTES = (
    {"method": "GET", "path": "/session_identity", "handler": "get_session_identity"},
    {"method": "POST", "path": "/level_catalog", "handler": "get_level_catalog"},
    {"method": "GET", "path": "/coordinate_diagnostic", "handler": "get_coordinate_diagnostic"},
    {"method": "POST", "path": "/geometry_inventory", "handler": "get_geometry_inventory"},
    {"method": "POST", "path": "/category_inventory", "handler": "get_category_inventory"},
    {"method": "POST", "path": "/geometry_detail", "handler": "get_geometry_detail"},
)

EXCLUDED_ROUTES = (
    {"method": "GET", "path": "/wall_types", "reason": "Wall workflow route excluded from this candidate."},
    {"method": "POST", "path": "/validate_walls", "reason": "Wall execution workflow route excluded."},
    {"method": "POST", "path": "/execute_walls", "reason": "Model-writing route; opens a Revit Transaction, creates walls, and writes parameters."},
    {"method": "POST", "path": "/verify_walls", "reason": "Wall execution workflow route excluded."},
    {"method": "GET", "path": "/execution_status", "reason": "Wall execution receipt route excluded."},
)

_STARTUP_TEMPLATE = SERVER_ROOT / "tools" / "read_only_candidate_startup.py"
_POLICY_TEMPLATE = SERVER_ROOT / "tools" / "read_only_candidate_policy.py"


def _sha256(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path, text):
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def build_candidate(output_path=DEFAULT_OUTPUT):
    """Build without replacing any existing output directory or source file."""
    output = Path(output_path).resolve()
    if output.exists():
        raise FileExistsError("Refusing to overwrite existing candidate: {}".format(output))
    extension = output / "LoraReadOnly.extension"
    provider = extension / "lib" / "lora_identity_provider"
    provider.mkdir(parents=True)

    shutil.copyfile(str(_STARTUP_TEMPLATE), str(extension / "startup.py"))
    shutil.copyfile(str(_POLICY_TEMPLATE), str(provider / "read_only_policy.py"))
    for name in PROVIDER_FILES:
        shutil.copyfile(str(SOURCE_EXTENSION / "lib" / "lora_identity_provider" / name),
                        str(provider / name))

    readme = """# Lora read-only Revit sidecar candidate

This isolated candidate contains only the Revit-side read routes listed in
`manifest.json`. It is not installed or deployed. It does not bundle an MCP
server. Pairing it with a separately write-capable MCP server would invalidate
the overall read-only claim.

The pyRevit Routes server is shared. Startup requires an empty
`lora-identity-v1` route registry and then verifies the exact six-route
allowlist. It fails closed on pre-existing or unexpected routes. Install only
in an explicitly approved test environment where no other Lora sidecar can
register routes in that API namespace. This candidate does not unregister or
replace existing handlers.

The candidate omits `wall_execution.py`, `/wall_types`, `/validate_walls`,
`/execute_walls`, `/verify_walls`, and `/execution_status`. It contains no
Revit transaction, creation, deletion, or parameter-write route. The source
development build and existing installations are separate and unchanged.

Verify payload hashes using `SHA256SUMS.txt` before any separately approved
handling. The checksum file itself is identified by its SHA256 in the
completion report because a checksum file cannot embed its own digest.
"""
    _write_text(output / "README.md", readme)

    payload_paths = sorted(path for path in output.rglob("*") if path.is_file())
    files = [{"path": path.relative_to(output).as_posix(), "sha256": _sha256(path)}
             for path in payload_paths]
    manifest = {
        "candidate_id": "lora-read-only-sidecar-2026-11-22",
        "artifact_type": "isolated pyRevit Revit-side sidecar",
        "scope": "Revit-side HTTP routes only; MCP server and MCP tool policy are not bundled",
        "read_only": True,
        "api_name": "lora-identity-v1",
        "route_registry_policy": "require empty registry before registration and exact allowlist after registration",
        "included_routes": list(ROUTES),
        "excluded_routes": list(EXCLUDED_ROUTES),
        "excluded_components": [
            "wall_execution.py",
            "lora_mcp/server.py (not bundled; source includes review, approval, generation and wall execution tools)",
        ],
        "files": files,
    }
    manifest_path = output / "manifest.json"
    _write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    checksum_files = files + [{"path": "manifest.json", "sha256": _sha256(manifest_path)}]
    sums = "".join("{}  {}\n".format(item["sha256"], item["path"])
                   for item in checksum_files)
    _write_text(output / "SHA256SUMS.txt", sums)
    verify_candidate(output)
    return output


def verify_candidate(output_path):
    """Verify the manifest, exact artifact file set, and detached checksum list."""
    output = Path(output_path).resolve()
    manifest_path = output / "manifest.json"
    with open(str(manifest_path), "r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    declared = {item["path"]: item["sha256"] for item in manifest.get("files", [])}
    actual_files = {path.relative_to(output).as_posix() for path in output.rglob("*")
                    if path.is_file() and path.name != "SHA256SUMS.txt"}
    if actual_files != set(declared) | {"manifest.json"}:
        raise ValueError("Candidate file set differs from manifest")
    expected = dict(declared)
    expected["manifest.json"] = _sha256(manifest_path)
    for relative, digest in expected.items():
        if _sha256(output / relative) != digest:
            raise ValueError("Candidate hash mismatch: {}".format(relative))
    with open(str(output / "SHA256SUMS.txt"), "r", encoding="utf-8") as stream:
        parsed = {}
        for line in stream:
            digest, relative = line.rstrip("\n").split("  ", 1)
            parsed[relative] = digest
    if parsed != expected:
        raise ValueError("Detached checksum list differs from manifest")
    if manifest.get("read_only") is not True:
        raise ValueError("Candidate is not declared read-only")
    return manifest


if __name__ == "__main__":
    print(build_candidate())
