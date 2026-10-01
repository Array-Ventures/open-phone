# Device API and setup

Control a physical iPhone from a Mac using **USB screen capture + Bluetooth Classic
HID + AssistiveTouch**. OpenPhone runs locally and can use an optional self-hosted
relay for remote access.

Verified on this Mac and the connected iPhone: live USB capture, pointer movement,
tap, long press, Back navigation, scrolling by dragging, US-layout keyboard text,
Escape, double/triple tap text selection, held dragging of a selection handle,
flick, and direct Home after configuring AssistiveTouch device Button 3 to Home.
The optional phone Shortcut bridge also completed clipboard copy/read, Unicode
paste, app launch and URL opening, with Button 9 invoking it from the current app.
Several consecutive settings-page taps were verified after an AssistiveTouch
reset. An earlier period of missed taps remains a reliability limitation; the
validation notes record the recovery and targeting observations.

OpenPhone is under development. The hardware driver works, with release gaps
listed in the validation notes.
The low-level Classic transport uses private macOS APIs. See the
[architecture](architecture.md) and [validation status](validation.md) for what is confirmed and what is
still uncertain.

An optional [self-hosted HTTPS relay](relay.md) now adds remote phone/job REST
access and a 13-tool agent-side MCP adapter. Its Mac worker uses this local
driver. Encrypted screenshot delivery and a resized-image MCP tap were verified
on the attached phone.
The [Streamable HTTP MCP endpoint](http_mcp.md) exposes those 13 tools directly
over authenticated HTTPS. The SDK connected through verified TLS to the actual
relay; phone input through this new transport remains unverified.
Optional [single-owner OAuth](oauth.md) now adds public-client registration,
PKCE consent, token refresh/revocation and durable grants. Protocol, crash/restart
and SDK fixture checks pass; rendered consent and provider login remain outstanding.
The relay now [persists phone/app metadata and bounded job results](state.md)
without resuming old input after restart.

The [local dashboard](dashboard.md) adds connection setup, requested live preview,
pointer gestures, keyboard controls and the optional Shortcut panel. It attaches
to the existing REST server. Its HTTP and isolated client checks pass; rendered
browser and physical dashboard interaction still need verification.

Optional [video and recording tools](video.md) deliver fresh phone frames to
WebRTC and record MP4 demos. Both H.264 and VP8 were decoded across real encrypted
peer connections on the development Mac. Remote signaling integration and a
browser streaming viewer are still under development.

## Requirements and setup

- Mac with Xcode command-line tools, Python 3.10+, and Node 20+ for MCP.
- USB **data** cable; phone trusts this Mac and remains unlocked.
- Bluetooth pairing between the phone and Mac; Bluetooth enabled on both.
- iPhone AssistiveTouch enabled. In its device-button settings, map Button 1 to
  Single-Tap if needed, and Button 3 to Home if using the direct Home tool. Default
  primary clicking worked on the test phone without adding a Button 1 mapping.
- Grant the regular macOS Bluetooth and Camera prompts. Launching helpers from a
  terminal may attribute these permissions to Terminal. Camera permission is used
  only to open the selected USB phone screen device; webcams are excluded.
- Close other applications using the same HID/capture resources while
  testing this prototype.
