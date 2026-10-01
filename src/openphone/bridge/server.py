#!/usr/bin/env python3
"""Optional, separately authenticated phone Shortcut bridge. No cloud dependency."""
import argparse
import collections
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import ipaddress
import json
import os
import pathlib
import secrets
import socketserver
import time
from openphone.bridge.queue import ActionQueue

from openphone.paths import RUNTIME_HOME as ROOT


def credential(path,create=True):
    path=pathlib.Path(path)
    if not path.exists():
        if not create:raise ValueError('Start the local phone API first, or supply a controller token file')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as output:output.write(secrets.token_urlsafe(32)+'\n')
    if path.stat().st_mode&0o077:raise ValueError('Token files must be owner-only (chmod 600)')
    value=path.read_text().strip()
    if not value:raise ValueError('Empty token file')
    return value


class BridgeServer(ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name='localhost';self.server_port=self.server_address[1]


def make_handler(actions,controller_token,phone_token,hosts,template=None):
    events=collections.deque(maxlen=64)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,value):
            events.append({'time':time.time(),'route':self.path.split('?')[0][:80],
                           'status':status,'error':value.get('error') if not value.get('ok') else None})
            body=json.dumps(value,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(body)
        def authorized(self,phone=False):
            if self.headers.get('Origin') or self.headers.get('Host') not in hosts:
                self.reply(403,{'ok':False,'error':'Origin or Host rejected'});return False
            provided=self.headers.get('X-OpenPhone-Token','') if phone else self.headers.get('Authorization','')
            expected=phone_token if phone else 'Bearer '+controller_token
            if not secrets.compare_digest(provided,expected):
                self.reply(401,{'ok':False,'error':'Phone credential required' if phone else 'Controller credential required'});return False
            return True
        def body(self):
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('application/json required')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=131072:raise ValueError('Invalid request length')
            value=json.loads(self.rfile.read(length))
            if not isinstance(value,dict):raise ValueError('Request must be an object')
            return value
        def do_GET(self):
            if self.path=='/UseOpenPhone.shortcut' and template is not None:
                if self.headers.get('Origin') or self.headers.get('Host') not in hosts:
                    self.reply(403,{'ok':False,'error':'Origin or Host rejected'});return
                self.send_response(200);self.send_header('Content-Type','application/octet-stream')
                self.send_header('Content-Disposition','attachment; filename="Use OpenPhone.shortcut"')
                self.send_header('Content-Length',str(len(template)));self.send_header('Cache-Control','no-store')
                self.end_headers();self.wfile.write(template);return
            if not self.authorized():return
            try:
                if self.path=='/v1/bridge/status':
                    self.reply(200,{'ok':True,'events':list(events)})
                elif self.path.startswith('/v1/bridge/actions/'):
                    self.reply(200,dict(ok=True,**actions.status(self.path.rsplit('/',1)[1])))
                else:self.reply(404,{'ok':False,'error':'Unknown route'})
            except KeyError:self.reply(404,{'ok':False,'error':'Unknown action'})
        def do_POST(self):
            phone=self.path in ('/v1/bridge/claim','/v1/bridge/complete')
            if not self.authorized(phone):return
            try:
                value=self.body()
                if self.path=='/v1/bridge/claim':result=actions.claim()
                elif self.path=='/v1/bridge/complete':result=actions.complete(value['id'],value['receipt'],value['result'])
                elif self.path=='/v1/bridge/actions':result=actions.enqueue(value['kind'],value['payload'],value.get('ttl',60))
                elif self.path.startswith('/v1/bridge/actions/') and self.path.endswith('/cancel'):
                    result=actions.cancel(self.path.split('/')[-2])
                else:self.reply(404,{'ok':False,'error':'Unknown route'});return
                self.reply(200,dict(ok=True,**result))
            except (ValueError,KeyError,TypeError) as error:self.reply(400,{'ok':False,'error':str(error)})
            except RuntimeError as error:self.reply(409,{'ok':False,'error':str(error)})
    return Handler


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='127.0.0.1',help='Numeric IPv4 address; use this Mac’s LAN address for phone access')
    parser.add_argument('--port',type=int,default=8767)
    parser.add_argument('--controller-token-file',type=pathlib.Path,default=ROOT/'.runtime-token')
    parser.add_argument('--phone-token-file',type=pathlib.Path,default=ROOT/'.bridge-token')
    parser.add_argument('--template-file',type=pathlib.Path,help='Optionally serve the signed credential-free Shortcut template at /UseOpenPhone.shortcut')
    args=parser.parse_args()
    host=ipaddress.IPv4Address(args.host)
    if host.is_unspecified:raise ValueError('Bind a specific Mac IPv4 address')
    controller=credential(args.controller_token_file,False)
    phone=credential(args.phone_token_file)
    if secrets.compare_digest(controller,phone):raise ValueError('Phone and controller credentials must differ')
    actions=ActionQueue()
    template=args.template_file.read_bytes() if args.template_file else None
    if template is not None and len(template)>2000000:raise ValueError('Shortcut template is too large')
    hosts={f'{args.host}:{args.port}',f'127.0.0.1:{args.port}',f'localhost:{args.port}'}
    server=BridgeServer((args.host,args.port),make_handler(actions,controller,phone,hosts,template))
    print(f'OpenPhone Shortcut bridge: http://{args.host}:{args.port}; phone token file: {args.phone_token_file}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();actions.close()


if __name__=='__main__':main()
