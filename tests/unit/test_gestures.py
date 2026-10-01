import base64
import pathlib
import struct
import sys
import unittest

from openphone.device.gestures import pixel, plan


def decode(report):
    return struct.unpack('<BIHH', base64.b64decode(report['data']))


class NativeGestureTests(unittest.TestCase):
    def test_pixel_endpoints_and_middle_use_inclusive_range_and_truncation(self):
        self.assertEqual([pixel(x, 3) for x in range(3)], [0, 16383, 32767])
        self.assertEqual(pixel(1179, 1180), 32767)
        self.assertEqual(pixel(2555, 2556), 32767)
        self.assertEqual(pixel(0, 1180), 0)
        for value, extent in [(1180, 1180), (-1, 1180), (1, 1), (True, 3), (1.5, 3)]:
            with self.assertRaises(ValueError): pixel(value, extent)

    def test_tap_and_multitap_have_distinct_releases_and_post_settle(self):
        tapped = plan('tap', {'x': 1, 'y': 1}, 3, 3)
        self.assertEqual([decode(r) for r in tapped['reports']],
                         [(2, 0, 16383, 16383), (2, 1, 16383, 16383), (2, 0, 16383, 16383)])
        self.assertEqual([r['delay_ms'] for r in tapped['reports']], [0, 100, 100])
        self.assertEqual(tapped['duration_ms'], 300)
        tripled = plan('triple-tap', {'x': 1, 'y': 1}, 3, 3)
        self.assertEqual([decode(r)[1] for r in tripled['reports']], [0, 1, 0, 1, 0, 1, 0])
        self.assertEqual(tripled['duration_ms'], 700)

    def test_drag_speed_depends_on_native_distance_and_uses_easing(self):
        args = {'from_x': 100, 'from_y': 200, 'to_x': 900, 'to_y': 200}
        durations = [plan('drag', dict(args, speed=s), 1000, 2000)['motion_ms'] for s in ['slow', 'medium', 'fast']]
        self.assertEqual(durations[:2], [2000, 1000])
        self.assertAlmostEqual(durations[2], 800 / 1400 * 1000)
        dragged = plan('drag', args, 1000, 2000)
        self.assertEqual(len(dragged['reports']), 103)
        # At one quarter of the motion: 100 + 800 * 0.15625 = 225 pixels.
        self.assertEqual(decode(dragged['reports'][26])[2], 225 * 32767 // 999)
        self.assertEqual(dragged['duration_ms'], 1400)
        self.assertEqual(decode(dragged['reports'][-1]), (2, 0, 900 * 32767 // 999, 200 * 32767 // 1999))

    def test_short_drag_still_reaches_its_endpoint_and_hold_precedes_motion(self):
        args = {'from_x': 100, 'from_y': 200, 'to_x': 108, 'to_y': 200, 'speed': 'fast'}
        dragged = plan('hold-and-drag', dict(args, hold_duration_ms=1500), 1000, 2000)
        self.assertEqual(len(dragged['reports']), 4)
        self.assertEqual(dragged['reports'][2]['delay_ms'], 1510)
        self.assertEqual(decode(dragged['reports'][2])[2], 108 * 32767 // 999)
        self.assertEqual(decode(dragged['reports'][-1])[1], 0)

    def test_flick_strength_changes_time_but_keeps_one_third_screen_travel(self):
        for strength, milliseconds in [('weak', 270), ('medium', 180), ('strong', 90)]:
            result = plan('flick', {'x': 500, 'y': 1500, 'direction': 'up', 'strength': strength}, 1000, 2000)
            self.assertEqual(result['motion_ms'], milliseconds)
            self.assertEqual(len(result['reports']), milliseconds // 10 + 3)
            self.assertEqual(decode(result['reports'][-1]), (2, 0, 500 * 32767 // 999, 834 * 32767 // 1999))
            self.assertEqual(result['duration_ms'], milliseconds + 400)

    def test_malformed_or_excessive_gestures_are_rejected_before_a_plan_is_returned(self):
        for args in [{'x': float('nan'), 'y': 1}, {'x': 1, 'y': 1, 'extra': 1}, {'x': True, 'y': 1}]:
            with self.assertRaises(ValueError): plan('tap', args, 1000, 2000)
        with self.assertRaises(ValueError): plan('flick', {'x': 0, 'y': 0, 'direction': 'up'}, 1000, 2000)
        with self.assertRaises(ValueError): plan('drag', {'from_x': 0, 'from_y': 0, 'to_x': 8191, 'to_y': 8191, 'speed': 'slow'}, 8192, 8192)


if __name__ == '__main__': unittest.main()
