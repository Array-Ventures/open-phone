#!/usr/bin/env python3
"""Loopback browser dashboard attached to the existing authenticated Mac API.

This process never owns capture/HID. It forwards once, with the selected phone
guard; browser access uses a separate, per-start cookie, not the driver token.
"""
import argparse
import base64
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser
from client import Client
from serve import LoopbackServer

ROOT = Path(__file__).resolve().parent
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/dashboard.js': ('dashboard.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}
ACTIONS = {'native_gesture', 'type_text', 'press_key', 'press_button',
           'shortcut_action', 'shortcut_cancel'}


class Dashboard(LoopbackServer):
    def __init__(self, port, local, key=None):
        self.local = local
        self.key = key or secrets.token_urlsafe(32)
        self.frame_slots = threading.BoundedSemaphore(2)
        super().__init__(('127.0.0.1', port), Handler)
        self.authority = f'127.0.0.1:{self.server_port}'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def reply(self, code, value, mime='application/json', headers=None):
        data = json.dumps(value).encode() if mime == 'application/json' else value
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for name, value in (headers or {}).items(): self.send_header(name, str(value))
        self.end_headers()
        self.wfile.write(data)

    def local_origin(self):
        origin = self.headers.get('Origin')
        if self.headers.get('Host') != self.server.authority or (origin and origin != 'http://' + self.server.authority) or self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
            self.reply(403, {'error': 'Open the dashboard at its printed local URL'})
            return False
        return True

    def authorized(self):
        if not self.local_origin(): return False
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get('Cookie', ''))
        except Exception: pass
        access = cookie.get('openphone_dashboard')
        if not access or not secrets.compare_digest(access.value, self.server.key):
            self.reply(401, {'error': 'Open the launch link or enter the dashboard access key'})
            return False
        return True

    def body(self):
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('application/json required')
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 131072: raise ValueError('Invalid request size')
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict): raise ValueError('Request must be an object')
        return value

    def do_GET(self):
        try:
            if not self.local_origin(): return
            parsed = urlsplit(self.path)
            if parsed.path in ASSETS:
                name, mime = ASSETS[parsed.path]
                self.reply(200, (ROOT / 'ui' / name).read_bytes(), mime)
                return
            if not self.authorized(): return
            if parsed.path == '/api/status':
                self.reply(200, self.server.local.call('status', diagnostics=True))
            elif parsed.path == '/api/devices':
                self.reply(200, self.server.local.call('devices'))
            elif parsed.path in ('/api/frame', '/api/apps', '/api/shortcut-result'):
                query = parse_qs(parsed.query, strict_parsing=True)
                address = query['address'][0]
                if parsed.path == '/api/apps':
                    self.reply(200, self.server.local.call_for(address, 'apps', include_system=False))
                elif parsed.path == '/api/shortcut-result':
                    self.reply(200, self.server.local.call_for(address, 'shortcut_result', action_id=query['id'][0]))
                else:
                    # Slow/multiple viewers cannot grow an unbounded encode queue.
                    if not self.server.frame_slots.acquire(blocking=False):
                        self.reply(429, {'error': 'Preview busy; wait for the next frame'}); return
                    try:
                        frame = self.server.local.call_for(address, 'screenshot', max_dimension=1344)
                    finally: self.server.frame_slots.release()
                    self.reply(200, base64.b64decode(frame['data'], validate=True), frame['mime_type'], {
                        'X-Screen-Width': frame['screen_width'], 'X-Screen-Height': frame['screen_height'],
                        'X-Image-Width': frame['width'], 'X-Image-Height': frame['height'],
                        'X-Frame-Generation': frame['generation'], 'X-Frame-Timestamp': frame['timestamp']})
            else: self.reply(404, {'error': 'Unknown route'})
        except (ValueError, KeyError, TypeError) as error: self.reply(400, {'error': str(error)})
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as error: self.reply(409, {'error': str(error)})

    def do_POST(self):
        try:
            if not self.local_origin(): return
            if self.path == '/session':
                request = self.body()
                key = request.get('key')
                if not isinstance(key, str) or not secrets.compare_digest(key, self.server.key):
                    self.reply(401, {'error': 'Invalid dashboard access key'}); return
                self.reply(200, {'ok': True}, headers={'Set-Cookie': 'openphone_dashboard=' + self.server.key + '; HttpOnly; SameSite=Strict; Path=/'})
                return
            if not self.authorized(): return
            request = self.body()
            if self.path == '/api/connect':
                if set(request) != {'address', 'capture_id'}: raise ValueError('Select a Bluetooth address and USB capture device')
                value = self.server.local.call('connect', **request)
            elif self.path == '/api/action':
                if set(request) != {'address', 'method', 'params'} or request['method'] not in ACTIONS:
                    raise ValueError('Unsupported dashboard action')
                if not isinstance(request['params'], dict): raise ValueError('Action params must be an object')
                if not isinstance(request['address'], str) or not request['address']: raise ValueError('Select a phone first')
                value = self.server.local.call_for(request['address'], request['method'], **request['params'])
            else: self.reply(404, {'error': 'Unknown route'}); return
            self.reply(200, value)
        except (ValueError, KeyError, TypeError) as error: self.reply(400, {'error': str(error)})
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as error: self.reply(409, {'error': str(error)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--local-url', default='http://127.0.0.1:8766')
    parser.add_argument('--local-token-file')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    parsed = urlsplit(args.local_url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password:
        raise ValueError('The dashboard attaches only to the 127.0.0.1 Mac API')
    server = Dashboard(args.port, Client(args.local_url, args.local_token_file))
    # This key is separate from all driver/relay/Shortcut credentials and rotates
    # on every dashboard start. Do not print the launch URL containing its hash.
    path = ROOT / '.dashboard-token'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as file: file.write(server.key + '\n')
    url = 'http://' + server.authority + '/'
    print(f'OpenPhone dashboard: {url}; access key file: {path}', flush=True)
    if not args.no_open: webbrowser.open(url + '#' + server.key)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
