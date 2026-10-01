# Video and demo recording

The optional video tools read fresh USB frames independently of HID gestures.
Use Python 3.10 or later supported by the pinned dependencies; the physical
video tests used Python 3.14 on the development Mac.

```sh
python3 -m venv .venv-video
.venv-video/bin/python -m pip install --only-binary=:all: -r requirements-video.txt
```

Build the native helpers, start `python3 serve.py`, and select the phone using
`client.py connect` as described in the README. Keep one capture/HID owner.

## Record an MP4

```sh
.venv-video/bin/python record_demo.py \
  --seconds 30 --output artifacts/my-demo.mp4
```

Wait for the printed `recording` message before starting the controls you want to
demonstrate. The recorder writes H.264 video and a JSON report. It records the
selected phone's screen, without audio. It refuses to overwrite an existing file.
Capture loss ends the recording with an error; it does not repeat cached frames.
Changing orientation ends the current clip; start a new one for the new dimensions.

Video can contain private app content. Recordings and reports are excluded from
source exports and Git by the ignored `artifacts/` directory.

## Validate actual WebRTC transport

```sh
.venv-video/bin/python tests/video_physical.py --codec H264 --seconds 20 \
  --output private/webrtc-h264.json
.venv-video/bin/python tests/video_physical.py --codec VP8 --seconds 15 \
  --output private/webrtc-vp8.json
```

These opt-in checks connect two real WebRTC peers on this Mac, encode USB phone
frames, receive and decode them, inspect DTLS/packet statistics, and close both
peers and the source. They use no external STUN or TURN server. They emit no HID
input. They require a selected, unlocked, trusted USB phone.

`PhoneVideoTrack` can supply a send-only peer or a latest-frame `MediaRelay`.
Frames carry monotonic 90 kHz timestamps and even encoder dimensions. Freshness,
generation, JPEG format and image dimensions are checked before transmission.

Remote streaming is under development. `video_signaling.py` provides bounded,
host-session-bound offers, one-use answer receipts, viewer leases and cleanup.
The outbound host worker and remote browser viewer are not yet integrated with
this broker.
