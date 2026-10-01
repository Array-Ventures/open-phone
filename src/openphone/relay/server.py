#!/usr/bin/env python3
"""Self-hosted phone/job REST service. Non-loopback listeners require TLS."""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import pathlib
import secrets
import socketserver
import ssl
import sys
import threading
from urllib.parse import urlsplit, parse_qs
from openphone.bridge.server import credential
from openphone.relay.store import RelayStore, command
import openphone.device.gestures as gestures

from openphone.paths import RUNTIME_HOME as ROOT
LOCAL_METHODS = {'move', 'tap', 'swipe', 'hold_and_drag', 'flick', 'scroll', 'type_text',
                 'press_key', 'press_button', 'screenshot', 'apps', 'shortcut_action',
                 'shortcut_result', 'shortcut_cancel', 'native_gesture'}


class RelayServer(ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = 'localhost'; self.server_port = self.server_address[1]

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def handler(store, agent_key, host_key, hosts):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            # Handshake in the request thread, with bounded socket reads. A
            # client that only opens TCP must not stall the listener's accept.
            self.request.settimeout(15)
            if isinstance(self.request, ssl.SSLSocket):
                self.request.do_handshake()
            super().setup()
        def log_message(self, *args):
            pass

        def send(self, status, value, mime='application/json'):
            body = json.dumps(value, ensure_ascii=False).encode() if mime == 'application/json' else value
            self.send_response(status); self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff'); self.end_headers(); self.wfile.write(body)

        def error(self, status, code, message):
            self.send(status, {'detail': {'error': code, 'message': message}})

        def body(self):
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16000000 or self.headers.get_content_type() != 'application/json':
                raise ValueError('A bounded application/json body is required')
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError('Body must be an object')
            return value

        def dispatch(self, verb):
            parsed = urlsplit(self.path); path = parsed.path.strip('/').split('/')
            worker = path[:2] == ['v1', 'host']
            if self.headers.get('Origin') or self.headers.get('Host') not in hosts:
                self.error(403, 'ORIGIN_REJECTED', 'Origin or Host rejected'); return
            supplied = self.headers.get('Authorization', '') if worker else self.headers.get('X-API-Key', '')
            expected = 'Bearer ' + host_key if worker else agent_key
            if not secrets.compare_digest(supplied, expected):
                self.error(401, 'AUTH_REQUIRED', 'Valid host credential required' if worker else 'Valid agent API key required'); return
            try:
                query = parse_qs(parsed.query)
                if query and query != {'async': ['true']} and query != {'async': ['false']}:
                    raise ValueError('Only ?async=true or ?async=false is supported')
                asynchronous = query == {'async': ['true']}
                data = self.body() if verb in ('POST', 'PATCH') else {}
                if worker:
                    if verb != 'POST' or len(path) != 3:
                        self.error(404, 'NOT_FOUND', 'Unknown host route'); return
                    operation = path[2]
                    if operation == 'register':
                        store.register(data['phone_id'], data['session'], data['name'], data['width'], data['height'], data.get('host_id'))
                        result = {'ok': True}
                    elif operation == 'claim':
                        result = {'job': store.claim(data['phone_id'], data['session'])}
                    elif operation == 'heartbeat':
                        store.heartbeat(data['phone_id'], data['session'], data.get('online', True)); result = {'ok': True}
                    elif operation == 'complete':
                        result = store.complete(data['id'], data['session'], data['receipt'], data['result'], data.get('error'))
                    elif operation == 'unregister':
                        store.unregister(data['phone_id'], data['session']); result = {'ok': True}
                    elif operation == 'video-poll':
                        if set(data) != {'phone_id', 'session'}: raise ValueError('Supply phone ID and host session')
                        result = store.video.poll(data['phone_id'], data['session'])
                    elif operation == 'video-answer':
                        if set(data) != {'id', 'session', 'receipt', 'answer', 'error'}: raise ValueError('Supply the video answer envelope')
                        result = store.video.answer(**dict(identifier=data['id'], session=data['session'], receipt=data['receipt'], answer=data['answer'], error=data['error']))
                    elif operation == 'video-status':
                        if set(data) != {'id', 'session', 'status'}: raise ValueError('Supply video ID, host session and status')
                        result = store.video.report(data['id'], data['session'], data['status'])
                    else:
                        self.error(404, 'NOT_FOUND', 'Unknown host route'); return
                    self.send(200, result); return
                if path == ['v1', 'phones'] and verb == 'GET':
                    self.send(200, store.phones_view()); return
                if len(path) == 3 and path[:2] == ['v1', 'video']:
                    if verb == 'GET': self.send(200, store.video.view(path[2])); return
                    if verb == 'DELETE': self.send(200, store.video.stop(path[2])); return
                if len(path) in (3, 4) and path[:2] == ['v1', 'jobs'] and verb == 'GET':
                    if len(path) == 3:
                        self.send(200, store.job_view(path[2])); return
                    if path[3] == 'download':
                        image, mime = store.download(path[2]); self.send(200, image, mime); return
                if len(path) >= 4 and path[:2] == ['v1', 'phones']:
                    identifier, operation = path[2], path[3]
                    phone = store.phone_view(identifier)
                    if operation == 'video' and len(path) == 4 and verb == 'POST':
                        self.send(200, store.video.offer(identifier, data)); return
                    if operation == 'status' and len(path) == 4 and verb == 'GET':
                        self.send(200, {'phone_id': identifier, 'phone_name': phone['display_name'] or phone['name'],
                            'connection_status': phone['connection_status'], 'width': phone['width'], 'height': phone['height']}); return
                    if operation == 'settings' and len(path) == 4 and verb == 'PATCH':
                        if set(data) != {'display_name'}:
                            raise ValueError('Supply display_name only')
                        self.send(200, store.rename(identifier, data['display_name'])); return
                    if operation == 'apps' and len(path) == 4 and verb == 'GET' and not asynchronous:
                        cached = store.apps_view(identifier)
                        if cached is not None:
                            self.send(200, cached); return
                    if operation == 'action' and len(path) == 4 and verb == 'POST':
                        if set(data) != {'method', 'params'} or data['method'] not in LOCAL_METHODS or not isinstance(data['params'], dict):
                            raise ValueError('Unsupported local action')
                        method, params = data['method'], data['params']
                        if method == 'native_gesture':
                            if set(params) != {'action', 'params', 'width', 'height'}:
                                raise ValueError('Supply native gesture, parameters and screenshot dimensions')
                            gestures.plan(**params)
                    elif (verb == 'GET' and operation in ('screenshot', 'apps') and len(path) == 4) or (verb == 'POST' and len(path) == 4) or (verb == 'POST' and path[3:] == ['apps', 'refresh']):
                        method, params = command('apps' if path[3:] == ['apps', 'refresh'] else operation, data, phone['width'], phone['height'])
                    else:
                        self.error(404, 'NOT_FOUND', 'Unknown phone route'); return
                    job_id = store.enqueue(identifier, method, params)
                    if asynchronous:
                        self.send(200, {'job_id': job_id}); return
                    result = store.wait(job_id)
                    if operation == 'action' and method == 'screenshot' and result['status'] == 'completed':
                        image, mime = store.download(job_id)
                        result['result'].update(data=base64.b64encode(image).decode(), mime_type=mime)
                        self.send(200, result)
                    elif operation == 'screenshot' and result['status'] == 'completed':
                        image, mime = store.download(job_id); self.send(200, image, mime)
                    elif operation == 'screenshot' and result['status'] == 'failed':
                        self.error(408, 'TIMEOUT' if result['result']['error'] == 'TIMEOUT' else 'CAPTURE_FAILED', result['result']['error'])
                    elif operation == 'apps' and result['status'] == 'completed':
                        self.send(200, store.apps_view(identifier))
                    else:
                        self.send(200, result)
                    return
                self.error(404, 'NOT_FOUND', 'Unknown route')
            except KeyError as error:
                code = str(error.args[0])
                if code not in ('PHONE_NOT_FOUND', 'DATA_NOT_FOUND'):
                    code = 'NOT_FOUND'
                self.error(404, code, str(error.args[0]))
            except (ValueError, TypeError, OverflowError) as error:
                self.error(400, 'INVALID_REQUEST', str(error))
            except RuntimeError as error:
                self.error(409, 'HOST_UNAVAILABLE', str(error))

        def do_GET(self):
            self.dispatch('GET')
        def do_POST(self):
            self.dispatch('POST')
        def do_PATCH(self):
            self.dispatch('PATCH')
        def do_DELETE(self):
            self.dispatch('DELETE')
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1'); parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--hostname', action='append', default=[], help='Additional allowed HTTP Host hostname (without port)')
    parser.add_argument('--cert', type=pathlib.Path); parser.add_argument('--key', type=pathlib.Path)
    parser.add_argument('--agent-token-file', type=pathlib.Path, default=ROOT/'.relay-agent-token')
    parser.add_argument('--host-token-file', type=pathlib.Path, default=ROOT/'.relay-host-token')
    parser.add_argument('--state-file', type=pathlib.Path, default=ROOT/'private/relay-state.sqlite')
    parser.add_argument('--ephemeral', action='store_true', help='Use memory-only state for disposable tests')
    args = parser.parse_args(); address = ipaddress.IPv4Address(args.host)
    if bool(args.cert) != bool(args.key) or (not address.is_loopback and not args.cert):
        parser.error('Non-loopback listeners require --cert and --key; supply both together')
    if address.is_unspecified:
        parser.error('Bind a specific interface address')
    agent_key, host_key = credential(args.agent_token_file), credential(args.host_token_file)
    if secrets.compare_digest(agent_key, host_key):
        parser.error('Agent and host credentials must differ')
    hosts = {f'{name}:{args.port}' for name in [args.host, 'localhost', *args.hostname]}
    store = RelayStore(state_file=None if args.ephemeral else args.state_file)
    server = RelayServer((args.host, args.port), handler(store, agent_key, host_key, hosts))
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    print(f'OpenPhone relay: {"https" if args.cert else "http"}://{args.host}:{args.port}; credentials remain in their token files', flush=True)
    stopping = threading.Event()
    def maintain():
        while not stopping.wait(5):
            try: store.maintain()
            except RuntimeError:
                print('Relay storage unavailable; requests will fail until storage is repaired and the relay restarted', file=sys.stderr, flush=True)
                return
    maintenance = threading.Thread(target=maintain, daemon=True); maintenance.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set(); maintenance.join(timeout=2)
        server.server_close()
        store.close()


if __name__ == '__main__':
    main()
