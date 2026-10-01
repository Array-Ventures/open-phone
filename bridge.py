"""Original one-phone action queue for an optional iOS Shortcut bridge."""
import collections
import copy
import secrets
import threading
import time
from urllib.parse import urlsplit


class ActionQueue:
    def __init__(self, clock=time.monotonic):
        self.clock=clock
        self.actions=collections.OrderedDict()
        self.lock=threading.RLock()

    def _prune(self):
        now=self.clock()
        for key, action in list(self.actions.items()):
            if now-action['created']>300:
                del self.actions[key]
            elif action['state'] in ('queued','delivered') and now>action['expires']:
                action.update(state='expired',payload={},receipt=None)

    def enqueue(self,kind,payload,ttl=60):
        if not isinstance(payload,dict):raise ValueError('Payload must be an object')
        fields={'copy_text':'text','read_clipboard':None,'open_app':'bundle_id','open_url':'url'}
        if kind not in fields:raise ValueError('Unsupported Shortcut action')
        field=fields[kind]
        if set(payload)!=(set() if field is None else {field}):raise ValueError('Unexpected payload fields')
        if field:
            value=payload[field]
            if not isinstance(value,str) or len(value)>20000:raise ValueError('Payload value must be at most 20000 characters')
            if kind=='open_app' and (not value or len(value)>255 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_' for c in value)):
                raise ValueError('Invalid bundle ID')
            if kind=='open_url':
                parsed=urlsplit(value)
                if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('open_url requires an HTTP(S) URL without credentials')
        if isinstance(ttl,bool) or not isinstance(ttl,(int,float)) or not 5<=ttl<=120:raise ValueError('TTL must be between 5 and 120 seconds')
        with self.lock:
            self._prune()
            if any(a['state'] in ('queued','delivered') for a in self.actions.values()):
                raise RuntimeError('Another Shortcut action is pending; complete or cancel it first')
            if len(self.actions)>=32:raise RuntimeError('Action history is full; wait for older entries to expire')
            identifier=secrets.token_hex(16)
            self.actions[identifier]={'id':identifier,'kind':kind,'payload':copy.deepcopy(payload),
                                     'created':self.clock(),'expires':self.clock()+ttl,'state':'queued','receipt':None}
            return self.status(identifier)

    def claim(self):
        with self.lock:
            self._prune()
            for action in self.actions.values():
                if action['state']=='queued':
                    action.update(state='delivered',receipt=secrets.token_urlsafe(24))
                    return {'pending':True,'action':{k:copy.deepcopy(action[k]) for k in ('id','kind','payload','receipt')}}
            return {'pending':False,'action':None}

    def complete(self,identifier,receipt,result):
        if not isinstance(result,dict) or set(result)-{'text','ok'}:raise ValueError('Invalid completion result')
        if 'text' in result and (not isinstance(result['text'],str) or len(result['text'])>20000):raise ValueError('Result text exceeds 20000 characters')
        if 'ok' in result and not isinstance(result['ok'],bool):raise ValueError('Result ok must be a boolean')
        with self.lock:
            self._prune()
            action=self.actions.get(identifier)
            if not action:raise KeyError('Unknown action')
            if action['state']!='delivered':raise RuntimeError('Action is not awaiting completion')
            if not isinstance(receipt,str) or not secrets.compare_digest(action['receipt'],receipt):raise ValueError('Invalid action receipt')
            action.update(state='completed',result=copy.deepcopy(result),payload={},receipt=None)
            return self.status(identifier)

    def status(self,identifier):
        with self.lock:
            self._prune()
            action=self.actions.get(identifier)
            if not action:raise KeyError('Unknown action')
            return {k:copy.deepcopy(action[k]) for k in ('id','kind','state','result') if k in action}

    def cancel(self,identifier):
        with self.lock:
            self._prune()
            action=self.actions.get(identifier)
            if not action:raise KeyError('Unknown action')
            if action['state'] not in ('queued','delivered'):raise RuntimeError('Action cannot be cancelled in its current state')
            action.update(state='cancelled',payload={},receipt=None)
            return self.status(identifier)

    def close(self):
        with self.lock:self.actions.clear()
