#!/usr/bin/env python3
"""Private RPC adapter for the relay MCP process; no local device access."""
import json
import os
import sys
from openphone.relay.client import RelayClient


def main():
    client = None
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if request.get('method') != 'request':
                raise ValueError('Unknown RPC method')
            if client is None:
                client = RelayClient(token_file=os.environ.get('OPEN_PHONE_RELAY_TOKEN_FILE'))
            result = client.request(**request['params'])
            if isinstance(result, bytes):
                raise ValueError('Use the JSON screenshot action from MCP')
            response = {'id': request.get('id'), 'ok': True, 'result': result}
        except Exception as error:
            response = {'id': request.get('id'), 'ok': False, 'error': str(error)}
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
