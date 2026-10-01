"""Original HID/SDP definitions using Bluetooth SIG and USB HID encodings.

Report IDs: 1 keyboard, 2 absolute mouse.
No vendor executable or descriptor is required.
"""
import base64
import json
import pathlib
import struct


def mouse(report_id, absolute):
    # Generic Desktop / Mouse / Pointer, 32 buttons, 16-bit X/Y.
    d = bytes.fromhex('05010902a10185') + bytes([report_id])
    d += bytes.fromhex('0901a10005091901292015002501952075018102')
    d += bytes.fromhex('050109300931')
    if absolute:
        d += bytes.fromhex('150026ff7f751095028102')
    else:
        d += bytes.fromhex('16018026ff7f751095028106')
    # The absolute report remains compatible with previously paired iPhones.
    # Scrolling can use a held-button drag without relying on a wheel report.
    if not absolute:
        d += bytes.fromhex('09381581257f750895018106050c0a380295018106')
    d += bytes.fromhex('c0c0')
    return d


DESCRIPTOR = mouse(2, True)
DESCRIPTOR += bytes.fromhex('05010906a1018501050719e029e71500250175019508810275089501810105081901290595057501910295017503910105071500256519002965750895068100c0')


def uint(value, width=2):
    return bytes([0x08 + {1:0, 2:1, 4:2}[width]]) + value.to_bytes(width,'big')


def uuid(value):
    return b'\x19' + value.to_bytes(2,'big')


def sequence(*items):
    value = b''.join(items)
    if len(value) < 256:
        return b'\x35' + bytes([len(value)]) + value
    return b'\x36' + len(value).to_bytes(2,'big') + value


def string(value):
    value = value.encode() if isinstance(value,str) else value
    if len(value) < 256:
        return b'\x25' + bytes([len(value)]) + value
    return b'\x26' + len(value).to_bytes(2,'big') + value


def boolean(value):
    return b'\x28' + bytes([bool(value)])


def sdp_attributes():
    attrs = {
        0x0001:sequence(uuid(0x1124)),
        0x0004:sequence(sequence(uuid(0x0100),uint(0x11)),sequence(uuid(0x0011))),
        0x0005:sequence(uuid(0x1002)),
        0x0006:sequence(uint(0x656e),uint(0x006a),uint(0x0100)),
        0x0009:sequence(sequence(uuid(0x1124),uint(0x0101))),
        0x000d:sequence(sequence(sequence(uuid(0x0100),uint(0x13)),sequence(uuid(0x0011)))),
        0x0100:string('Open Phone HID'),
        0x0101:string('Local open-source iPhone controller'),
        0x0102:string('Open Phone'),
        0x0200:uint(0x0100),
        0x0201:uint(0x0111),
        0x0202:uint(0xc0,1),
        0x0203:uint(0,1),
        0x0204:boolean(True),
        0x0205:boolean(True),
        0x0206:sequence(sequence(uint(0x22,1),string(DESCRIPTOR))),
        0x0207:sequence(sequence(uint(0x0409),uint(0x0100))),
        0x0208:boolean(False),
        0x0209:boolean(False),
        0x020a:boolean(True),
        0x020b:uint(0x0101),
        0x020c:uint(3200),
        0x020d:boolean(True),
        0x020e:boolean(False),
    }
    return attrs


def make_sdp():
    """Bluetooth wire encoding, useful for inspection but not CBClassicManager."""
    return sequence(*(uint(k)+v for k,v in sorted(sdp_attributes().items())))


def apple_element(wire):
    """Convert standard SDP into the installed CoreBluetooth daemon's encoding.

    Each element has a type byte and a little-endian uint16 width/count.
    Integer/short UUID payloads occupy at least four bytes. Sequences count
    children rather than payload bytes. Derived from getLocalSDPDatabase.
    """
    header = wire[0]
    kind, size_code = header >> 3, header & 7
    cursor = 1
    if kind == 0:
        return b'\0', cursor
    if size_code <= 4:
        size = 1 << size_code
    else:
        size_width = {5:1, 6:2, 7:4}[size_code]
        size = int.from_bytes(wire[cursor:cursor+size_width], 'big')
        cursor += size_width
    payload = wire[cursor:cursor+size]
    if len(payload) != size:
        raise ValueError('Truncated SDP element')
    if kind in (1, 2, 3):
        value = int.from_bytes(payload, 'big', signed=kind == 2)
        encoded = value.to_bytes(max(4,size), 'little', signed=kind == 2)
    elif kind in (6, 7):
        children, at = [], 0
        while at < size:
            child, consumed = apple_element(payload[at:])
            children.append(child); at += consumed
        encoded, size = b''.join(children), len(children)
    else:
        encoded = payload
    return bytes([kind])+struct.pack('<H',size)+encoded, cursor+len(payload)


def make_apple_sdp():
    attrs = sdp_attributes()
    return struct.pack('<H',len(attrs)) + b''.join(
        struct.pack('<H',k)+apple_element(v)[0] for k,v in sorted(attrs.items()))


def pointer(x=0,y=0,buttons=0,wheel=0,pan=0,absolute=True):
    if not -127 <= wheel <= 127 or not -127 <= pan <= 127 or not 0 <= buttons <= 0xffffffff:
        raise ValueError('Pointer field out of range')
    if absolute:
        if not 0 <= x <= 32767 or not 0 <= y <= 32767:
            raise ValueError('Absolute coordinate out of range')
        if wheel or pan:
            raise ValueError('This service exposes no wheel report; use a drag to scroll')
        return struct.pack('<BIHH',2,buttons,x,y)
    raise ValueError('This service exposes an absolute pointer only; use a drag to scroll')


def keyboard(keys=(), modifiers=0):
    if not 0 <= modifiers <= 255 or len(keys)>6 or any(not 0 <= k <= 255 for k in keys):
        raise ValueError('Invalid keyboard report')
    return bytes([1,modifiers,0,*keys])+bytes(6-len(keys))
