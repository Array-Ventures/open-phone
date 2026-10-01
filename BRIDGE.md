# Optional phone Shortcut bridge

The optional bridge lets a phone Shortcut copy text, read clipboard text, open
an app by bundle ID, and open an HTTP(S) URL through the self-hosted service.
The driver’s Bluetooth gestures and USB capture continue to work without it.

The original 31-action template was signed and imported on the physical iPhone.
All four operations completed through the authenticated bridge: copy text, read
back that text, open Settings by bundle ID, and open an HTTP(S) webpage. The test
copied `OpenPhone ✓ 🌍`, read back the exact value, and visibly pasted it into
Spotlight using Command-V. Button 9 launched the Shortcut directly from Settings.
The plist format is undocumented and must be validated on each target OS; signing
alone does not prove the action parameters or phone permissions are correct.

## Setup

Start the normal phone REST API once to create its owner-only `.runtime-token`.
Then generate and sign a template using the Mac address reachable by the phone:

```sh
python3 shortcut_template.py --url http://MAC_LAN_IP:8767
shortcuts sign --mode anyone --input /absolute/path/to/private/Use\ OpenPhone.unsigned.shortcut --output /absolute/path/to/private/Use\ OpenPhone.shortcut
python3 bridge_serve.py --host MAC_LAN_IP --template-file 'private/Use OpenPhone.shortcut'
```

Use **absolute paths** for signing: the initial relative-path command exited
successfully without creating the requested artifact on the test Mac. Inspect
the output file before using it. Apple's “anyone” signing mode submits the
credential-free workflow to Apple for validation; the template has a placeholder,
not the runtime phone key. Do not embed the runtime key before sharing or signing.

The bridge creates an owner-only `.bridge-token`, distinct from the controller
token. Import the signed template on the iPhone as **Use OpenPhone**, set its
service origin and phone key through its import questions, and allow the required
local-network/clipboard actions when first running it. The default HTTP server is
for the local test network; it does not encrypt credentials or payloads in transit.
It does not expose the main HID API to the LAN.

To trigger it through HID, map your Mac's **Button 9** to **Use OpenPhone** under
Settings → Accessibility → Touch → AssistiveTouch → Devices. Send
`press_button {"button":"shortcut"}` to register that button when prompted.
Button 3 remains Home. Before mapping Button 9, invoke the Shortcut manually.

Set `OPEN_PHONE_BRIDGE_URL=http://MAC_LAN_IP:8767` in the environment of the REST
or MCP driver. Its controller requests read the existing `.runtime-token` file;
the phone receives only `.bridge-token`. Tokens and generated files are ignored
by Git. The optional `/UseOpenPhone.shortcut` download contains the signed
credential-free template and is available only when `--template-file` is supplied.

## API and agent use

The controller uses bearer authentication. It can enqueue, inspect or cancel an
action. The iPhone uses `X-OpenPhone-Token` on its claim and completion routes.
Neither credential substitutes for the other.

| Route | Credential and purpose |
|---|---|
| `POST /v1/bridge/actions` | Controller; `{kind, payload, ttl}` |
| `GET /v1/bridge/actions/ID` | Controller; state and completion result |
| `POST /v1/bridge/actions/ID/cancel` | Controller; cancel with an empty object |
| `GET /v1/bridge/status` | Controller; recent route/status/error diagnostics, no payloads or credentials |
| `POST /v1/bridge/claim` | Phone; return one queued action and its receipt |
| `POST /v1/bridge/complete` | Phone; `{id, receipt, result}` |

The four kinds/payloads are `copy_text: {text}`, `read_clipboard: {}`,
`open_app: {bundle_id}`, and `open_url: {url}`. Payloads and clipboard results
are limited to 20,000 characters; HTTP(S) URLs cannot contain credentials.

MCP exposes `phone_shortcut_action`, `phone_shortcut_result` and
`phone_shortcut_cancel`. The REST driver's methods have the same names without
the `phone_` prefix. For example:

```python
from client import Client
c = Client()
action = c.call('shortcut_action', kind='copy_text',
                payload={'text': 'OpenPhone ✓ 🌍'}, trigger=True)
result = c.call('shortcut_result', action_id=action['id'])
```

`trigger=True` requires a connected phone and its Button 9 mapping. A queued
response is not a completed action. Inspect the result until it is completed,
expired or cancelled, then verify app behavior with a screenshot. After a
confirmed copy, `press_key {"key":"v", "modifiers":8}` sends Command-V into the
focused field. iOS may ask the destination app to allow pasting from Shortcuts;
the physical test granted that permission for the test string in Spotlight.

When configuring MCP, supply `OPEN_PHONE_BRIDGE_URL` in the server's explicit
environment. MCP clients can filter the calling shell's environment, so merely
exporting the variable may leave the driver using its loopback default.

The initial imported workflow failed because a dictionary-value magic variable
was not explicitly coerced to text before its comparisons. The generator now
sets that coercion and the nested JSON dictionary serialization required by iOS.
The corrected workflow is named **Use OpenPhone 2** on the test phone, and Button
9 maps to that name. New installations can use **Use OpenPhone** as described
above. The earlier test workflow has an obsolete rotated credential.

Each invocation fetches once. A delivered action is not handed out again, even
if its result is missing. The default deadline is 60 seconds (configurable 5–120);
expired actions cannot be claimed or completed. Pending payloads are removed on
completion, cancellation or expiry. Up to 32 result records remain in memory for
five minutes; restarting the bridge clears them. Cancelling cannot undo a phone
action already executed.
