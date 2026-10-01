# MCP

OpenPhone uses the official `@modelcontextprotocol/sdk`, pinned to 1.27.1.
It exposes two tool sets:

| Entry point | Tools | Device ownership |
|---|---:|---|
| `packages/mcp/src/mcp.mjs` | 17 | Starts the local Python device driver |
| `packages/mcp/src/relay_mcp.mjs` | 13 | Talks to an existing self-hosted relay |
| `packages/mcp/src/mcp_http.mjs` | 13 | HTTP frontend for the relay tool set |

## Local stdio

Install the Python package into the repository's `.venv` and run `npm ci`.
The MCP package selects that environment automatically. Set `OPEN_PHONE_PYTHON`
to use another environment with OpenPhone installed. Python subprocesses run
installed modules, independently of the caller's working directory.

```toml
[mcp_servers.open_phone]
command = "node"
args = ["/absolute/path/to/open-phone/packages/mcp/src/mcp.mjs"]
```

The local MCP starts its own driver. Do not give a separate REST or MCP process
concurrent ownership of the same capture and Bluetooth resources.

Connect the phone before input. Screenshots return images and metadata. Local
pointer coordinates are normalized to 0–1. A successful tool result confirms
report delivery; inspect a subsequent screenshot to verify the intended UI result.

## Remote transports

The relay adapter uses `OPEN_PHONE_RELAY_URL`, `OPEN_PHONE_RELAY_TOKEN_FILE`, and
optionally `OPEN_PHONE_RELAY_CA`. It launches no local device helper. The remote
tools require a recent screenshot before pointer input and preserve its native
dimensions in the job envelope.

See [relay configuration](relay.md), [HTTP transport](http_mcp.md) and
[OAuth](oauth.md) for setup and the respective execution guarantees.

## Documentation and validation

Development uses the [official MCP documentation](https://modelcontextprotocol.io/docs)
and the [TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk).
Current documentation was refreshed through Context7 during the package refactor.
The protocol/SDK dependency remains pinned; the directory migration is not an
SDK-major or negotiated-protocol migration.

SDK clients check tool discovery, image results, stdio, HTTP session isolation,
authentication, duplicate requests, expiry, OAuth and durable grant recovery.
Run all integration checks with `npm run test --workspace @openphone/mcp`.
