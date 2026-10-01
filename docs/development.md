# Development

From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm ci --ignore-scripts
npm test
openphone build
```

The Python package has no mandatory third-party runtime dependencies. Recording
and WebRTC need the optional video dependencies:

```sh
python -m pip install -e '.[video]' -r requirements/video.txt
```

## Boundaries

- `src/openphone/device`: device orchestration, HID reports, gesture planning,
  USB metadata and native helper RPC.
- `src/openphone/services`: local API, dashboard and bundled web assets.
- `src/openphone/relay`: command broker, persistence, HTTP server, client,
  outbound worker and video signaling.
- `src/openphone/bridge`: optional phone Shortcut queue, server and generator.
- `src/openphone/auth`: the Python SQLite helper used by the MCP OAuth provider.
- `src/openphone/video`: fresh-frame video source and recording.
- `packages/mcp`: Node workspace containing transports, tools and OAuth.
- `native`: the macOS helper implementation.

Product modules use package imports. Service startup belongs to `openphone`
subcommands; importing a module must not start a server or build helpers.
Node uses installed Python modules through the shared MCP runtime adapter.

## Runtime locations

Runtime files retain their repository-root location for the existing development
setup. Set `OPEN_PHONE_HOME` to select a different runtime directory explicitly.
Both Python and Node use it. This does not copy credentials or change the
identity of existing OAuth databases. Set `OPEN_PHONE_NATIVE_BUNDLE` to select a
separately built `OpenPhone.app` bundle.

This checkout supports editable development installation. Packaging a signed
standalone Mac distribution is separate release work.

## Checks

`npm test` runs the Python suite and the MCP workspace integration checks. Native
compilation runs separately. Hardware checks under `tests/hardware` are opt-in;
they require real capture and can change the phone UI. Private evidence belongs
in ignored `private/` or `artifacts/` directories.

Keep detailed documentation under `docs/`. The root README provides the product
overview and setup entry point. Generated data and dependency directories do not
belong in source control.
