import base64
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard import Dashboard

ADDRESS = 'AA:BB:CC:DD:EE:FF'


class LocalFixture:
    def __init__(self): self.calls=[]; self.address=ADDRESS; self.fail=False
    def call(self, method, **params):
        self.calls.append((None, method, params))
        if method=='status': return {'address':self.address,'capture_id':'fixture-capture','capture':{'fresh':True}}
        if method=='devices': return {'devices':[], 'usb_phones':[]}
        if method=='connect': self.address=params['address'];return {'address':self.address,'capture_id':params['capture_id']}
        raise AssertionError(method)
    def call_for(self, address, method, **params):
        self.calls.append((address,method,params))
        if address!=self.address: raise RuntimeError('The selected phone differs; no action was sent')
        if self.fail: raise RuntimeError('Partial report write failed')
        if method=='screenshot':return {'data':base64.b64encode(b'fixture-image').decode(),'mime_type':'image/jpeg','width':621,'height':1344,'screen_width':1180,'screen_height':2556,'generation':123,'timestamp':42}
        if method=='apps':return {'applications':[{'name':'Fixture app','bundle_id':'org.example.fixture'}]}
        if method=='shortcut_result':return {'id':params['action_id'],'state':'completed','result':{'text':'Fixture ✓'}}
        return {'ok':True}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.local=LocalFixture();self.server=Dashboard(0,self.local,key='dashboard-fixture-key')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://'+self.server.authority
        self.cookie={'Cookie':'openphone_dashboard=dashboard-fixture-key'}
    def tearDown(self): self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,body=None,headers=None):
        headers=dict(headers or {})
        if body is not None:headers['Content-Type']='application/json';body=json.dumps(body).encode()
        try:
            with urlopen(Request(self.url+path,data=body,headers=headers),timeout=3) as response:
                return response.status,response.read(),dict(response.headers)
        except HTTPError as error:
            with error:return error.code,error.read(),dict(error.headers)
    def test_static_page_does_not_expose_key_and_device_routes_require_cookie(self):
        status,body,headers=self.request('/')
        self.assertEqual(status,200);self.assertNotIn(self.server.key.encode(),body)
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(self.request('/api/status')[0],401)
        self.assertEqual(self.local.calls,[])
    def test_session_key_is_required_and_cookie_is_http_only(self):
        self.assertEqual(self.request('/session',{'key':'wrong'})[0],401)
        status,_,headers=self.request('/session',{'key':self.server.key},headers={'Origin':self.url})
        self.assertEqual(status,200);self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        self.assertEqual(self.request('/api/status',headers=self.cookie)[0],200)
    def test_cross_origin_and_wrong_host_are_rejected_even_with_cookie(self):
        for extra in ({'Origin':'https://example.com'},{'Host':'example.com'},{'Sec-Fetch-Site':'cross-site'}):
            headers=dict(self.cookie,**extra)
            self.assertEqual(self.request('/api/action',{'address':ADDRESS,'method':'press_button','params':{'button':'home'}},headers)[0],403)
        self.assertEqual(self.local.calls,[])
    def test_frame_is_phone_guarded_and_contains_native_geometry(self):
        status,body,headers=self.request('/api/frame?address='+ADDRESS,headers=self.cookie)
        self.assertEqual((status,body),(200,b'fixture-image'))
        self.assertEqual(headers['X-Screen-Width'],'1180');self.assertEqual(headers['X-Image-Height'],'1344')
        self.assertEqual(headers['X-Frame-Generation'],'123')
        self.assertEqual(self.local.calls[-1],(ADDRESS,'screenshot',{'max_dimension':1344}))
        self.assertEqual(self.request('/api/frame?address=11:22:33:44:55:66',headers=self.cookie)[0],409)
    def test_native_action_forwards_once_with_the_selected_phone_and_dimensions(self):
        params={'action':'tap','params':{'x':1179,'y':2555},'width':1180,'height':2556}
        self.assertEqual(self.request('/api/action',{'address':ADDRESS,'method':'native_gesture','params':params},self.cookie)[0],200)
        self.assertEqual(self.local.calls,[(ADDRESS,'native_gesture',params)])
        self.local.fail=True
        self.assertEqual(self.request('/api/action',{'address':ADDRESS,'method':'native_gesture','params':params},self.cookie)[0],409)
        self.assertEqual(len(self.local.calls),2)
    def test_missing_address_and_non_ui_operations_are_rejected(self):
        for method,address in [('connect',ADDRESS),('native_gesture','')]:
            self.assertEqual(self.request('/api/action',{'address':address,'method':method,'params':{}},self.cookie)[0],400)
        self.assertEqual(self.local.calls,[])
    def test_preview_capacity_does_not_enqueue_another_capture(self):
        self.server.frame_slots.acquire();self.server.frame_slots.acquire()
        try:self.assertEqual(self.request('/api/frame?address='+ADDRESS,headers=self.cookie)[0],429)
        finally:self.server.frame_slots.release();self.server.frame_slots.release()
        self.assertEqual(self.local.calls,[])
    def test_apps_and_phone_action_ids_are_not_replaced_by_http_requests(self):
        status,body,_=self.request('/api/apps?address='+ADDRESS,headers=self.cookie)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['applications'][0]['bundle_id'],'org.example.fixture')
        status,body,_=self.request('/api/shortcut-result?address='+ADDRESS+'&id=original-action',headers=self.cookie)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['id'],'original-action')


if __name__=='__main__':unittest.main()
