"""Original local USB capture + Bluetooth HID driver, using only Python's stdlib."""
import base64
import collections
import json
import math
import pathlib
import queue
import re
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'native'))
import protocol
import usb
import gestures
from bridge_client import BridgeClient


class NativeRPC:
    def __init__(self, executable):
        self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=sys.stderr, text=True, bufsize=1)
        self.lock = threading.Lock()
        self.pending = {}
        self.condition = threading.Condition()
        self.counter = 0
        self.events = collections.deque(maxlen=100)
        self.ready = threading.Event()
        self.dead = False
        threading.Thread(target=self._read, daemon=True).start()
        if not self.ready.wait(10):
            self.close(); raise RuntimeError('Native helper did not start')

    def _read(self):
        try:
            for line in self.process.stdout:
                try: value = json.loads(line)
                except ValueError: continue
                with self.condition:
                    if 'event' in value:
                        self.events.append(value)
                        if value['event'] in ('ready', 'fatal'): self.ready.set()
                    elif value.get('id') in self.pending:
                        self.pending[value['id']] = value
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.dead = True; self.condition.notify_all()

    def call(self, method, timeout=20, **params):
        # Serialize requests: each helper owns one capture session or HID service.
        with self.lock:
            with self.condition:
                if self.dead: raise RuntimeError('Native helper exited')
                self.counter += 1; identifier = self.counter
                self.pending[identifier] = None
            try:
                self.process.stdin.write(json.dumps(dict(params, id=identifier, method=method))+'\n')
                self.process.stdin.flush()
                deadline = time.monotonic()+timeout
                with self.condition:
                    while self.pending[identifier] is None:
                        if self.dead: raise RuntimeError('Native helper exited during request')
                        left = deadline-time.monotonic()
                        if left <= 0: raise TimeoutError(f'{method} timed out')
                        self.condition.wait(left)
                    value = self.pending[identifier]
                if not value.get('ok'): raise RuntimeError(value.get('error', f'{method} failed: {value}'))
                value.pop('id', None)
                return value
            finally:
                with self.condition: self.pending.pop(identifier, None)

    def close(self):
        if self.process.poll() is None:
            try: self.process.stdin.close(); self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.process.stdout.close()


