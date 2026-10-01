# OpenPhone

Control a physical iPhone from macOS using USB screen capture, Bluetooth HID and
AssistiveTouch. Use the local dashboard, connect an agent through MCP, or run your
own relay for remote access.

OpenPhone is under development. The device controls and recording tools work on
the tested phone; remote streaming, onboarding and broader recovery testing are
still in progress. [Validation status](docs/validation.md) records the evidence.

## Get started

You need macOS, Xcode command-line tools, Python 3.10+, Node 20+, an unlocked
trusted USB iPhone, and Bluetooth pairing with AssistiveTouch enabled. See the
[device setup guide](docs/device-api.md) for permissions and button mappings.

```sh
git clone https://github.com/Array-Ventures/open-phone.git
cd open-phone
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm ci --ignore-scripts
openphone build
openphone serve
```

In a second terminal, activate the same environment and run `openphone dashboard`.
Select your phone, connect, and use the live preview and controls.

`openphone --help` lists device, dashboard, relay, bridge, recording and build
commands. Every command has its own `--help`.

## Connect an agent

The local MCP exposes 17 phone tools. Configure your MCP client with:

```toml
[mcp_servers.open_phone]
command = "node"
args = ["/absolute/path/to/open-phone/packages/mcp/src/mcp.mjs"]
```

The MCP uses the repository's installed `.venv` by default. Stop the local API
before giving the local MCP ownership of the same phone. The remote MCP exposes
13 relay tools and also supports Streamable HTTP with optional OAuth.
[MCP guide](docs/mcp.md)

## Project layout

```text
src/openphone/        Python package: device, services, relay, bridge, video
packages/mcp/        MCP transports, tools, OAuth and integration tests
native/              Objective-C and Swift macOS helper sources
tests/               Python unit tests and opt-in hardware checks
docs/                Setup, architecture and validation guides
requirements/        Optional video dependency lock
```

Credentials, databases, recordings and compiled helpers stay outside Git.

## Development

```sh
npm test
openphone build
```

CI runs the protocol tests and macOS native compilation. Hardware tests require
a connected phone and are opt-in. [Architecture](docs/architecture.md) ·
[Development guide](docs/development.md) · [All documentation](docs/index.md)

MIT licensed. See [LICENSE](LICENSE).