- Optional [go-ios](https://github.com/danielpaulus/go-ios) CLI for USB device
  identity and installed-app inventory. Set `OPEN_PHONE_IOS` to its executable
  if it is outside PATH. These operations use the normal trusted USB connection;
  the adapter does not start a developer tunnel or install a phone agent.

The implementation does not launch a phone-side development agent, WDA, an iOS
simulator or iPhone Mirroring. It does not use the phone accessibility tree.
The test phone already has Developer Mode enabled. A run with that setting
disabled has not yet been performed.

```sh
cd open-phone
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm ci --ignore-scripts
openphone build
python3 -m unittest discover -s tests/unit -v
```

The build creates ad-hoc-signed local helpers in `build/OpenPhone.app`. The muxed
capture enumeration intentionally uses the still-available deprecated devices
API: these USB screen devices differ from ordinary video cameras.

## REST API

```sh
openphone serve
```

The server binds **127.0.0.1:8766** and creates an owner-readable `.runtime-token`.
All device endpoints require that token. Browser origins are rejected. The client
reads the token file so it does not need to appear in command arguments.

```sh
openphone device devices
openphone device connect '{"address":"AA:BB:CC:DD:EE:FF","capture_id":"auto"}'
openphone device screenshot --out /tmp/phone.jpg
openphone device tap '{"x":0.5,"y":0.5}'
openphone device scroll '{"direction":"down"}'
openphone device type_text '{"text":"Settings"}'
openphone device press_key '{"key":"escape"}'
```

Use the **iPhone's** Bluetooth hardware address. `auto` capture is accepted only
when exactly one USB screen device is available. Select `capture_id` from the
device inventory otherwise. Capture IDs differ from USB UDIDs. The wrapper
verifies the Bluetooth address against trusted USB metadata when go-ios is
available. With several USB capture devices, check that the selected capture ID
also belongs to that phone; this mapping is not automatically proven.

`devices` includes optional `usb_phones` metadata with Bluetooth addresses and
USB UDIDs. After connecting, `apps` returns bundle ID, display name, version and
application type. System inventory can include background components; use
`{"include_system":false}` for user applications. No app content is read.

Endpoints:

| Route | Behavior |
|---|---|
| `GET /health` | Process health, no device access |
| `GET /v1/devices` | USB phone screen-device inventory |
| `GET /v1/status` | Selected device and HID channel state |
| `GET /v1/apps` | Installed-app metadata, requires optional go-ios |
| `GET /v1/screenshot` | Fresh JPEG image, no-store response |
| `POST /v1/action` | JSON `{ "method": "tap", "params": {"x":0.5,"y":0.5} }` |

Actions currently include `devices`, `connect`, `status`, `apps`, `screenshot`, `move`,
`tap`, `swipe`, `hold_and_drag`, `flick`, `scroll`, `type_text`, `press_key` and
`press_button`, plus optional `shortcut_action`, `shortcut_result` and
`shortcut_cancel`. See [Shortcut bridge setup and validation](bridge.md).
Long press is a tap with `hold_ms` above the normal tap threshold;
`count: 2` and `count: 3` request double and triple taps. `swipe` is a held-pointer
drag with a brief pause before release; `flick` releases while moving.
Scroll directions describe the movement through content: `down` drags upward.

For Home setup, navigate to Settings → Accessibility → Touch → AssistiveTouch →
Devices → your Mac → Customize Additional Buttons. When prompted for a button,
send `press_button {"button":"home"}` once to register **Button 3**. Open the
Button 3 row and select **Home**. The Home action then uses a standard mouse
button report, not a keyboard shortcut. This mapping persists on the phone.

All pointer coordinates are normalized **0–1** across the returned image. A pixel
coordinate `(x, y)` from an image of `(width, height)` becomes approximately
`(x / width, y / height)`. Original capture dimensions are returned with MCP
screenshots. Do not apply a second scaling conversion.

The relay's named gesture routes use a separate **native-pixel** profile. It maps
inclusive pixel endpoints with `pixel * 32767 // (extent - 1)`, taps with 100 ms
press/release intervals, and drags at 400/800/1400 native pixels per second using
10 ms movement steps and smoothstep easing. Flick travels one third of the screen;
the relay chooses medium strength (180 ms motion). The normalized local tools retain their
earlier configurable gesture profile. See [RELAY.md](relay.md) for limits.

## MCP

Run `node /absolute/path/to/open-phone/packages/mcp/src/mcp.mjs` as a stdio MCP server. The entry
point uses the official MCP TypeScript SDK and spawns the Python driver locally.
Stop the REST server before giving an MCP instance ownership of the same phone.

```toml
[mcp_servers.open_phone]
command = "node"
args = ["/absolute/path/to/open-phone/packages/mcp/src/mcp.mjs"]
# Optional, after importing the phone Shortcut and starting its separate bridge:
env = { OPEN_PHONE_BRIDGE_URL = "http://MAC_LAN_IP:8767" }
```

It exposes seventeen `phone_*` tools, including image-returning screenshots and
three optional Shortcut bridge tools. Connect
the chosen phone before input. Each input checks that capture is fresh; gestures
are emitted and timed within the native helper, independently of model round
trips. Check a screenshot afterward to verify the intended UI result. An `ok`
gesture response means reports were written, not that the target app did what the
agent intended. UI animations can outlast gesture execution.

After scrolling, inspect a settled screenshot before choosing a row. If input
stops activating targets despite pointer movement, an AssistiveTouch off/on reset
recovered the test phone. The driver does not automatically change that setting.
Normal shutdown releases the driver's keys and pointer buttons before removing
its HID service and explicitly closes the owned L2CAP channels. Missing channels
are retried before input, with duplicate pending opens suppressed. Three fresh
close/reopen cycles visibly opened Settings and returned Home; one encountered a
transient interrupt-channel failure and recovered before sending input. This is
a bounded reconnect test, not evidence of indefinite unattended reliability.
Use `status {"diagnostics":true}` to inspect recent native connection events.
Status also reports capture-device presence, session running state, frame age and
freshness. A running session alone is insufficient: connect checks a fresh frame
before enabling HID. Explicit reconnect restarts a stale session only if its
selected USB device is still available. Input fails when capture is unavailable;
it does not replay a gesture or automatically change phone settings.

## Limits

- One selected phone per driver instance. Simultaneous multi-phone operation is
  not implemented or tested in this wrapper.
- Existing pairings may cache a HID descriptor. This build preserves compatible
  absolute-pointer and keyboard report layouts, but first-time pairing has not
  yet been verified with a second device.
- Text input currently uses a US HID keyboard layout, at most 200 characters.
  The optional Shortcut bridge adds Unicode clipboard transfer, app launch and
  URL opening. These operations and Button 9 invocation worked on the test phone.
  First-run permissions and clipboard paste prompts can interrupt automation.
- Relative-wheel, landscape-coordinate calibration, volume/power actions, and
  long-running disconnect/reconnect behavior are not fully verified.
- Double/triple taps, flick and hold-and-drag have each produced visible results
  on the test phone. Broader app-level and long-running testing is still needed.
- The optional self-hosted relay forwards requested screenshots and commands.
  Organizational accounts, provider login, a remote streaming dashboard and a hosted
  model are not implemented. Owner-key OAuth has its own durable prototype store.
  The local dashboard uses requested image frames;
  local mode keeps frames and input on the Mac.
- Private CoreBluetooth selectors may change or disappear after macOS updates.
  Failures are reported rather than silently falling back to another control path.

The test suite checks HID descriptor/report lengths, Apple SDP serialization,
gesture validation/release behavior, failed/mismatched connection handling and
local API authentication, Shortcut queue/credential separation and preservation
of operation IDs through the stdio transport, relay job receipts/expiry and
credential separation, coordinate conversion, phone-target guarding and TLS
listener concurrency, inclusive pixel mapping, distance-based gesture timing,
changed screen dimensions, unavailable capture and dashboard browser access /
phone-target guarding, snapshot dimension preservation, durable relay recovery
and source exclusion of runtime databases. The 73 Python checks
pass on this Mac. Hardware tests and
results are documented in [VALIDATION.md](validation.md). Private screenshots and
device identifiers are excluded from this repository. The official SDK
client verified seventeen MCP tools, a real connection, image delivery,
Home report delivery, the 333-entry USB application inventory, and an exact
Unicode clipboard round trip through the real phone Shortcut. The optional
Shortcut tools require their separate phone-side setup and bridge server.

```sh
node packages/mcp/tests/mcp.mjs
node packages/mcp/tests/dashboard_client.mjs
node packages/mcp/tests/mcp_http.mjs
node packages/mcp/tests/oauth_http.mjs
node packages/mcp/tests/oauth_persistence.mjs
node packages/mcp/tests/mcp.mjs --hardware AA:BB:CC:DD:EE:FF --apps --home
OPEN_PHONE_BRIDGE_URL=http://MAC_LAN_IP:8767 node packages/mcp/tests/mcp.mjs --hardware AA:BB:CC:DD:EE:FF --bridge --home
```

The optional hardware command requires an unlocked, trusted USB phone, existing
Bluetooth pairing, go-ios for `--apps`, and the Button 3 mapping for `--home`.
Its Home test records a screenshot for visual inspection; a successful write
does not by itself prove the phone reached the Home Screen.
The `--bridge` check replaces the phone clipboard with its own Unicode test
string, invokes the mapped Shortcut, and requires an exact read-back result.
The separate relay SDK/hardware check is documented in [RELAY.md](relay.md).

`openphone export` creates a reproducible source archive, excluding
runtime credentials, private certificates, compiled helpers, dependencies and
private research and recordings. It stops if a runtime key/private key appears in an
included source or document.
