"""Original bounded WebRTC signaling, separate from one-shot phone input jobs.

The authenticated relay carries offers/answers only; media uses DTLS-SRTP over
ICE (direct or operator-configured TURN). Streams are ephemeral and leased.
"""
import collections
import copy
import secrets
import time
import uuid


def description(value, kind):
    if not isinstance(value, dict) or set(value) != {'type', 'sdp'} or value['type'] != kind:
        raise ValueError('Supply the expected WebRTC description type and SDP')
    sdp = value['sdp']
    if not isinstance(sdp, str) or len(sdp.encode()) > 32768 or '\x00' in sdp or not sdp.startswith('v=0'):
        raise ValueError('Invalid or oversized video SDP')
    lines = sdp.replace('\r\n', '\n').split('\n')
    media = [line for line in lines if line.startswith('m=')]
    if len(media) != 1 or not media[0].startswith('m=video ') or 'a=rtcp-mux' not in lines:
        raise ValueError('Only one multiplexed video track is supported')
    if ('a=recvonly' if kind == 'offer' else 'a=sendonly') not in lines:
        raise ValueError('The viewer must receive video and the host must send it')
    return dict(value)


class VideoBroker:
    ACTIVE = {'offered', 'claimed', 'answered', 'connected'}

    def __init__(self, store):
        self.store = store
        self.streams = collections.OrderedDict()

    def _finish(self, stream, reason):
        stream.update(status='closed', error=reason, answer=None, offer=None,
                      receipt=None, expires=self.store.clock()+60)

    def prune(self):
        now = self.store.clock()
        for key, stream in list(self.streams.items()):
            phone = self.store.phones.get(stream['phone_id'])
            if stream['status'] in self.ACTIVE:
                if not phone or phone['session'] != stream['host_session']:
                    self._finish(stream, 'HOST_REPLACED')
                elif not phone['ready'] or now-phone['seen'] >= 20:
                    self._finish(stream, 'PHONE_OFFLINE')
                elif now >= stream['expires']:
                    self._finish(stream, 'VIEWER_LEASE_EXPIRED')
            elif now >= stream['expires']:
                self.streams.pop(key)

    def close_phone(self, identifier, reason):
        for stream in self.streams.values():
            if stream['phone_id'] == identifier and stream['status'] in self.ACTIVE:
                self._finish(stream, reason)

    def _phone(self, identifier, session=None):
        self.store._ensure_persistence(); self.prune()
        phone = self.store._phone(identifier)
        if session is not None and phone['session'] != session:
            raise ValueError('Video host session is no longer current')
        if not phone['ready'] or self.store.clock()-phone['seen'] >= 20:
            raise RuntimeError('Phone is offline; no video was started')
        return phone

    def _stream(self, identifier):
        self.store._ensure_persistence(); self.prune()
        if identifier not in self.streams:
            raise KeyError('VIDEO_NOT_FOUND')
        return self.streams[identifier]

    @staticmethod
    def _view(stream):
        return {key: copy.deepcopy(stream[key]) for key in
                ('id', 'phone_id', 'status', 'answer', 'error', 'fps', 'max_dimension')}

    def offer(self, identifier, value):
        if not isinstance(value, dict) or value.keys()-{'description', 'fps', 'max_dimension'} or 'description' not in value:
            raise ValueError('Supply a video description and optional fps/max_dimension')
        offer = description(value['description'], 'offer')
        fps = value.get('fps', 15); maximum = value.get('max_dimension', 1344)
        if isinstance(fps, bool) or not isinstance(fps, int) or not 5 <= fps <= 30:
            raise ValueError('Video fps must be an integer from 5 through 30')
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 320 <= maximum <= 1920:
            raise ValueError('Video max_dimension must be an integer from 320 through 1920')
        with self.store.condition:
            phone = self._phone(identifier)
            active = [s for s in self.streams.values() if s['status'] in self.ACTIVE]
            if len(active) >= 16 or sum(s['phone_id'] == identifier for s in active) >= 2:
                raise RuntimeError('Video stream capacity reached')
            while len(self.streams) >= 64:
                old = next((key for key, s in self.streams.items() if s['status'] == 'closed'), None)
                if old is None: raise RuntimeError('Video history capacity reached')
                self.streams.pop(old)
            key = str(uuid.uuid4())
            stream = dict(id=key, phone_id=identifier, host_session=phone['session'],
                          offer=offer, answer=None, receipt=None, status='offered', error=None,
                          fps=fps, max_dimension=maximum, expires=self.store.clock()+30)
            self.streams[key] = stream; self.store.condition.notify_all()
            return self._view(stream)

    def view(self, identifier):
        with self.store.condition:
            stream = self._stream(identifier)
            # Polling by an authenticated viewer renews its lease, not a host.
            if stream['status'] in self.ACTIVE:
                stream['expires'] = self.store.clock()+30
            return self._view(stream)

    def stop(self, identifier):
        with self.store.condition:
            stream = self._stream(identifier)
            if stream['status'] in self.ACTIVE: self._finish(stream, 'VIEWER_STOPPED')
            self.store.condition.notify_all(); return self._view(stream)

    def poll(self, identifier, session, wait=5):
        with self.store.condition:
            self._phone(identifier, session)
            deadline = time.monotonic()+wait
            while True:
                self._phone(identifier, session)
                offer = next((s for s in self.streams.values() if s['phone_id'] == identifier
                              and s['host_session'] == session and s['status'] == 'offered'), None)
                if offer:
                    offer.update(status='claimed', receipt=secrets.token_urlsafe(24))
                active = [s['id'] for s in self.streams.values() if s['phone_id'] == identifier
                          and s['host_session'] == session and s['status'] in self.ACTIVE]
                if offer or time.monotonic() >= deadline:
                    return {'offer': {key: copy.deepcopy(offer[key]) for key in
                                     ('id', 'phone_id', 'offer', 'receipt', 'fps', 'max_dimension')} if offer else None,
                            'active': active}
                self.store.condition.wait(min(1, deadline-time.monotonic()))

    def answer(self, identifier, session, receipt, answer=None, error=None):
        if (answer is None) == (error is None): raise ValueError('Supply either a video answer or an error')
        if answer is not None: answer = description(answer, 'answer')
        if error is not None and (not isinstance(error, str) or not 1 <= len(error) <= 120):
            raise ValueError('Invalid video error')
        with self.store.condition:
            stream = self._stream(identifier); self._phone(stream['phone_id'], session)
            if stream['status'] != 'claimed' or stream['host_session'] != session or not isinstance(receipt, str) or not secrets.compare_digest(stream['receipt'], receipt):
                raise RuntimeError('Video offer is no longer awaiting this answer')
            if error: self._finish(stream, error)
            else: stream.update(status='answered', answer=answer, offer=None, receipt=None)
            self.store.condition.notify_all(); return self._view(stream)

    def report(self, identifier, session, status):
        if status not in ('connected', 'failed'): raise ValueError('Invalid video connection state')
        with self.store.condition:
            stream = self._stream(identifier); self._phone(stream['phone_id'], session)
            if stream['host_session'] != session: raise ValueError('Video belongs to another host session')
            if stream['status'] not in ('answered', 'connected'):
                raise RuntimeError('Video session is no longer active')
            if status == 'failed': self._finish(stream, 'VIDEO_CONNECTION_FAILED')
            else: stream['status'] = 'connected'
            self.store.condition.notify_all(); return self._view(stream)
