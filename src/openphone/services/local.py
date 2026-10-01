#!/usr/bin/env python3
"""Authenticated loopback REST API. This is not a cloud relay."""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import pathlib
import secrets
import socketserver
from openphone.device.driver import PhoneDriver
from openphone.paths import RUNTIME_HOME as ROOT


class LoopbackServer(ThreadingHTTPServer):
    def server_bind(self):
        # A loopback API has no need for HTTPServer's reverse-DNS lookup, which
        # can delay startup before requests are accepted on some networks.
        socketserver.TCPServer.server_bind(self)
        self.server_name='localhost'
        self.server_port=self.server_address[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--token-file',type=pathlib.Path,default=ROOT/'.runtime-token')
    args=parser.parse_args()
    if args.token_file.exists(): token=args.token_file.read_text().strip()
    else:
        token=secrets.token_urlsafe(32)
        fd=os.open(args.token_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(token+'\n')
    if not token: raise ValueError('Token file is empty')
    driver=PhoneDriver()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self,status,value):
            data=json.dumps(value).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
        def authorized(self):
            if self.headers.get('Origin') or self.headers.get('Host') not in (f'127.0.0.1:{args.port}',f'localhost:{args.port}'):
                self.reply(403,{'ok':False,'error':'Only local non-browser clients are accepted'});return False
            expected='Bearer '+token
            if not secrets.compare_digest(self.headers.get('Authorization',''),expected):
                self.reply(401,{'ok':False,'error':'Bearer token required'});return False
            return True
        def do_GET(self):
            if self.path=='/health':self.reply(200,{'ok':True,'backend':'usb+classic-hid'});return
            if not self.authorized():return
            try:
                if self.path=='/v1/screenshot':
                    result=driver.call('screenshot',{});data=base64.b64decode(result['data'])
                    self.send_response(200);self.send_header('Content-Type',result['mime_type'])
                    self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
                    self.send_header('X-Frame-Generation',str(result['generation']));self.end_headers();self.wfile.write(data);return
                routes={'/v1/devices':'devices','/v1/status':'status','/v1/apps':'apps'}
                if self.path not in routes:self.reply(404,{'ok':False,'error':'Unknown route'});return
                self.reply(200,driver.call(routes[self.path],{}))
            except Exception as e:self.reply(409,{'ok':False,'error':str(e)})
        def do_POST(self):
            if not self.authorized():return
            if self.path!='/v1/action':self.reply(404,{'ok':False,'error':'Unknown route'});return
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                self.reply(415,{'ok':False,'error':'application/json required'});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=131072:raise ValueError('Invalid request size')
                request=json.loads(self.rfile.read(size))
                self.reply(200,driver.call(request['method'],request.get('params',{}),request.get('expected_address')))
            except (ValueError,KeyError,TypeError) as e:self.reply(400,{'ok':False,'error':str(e)})
            except Exception as e:self.reply(409,{'ok':False,'error':str(e)})
    server=LoopbackServer(('127.0.0.1',args.port),Handler)
    print(f'OpenPhone listening on http://127.0.0.1:{args.port}; token file: {args.token_file}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();driver.close()


if __name__=='__main__':main()
