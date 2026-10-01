# Streamable HTTP MCP endpoint

`packages/mcp/src/mcp_http.mjs` exposes the same 13 phone tools as `packages/mcp/src/relay_mcp.mjs` through the
official SDK's stateful Streamable HTTP transport. Each session has its own
screenshot references and request history. The endpoint reaches the original
relay through the file-authenticated Python client and starts no native helpers.

This original self-hosted endpoint supports a separate bearer key, or optional
[single-owner OAuth](oauth.md) with discovery, dynamic registration, S256 PKCE,
consent, refresh rotation and revocation.
Our owner-key consent has optional durable SQLite grants; provider login,
organizational identity and rendered browser validation remain outstanding.

## Run

Install the pinned dependencies with `npm ci --ignore-scripts`. Start the relay
and Mac worker described in [RELAY.md](relay.md). On the relay computer:

```sh
OPEN_PHONE_RELAY_URL=https://relay.example:8768 node packages/mcp/src/mcp_http.mjs --host RELAY_IP --cert /path/mcp-cert.pem --key /path/mcp-key.pem --hostname mcp.example
```

`--key` is the TLS private-key file. The independent MCP bearer credential is
read from `--token-file /path/mcp-token`, or generated in owner-only
`.mcp-http-token`. Its value is never printed or passed as a command argument.
The server-side relay agent key stays in `.relay-agent-token`, or the path set
by `OPEN_PHONE_RELAY_TOKEN_FILE`. Give agents only the MCP bearer key.

Use a certificate valid for the MCP hostname/IP. Non-loopback binding requires
TLS; wildcard addresses are refused. Add DNS hostnames with `--hostname` for
exact Host/Origin checks. A private relay CA can be set in `OPEN_PHONE_RELAY_CA`
for the Python client. A remote Node SDK client can trust its MCP CA using
`NODE_EXTRA_CA_CERTS` at startup. Public deployment should use a normally trusted
certificate. The one-day private test certificate is a development fixture.

Locally, `node packages/mcp/src/mcp_http.mjs` binds `http://127.0.0.1:8770/mcp`. Agents send
`Authorization: Bearer <MCP key>` on every request. A session ID is not a key.
Starting the process does not create a browser login or public deployment.

## Sessions and execution

The official SDK handles initialization, protocol versions, session headers,
JSON responses, optional SSE and DELETE termination. Defaults are 16 sessions,
20-minute idle expiry and 300 HTTP requests per session/minute. Bodies are at
most 64 KiB; batches are rejected.

Shared tools validate arguments, admit one active tool call per session and
require a screenshot within 30 seconds before pointer input. Image endpoints
map inclusively to native endpoints. Pointer jobs carry the screenshot's native
dimensions through the relay to the Mac, which compares them with fresh capture
before HID input. Input attempts invalidate the session's frame reference;
take another screenshot before the next pointer action.

Repeated tool-call IDs within a session never execute again. Up to 64 results /
8 MiB are cached and 4096 accepted IDs remembered. Evicted results cause repeats
to fail rather than execute. Concurrent repeats and reused IDs with different
parameters are rejected. History is not durable across a new session or restart;
a new request ID represents a new request. Inspect relay jobs/phone state before
deciding to issue another action after an uncertain network failure.

Close/expiry releases the session's relay process and transport. It cannot undo
an already executed action. Relay jobs retain one-shot host receipts and no
automatic replay. The relay now [persists its catalog and bounded history](state.md)
in SQLite. Restored phones start offline and unfinished jobs fail; a fresh host
registration is required before new input. MCP session history remains in memory.

## Verification

```sh
node packages/mcp/tests/mcp_http.mjs
node packages/mcp/tests/oauth_http.mjs
node packages/mcp/tests/oauth_persistence.mjs
node packages/mcp/tests/http_remote.mjs --url https://mcp.example:8770/mcp --token-file /path/mcp-token
```

The first command has 12 real SDK/HTTP check groups with an isolated relay
fixture: access/origin/host rejection, session isolation, images/geometry,
dimension-preserving pointer calls, frame expiry/invalidation, validation,
duplicate/pending/evicted IDs without replay, DELETE, capacity/rate limits,
idle cleanup and required TLS. Fixtures do not prove real-phone input or image
validity. Existing Python checks also cover the relay's native envelope.

The OAuth command adds 14 groups covering discovery, registration, browser-form
HTTP requests, PKCE/resource/redirect validation, code expiry and reuse,
client-bound sessions/origins, refresh rotation/reuse, revocation and a full
official SDK registration-to-tool-discovery flow. It uses an isolated relay
fixture, does not render a browser and sends no input to a phone. See [OAUTH.md](oauth.md).
The persistence command adds 13 groups proving client/grant/code/revocation
recovery through real HTTP restart and SIGKILL fixtures, plus storage failure
and ownership checks. MCP sessions remain process-local.

The second command lists tools and reads a running service's actual catalog.
Optional `--phone PHONE_ID` reads status and checks pointer rejection without
a screenshot. Optional `--screenshot` requests a real image; it does not tap.

On this Mac the real TLS endpoint rejected an untrusted self-signed certificate,
then passed SDK initialization, 13-tool discovery, catalog/status reading and
missing-frame rejection with its explicit test CA. The phone was offline. After
reloading the relay with native-envelope support, another SDK run passed and
reported the now-empty in-memory catalog. Phone input through this HTTP endpoint
remains unverified; earlier driver/relay gestures are separate physical evidence.
