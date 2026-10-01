import json
import pathlib
import sys
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from openphone.bridge.queue import ActionQueue
from openphone.bridge.server import BridgeServer,make_handler


class QueueTests(unittest.TestCase):
    def test_claim_does_not_replay_delivered_action(self):
        q=ActionQueue();a=q.enqueue('copy_text',{'text':'Hello 🌍'})
        self.assertEqual(q.claim()['action']['id'],a['id'])
        self.assertFalse(q.claim()['pending'])
        self.assertEqual(q.status(a['id'])['state'],'delivered')

    def test_receipt_is_required_and_completion_cannot_replay(self):
        q=ActionQueue();a=q.enqueue('read_clipboard',{});claimed=q.claim()['action']
        with self.assertRaises(ValueError):q.complete(a['id'],'wrong',{'text':'test'})
        self.assertEqual(q.status(a['id'])['state'],'delivered')
        q.complete(a['id'],claimed['receipt'],{'text':'test'})
        self.assertEqual(q.status(a['id'])['result'],{'text':'test'})
        with self.assertRaises(RuntimeError):q.complete(a['id'],claimed['receipt'],{'text':'changed'})

    def test_expired_and_cancelled_actions_are_never_claimed(self):
        now=[0];q=ActionQueue(clock=lambda:now[0]);a=q.enqueue('open_app',{'bundle_id':'com.apple.Preferences'},ttl=5)
        now[0]=6;self.assertFalse(q.claim()['pending']);self.assertEqual(q.status(a['id'])['state'],'expired')
        b=q.enqueue('copy_text',{'text':'local'});q.cancel(b['id']);self.assertFalse(q.claim()['pending'])

    def test_pending_action_prevents_overwrite_and_invalid_url_is_rejected(self):
        q=ActionQueue()
        with self.assertRaises(ValueError):q.enqueue('open_url',{'url':'file:///etc/passwd'})
        with self.assertRaises(ValueError):q.enqueue('open_url',{'url':'https://user:secret@example.com'})
        a=q.enqueue('copy_text',{'text':'first'})
        with self.assertRaises(RuntimeError):q.enqueue('copy_text',{'text':'second'})
        self.assertEqual(q.claim()['action']['id'],a['id'])


class BridgeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.q=ActionQueue();self.server=BridgeServer(('127.0.0.1',0),make_handler(self.q,'controller-test','phone-test',set()))
        self.host=f'127.0.0.1:{self.server.server_port}';self.url='http://'+self.host
        self.server.RequestHandlerClass=make_handler(self.q,'controller-test','phone-test',{self.host})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.q.close()
    def request(self,path,value=None,phone=False,extra=None):
        headers={'X-OpenPhone-Token':'phone-test'} if phone else {'Authorization':'Bearer controller-test'}
        headers.update(extra or {})
        data=None
        if value is not None:headers['Content-Type']='application/json';data=json.dumps(value).encode()
        try:
            with urlopen(Request(self.url+path,data=data,headers=headers),timeout=2) as r:return r.status,json.load(r)
        except HTTPError as error:return error.code,json.load(error)

    def test_phone_credential_cannot_enqueue_actions(self):
        status,_=self.request('/v1/bridge/actions',{'kind':'read_clipboard','payload':{}},phone=True)
        self.assertEqual(status,401)

    def test_controller_credential_cannot_claim_phone_actions(self):
        self.assertEqual(self.request('/v1/bridge/claim',{},phone=False)[0],401)

    def test_browser_origin_and_unknown_host_are_rejected(self):
        self.assertEqual(self.request('/v1/bridge/claim',{},phone=True,extra={'Origin':'https://example.com'})[0],403)
        self.assertEqual(self.request('/v1/bridge/claim',{},phone=True,extra={'Host':'example.com'})[0],403)

    def test_controller_phone_roundtrip_with_unicode_result(self):
        status,a=self.request('/v1/bridge/actions',{'kind':'read_clipboard','payload':{}});self.assertEqual(status,200)
        _,claimed=self.request('/v1/bridge/claim',{},phone=True);c=claimed['action']
        status,_=self.request('/v1/bridge/complete',{'id':c['id'],'receipt':c['receipt'],'result':{'text':'OpenPhone ✓ 🌍'}},phone=True)
        self.assertEqual(status,200)
        _,result=self.request('/v1/bridge/actions/'+a['id']);self.assertEqual(result['result']['text'],'OpenPhone ✓ 🌍')
        self.assertEqual(result['state'],'completed')


if __name__=='__main__':unittest.main()
