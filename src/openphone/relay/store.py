"""Original self-hosted command relay; the Mac retains all device ownership.

Phone/job routes use authenticated HTTP. Running jobs are never redelivered
after a timeout.
"""
import base64
import collections
import copy
import datetime
import math
import re
import secrets
import threading
import time
import uuid
import openphone.device.gestures as gestures
from openphone.relay.state import RelayState
from openphone.relay.video import VideoBroker


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def number(value, low, high, name, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or (integer and not isinstance(value, int)):
        raise ValueError(f'{name} must be a {"whole" if integer else "finite"} number in [{low}, {high}]')
    return value


def command(action, body, width, height):
    """Validate public actions and preserve native pixels for gesture execution."""
    if not isinstance(body, dict):
        raise ValueError('Body must be an object')
    fields = {
        'tap': ({'x', 'y'}, set()), 'double-tap': ({'x', 'y'}, set()),
        'triple-tap': ({'x', 'y'}, set()), 'tap-and-hold': ({'x', 'y'}, {'duration_ms'}),
        'flick': ({'x', 'y', 'direction'}, set()),
        'drag': ({'from_x', 'from_y', 'to_x', 'to_y'}, {'speed'}),
        'hold-and-drag': ({'from_x', 'from_y', 'to_x', 'to_y'}, {'speed', 'hold_duration_ms'}),
        'type': ({'text'}, set()), 'keypress': ({'key'}, {'modifiers', 'repeat'}),
        'home': (set(), set()), 'screenshot': (set(), set()),
        'apps': (set(), set()),
    }
    if action not in fields:
        raise ValueError('Unknown action')
    required, optional = fields[action]
    if not required <= body.keys() or body.keys() - required - optional:
        raise ValueError('Missing or unexpected action fields')
    if action in gestures.FIELDS:
        gestures.plan(action, body, width, height)
        return 'native_gesture', dict(action=action, params=dict(body), width=width, height=height)
    if action == 'type':
        text = body['text']
        if not isinstance(text, str) or len(text) > 100 or not text.isascii():
            raise ValueError('Public type route accepts at most 100 ASCII characters')
        return 'type_text', {'text': text}
    if action == 'keypress':
        key = body['key']; modifiers = body.get('modifiers', [])
        allowed = ['enter', 'escape', 'backspace', 'arrow_up', 'arrow_down', 'arrow_left', 'arrow_right']
        if key not in allowed or not isinstance(modifiers, list) or any(m not in ('control', 'shift', 'alternate', 'command') for m in modifiers):
            raise ValueError('Invalid key or modifiers')
        mask = 0
        for modifier in modifiers:
            mask |= {'control': 1, 'shift': 2, 'alternate': 4, 'command': 8}[modifier]
        repeat = number(body.get('repeat', 1), 1, 20, 'repeat', True)
        return 'press_key', dict(key=key.removeprefix('arrow_'), modifiers=mask, repeat_count=repeat)
    if action == 'home':
        return 'press_button', {'button': 'home'}
    if action == 'screenshot':
        return 'screenshot', {'format': 'png', 'max_dimension': 4096}
    return 'apps', {}


class RelayStore:
    def __init__(self, clock=time.monotonic, deadline=60, history=300, state_file=None, wall_clock=time.time):
        self.clock = clock; self.deadline = deadline; self.history = history
        self.wall_clock = wall_clock; self.state = None; self.persistence_error = False; self.closed = False
        self.condition = threading.Condition(threading.RLock())
        self.phones = {}; self.jobs = collections.OrderedDict(); self.images = {}; self.app_cache = {}
        self.video = VideoBroker(self)
        if state_file is not None:
            self.state = RelayState(state_file)
            try: self._restore(self.state.load()); self._save()
            except Exception: self.state.close(); raise

    def _ensure_persistence(self):
        if self.closed: raise RuntimeError('Relay store is closed')
        if self.persistence_error: raise RuntimeError('Relay persistence failed; restart after repairing storage. No action can be claimed.')

    def _restore(self, data):
        now = self.clock(); wall = self.wall_clock()
        if len(data['phones']) > 32 or len(data['jobs']) > 128 or sum(len(v[0]) for v in data['images'].values()) > 32000000:
            raise ValueError('Relay state exceeds configured capacity')
        for identifier, value in data['phones'].items():
            phone = dict(value); apps = phone.pop('apps', None)
            phone.update(session=None, seen=now-21, ready=False)
            self.phones[identifier] = phone
            if apps is not None: self.app_cache[identifier] = apps
        for identifier, value in sorted(data['jobs'].items(), key=lambda item: item[1]['created_wall']):
            age = max(0, wall-value['created_wall'])
            if age > self.history: continue
            job = dict(value, created=now-age, expires=now-1, session=None, params={}, receipt=None)
            if job['status'] in ('pending', 'running'):
                job.update(status='failed', result={'error': 'RELAY_RESTARTED'}, completed_at=stamp())
            self.jobs[identifier] = job
            if job['status'] == 'completed' and identifier in data['images']:
                self.images[identifier] = data['images'][identifier]

    def _save(self):
        if self.state is None: return
        phones = {key: {name: copy.deepcopy(value[name]) for name in ('id', 'name', 'display_name', 'created_at', 'width', 'height', 'host_id')}
                  for key, value in self.phones.items()}
        for key, apps in self.app_cache.items(): phones[key]['apps'] = apps
        # Pending command payloads, completion receipts and host sessions are not
        # needed for recovery: unfinished requests will never be resumed.
        jobs = {key: {name: copy.deepcopy(value[name]) for name in ('id', 'phone_id', 'method', 'status', 'result', 'created_at', 'completed_at', 'created_wall')}
                for key, value in self.jobs.items()}
        try: self.state.save(phones, jobs, self.images)
        except Exception:
            self.persistence_error = True
            raise RuntimeError('Relay persistence failed; no action can be claimed until storage is repaired and the relay restarted') from None

    def close(self):
        with self.condition:
            if self.closed: return
            try:
                if not self.persistence_error:
                    for job in self.jobs.values():
                        if job['status'] in ('pending', 'running'):
                            job.update(status='failed', result={'error': 'RELAY_STOPPED'}, completed_at=stamp(), receipt=None, params={})
                    self._save()
            finally:
                for phone_id in self.phones: self.video.close_phone(phone_id, 'RELAY_STOPPED')
                self.closed = True
                if self.state: self.state.close()
                self.condition.notify_all()

    def _prune(self):
        self._ensure_persistence()
        now = self.clock()
        self.video.prune()
        dirty = False
        for identifier, job in list(self.jobs.items()):
            if now - job['created'] > self.history:
                self.jobs.pop(identifier); self.images.pop(identifier, None)
                dirty = True
            elif job['status'] in ('pending', 'running') and now >= job['expires']:
                job.update(status='failed', result={'error': 'TIMEOUT'}, completed_at=stamp(), receipt=None, params={})
                dirty = True
        if dirty: self._save()

    def maintain(self):
        with self.condition:
            self._ensure_persistence()
            self._prune()
            self.condition.notify_all()

    def _phone(self, identifier):
        if identifier not in self.phones:
            raise KeyError('PHONE_NOT_FOUND')
        return self.phones[identifier]

    def register(self, identifier, session, name, width, height, host_id=None):
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identifier):
            raise ValueError('Invalid phone ID')
        if not isinstance(session, str) or not re.fullmatch(r'[0-9a-f]{32}', session):
            raise ValueError('Invalid host session')
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise ValueError('Invalid phone name')
        number(width, 2, 8192, 'width', True); number(height, 2, 8192, 'height', True)
        host_id = host_id or identifier
        if not isinstance(host_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', host_id):
            raise ValueError('Invalid host ID')
        with self.condition:
            self._ensure_persistence()
            old = self.phones.get(identifier)
            if old and old['session'] != session and self.clock() - old['seen'] < 20:
                raise RuntimeError('Phone already has a live host; stop that host first')
            if not old and len(self.phones) >= 32:
                raise RuntimeError('Phone capacity reached')
            if old and old['session'] != session:
                self.video.close_phone(identifier, 'HOST_REPLACED')
                for job in self.jobs.values():
                    if job['phone_id'] == identifier and job['status'] in ('pending', 'running'):
                        job.update(status='failed', result={'error': 'HOST_REPLACED'}, completed_at=stamp(), receipt=None, params={})
            self.phones[identifier] = dict(id=identifier, session=session, name=name, width=width, height=height,
                display_name=old.get('display_name') if old else None, created_at=old['created_at'] if old else stamp(), seen=self.clock(), ready=True, host_id=host_id)
            self._save()
            self.condition.notify_all()

    def phones_view(self):
        with self.condition:
            self._ensure_persistence()
            return [dict(id=p['id'], name=p['name'], display_name=p['display_name'],
                         connection_status='online' if p['ready'] and self.clock()-p['seen'] < 20 else 'offline',
                         activation_state='active', connected_mac_id=p['host_id'] if p['ready'] and self.clock()-p['seen']<20 else None,
                         width=p['width'], height=p['height'], created_at=p['created_at'])
                    for p in self.phones.values()]

    def phone_view(self, identifier):
        with self.condition:
            self._ensure_persistence()
            self._phone(identifier)
            return next(p for p in self.phones_view() if p['id'] == identifier)

    def rename(self, identifier, name):
        if name is not None and (not isinstance(name, str) or not 1 <= len(name) <= 120):
            raise ValueError('display_name must be null or 1–120 characters')
        with self.condition:
            self._ensure_persistence()
            self._phone(identifier)['display_name'] = name
            self._save()
            return self.phone_view(identifier)

    def enqueue(self, identifier, method, params):
        with self.condition:
            self._ensure_persistence()
            self._prune(); phone = self._phone(identifier)
            if not phone['ready'] or self.clock() - phone['seen'] >= 20:
                raise RuntimeError('MAC_APP_NOT_RUNNING')
            while len(self.jobs) >= 128:
                finished = next((key for key, job in self.jobs.items() if job['status'] in ('completed', 'failed')), None)
                if finished is None:
                    break
                self.jobs.pop(finished); self.images.pop(finished, None)
            if len(self.jobs) >= 128 or sum(j['status'] in ('pending', 'running') for j in self.jobs.values()) >= 32:
                raise RuntimeError('Job capacity reached')
            job_id = str(uuid.uuid4())
            self.jobs[job_id] = dict(id=job_id, phone_id=identifier, session=phone['session'], method=method, params=copy.deepcopy(params),
                status='pending', result=None, created=self.clock(), expires=self.clock()+self.deadline, created_wall=self.wall_clock(), created_at=stamp(), completed_at=None, receipt=None)
            self._save()
            self.condition.notify_all()
            return job_id

    def claim(self, identifier, session, wait=5):
        with self.condition:
            self._ensure_persistence()
            phone = self._phone(identifier)
            if phone['session'] != session:
                raise ValueError('Host session is no longer current')
            phone['seen'] = self.clock()
            end = time.monotonic() + wait
            while True:
                if self._phone(identifier)['session'] != session:
                    raise ValueError('Host session was replaced while waiting')
                self._prune()
                if self._phone(identifier)['ready'] and not any(j['phone_id'] == identifier and j['status'] == 'running' for j in self.jobs.values()):
                    for job in self.jobs.values():
                        if job['phone_id'] == identifier and job['status'] == 'pending':
                            job.update(status='running', receipt=secrets.token_urlsafe(24))
                            self._save()
                            return {k: copy.deepcopy(job[k]) for k in ('id', 'method', 'params', 'receipt')}
                left = end-time.monotonic()
                if left <= 0:
                    return None
                self.condition.wait(left)

    def heartbeat(self, identifier, session, online=True):
        if not isinstance(online, bool):
            raise ValueError('online must be a boolean')
        with self.condition:
            self._ensure_persistence()
            phone = self._phone(identifier)
            if phone['session'] != session:
                raise ValueError('Host session is no longer current')
            phone['seen'] = self.clock(); phone['ready'] = online
            if not online:
                self.video.close_phone(identifier, 'PHONE_OFFLINE')
                for job in self.jobs.values():
                    if job['phone_id'] == identifier and job['status'] == 'pending':
                        job.update(status='failed', result={'error': 'PHONE_DISCONNECTED'}, completed_at=stamp(), receipt=None, params={})
            self._save()
            self.condition.notify_all()

    def complete(self, identifier, session, receipt, result, error=None):
        if not isinstance(result, dict) or (error is not None and (not isinstance(error, str) or len(error) > 1000)):
            raise ValueError('Invalid job result')
        with self.condition:
            self._ensure_persistence()
            self._prune(); job = self.jobs[identifier]
            if job['status'] != 'running' or job['session'] != session or not isinstance(receipt, str) or not secrets.compare_digest(job['receipt'], receipt):
                raise RuntimeError('Job is no longer awaiting this completion')
            result = copy.deepcopy(result)
            if job['method'] == 'apps' and error is None:
                apps = result.get('applications')
                if not isinstance(apps, list) or len(apps) > 5000:
                    raise ValueError('Invalid app inventory')
                normalized = []
                for app in apps:
                    if not isinstance(app, dict) or not isinstance(app.get('bundle_id'), str):
                        raise ValueError('Invalid app entry')
                    name = app.get('name', app.get('display_name', ''))
                    if not isinstance(name, str) or len(name) > 512 or len(app['bundle_id']) > 255:
                        raise ValueError('Invalid app metadata')
                    normalized.append({'name': name, 'bundle_id': app['bundle_id']})
                self.app_cache[job['phone_id']] = dict(apps=normalized, app_count=len(normalized), updated_at=stamp())
            if job['method'] == 'screenshot' and 'screen_width' in result and 'screen_height' in result:
                number(result['screen_width'], 2, 8192, 'screen_width', True)
                number(result['screen_height'], 2, 8192, 'screen_height', True)
                self._phone(job['phone_id']).update(width=result['screen_width'], height=result['screen_height'])
            if 'data' in result:
                data = result.pop('data')
                if job['method'] != 'screenshot' or not isinstance(data, str) or len(data) > 12000000:
                    raise ValueError('Invalid screenshot data')
                raw = base64.b64decode(data, validate=True)
                mime = result.get('mime_type')
                if mime not in ('image/png', 'image/jpeg') or not raw.startswith(b'\x89PNG\r\n\x1a\n' if mime == 'image/png' else b'\xff\xd8\xff'):
                    raise ValueError('Screenshot MIME/header mismatch')
                while self.images and sum(len(image[0]) for image in self.images.values()) + len(raw) > 32000000:
                    self.images.pop(next(iter(self.images)))
                self.images[identifier] = raw, mime
            job.update(status='failed' if error is not None else 'completed', result={'error': error} if error is not None else result,
                       completed_at=stamp(), receipt=None, params={})
            self._phone(job['phone_id'])['seen'] = self.clock()
            self._save()
            self.condition.notify_all()
            return self.job_view(identifier)

    def job_view(self, identifier):
        with self.condition:
            self._ensure_persistence()
            self._prune(); job = self.jobs[identifier]
            return {k: copy.deepcopy(job[k]) for k in ('id', 'status', 'result', 'created_at', 'completed_at')}

    def wait(self, identifier):
        with self.condition:
            self._ensure_persistence()
            while True:
                result = self.job_view(identifier)
                if result['status'] in ('completed', 'failed'):
                    return result
                self.condition.wait(.25)

    def download(self, identifier):
        with self.condition:
            self._ensure_persistence()
            self._prune()
            if self.jobs[identifier]['status'] != 'completed' or identifier not in self.images:
                raise KeyError('DATA_NOT_FOUND')
            return self.images[identifier]

    def apps_view(self, identifier):
        with self.condition:
            self._ensure_persistence()
            self._phone(identifier)
            return copy.deepcopy(self.app_cache.get(identifier))

    def unregister(self, identifier, session):
        with self.condition:
            self._ensure_persistence()
            phone = self._phone(identifier)
            if phone['session'] != session:
                raise ValueError('Host session is no longer current')
            phone['seen'] = self.clock()-21
            self.video.close_phone(identifier, 'HOST_DISCONNECTED')
            for job in self.jobs.values():
                if job['phone_id'] == identifier and job['status'] in ('pending', 'running'):
                    job.update(status='failed', result={'error': 'HOST_DISCONNECTED'}, completed_at=stamp(), receipt=None, params={})
            self._save()
            self.condition.notify_all()
