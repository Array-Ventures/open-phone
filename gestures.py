"""Original native-pixel HID gesture planner, based on observed client behavior.

No vendor code runs here. Delays precede reports; finish_ms follows the last
report. The native helper's 1000-report/15-second bounds are enforced up front.
"""
import base64
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'native'))
import protocol

SPEED = {'slow': 400, 'medium': 800, 'fast': 1400}  # native pixels / second
STRENGTH = {'weak': 270, 'medium': 180, 'strong': 90}  # motion milliseconds
FIELDS = {
    'tap': ({'x', 'y'}, set()), 'double-tap': ({'x', 'y'}, set()),
    'triple-tap': ({'x', 'y'}, set()),
    'tap-and-hold': ({'x', 'y'}, {'duration_ms'}),
    'drag': ({'from_x', 'from_y', 'to_x', 'to_y'}, {'speed'}),
    'hold-and-drag': ({'from_x', 'from_y', 'to_x', 'to_y'}, {'speed', 'hold_duration_ms'}),
    'flick': ({'x', 'y', 'direction'}, {'strength'}),
}


def number(value, low, high, name, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or (integer and not isinstance(value, int)):
        raise ValueError(f'{name} must be a {"whole" if integer else "finite"} number in [{low}, {high}]')
    return value


def pixel(value, extent):
    """Map inclusive image endpoints to inclusive HID endpoints, truncating."""
    number(extent, 2, 8192, 'screen extent', True)
    return number(value, 0, extent - 1, 'pixel coordinate', True) * 32767 // (extent - 1)


def rounded_delta(value):
    # ARM64 FRINTA / Swift .rounded(): halfway away from zero, unlike round().
    return math.copysign(math.floor(abs(value) + .5), value)


def plan(action, params, width, height):
    number(width, 2, 8192, 'width', True); number(height, 2, 8192, 'height', True)
    if action not in FIELDS or not isinstance(params, dict):
        raise ValueError('Unknown native gesture or invalid parameters')
    required, optional = FIELDS[action]
    if not required <= params.keys() or params.keys() - required - optional:
        raise ValueError('Missing or unexpected gesture fields')

    def point(x, y):
        return number(params[x], 0, width - 1, x, True), number(params[y], 0, height - 1, y, True)

    def step(x, y, buttons=0, delay=0):
        data = protocol.pointer(pixel(x, width), pixel(y, height), buttons)
        return {'data': base64.b64encode(data).decode(), 'delay_ms': delay}

    if action in ('tap', 'double-tap', 'triple-tap', 'tap-and-hold'):
        x, y = point('x', 'y')
        count = {'double-tap': 2, 'triple-tap': 3}.get(action, 1)
        hold = number(params.get('duration_ms', 1000), 30, 2000, 'duration_ms') if action == 'tap-and-hold' else 100
        reports = [step(x, y)]
        for _ in range(count):
            reports.extend([step(x, y, 1, 100), step(x, y, 0, hold)])
        motion_ms = 0
    else:
        if action == 'flick':
            x, y = point('x', 'y')
            direction = params['direction']; strength = params.get('strength', 'medium')
            vectors = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}
            if direction not in vectors or strength not in STRENGTH:
                raise ValueError('Invalid flick direction or strength')
            dx, dy = vectors[direction]
            # Client movement uses one third of the native screen dimensions.
            end_x = max(0, min(width - 1, x + int(dx * width / 3)))
            end_y = max(0, min(height - 1, y + int(dy * height / 3)))
            if (x, y) == (end_x, end_y):
                raise ValueError('No room to flick in that direction')
            motion_ms = STRENGTH[strength]; hold = 100; eased = False
        else:
            x, y = point('from_x', 'from_y'); end_x, end_y = point('to_x', 'to_y')
            speed = params.get('speed', 'medium')
            if speed not in SPEED:
                raise ValueError('Invalid drag speed')
            motion_ms = math.hypot(end_x - x, end_y - y) * 1000 / SPEED[speed]
            hold = number(params.get('hold_duration_ms', 500), 0, 2000, 'hold_duration_ms') if action == 'hold-and-drag' else 100
            eased = True
        count = max(1, math.floor(motion_ms / 10))
        if count + 3 > 1000:
            raise ValueError('Gesture exceeds the native helper report capacity')
        reports = [step(x, y), step(x, y, 1, 100)]
        for index in range(1, count + 1):
            t = index / count
            if eased:
                t = t * t * (3 - 2 * t)
            next_x = x + int(rounded_delta((end_x - x) * t))
            next_y = y + int(rounded_delta((end_y - y) * t))
            reports.append(step(next_x, next_y, 1, 10 + (hold if index == 1 else 0)))
        reports.append(step(end_x, end_y, 0, 100))

    duration_ms = sum(report['delay_ms'] for report in reports) + 100
    if duration_ms > 15000:
        raise ValueError('Gesture exceeds the native helper duration capacity')
    return {'reports': reports, 'finish_ms': 100, 'duration_ms': duration_ms,
            'motion_ms': motion_ms, 'screen_width': width, 'screen_height': height}
