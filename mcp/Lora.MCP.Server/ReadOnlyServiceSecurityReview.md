# Read-Only Revit Service Security Review

Review status: architecture analysis only. No listener, extension, Routes configuration or installation was changed.

## Current boundary

The CPython MCP process calls the existing pyRevit Routes service over loopback. The Routes registry is shared and mutable across co-loaded extensions. A route namespace/allowlist protects the Lora MCP client's own calls, but it cannot prevent another co-loaded extension from adding write routes to the same listener. Therefore the current arrangement does **not** guarantee that a read-only service cannot expose model-writing routes.

The installed Routes handler request contract inspected in this repository does not expose HTTP headers or the remote peer address to the route callback. The HTTP service has no authentication middleware. Binding to `127.0.0.1` prevents remote-network access when verified at the listener socket, but does not authenticate another local process. A localhost URL on the client is not proof of server binding.

## Candidate architecture: dedicated authenticated Revit bridge

The smallest maintainable strong boundary is a Lora-owned in-host bridge with an immutable dispatcher and an explicit read-only route set, coupled to a separate stdio MCP broker. The bridge is separate from the shared Routes registry. The broker has no Revit API assembly access and can request only named diagnostic operations.

Revit API calls must run in a valid Revit API context. The HTTP worker must not invoke the Revit API directly. It should validate bounded request data, enqueue a typed request, raise a Revit `ExternalEvent`, and await a bounded result from the Revit UI thread. The handler should bind each request to captured application/process/document session identity and revalidate identity immediately before reading. A document switch, closed document, missing API context, timeout, malformed request or unknown operation must fail closed and must not fall back to another process/document.

Transport should bind only to IPv4 loopback. Authenticate local callers using a per-session capability delivered over current-user ACL-protected IPC (for example, a named pipe); do not place secrets in extension files, command lines, environment variables, URLs or logs. Validate caller/session and body size, reject redirects, use timeouts, and return no generic route forwarding or arbitrary method support. Keep the read-only dispatcher structurally separate from write execution code.

## Route and capability separation

- Read-only bridge: only named identity, coordinate diagnostic, category/geometry inventory and other explicitly reviewed reads.
- Write bridge: excluded from a read-only build entirely. If later needed, implement as a separate consent-gated capability with explicit preview, transaction validation, idempotency/receipt, post-write verification and rollback reporting.
- Build-time route manifest and runtime startup assertion must compare the compiled dispatcher with the approved route list. Unknown routes should not register.
- A co-loaded extension cannot mutate this Lora-owned dispatcher; the dedicated listener must not register with or proxy through shared pyRevit Routes.

## Threats and failure modes to test

1. Untrusted remote network client, wildcard binding, IPv6 wildcard and redirect attempts.
2. Same-machine unauthorized process, replayed/stale capability, token leakage and cross-user pipe access.
3. Revit API calls from worker threads, invalid `ExternalEvent` state, busy UI, shutdown and document switch during queue wait.
4. Duplicate/ambiguous Revit processes and stale process/document tokens.
5. Oversized, malformed, unknown-route and method-confused requests.
6. Co-loaded extension registering a conflicting route or attempting to modify shared listener state.
7. Timeout after a read request and uncertain result handling; no fallback to a different document.
8. Read-only artifact inspection proving absence of `Transaction`, `Wall.Create`, document writes and imported execution modules.

## Decision and prerequisites

Do not implement an additional listener until its lifecycle, ownership, signing/update path, ACL IPC, ExternalEvent marshalling, queue limits, cancellation/shutdown behavior and route split have a reviewed design. Tests must include unit fakes, Windows pipe ACL integration in a disposable profile, Revit 2024 API-context tests, negative network binding tests, and co-loaded-extension simulations. A separate process without an in-host bridge cannot call Revit API safely by itself.

The prepared six-route read-only candidate remains unchanged. It demonstrates source-level allowlisting and artifact hashes only; it is not an isolation boundary. Transform status stays **UNVERIFIED** pending independent surveyed asymmetric controls; clearance stays **NOT IMPLEMENTED**.
