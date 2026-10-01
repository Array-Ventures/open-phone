# Single-owner OAuth prototype

`mcp_http.mjs --oauth` serves the original 13-tool MCP endpoint behind OAuth
instead of the static MCP bearer credential. This implements the public-client
authorization-code flow using the pinned official MCP SDK.

The owner explicitly grants access using a **separate OpenPhone owner key** on
the consent page. This key is not a Mac/iPhone password, relay agent key or MCP
access token. The page describes screenshot and phone-input access. There is one
owner and one `phone:control` scope covering all phones available to the backend.
There is no per-phone authorization or Google/organizational identity yet.

## Run

Install the pinned dependencies with `npm ci --ignore-scripts`; run the relay
and Mac worker as described in [RELAY.md](RELAY.md). On the relay computer:

```sh
OPEN_PHONE_RELAY_URL=https://relay.example:8768 node mcp_http.mjs --oauth --host RELAY_IP --cert /path/mcp-cert.pem --key /path/mcp-key.pem --public-url https://mcp.example:8770/mcp
```

Locally, `node mcp_http.mjs --oauth` uses `http://127.0.0.1:8770/mcp`. The owner
key is generated/read in owner-only `.oauth-owner-token`, or the path supplied
with `--owner-token-file`. Its value is never printed. Read that file privately
when granting consent. Agents receive OAuth tokens; entering the owner key as a
Bearer token is rejected. Bearer mode remains available without `--oauth` and
uses its separate `.mcp-http-token` file.

OAuth mode now defaults to durable state in owner-only `private/oauth-state.sqlite`.
Select a different file with `--oauth-state-file /path/oauth-state.sqlite`, or
use `--oauth-ephemeral` for disposable process-local grants. The Node API uses
ephemeral mode unless its `oauthStateFile` option is supplied. Persistence uses
Python's standard-library SQLite and Unix file locks; Python 3.10+ is required,
and this persistence adapter targets macOS/Linux.

The public URL must use the listener's HTTP/HTTPS scheme and exact `/mcp` path,
without credentials, query or fragment. HTTPS is required outside IPv4 loopback.
Set an accurate canonical hostname with a trusted certificate; consent POSTs
require that exact origin. The URL is an OAuth audience, not just a display
label. This CLI does not implement reverse-proxy TLS termination or configure
DNS, a public deployment or a trusted certificate for you.

## Protocol and boundaries

- Protected-resource discovery: `/.well-known/oauth-protected-resource/mcp`.
  Authorization-server discovery: `/.well-known/oauth-authorization-server`.
- `/register` accepts public clients (`token_endpoint_auth_method: none`).
  Redirects require HTTPS or loopback HTTP, with no credentials/fragments.
  Redirect matching is exact. Custom schemes and arbitrary loopback-port
  substitution are not supported by this implementation.
- `/authorize` requires S256 PKCE and the exact MCP resource. The client receives
  a five-minute consent request. Owner approval checks a hidden CSRF nonce,
  matching HttpOnly/SameSite cookie, canonical Origin and the independent key.
  Client-controlled text is HTML escaped; the page blocks framing and scripts.
- `/token` checks the verifier (43–128 unreserved characters), registered
  redirect, one-use five-minute code, client and resource. The SDK performs the
  challenge comparison. Tokens are opaque random secrets indexed by SHA-256.
- Access lasts up to ten minutes, bounded by the grant deadline. Refresh grants
  last seven days from initial consent.
  Every refresh rotates both tokens and invalidates the old access token.
  Reusing a spent refresh token revokes the entire grant, including new tokens.
  Refresh cannot expand scope or change client/resource.
- `/revoke` revokes only the requesting client's grant. Revocation closes its
  MCP sessions. A new grant, including one for the same client, cannot use another
  grant's session. Rotation retains the grant, so a refreshed token can keep its
  session. Tokens are rechecked after request-body reading, before dispatch.
- Host checking covers all routes. MCP browser Origins must match the canonical
  server or the authenticated client's registered callback origin. OAuth
  protocol routes use the SDK's CORS support. MCP challenge/session headers are
  exposed for registered browser origins, including on unauthorized requests.

Registration does not establish client trust: the owner must assess the displayed
client and callback before granting access. The SDK defaults limit registration
to 20/hour, authorization to 100/15 minutes, and token/revocation to 50/15 minutes
per IP. Owner-key attempts have a process-wide 20/minute limit. Bodies are bounded
(16 KiB consent, 64 KiB protocol bodies). Process caps are 128 clients, 64 pending
consents, 128 codes, 256 grants and 32,768 refresh-token records.

Revocation prevents new authenticated requests; it cannot undo phone input or
reliably cancel a job already accepted by the relay. Owner account recovery,
client management, provider identity and broader deployment testing remain.

