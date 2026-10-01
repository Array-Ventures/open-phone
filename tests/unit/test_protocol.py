import base64
import pathlib
import struct
import sys
import unittest
from openphone.device.driver import coordinate,tap_reports,swipe_reports,key_reports,CHAR_KEYS
import openphone.device.protocol as protocol


def report_lengths(descriptor):
    """Independent HID short-item parser: count bits declared by each Input."""
    pos=0;size=count=identifier=0;bits={}
    while pos<len(descriptor):
        header=descriptor[pos];pos+=1
        width=(0,1,2,4)[header&3];value=int.from_bytes(descriptor[pos:pos+width],'little');pos+=width
        kind=(header>>2)&3;tag=header>>4
        if kind==1:
            if tag==7:size=value
            if tag==8:identifier=value
            if tag==9:count=value
        elif kind==0 and tag==8:bits[identifier]=bits.get(identifier,0)+size*count
    return {identifier:1+(n+7)//8 for identifier,n in bits.items()}


class Reader:
    def __init__(self,data):self.data=data;self.pos=0
    def take(self,n):
        out=self.data[self.pos:self.pos+n];self.pos+=n
        if len(out)!=n:raise ValueError('truncated')
        return out
    def u16(self):return int.from_bytes(self.take(2),'little')
    def element(self):
        kind=self.take(1)[0]
        if kind==0:return None
        n=self.u16()
        if kind in (1,2,3):return int.from_bytes(self.take(max(4,n)),'little')
        if kind in (6,7):return [self.element() for _ in range(n)]
        if kind==5:return bool(self.take(n)[0])
        return self.take(n)


class ProtocolTests(unittest.TestCase):
    def test_reports_match_descriptor_lengths(self):
        sizes=report_lengths(protocol.DESCRIPTOR)
        self.assertEqual(sizes,{1:9,2:9})
        self.assertEqual(len(protocol.pointer()),sizes[2])
        with self.assertRaises(ValueError):protocol.pointer(absolute=False)
        self.assertEqual(len(protocol.keyboard([4],2)),sizes[1])

    def test_apple_record_uses_internal_counts_not_wire_lengths(self):
        data=protocol.make_apple_sdp();r=Reader(data);attrs={}
        for _ in range(r.u16()):
            key=r.u16();self.assertNotIn(key,attrs);attrs[key]=r.element()
        self.assertEqual(r.pos,len(data))
        self.assertEqual(attrs[1],[0x1124])
        self.assertEqual(attrs[4][0],[0x0100,0x11])
        self.assertEqual(attrs[0xd][0][0],[0x0100,0x13])
        self.assertEqual(attrs[0x206][0],[0x22,protocol.DESCRIPTOR])
        self.assertEqual(attrs[0x100],b'Open Phone HID')

    def test_tap_releases_all_buttons_and_settles_pointer(self):
        reports=tap_reports(.25,.75)
        self.assertGreaterEqual(reports[1]['delay_ms'],50)
        self.assertGreaterEqual(reports[2]['delay_ms'],30)
        decoded=[struct.unpack('<BIHH',base64.b64decode(r['data'])) for r in reports]
        self.assertEqual([r[1] for r in decoded],[0,1,0])
        self.assertEqual(decoded[-1][2:],(8192,24575))

    def test_swipe_is_continuous_held_drag_with_final_release(self):
        reports=swipe_reports(.5,.8,.5,.2)
        decoded=[struct.unpack('<BIHH',base64.b64decode(r['data'])) for r in reports]
        self.assertEqual(decoded[0][1],0);self.assertEqual(decoded[-1][1],0)
        self.assertTrue(all(r[1]==1 for r in decoded[1:-1]))
        self.assertTrue(all(a[3]>=b[3] for a,b in zip(decoded[1:],decoded[2:])))

    def test_reject_invalid_coordinates_before_input(self):
        for v in [-.1,1.1,float('nan'),float('inf'),'0.5',True]:
            with self.assertRaises(ValueError):coordinate(v)
        with self.assertRaises(ValueError):swipe_reports(0,0,2,0)
        with self.assertRaises(ValueError):tap_reports(.5,.5,1)

    def test_multi_tap_releases_between_every_press(self):
        reports=tap_reports(.5,.5,count=3,interval_ms=120)
        buttons=[struct.unpack('<BIHH',base64.b64decode(r['data']))[1] for r in reports]
        self.assertEqual(buttons,[0,1,0,1,0,1,0])
        self.assertEqual(sum(r['delay_ms'] for r in reports),540)

    def test_drag_pauses_before_release_and_can_hold_before_motion(self):
        reports=swipe_reports(.2,.3,.7,.8,hold_ms=1500)
        self.assertGreaterEqual(reports[2]['delay_ms'],1500)
        self.assertGreaterEqual(reports[-1]['delay_ms'],100)
        flick=swipe_reports(.2,.3,.7,.8,release_delay_ms=16)
        self.assertLess(flick[-1]['delay_ms'],reports[-1]['delay_ms'])

    def test_nonfinite_timing_rejected_before_gesture_emission(self):
        for bad in [float('nan'),float('inf'),True,'80']:
            with self.assertRaises(ValueError):tap_reports(.5,.5,hold_ms=bad)
            with self.assertRaises(ValueError):swipe_reports(.5,.8,.5,.2,duration_ms=bad)
        for count in [0,4,1.5,True]:
            with self.assertRaises(ValueError):tap_reports(.5,.5,count=count)

    def test_shift_and_us_layout(self):
        self.assertEqual(CHAR_KEYS['a'],(4,0));self.assertEqual(CHAR_KEYS['A'],(4,2))
        self.assertEqual(CHAR_KEYS['!'],(30,2));self.assertNotIn('é',CHAR_KEYS)

    def test_repeated_modified_key_releases_keys_and_modifiers(self):
        reports=key_reports('v',modifiers=8,repeat_count=2)
        decoded=[base64.b64decode(r['data']) for r in reports]
        self.assertEqual([r[1] for r in decoded],[8,0,8,0])
        self.assertEqual([r[3] for r in decoded],[25,0,25,0])
        for params in [dict(key='unsupported'),dict(key='v',modifiers=True),dict(key='v',repeat_count=0)]:
            with self.assertRaises(ValueError):key_reports(**params)

if __name__=='__main__':unittest.main()
