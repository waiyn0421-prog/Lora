"""Compatibility declaration for the sidecar API event surface."""

TARGET_REVIT_VERSIONS = (2023, 2024, 2025, 2026)


def classify_revit_version(version_number):
    try:
        value = int(str(version_number).strip())
    except (TypeError, ValueError):
        return {"status": "unknown", "version": None, "targeted": False}
    return {"status": "targeted" if value in TARGET_REVIT_VERSIONS else "unsupported",
            "version": value, "targeted": value in TARGET_REVIT_VERSIONS}
