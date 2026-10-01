# Validation status

This repository contains working hardware controls and supporting services. It
is under development. Protocol fixtures, physical tests and rendered UI checks
are separate forms of evidence.

## Physical checks on October 1, 2026

One trusted USB iPhone, with an existing Bluetooth pairing and configured
AssistiveTouch buttons, was tested on the development Mac:

| Check | Result |
|---|---|
| Fresh USB capture and native helper restart | Connected again after clean shutdown/rebuild |
| Tap and native-pixel targeting | Opened Settings and focused its search field |
| Scroll in both directions | Visible Settings list movement |
| Keyboard text | Search query appeared correctly |
| Home button | Returned visibly to the Home Screen |
| Preview during held drag | 30 fresh frames during the gesture; maximum sampled age about 24 ms |
| Wrong phone or screen dimensions | Rejected before gesture emission |
| H.264 WebRTC | 321 decoded frames over 20 seconds, about 16 fps, DTLS connected, no reported packet loss |
| VP8 WebRTC | 255 decoded frames over 15 seconds, about 17 fps, DTLS connected, no reported packet loss |
| MP4 recording while controlling | Recorded a 23-second real-phone navigation demo |

The WebRTC checks used two peer connections on the same Mac, with external STUN
and TURN disabled. They establish real encoding, encrypted transport, decoding
and cleanup. They do not establish Internet latency, TURN traversal, or a finished
remote viewer.

One attempted flick hit a restored Settings search keyboard because the test
assumed that reopening the app returned to its main list. That trial is not
counted as successful scrolling. The subsequent recorded demo closed search and
verified the intended list context. Capture freshness and matching dimensions do
not prove that an agent chose the correct UI target.

Earlier physical checks also exercised long press, double/triple text selection,
held dragging, Escape, installed-app inventory, and the optional Shortcut's
Unicode clipboard/app/URL operations. A period of missed taps recovered after an
AssistiveTouch reset. That incident remains a reliability limitation.

## Automated checks

The Python suite and Node integration scripts cover report layout, gesture
validation, phone guards, authentication, job receipts, bounded caches,
no-replay behavior, durable state and OAuth recovery/revocation. CI runs without
a physical phone. Native compilation runs separately on macOS.

Run:

```sh
python3 -m unittest discover -s tests -v
node tests/mcp.mjs
node tests/relay_mcp.mjs
node tests/dashboard_client.mjs
node tests/mcp_http.mjs
node tests/oauth_http.mjs
node tests/oauth_persistence.mjs
```

Hardware checks are opt-in. They can change the phone UI. Their screenshots and
recordings belong in ignored `private/` or `artifacts/` directories.

## Release work remaining

- First-time pairing and clean onboarding on additional devices.
- Developer Mode disabled, landscape calibration and broader app coverage.
- Longer capture/Bluetooth recovery, lock/unlock and Mac sleep testing.
- Rendered dashboard and consent validation.
- Continuous video integrated through the relay and remote browser controls.
- Provider identity, organization accounts and hosted deployment, if provided.

Passing fixture tests or a successful HID write does not close these gaps.