def coordinate(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Coordinates must be normalized numbers in [0, 1]')
    return round(value*32767)


def pointer_step(x,y,buttons=0,delay=0):
    return {'data':base64.b64encode(protocol.pointer(coordinate(x),coordinate(y),buttons)).decode(), 'delay_ms':delay}


def bounded_number(value,lower,upper,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f'{name} must be a finite number between {lower} and {upper}')
    return value


def tap_reports(x,y,hold_ms=80,count=1,settle_ms=60,interval_ms=150):
    bounded_number(hold_ms,30,2000,'hold_ms')
    bounded_number(settle_ms,0,1500,'settle_ms')
    bounded_number(interval_ms,50,500,'interval_ms')
    if isinstance(count,bool) or not isinstance(count,int) or not 1 <= count <= 3:
        raise ValueError('count must be an integer between 1 and 3')
    reports=[pointer_step(x,y)]
    for i in range(count):
        reports.extend([pointer_step(x,y,1,settle_ms if i==0 else interval_ms),pointer_step(x,y,0,hold_ms)])
    return reports


def swipe_reports(x1,y1,x2,y2,duration_ms=450,hold_ms=0,release_delay_ms=120):
    bounded_number(duration_ms,100,2000,'duration_ms')
    bounded_number(hold_ms,0,2000,'hold_ms')
    bounded_number(release_delay_ms,0,500,'release_delay_ms')
    for n in (x1,y1,x2,y2): coordinate(n)
    steps = max(8,round(duration_ms/12))
    reports = [pointer_step(x1,y1),pointer_step(x1,y1,1,60)]
    for i in range(1,steps+1):
        t=i/steps
        reports.append(pointer_step(x1+(x2-x1)*t,y1+(y2-y1)*t,1,duration_ms/steps+(hold_ms if i==1 else 0)))
    # A brief pause at the end prevents a drag from becoming an accidental flick.
    reports.append(pointer_step(x2,y2,0,release_delay_ms))
    return reports


# USB HID Keyboard/Keypad usages for a US layout. Reject unsupported text before
# emitting any report; Unicode composition needs a separate input method.
CHAR_KEYS = {chr(ord('a')+i):(4+i,0) for i in range(26)}
CHAR_KEYS.update({chr(ord('A')+i):(4+i,2) for i in range(26)})
CHAR_KEYS.update({c:(30+i,0) for i,c in enumerate('1234567890')})
CHAR_KEYS.update({c:(30+i,2) for i,c in enumerate('!@#$%^&*()')})
for plain, shifted, key in [('-', '_',45),('=','+',46),('[','{',47),(']','}',48),('\\','|',49),(';',':',51),("'",'"',52),('`','~',53),(',','<',54),('.','>',55),('/','?',56)]:
    CHAR_KEYS[plain]=(key,0); CHAR_KEYS[shifted]=(key,2)
CHAR_KEYS.update({' ':(44,0),'\n':(40,0),'\t':(43,0)})
NAMED_KEYS={'enter':40,'escape':41,'backspace':42,'tab':43,'space':44,'right':79,'left':80,'down':81,'up':82}


def key_step(keys=(),modifiers=0,delay=0):
    return {'data':base64.b64encode(protocol.keyboard(keys,modifiers)).decode(),'delay_ms':delay}


def key_reports(key,modifiers=0,repeat_count=1):
    if not isinstance(key,str):raise ValueError('Key must be a string')
    if isinstance(modifiers,bool) or not isinstance(modifiers,int) or not 0<=modifiers<=255:raise ValueError('Invalid modifier mask')
    if isinstance(repeat_count,bool) or not isinstance(repeat_count,int) or not 1<=repeat_count<=20:raise ValueError('repeat_count must be an integer between 1 and 20')
    if key in NAMED_KEYS:usage,shift=NAMED_KEYS[key],0
    elif len(key)==1 and key in CHAR_KEYS:usage,shift=CHAR_KEYS[key]
    else:raise ValueError('Unsupported key; use a named key or one US-layout character')
    return [report for _ in range(repeat_count) for report in (key_step([usage],modifiers|shift),key_step(delay=60))]


class PhoneDriver:
    def __init__(self):
        self.hid = None; self.capture = None; self.address = None
        self.capture_id = None; self.usb_udid = None; self.operation = threading.RLock()
        self.capture_selection = threading.RLock()

    def _helper(self,name):
        path=ROOT/'build/OpenPhone.app/Contents/MacOS'/name
        if not path.exists(): raise RuntimeError('Run python3 native/build.py first')
        return NativeRPC(path)

    def devices(self):
        if not self.capture: self.capture=self._helper('open-phone-capture')
        result=self.capture.call('list')
        result['usb_inventory_available']=bool(usb.executable())
        if result['usb_inventory_available']:
            try:result['usb_phones']=usb.phones()
            except (RuntimeError,OSError,subprocess.TimeoutExpired) as error:result['usb_inventory_error']=str(error)
        return result

    def connect(self,address,capture_id='auto'):
        with self.capture_selection:
            return self._select_phone(address,capture_id)

    def _select_phone(self,address,capture_id):
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',address): raise ValueError('Invalid Bluetooth address')
        address=address.upper()
        # Capture is established first; no input is sent to an unseen phone.
        devices=self.devices()
        usb_udid=None
        if devices.get('usb_phones'):
            matches=[p for p in devices['usb_phones'] if p['bluetooth_address']==address]
            if len(matches)!=1:raise ValueError('Bluetooth address does not identify exactly one connected USB iPhone')
            usb_udid=matches[0]['udid']
        # Once selection begins, an unsuccessful connection must not leave input
        # enabled against the previously selected phone and a different capture.
        self.address=None;self.capture_id=None;self.usb_udid=None
        result=self.capture.call('start',capture_id=capture_id)
        # Session running is not proof of a connected device or fresh video.
        # Verify a frame before enabling HID, including when reusing a session.
        self.capture.call('screenshot',max_dimension=320)
        self.capture_id=result['capture_id'];self.usb_udid=usb_udid
        if not self.hid: self.hid=self._helper('open-phone-hid')
        self.hid.call('initialize')
        deadline=time.monotonic()+15
        while True:
            status=self.hid.call('status')
            if status['state']==5 and status['authorization']==3: break
            if status['authorization'] in (1,2): raise RuntimeError('Bluetooth permission denied or restricted')
            if time.monotonic()>deadline: raise RuntimeError('Bluetooth permission or power is not ready; allow the normal macOS prompt and retry')
            if status['state']==2 and status['authorization']==3: self.hid.call('initialize')
            time.sleep(0.1)
        if not status['service_handle']:
            self.hid.call('advertise',sdp=base64.b64encode(protocol.make_apple_sdp()).decode())
        self.address=address
        try:self._connect_channels()
        except Exception:
            self.address=None
            raise
        return dict(result,address=address,udid=self.usb_udid,backend='usb+classic-hid')

    def _connect_channels(self):
        status=self.hid.call('status')
        host=status['hosts'].get(self.address,{})
        connected=self.hid.call('peer_status',address=self.address)['peer_state']==2
        if connected and 'control_fd' in host and 'interrupt_fd' in host: return
        if not connected:self.hid.call('connect',address=self.address)
        deadline=time.monotonic()+10
        while self.hid.call('peer_status',address=self.address)['peer_state']!=2:
            if time.monotonic()>deadline: raise RuntimeError('Classic Bluetooth connection failed; pairing may be required')
            time.sleep(0.05)
        while True:
            host=self.hid.call('status')['hosts'].get(self.address,{})
            if 'control_fd' in host and 'interrupt_fd' in host: return
            if time.monotonic()>deadline: raise RuntimeError('iPhone did not open both HID channels')
            # The native helper suppresses requests for live/pending channels.
            # A transient failed open can be retried before any input is emitted.
            self.hid.call('open_channels',address=self.address)
            time.sleep(0.2)

    def screenshot(self,**params):
        if not self.capture or not self.capture_id: raise RuntimeError('Connect a phone first')
        return self.capture.call('screenshot',**params)

    def video_frame(self, expected_address, max_dimension=1344, after_generation=-1):
        if not isinstance(expected_address,str) or not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}',expected_address):
            raise ValueError('Video frames require the selected phone address')
        if isinstance(max_dimension,bool) or not isinstance(max_dimension,int) or not 320 <= max_dimension <= 1920:
            raise ValueError('Video max_dimension must be 320–1920')
        if isinstance(after_generation,bool) or not isinstance(after_generation,int) or not -1 <= after_generation < 2**53:
            raise ValueError('Invalid video frame generation')
        # Independent of the input operation lock: selection is stable across
        # this read, but HID sequences can proceed while the encoder samples.
        with self.capture_selection:
            if self.address != expected_address or not self.capture or not self.capture_id:
                raise RuntimeError('The selected phone differs from the video target or is unavailable')
            result=self.capture.call('screenshot',timeout=5,format='jpeg',max_dimension=max_dimension,after_generation=after_generation)
            if result.get('capture_id') != self.capture_id:
                raise RuntimeError('Video capture selection changed; no frame was delivered')
            return result

    def apps(self,include_system=True):
        if not self.usb_udid:raise RuntimeError('Connect with go-ios available to identify the USB phone before listing its apps')
        return usb.apps(self.usb_udid,include_system)

    def sequence(self,reports,dimensions=None):
        if not self.address: raise RuntimeError('Connect a phone first')
        # Verify a fresh capture before each input action and reconnect transport
        # before emission. Never replay an action after a partial write failure.
        frame=self.screenshot(max_dimension=320)
        if dimensions is not None and (frame.get('screen_width'),frame.get('screen_height'))!=dimensions:
            raise RuntimeError('The phone dimensions changed since targeting; take a fresh screenshot before input')
        self._connect_channels()
        return self.hid.call('sequence',address=self.address,reports=reports)

    def native_gesture(self,action,params,width,height):
        planned=gestures.plan(action,params,width,height)
        result=self.sequence(planned['reports'],dimensions=(width,height))
        # Keep the action lock until the post-release settling delay has passed.
        # A partially failed sequence raises before this point and is not replayed.
        time.sleep(planned['finish_ms']/1000)
        return dict(result,profile='native-pixel',duration_ms=planned['duration_ms'],motion_ms=planned['motion_ms'])

    def move(self,x,y): return self.sequence([pointer_step(x,y)])
    def tap(self,x,y,hold_ms=80,count=1,settle_ms=60,interval_ms=150):
        return self.sequence(tap_reports(x,y,hold_ms,count,settle_ms,interval_ms))
    def swipe(self,x1,y1,x2,y2,duration_ms=450): return self.sequence(swipe_reports(x1,y1,x2,y2,duration_ms))
    def hold_and_drag(self,x1,y1,x2,y2,hold_ms=1000,duration_ms=450):
        return self.sequence(swipe_reports(x1,y1,x2,y2,duration_ms,hold_ms))
    def flick(self,x1,y1,x2,y2,duration_ms=180):
        return self.sequence(swipe_reports(x1,y1,x2,y2,duration_ms,release_delay_ms=16))
    def scroll(self,direction='down',distance=0.45,duration_ms=450):
        bounded_number(distance,0.05,0.7,'distance')
        starts={'down':(.5,.5+distance/2,.5,.5-distance/2),'up':(.5,.5-distance/2,.5,.5+distance/2),'right':(.5+distance/2,.5,.5-distance/2,.5),'left':(.5-distance/2,.5,.5+distance/2,.5)}
        if direction not in starts: raise ValueError('Unknown scroll direction')
        return self.swipe(*starts[direction],duration_ms)

    def type_text(self,text):
        if not isinstance(text,str) or len(text)>200: raise ValueError('Text must be at most 200 US-layout characters')
        invalid=[c for c in text if c not in CHAR_KEYS]
        if invalid: raise ValueError('Unicode text is not supported by the US HID keyboard layout')
        reports=[]
        for c in text:
            key,modifier=CHAR_KEYS[c]; reports.extend([key_step([key],modifier,20),key_step(delay=25)])
        return self.sequence(reports)

    def press_key(self,key,modifiers=0,repeat_count=1):
        return self.sequence(key_reports(key,modifiers,repeat_count))

    def press_button(self,button):
        # Device Button 3 -> Home; optional Button 9 -> Use OpenPhone Shortcut.
        masks={'home':4,'shortcut':256}
        if button in masks:return self.sequence([pointer_step(.5,.5,masks[button]),pointer_step(.5,.5,0,80)])
        raise ValueError('Unsupported device button; volume/power are not verified')

    def shortcut_action(self,kind,payload,trigger=False,ttl=60):
        if not isinstance(trigger,bool):raise ValueError('trigger must be a boolean')
        if trigger and not self.address:raise RuntimeError('Connect the phone before triggering its Shortcut')
        result=BridgeClient().enqueue(kind,payload,ttl)
        if trigger:
            try:self.press_button('shortcut')
            except Exception as error:
                # Do not replay a partial HID action. Cancel the queue entry so
                # a later manual Shortcut invocation cannot pick it up.
                try:
                    cancelled=BridgeClient().cancel(result['id'])
                    state=cancelled['state']
                except Exception:state='unknown'
                raise RuntimeError(f"Shortcut trigger failed for action {result['id']} (state: {state}): {error}") from error
        return result

    def shortcut_result(self,action_id):return BridgeClient().result(action_id)
    def shortcut_cancel(self,action_id):return BridgeClient().cancel(action_id)

    def status(self,diagnostics=False):
        result={'ok':True,'address':self.address,'capture_id':self.capture_id,'udid':self.usb_udid,'hid':self.hid.call('status') if self.hid else None,
                'capture':self.capture.call('status') if self.capture else None}
        if diagnostics and self.hid:result['events']=list(self.hid.events)
        if diagnostics and self.capture:result['capture_events']=list(self.capture.events)
        return result

    def call(self,method,params,expected_address=None):
        if method=='video_frame':
            if not isinstance(params,dict) or params.keys()-{'max_dimension','after_generation'}: raise ValueError('Invalid video frame parameters')
            return self.video_frame(expected_address,**params)
        methods={'devices':self.devices,'connect':self.connect,'apps':self.apps,'screenshot':self.screenshot,'move':self.move,'tap':self.tap,'swipe':self.swipe,'hold_and_drag':self.hold_and_drag,'flick':self.flick,'scroll':self.scroll,'type_text':self.type_text,'press_key':self.press_key,'press_button':self.press_button,'status':self.status,'shortcut_action':self.shortcut_action,'shortcut_result':self.shortcut_result,'shortcut_cancel':self.shortcut_cancel,'native_gesture':self.native_gesture}
        if method not in methods: raise ValueError('Unknown driver method')
        with self.operation:
            if expected_address is not None and self.address!=expected_address:
                raise RuntimeError('The selected phone differs from the requested phone; no action was sent')
            return methods[method](**params)

    def close(self):
        if self.hid: self.hid.close()
        if self.capture: self.capture.close()


def stdio():
    driver=PhoneDriver()
    try:
        for line in sys.stdin:
            request={}
            try:
                request=json.loads(line)
                result=driver.call(request['method'],request.get('params',{}))
                # Keep operation payload IDs separate from the stdio request ID.
                response={'ok':True,'result':result,'id':request.get('id')}
            except Exception as error: response={'ok':False,'error':str(error),'id':request.get('id')}
            print(json.dumps(response),flush=True)
    finally: driver.close()


if __name__=='__main__': stdio()