## Durable state

Registered clients, grants, current access-token hashes, refresh-token hashes /
spent-token history, revocations and approved authorization-code hashes survive
restart. No raw bearer/refresh token, owner key or pending consent cookie persists.
Unapproved consent pages expire on restart; previously approved codes retain
their original five-minute expiry and PKCE/resource/redirect/client bindings.
The restored provider rebuilds its indexes, validates relationships and prunes
expired codes/grants using absolute wall-clock deadlines.

MCP sessions and tool-call history are still process-local. After restart, a
valid access token can initialize a new session; an old session ID returns 404.
Inspect relay job outcomes before submitting phone input again after a restart
or uncertain network failure. OAuth recovery does not replay old actions.

Each mutation commits a signed snapshot in an independent SQLite transaction
before returning registration, code, tokens or revocation success. Refresh
consumption and replacement hashes commit together. SQLite uses synchronous FULL,
DELETE journaling and secure deletion. Unchanged snapshots do not write again.
Snapshots have a 16 MiB envelope limit and a format/application version marker.
The state DB and both lock files are regular owner-only files (0600).

A Python lock keeper retains a Unix lifetime flock. A second operation lock
prevents a new owner from taking over between a writer's ownership check and
commit. The lock keeper exits on stdin EOF when its Node parent exits, including
SIGKILL. A second server using the same file is refused. Lock-helper death and
storage errors disable authorization in the current process and close its MCP
sessions. No successful token/registration/revocation response precedes commit.
Repair and restart are required after a storage failure. Recovery uses the last
confirmed snapshot; a failed revocation is **not** a durable revocation.

Snapshots are authenticated with HMAC-SHA256 using the separate owner key and
include the exact canonical resource. Modified contents, a different key or a
different resource are rejected without overwriting the stored snapshot. Keep
the same owner key and URL when reloading. Select a new state file explicitly
when deliberately replacing that identity. HMAC is integrity protection, not
database encryption or protection against restoring an older valid backup.
The OAuth store remains separate from the relay's [job/catalog SQLite ledger](STATE.md). Replicated identity
storage, cross-machine locking and Windows persistence are not implemented.

## Verification

```sh
node tests/oauth_http.mjs
node tests/oauth_persistence.mjs
node tests/mcp_http.mjs
```

Fourteen OAuth check groups pass through real HTTP routes and the official SDK
against an isolated relay fixture. They cover discovery, registration and unsafe
metadata, unregistered redirects, S256-only/resource/scope checks, escaped consent
HTML, key/CSRF/cookie/Origin rejection and denial, code replay and expiry, wrong
verifiers/clients/redirects/resources, session and browser-origin separation,
refresh rotation/reuse/expiry, revocation and Host rejection. A real SDK client
discovers/registers, exchanges the HTTP-fixture consent code, connects, lists all
13 tools, invokes list_phones and refreshes without replacing its MCP session.
Ephemeral restart starts with empty grant/client stores. Existing bearer-mode
integration still passes its 12 check groups.

Thirteen durable-state check groups pass with isolated credentials and a real
HTTP fixture process killed with SIGKILL, then restarted on its same URL/DB.
Clients, active tokens, approved codes and consumed-code/refresh history survived;
old MCP sessions and unapproved consent did not. Refresh reuse and acknowledged
revocation remained effective across restart. A real SQLite abort returned no new
tokens, retained the old committed snapshot and disabled further authorization.
Checks also cover expiry, writer/operation ownership, lock-helper death, absence
of raw secrets, file permissions, integrity, key/resource mismatch and preservation
of unrelated databases, including ones containing only foreign schema markers.

These are protocol tests, not rendered-browser tests or real-phone input.
No third-party client or external provider login was authorized. The flow
uses the current [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
and SDK 1.27.1's installed server/client implementation.

The separate live TLS endpoint on this Mac also passed trusted-certificate
discovery and official SDK dynamic registration/PKCE URL construction. The
owner-key file had mode 0600 and that key was rejected as an MCP Bearer token.
No live grant was approved, browser rendered or phone input sent in that check.
After reloading the live TLS service with durable storage, a public SDK test
registration survived another confirmed stop/reload and could start a new
consent request. No access token was granted in that live recovery check.
Browser Use reached the live service but stopped at
`net::ERR_CERT_AUTHORITY_INVALID` for its private test certificate. No certificate
warning was bypassed; rendered consent remains unverified.

During the later final reload, the Mac's network changed and its previous LAN
bind address disappeared. Startup returned `EADDRNOTAVAIL`; the live OAuth
listener is currently stopped. The durable DB remains intact. The earlier TLS /
registration recovery results are historical evidence, not current network
availability. Restore an appropriate bind/route while retaining the canonical
OAuth identity, or select a new state file explicitly for a different identity.
