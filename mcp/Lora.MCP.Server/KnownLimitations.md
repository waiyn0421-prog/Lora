# Known Limitations

- The category inventory and selected-element geometry routes are source-implemented and mocked, but have not been verified in a live Revit host in this work period.
- Revit 2023–2026 are targets, not verified runtime versions. Current sidecar sources have CPython compilation and IronPython 2.7.12PR/3.4.2 parser evidence; parser checks do not verify pyRevit command loading or host behavior. Only Revit 2024 API assembly metadata was inspected, and no current-source live-host test was performed.
- The drawing workflow does not automatically consume category inventory or model geometry evidence. Drawing-to-Revit proximity checks are unsafe while coordinate-transform verification is unverified.
- Category support is an explicit BuiltInCategory map. Custom, unmapped and overlapping categories can remain `other` or `unknown`.
- Type inventory completeness depends on upstream routes and supported categories. Some route results expose only instance-used types.
- Selected geometry extraction is limited to explicit host-document IDs, 25 elements, 200 output entities, 5,000 visits and depth 8. Linked geometry is not traversed.
- The geometry routes report evidence only. They do not calculate clash, physical obstruction or clearance; clearance is `unverified`.
- The drawing workflow can plan multiple recognized categories, but controlled Revit execution currently supports Basic Walls only. Structural and MEP execution are not implemented.
- The MCP server provides deterministic query routing rather than a remote AI model. Recognition candidates can be false positives and require human review.
- Review records are local SQLite state; drawing render caches are process-local. Reviewer names are caller-provided, not authenticated.
- Revit Routes uses plain HTTP. The identity sidecar requires loopback binding, but this project does not modify host configuration or authenticate other local processes.
- No live deployment, Revit model write, or version-specific host test was performed for these source changes.

See [CompatibilityMatrix.md](CompatibilityMatrix.md) and [Security.md](Security.md) for details.
