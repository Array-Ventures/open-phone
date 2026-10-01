import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError

ROOT=pathlib.Path(__file__).resolve().parents[2]


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.token_file=pathlib.Path(cls.tmp.name)/'token'
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));cls.port=sock.getsockname()[1]
        cls.process=subprocess.Popen([sys.executable,'-m','openphone','serve','--port',str(cls.port),'--token-file',str(cls.token_file)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.url=f'http://127.0.0.1:{cls.port}'
        for _ in range(50):
            try:
                with urlopen(cls.url+'/health',timeout=.2) as r:assert r.status==200
                break
            except OSError:time.sleep(.02)
        else:raise RuntimeError('Test API did not start')
        cls.token=cls.token_file.read_text().strip()
    @classmethod
    def tearDownClass(cls):cls.process.terminate();cls.process.wait(timeout=3);cls.tmp.cleanup()
    def request(self,path,headers=None,data=None):
        try:
            with urlopen(Request(self.url+path,headers=headers or {},data=data),timeout=2) as r:return r.status,json.load(r)
        except HTTPError as e:return e.code,json.load(e)
    def test_status_requires_token(self):self.assertEqual(self.request('/v1/status')[0],401)
    def test_valid_token_status_without_starting_hardware(self):
        status,data=self.request('/v1/status',{'Authorization':'Bearer '+self.token})
        self.assertEqual(status,200);self.assertIsNone(data['hid'])
    def test_browser_origin_rejected_even_with_token(self):
        self.assertEqual(self.request('/v1/status',{'Authorization':'Bearer '+self.token,'Origin':'https://example.com'})[0],403)
    def test_token_is_owner_only(self):self.assertEqual(self.token_file.stat().st_mode&0o777,0o600)
    def test_unknown_action_does_not_start_hardware(self):
        status,data=self.request('/v1/action',{'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},json.dumps({'method':'unknown'}).encode())
        self.assertEqual(status,400);self.assertIn('Unknown',data['error'])

if __name__=='__main__':unittest.main()
