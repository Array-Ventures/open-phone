#!/usr/bin/env python3
"""Small local REST client; credentials stay out of command arguments."""
import argparse
import json
import pathlib
from urllib.request import Request,urlopen
from urllib.error import HTTPError


class Client:
    def __init__(self,url='http://127.0.0.1:8766',token_file=None):
        self.url=url
        self.token=pathlib.Path(token_file or pathlib.Path(__file__).parent/'.runtime-token').read_text().strip()
    def request(self,path,data=None,timeout=45):
        headers={'Authorization':'Bearer '+self.token}
        if data is not None:headers['Content-Type']='application/json';data=json.dumps(data).encode()
        try:
            with urlopen(Request(self.url+path,data=data,headers=headers),timeout=timeout) as r:
                body=r.read()
                return json.loads(body) if r.headers.get_content_type()=='application/json' else body
        except HTTPError as e:
            with e: message=json.loads(e.read()).get('error',str(e))
            raise RuntimeError(message) from None
    def call(self,method,**params):return self.request('/v1/action',{'method':method,'params':params})
    def call_for(self,address,method,**params):
        return self.request('/v1/action',{'method':method,'params':params,'expected_address':address})
    def screenshot(self,path):
        data=self.request('/v1/screenshot');pathlib.Path(path).write_bytes(data)
        return {'ok':True,'saved':str(path),'bytes':len(data)}


def main():
    p=argparse.ArgumentParser();p.add_argument('method');p.add_argument('params',nargs='?',default='{}')
    p.add_argument('--out');p.add_argument('--url',default='http://127.0.0.1:8766');p.add_argument('--token-file')
    a=p.parse_args();client=Client(a.url,a.token_file)
    if a.method=='screenshot' and a.out:result=client.screenshot(a.out)
    else:result=client.call(a.method,**json.loads(a.params))
    print(json.dumps(result))


if __name__=='__main__':main()
