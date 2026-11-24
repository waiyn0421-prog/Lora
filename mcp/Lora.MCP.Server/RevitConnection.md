# Revit Connection and Identity

Lora's CPython client discovers the existing pyRevit Routes service through a configured set of IPv4 loopback ports. It accepts one unambiguous RevitMCP endpoint, pins it, rejects redirects/non-loopback targets, and does not switch to another candidate after an operation fails.

## Identity checks

Where available, the identity sidecar reports process, application-session and document-session tokens with document state. Guarded operations compare identity before and after the request. A process switch, document close/reopen or uncertain identity stops the operation. A document title or file path alone is not sufficient to prove that the active document is unchanged.

Existing upstream diagnostic routes may omit a stable document GUID, Revit API version, application-session token or other identity fields. Lora reports unavailable fields explicitly. Operations requiring certainty fail closed when required identifiers are missing or changed.

## Network boundary

The client contacts only loopback addresses and uses bounded timeouts/responses. Loopback keeps the service off remote interfaces only when the Routes listener is itself bound to loopback. A client URL using loopback does **not** constrain the listener's bind address. Check listener address and owning Revit process independently before use.

Routes uses HTTP and does not provide authenticated caller metadata to these handlers. Loopback is not authentication against other processes on the same machine. The route registry is shared with co-loaded extensions; route allowlists do not create process isolation.

## Evidence and limitations

Connection and session behavior is covered by mocked tests. No current-source Revit host test is claimed, and no Revit version is marked live-verified by this source documentation.

See [Architecture.md](Architecture.md), [Security.md](Security.md) and [CompatibilityMatrix.md](CompatibilityMatrix.md) for system boundaries and test evidence.
