"""HTTP(S) client for the original relay, with verified TLS and file credentials."""
import ipaddress
import json
import os
import pathlib
import ssl
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from bridge_serve import credential

ROOT = pathlib.Path(__file__).resolve().parent


class RelayClient:
    def __init__(self, url=None, token_file=None, host=False, ca_file=None):
        self.url = (url or os.environ.get('OPEN_PHONE_RELAY_URL', 'http://127.0.0.1:8768')).rstrip('/')
        parsed = urlsplit(self.url)
        loopback = parsed.hostname == 'localhost'
        try:
            loopback = loopback or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('Supply an HTTP(S) origin without credentials or a path')
        if parsed.scheme == 'http' and not loopback:
            raise ValueError('Non-loopback relay connections require HTTPS')
        self.context = ssl.create_default_context(cafile=ca_file or os.environ.get('OPEN_PHONE_RELAY_CA'))
        token = credential(token_file or ROOT / ('.relay-host-token' if host else '.relay-agent-token'), create=False)
        self.headers = {'Authorization': 'Bearer ' + token} if host else {'X-API-Key': token}

    def request(self, path, data=None, method=None, timeout=70):
        if not path.startswith('/v1/') or any(c in path for c in ('\r', '\n', '#')):
            raise ValueError('Invalid relay path')
        headers = dict(self.headers)
        if data is not None:
            headers['Content-Type'] = 'application/json'; data = json.dumps(data).encode()
        request = Request(self.url+path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout, context=self.context) as response:
                body = response.read(16000001)
                if len(body) > 16000000:
                    raise RuntimeError('Relay response exceeds size limit')
                return json.loads(body) if response.headers.get_content_type() == 'application/json' else body
        except HTTPError as error:
            with error:
                value = json.loads(error.read())
            raise RuntimeError(value.get('detail', {}).get('message', 'Relay request failed')) from None
