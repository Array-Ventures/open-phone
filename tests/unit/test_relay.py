import base64
import json
import pathlib
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from openphone.relay.store import RelayStore, command
from openphone.relay.server import RelayServer, handler
from openphone.relay.client import RelayClient

SESSION = 'a' * 32


class RelayStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.store = RelayStore(clock=lambda: self.now, deadline=5)
        self.store.register('phone', SESSION, 'Test phone', 1000, 2000)

    def test_only_one_job_runs_per_phone_and_wrong_receipt_cannot_complete(self):
        first = self.store.enqueue('phone', 'tap', {'x': .5, 'y': .5})
        second = self.store.enqueue('phone', 'press_button', {'button': 'home'})
        job = self.store.claim('phone', SESSION, wait=0)
        self.assertEqual(job['id'], first)
        self.assertIsNone(self.store.claim('phone', SESSION, wait=0))
        with self.assertRaises(RuntimeError):
            self.store.complete(first, SESSION, 'wrong', {})
        self.store.complete(first, SESSION, job['receipt'], {'ok': True})
        self.assertEqual(self.store.claim('phone', SESSION, wait=0)['id'], second)

    def test_timed_out_job_is_not_replayed_or_completed_late(self):
        identifier = self.store.enqueue('phone', 'tap', {})
        job = self.store.claim('phone', SESSION, wait=0)
        self.now = 6
        self.assertIsNone(self.store.claim('phone', SESSION, wait=0))
        self.assertEqual(self.store.job_view(identifier)['result'], {'error': 'TIMEOUT'})
        with self.assertRaises(RuntimeError):
            self.store.complete(identifier, SESSION, job['receipt'], {})

    def test_replaced_host_cannot_receive_or_complete_old_session_jobs(self):
        identifier = self.store.enqueue('phone', 'tap', {})
        job = self.store.claim('phone', SESSION, wait=0)
        with self.assertRaises(RuntimeError):
            self.store.register('phone', 'b' * 32, 'Replacement', 1000, 2000)
        self.now = 21
        self.store.register('phone', 'b' * 32, 'Replacement', 1000, 2000)
        self.assertEqual(self.store.job_view(identifier)['status'], 'failed')
        with self.assertRaises(ValueError):
            self.store.claim('phone', SESSION, wait=0)
        with self.assertRaises(RuntimeError):
            self.store.complete(identifier, SESSION, job['receipt'], {})

    def test_phone_loss_fails_pending_jobs_and_prevents_new_input(self):
        identifier = self.store.enqueue('phone', 'tap', {})
        self.store.heartbeat('phone', SESSION, online=False)
        self.assertEqual(self.store.phone_view('phone')['connection_status'], 'offline')
        self.assertEqual(self.store.job_view(identifier)['result']['error'], 'PHONE_DISCONNECTED')
        self.assertIsNone(self.store.claim('phone', SESSION, wait=0))
        with self.assertRaises(RuntimeError):
            self.store.enqueue('phone', 'tap', {})

    def test_native_pixel_conversion_and_direction_differ_from_content_scroll(self):
        method, params = command('tap', {'x': 250, 'y': 1500}, 1000, 2000)
        self.assertEqual((method, params['action'], params['params']), ('native_gesture', 'tap', {'x': 250, 'y': 1500}))
        method, params = command('flick', {'x': 500, 'y': 1500, 'direction': 'up'}, 1000, 2000)
        self.assertEqual((method, params['action'], params['params']['direction']), ('native_gesture', 'flick', 'up'))
        for x in (True, 1000, -1, float('nan')):
            with self.assertRaises(ValueError):
                command('tap', {'x': x, 'y': 0}, 1000, 2000)

    def test_input_limits_and_modifier_conversion(self):
        with self.assertRaises(ValueError):
            command('type', {'text': '🌍'}, 1000, 2000)
        method, params = command('keypress', {'key': 'arrow_up', 'modifiers': ['shift', 'command'], 'repeat': 2}, 1000, 2000)
        self.assertEqual((method, params), ('press_key', {'key': 'up', 'modifiers': 10, 'repeat_count': 2}))
        with self.assertRaises(ValueError):
            command('drag', {'from_x': 0, 'from_y': 0, 'to_x': 1, 'to_y': 1, 'speed': 'unknown'}, 1000, 2000)

    def test_plaintext_external_client_is_rejected_before_reading_credential(self):
        with self.assertRaisesRegex(ValueError, 'require HTTPS'):
            RelayClient('http://192.168.1.2:8768', token_file='/nonexistent')

    def test_app_cache_survives_host_disconnect_and_is_replaced_by_refresh(self):
        for name in ('Before', 'After'):
            identifier = self.store.enqueue('phone', 'apps', {})
            job = self.store.claim('phone', SESSION, wait=0)
            self.store.complete(identifier, SESSION, job['receipt'], {'applications': [{'name': name, 'bundle_id': 'org.example.app'}]})
            self.assertEqual(self.store.apps_view('phone')['apps'][0]['name'], name)
        self.store.unregister('phone', SESSION)
        self.assertEqual(self.store.apps_view('phone')['app_count'], 1)


class RelayHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.server = RelayServer(('127.0.0.1', 0), handler(self.store, 'agent-test', 'host-test', set()))
        host = '127.0.0.1:' + str(self.server.server_port)
        self.server.RequestHandlerClass = handler(self.store, 'agent-test', 'host-test', {host})
        self.url = 'http://' + host
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, path, data=None, host=False, credential=None, extra=None):
        headers = {'Authorization': 'Bearer ' + (credential or 'host-test')} if host else {'X-API-Key': credential or 'agent-test'}
        headers.update(extra or {})
        if data is not None:
            headers['Content-Type'] = 'application/json'; data = json.dumps(data).encode()
        try:
            with urlopen(Request(self.url + path, data=data, headers=headers), timeout=2) as response:
                body = response.read()
                return response.status, json.loads(body) if response.headers.get_content_type() == 'application/json' else body
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_agent_and_host_credentials_are_not_interchangeable(self):
        self.assertEqual(self.request('/v1/phones', credential='host-test')[0], 401)
        self.assertEqual(self.request('/v1/host/register', {}, host=True, credential='agent-test')[0], 401)
        self.assertEqual(self.request('/v1/phones', extra={'Origin': 'https://attacker.invalid'})[0], 403)
        self.assertEqual(self.request('/v1/phones', extra={'Host': 'attacker.invalid'})[0], 403)

    def test_native_gesture_keeps_its_screenshot_dimensions_until_execution(self):
        self.request('/v1/host/register', {'phone_id': 'phone', 'session': SESSION, 'name': 'Test phone', 'width': 1000, 'height': 2000}, host=True)
        params={'action':'tap','params':{'x':1179,'y':2555},'width':1180,'height':2556}
        status,result=self.request('/v1/phones/phone/action?async=true',{'method':'native_gesture','params':params})
        self.assertEqual(status,200)
        job=self.request('/v1/host/claim',{'phone_id':'phone','session':SESSION},host=True)[1]['job']
        self.assertEqual(job['params'],params)
        self.assertEqual(job['method'],'native_gesture')
        # The Mac driver independently checks these dimensions against a fresh
        # frame before HID emission; relay metadata cannot silently replace them.
        invalid=dict(params,params={'x':1180,'y':0})
        self.assertEqual(self.request('/v1/phones/phone/action?async=true',{'method':'native_gesture','params':invalid})[0],400)

    def test_authenticated_async_screenshot_round_trip_and_rename(self):
        self.assertEqual(self.request('/v1/host/register', {'phone_id': 'phone', 'session': SESSION, 'name': 'Test phone', 'width': 1000, 'height': 2000}, host=True)[0], 200)
        self.assertEqual(self.request('/v1/phones')[1][0]['connection_status'], 'online')
        status, result = self.request('/v1/phones/phone/screenshot?async=true')
        self.assertEqual(status, 200); identifier = result['job_id']
        status, value = self.request('/v1/host/claim', {'phone_id': 'phone', 'session': SESSION}, host=True)
        job = value['job']; self.assertEqual(job['id'], identifier)
        png = b'\x89PNG\r\n\x1a\n' + b'fixture'
        completion = {'id': identifier, 'session': SESSION, 'receipt': job['receipt'],
            'result': {'data': base64.b64encode(png).decode(), 'mime_type': 'image/png'}}
        self.assertEqual(self.request('/v1/host/complete', completion, host=True)[0], 200)
        self.assertNotIn('data', self.request('/v1/jobs/' + identifier)[1]['result'])
        self.assertEqual(self.request('/v1/jobs/' + identifier + '/download'), (200, png))
        self.assertEqual(self.request('/v1/host/complete', completion, host=True)[0], 409)
        with urlopen(Request(self.url + '/v1/phones/phone/settings', data=b'{"display_name":"Lab"}',
                     headers={'X-API-Key': 'agent-test', 'Content-Type': 'application/json'}, method='PATCH')) as response:
            self.assertEqual(json.load(response)['display_name'], 'Lab')

    def test_sync_action_timeout_is_a_failed_job_and_screenshot_timeout_is_http_error(self):
        self.store.register('phone', SESSION, 'Test phone', 1000, 2000)
        self.store.deadline = .01
        status, job = self.request('/v1/phones/phone/tap', {'x': 1, 'y': 1})
        self.assertEqual((status, job['status'], job['result']['error']), (200, 'failed', 'TIMEOUT'))
        status, error = self.request('/v1/phones/phone/screenshot')
        self.assertEqual((status, error['detail']['error']), (408, 'TIMEOUT'))

    def test_idle_tcp_client_does_not_block_other_tls_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)
            config = path/'cert.cnf'
            config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n')
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-config',str(config),
                            '-keyout',str(path/'key.pem'),'-out',str(path/'cert.pem')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            server = RelayServer(('127.0.0.1',0),handler(RelayStore(),'agent-test','host-test',set()))
            authority = f'127.0.0.1:{server.server_port}'
            server.RequestHandlerClass = handler(RelayStore(),'agent-test','host-test',{authority})
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(path/'cert.pem',path/'key.pem')
            server.socket = context.wrap_socket(server.socket,server_side=True,do_handshake_on_connect=False)
            thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            raw = socket.create_connection(server.server_address,timeout=1)
            try:
                trusted = ssl.create_default_context(cafile=path/'cert.pem')
                request = Request('https://'+authority+'/v1/phones',headers={'X-API-Key':'agent-test'})
                with urlopen(request,context=trusted,timeout=1) as response:
                    self.assertEqual(json.load(response),[])
            finally:
                raw.close();server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
