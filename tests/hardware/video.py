#!/usr/bin/env python3
"""Opt-in real USB phone -> DTLS/SRTP -> decoded WebRTC video validation.

No synthetic image, HID action, browser, or cloud server participates. Run with
the optional video environment while the local API has a selected phone.
"""
import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import time

from aiortc import RTCConfiguration, RTCPeerConnection, RTCRtpSender
from openphone.clients.local import Client
from openphone.video.track import PhoneVideoTrack


async def verify(args):
    local = Client(args.local_url, args.token_file)
    status = await asyncio.to_thread(local.call, 'status')
    if not status.get('address'):
        raise RuntimeError('Select the phone in the local API first')
    source = PhoneVideoTrack(local, status['address'], fps=args.fps, max_dimension=args.max_dimension)
    host = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    viewer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    received = asyncio.get_running_loop().create_future()
    @viewer.on('track')
    def on_track(track):
        if track.kind == 'video' and not received.done():
            received.set_result(track)
    rows = []
    started = time.monotonic()
    report = None
    try:
        viewer.addTransceiver('video', direction='recvonly')
        host.addTrack(source)
        transceiver = host.getTransceivers()[0]
        transceiver.direction = 'sendonly'
        codecs = [codec for codec in RTCRtpSender.getCapabilities('video').codecs
                  if codec.mimeType.lower() == 'video/' + args.codec.lower()]
        if not codecs:
            raise RuntimeError('Requested codec is unavailable')
        transceiver.setCodecPreferences(codecs)
        await viewer.setLocalDescription(await viewer.createOffer())
        await host.setRemoteDescription(viewer.localDescription)
        await host.setLocalDescription(await host.createAnswer())
        await viewer.setRemoteDescription(host.localDescription)
        track = await asyncio.wait_for(received, 10)
        first = await asyncio.wait_for(track.recv(), 10)
        first_at = time.monotonic()
        previous_pts = first.pts
        rows.append({'elapsed': 0, 'width': first.width, 'height': first.height, 'pts': first.pts})
        deadline = first_at + args.seconds
        while time.monotonic() < deadline:
            frame = await asyncio.wait_for(track.recv(), 5)
            if frame.pts <= previous_pts:
                raise RuntimeError('Decoded video timestamps did not advance')
            previous_pts = frame.pts
            rows.append({'elapsed': time.monotonic() - first_at,
                         'width': frame.width, 'height': frame.height, 'pts': frame.pts})
        inbound = [vars(value) for value in (await viewer.getStats()).values()
                   if value.type == 'inbound-rtp']
        transport = [dict(bytesReceived=value.bytesReceived, dtlsState=value.dtlsState)
                     for value in (await viewer.getStats()).values() if value.type == 'transport']
        if host.connectionState != 'connected' or viewer.connectionState != 'connected':
            raise RuntimeError('Peer connection was not connected at the end')
        if not transport or any(value['dtlsState'] != 'connected' for value in transport):
            raise RuntimeError('Encrypted transport was not established')
        gaps = [b['elapsed'] - a['elapsed'] for a, b in zip(rows, rows[1:])]
        report = dict(codec=args.codec, decoded_frames=len(rows),
                      measured_fps=(len(rows) - 1) / rows[-1]['elapsed'],
                      first_frame_seconds=first_at - started,
                      median_frame_gap_ms=statistics.median(gaps)*1000,
                      max_frame_gap_ms=max(gaps)*1000,
                      source_frames=source.frames, source_metadata=source.last_metadata,
                      transport=transport, inbound=inbound, frames=rows)
    finally:
        source.stop()
        await asyncio.gather(host.close(), viewer.close())
    report['cleanup'] = dict(source=source.readyState, host=host.connectionState, viewer=viewer.connectionState)
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2, default=str) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'frames'}, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-url', default='http://127.0.0.1:8766')
    parser.add_argument('--token-file')
    parser.add_argument('--codec', choices=['H264', 'VP8'], default='H264')
    parser.add_argument('--fps', type=int, default=20)
    parser.add_argument('--max-dimension', type=int, default=960)
    parser.add_argument('--seconds', type=int, default=20, choices=range(5, 121))
    parser.add_argument('--output')
    asyncio.run(verify(parser.parse_args()))


if __name__ == '__main__':
    main()
