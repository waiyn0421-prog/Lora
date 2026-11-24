# Version and Compatibility State

This checkout is development source and has no published semantic release version. `0.1.0-rc.7` identifies a preserved source review snapshot only; it is not a release or deployment qualification.

The declared Revit target range is 2023–2026. Revit 2024 API metadata has been inspected, but current-source host behavior remains unverified for every version. Current evidence includes 303 passing CPython tests, 31-file syntax parsing under IronPython 2.7.12PR and 3.4.2, and CPython compilation. These checks do not establish Revit runtime compatibility.

Coordinate transform verification remains **UNVERIFIED** pending independent surveyed controls. Physical clearance is **NOT IMPLEMENTED**. See [CompatibilityMatrix.md](CompatibilityMatrix.md), [KnownLimitations.md](KnownLimitations.md) and [TestReport.md](TestReport.md).
