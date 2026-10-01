# Local Mac dashboard

The original dashboard adds connection setup, a live screen preview and manual
phone controls to the existing local driver.
It is a browser UI bound to `127.0.0.1`, not a native signed Mac distribution or
an internet dashboard. It attaches to the running REST server without starting
another capture or Bluetooth owner.

## Start

Build the helpers and start the normal REST server first, unless it is already
running. In a second terminal:

```sh
cd open-phone
openphone dashboard
```

The default opens the browser at `http://127.0.0.1:8769/` with an ephemeral access
key in the URL fragment. The page removes the fragment from browser history and
exchanges it for an HttpOnly, SameSite=Strict cookie. This key is independent of
the driver, relay and Shortcut keys and rotates when the dashboard restarts.
The driver key stays in the Python proxy; it is never served to browser JavaScript.
The access key is also stored in owner-only `.dashboard-token` for manual login.
Neither key is included in the source archive.

Use `openphone dashboard --no-open` to start without launching a browser. Open
the printed URL and enter the dashboard key from that file. The listener accepts
only its exact `127.0.0.1` host and rejects browser requests from other origins.
Use the printed URL rather than `localhost`, which has a different authority.

## Connect and control

1. Unlock the phone, attach its USB data cable and trust the Mac. Pair Bluetooth,
   turn on AssistiveTouch, and grant the driver's normal Mac permissions.
2. Refresh the device list, select its USB screen and enter its Bluetooth address
   from Settings → General → About. With one trusted phone, the address can be
   filled from USB metadata. Multiple-device capture/address identity still needs
   the user's check.
3. Connect, then wait for a fresh image. Click to tap, drag to scroll, or hold and
   release for a long press. The gesture selector adds double/triple tap and held
   drag. Scroll buttons emit flicks. Home requires device Button 3 mapped to Home.
4. Inspect the phone screen after input. Reports being written does not prove the
   app did what was intended. Controls wait for a new preview after each action.

Typing uses the US HID keyboard layout. The expandable Shortcut panel supports
Unicode clipboard copy/read, pasting into a focused field, app inventory/launch
and HTTP(S) URLs. Configure [the phone Shortcut bridge](bridge.md) first and map
device Button 9 to the imported Shortcut. Start the REST server with its correct
`OPEN_PHONE_BRIDGE_URL`; changing the dashboard environment cannot reconfigure
the already-running driver. The UI polls each phone action's original ID until
completion, expiry or timeout and does not retry the action.

## Preview and execution

The preview fetches resized images serially with a 250 ms pause, at most about
four frames per second plus capture/encoding/network time. It suspends in a
hidden tab, during a gesture or action, and when explicitly paused. Two concurrent
frame requests are admitted; additional viewers receive a busy response. This
uses requested JPEG frames. It has not been
measured for end-to-end latency or validated as a remote streaming service.

Pointer positions map the visible image to inclusive native pixel coordinates.
The driver checks fresh capture, the selected phone and native dimensions before
HID input. A capture error clears the active frame and disables controls. An
action failure does not trigger replay. Preview frames do not enter the source
archive or a cloud service.

## Validation and current limits

Eight HTTP checks cover browser access control, cookie flags, target guarding,
one-time forwarding on error, geometry headers, bounded frame admission and
phone action-ID preservation. Seven isolated JavaScript checks cover coordinate
endpoints, stale/missing frame rejection, post-action invalidation, failure
without replay, double tap/held-drag requests and the real app inventory shape.
Run:

```sh
python3 -m unittest discover -s tests/unit -v
node packages/mcp/tests/dashboard_client.mjs
```

The real running dashboard authenticated, fetched the actual Mac status and
inventory, and returned the driver's USB-unavailable error without enabling HID.
The connected-phone inventory is currently empty. The computer-use browser tool
also rejected its loopback URL with `net::ERR_BLOCKED_BY_CLIENT`. A rendered
browser check and physical dashboard interaction therefore remain unverified;
the isolated fixtures do not establish those results. Existing driver/relay
hardware results are separate evidence.
