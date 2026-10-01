#!/usr/bin/env python3
"""Record the selected real iPhone to MP4 without blocking its HID controls.

Run with .venv-video/bin/python after selecting a phone in the local API.
"""
import argparse
import asyncio
from fractions import Fraction
import json
from pathlib import Path
import time

import av
from client import Client
from video_track import PhoneVideoTrack


async def record(args):
    destination = Path(args.output).expanduser().resolve()
    if destination.exists():
        raise ValueError('The recording destination already exists; choose a new filename')
    destination.parent.mkdir(parents=True, exist_ok=True)
    client = Client(args.local_url, args.token_file)
    status = await asyncio.to_thread(client.call, 'status')
    if not status.get('address'):
        raise RuntimeError('Connect a phone in the local API first')
    track = PhoneVideoTrack(client, status['address'], args.fps, args.max_dimension)
    count = 0
    started = time.monotonic()
    error = None
    try:
        with av.open(str(destination), mode='w', options={'movflags': '+faststart'}) as output:
            stream = output.add_stream('libx264', rate=args.fps)
            stream.pix_fmt = 'yuv420p'
            stream.time_base = Fraction(1, 90000)
            stream.codec_context.time_base = Fraction(1, 90000)
            stream.options = {'crf': '20', 'preset': 'veryfast', 'tune': 'zerolatency'}
            frame = await track.recv()
            stream.width, stream.height = frame.width, frame.height
            started = time.monotonic()
            print(json.dumps({'recording': str(destination), 'width': frame.width,
                              'height': frame.height, 'seconds': args.seconds}), flush=True)
            try:
                while True:
                    for packet in stream.encode(frame):
                        output.mux(packet)
                    count += 1
                    if time.monotonic() - started >= args.seconds:
                        break
                    frame = await track.recv()
                    if (frame.width, frame.height) != (stream.width, stream.height):
                        raise RuntimeError('Phone orientation changed; stop this clip and start a new one')
            finally:
                for packet in stream.encode():
                    output.mux(packet)
    except BaseException as failure:
        error = type(failure).__name__ + ': ' + str(failure)
        raise
    finally:
        track.stop()
        report = {'file': str(destination), 'frames': count,
                  'elapsed_seconds': time.monotonic() - started,
                  'source': track.last_metadata, 'error': error}
        destination.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seconds', type=int, default=30, choices=range(1, 3601))
    parser.add_argument('--fps', type=int, default=20)
    parser.add_argument('--max-dimension', type=int, default=1344)
    parser.add_argument('--local-url', default='http://127.0.0.1:8766')
    parser.add_argument('--token-file')
    asyncio.run(record(parser.parse_args()))


if __name__ == '__main__':
    main()
