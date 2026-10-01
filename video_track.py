"""Fresh USB frames for WebRTC. Optional requirements-video.txt dependencies.

The source reads independently of HID sequences. A capture failure ends the
track; cached images are never substituted for a disconnected phone.
"""
import asyncio
import base64
from fractions import Fraction
from io import BytesIO
import time

import av
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError


class PhoneVideoTrack(VideoStreamTrack):
    def __init__(self, local, address, fps=20, max_dimension=1344):
        super().__init__()
        if isinstance(fps, bool) or not isinstance(fps, int) or not 5 <= fps <= 30:
            raise ValueError('Video fps must be 5–30')
        if isinstance(max_dimension, bool) or not isinstance(max_dimension, int) or not 320 <= max_dimension <= 1920:
            raise ValueError('Video max_dimension must be 320–1920')
        self.local = local
        self.address = address
        self.fps = fps
        self.max_dimension = max_dimension
        self.generation = -1
        self.started = None
        self.next_frame = None
        self.last_metadata = None
        self.frames = 0

    async def recv(self):
        if self.readyState != 'live':
            raise MediaStreamError
        now = time.monotonic()
        if self.next_frame is not None and self.next_frame > now:
            await asyncio.sleep(self.next_frame - now)
        try:
            result = await asyncio.to_thread(self.local.request, '/v1/action', {
                'method': 'video_frame', 'expected_address': self.address,
                'params': {'max_dimension': self.max_dimension, 'after_generation': self.generation},
            }, timeout=7)
            if self.readyState != 'live':
                raise MediaStreamError
            generation, timestamp = result['generation'], result['timestamp']
            if (not isinstance(generation, int) or isinstance(generation, bool)
                    or generation <= self.generation
                    or not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
                    or not 0 <= time.time() - timestamp < 2
                    or result['mime_type'] != 'image/jpeg'
                    or not isinstance(result['data'], str) or len(result['data']) > 6_000_000):
                raise ValueError('Capture returned a stale or invalid video frame')
            encoded = base64.b64decode(result['data'], validate=True)
            if not encoded.startswith(b'\xff\xd8'):
                raise ValueError('Capture returned invalid JPEG data')
            with av.open(BytesIO(encoded), format='mjpeg') as container:
                frame = next(container.decode(video=0))
            if (frame.width != result['width'] or frame.height != result['height']
                    or not 2 <= min(frame.width, frame.height)
                    or max(frame.width, frame.height) > self.max_dimension):
                raise ValueError('Video dimensions differ from the capture metadata')
            frame = frame.reformat(width=frame.width - frame.width % 2,
                                   height=frame.height - frame.height % 2, format='yuv420p')
            now = time.monotonic()
            if self.started is None:
                self.started = now
            frame.pts = round((now - self.started) * 90000)
            frame.time_base = Fraction(1, 90000)
            self.next_frame = now + 1 / self.fps
            self.generation = generation
            self.frames += 1
            self.last_metadata = {key: result[key] for key in (
                'capture_id', 'generation', 'timestamp', 'screen_width', 'screen_height')}
            return frame
        except asyncio.CancelledError:
            self.stop()
            raise
        except Exception as error:
            self.stop()
            raise MediaStreamError from error
