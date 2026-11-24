# Security and Data Handling

## Network and identity

The MCP server uses stdio with its client. Its Revit client uses explicit route allowlists, IPv4 loopback, pinned endpoint discovery, bounded timeouts/response sizes, redirect rejection, disabled proxy/netrc handling and document identity checks around guarded requests.

The pyRevit Routes listener is shared by co-loaded extensions. The identity provider registers only when the listener/configuration reports the required loopback host, but this check does not set the listener address. Verify the actual listening socket and owning Revit process before a test. Routes uses HTTP and the callback interface does not provide authenticated caller metadata; loopback is not authentication against other local processes. Route allowlists do not create process isolation.

The main MCP server includes a guarded Basic Wall execution workflow. It is not a read-only server. Ribbon commands also include model-writing workflows. Review each tool and command's side effects before use. The prepared read-only candidate is a separate artifact, not an isolation guarantee for co-loaded extensions.

## Revit operations

Category and selected geometry inventory are read-only and bounded. Selected geometry requires explicit element IDs and has output/visit/depth limits. The routes do not calculate physical clearance. Coordinate transform status remains unverified. The wall execution path is separately guarded with plan/approval, identity and transaction-result checks; mock tests do not establish live transaction safety.

## Drawing and local persistence

Drawing tools accept filenames within a configured inbox and validate the opened file's identity, signature and resource limits. They do not provide arbitrary filesystem reads. Render and recognition caches are bounded and in memory. Review, planning, preview and execution records persist in local SQLite databases and may contain project-derived geometry, annotations, corrections and request history. Store and back up that data according to the project's information policy. Reviewer labels are caller-supplied and are not authenticated identity.

## Deployment checks

Before a controlled test, verify loopback-only listener binding, process ownership, extension/source hashes, current document identity and rollback. Use a disposable model and isolated extension profile. Do not expose the Routes listener to other interfaces. No current-source production deployment or host security test is claimed here.
