#!/usr/bin/env python3
"""Mac worker: connect locally, receive jobs outbound, return one completion."""
import argparse
import hashlib
import json
import secrets
import socket
import threading
import time
from client import Client
from relay_client import RelayClient


def run(relay, local, phone_id, address):
    address = address.upper()
    connected = local.call('connect', address=address)
    frame = local.call('screenshot', max_dimension=320)
    session = secrets.token_hex(16)
    relay.request('/v1/host/register', dict(phone_id=phone_id, session=session,
        name=connected.get('name', 'iPhone'), width=frame['screen_width'], height=frame['screen_height'],
        host_id=hashlib.sha256(socket.gethostname().encode()).hexdigest()[:32]))
    print(json.dumps({'host': 'registered', 'phone_id': phone_id}), flush=True)
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(5):
            online = True
            try:
                local.call_for(address, 'screenshot', max_dimension=320)
            except Exception:
                online = False
            try:
                relay.request('/v1/host/heartbeat', dict(phone_id=phone_id, session=session, online=online), timeout=10)
            except Exception:
                # The main connection will resolve the job or fail without replay.
                pass
    thread = threading.Thread(target=heartbeat, daemon=True); thread.start()
    try:
        while True:
            job = relay.request('/v1/host/claim', dict(phone_id=phone_id, session=session))['job']
            if not job:
                continue
            result, error = {}, None
            try:
                result = local.call_for(address, job['method'], **job['params'])
            except Exception as failure:
                error = str(failure)[:1000]
            # A lost completion response must never cause a gesture to be replayed.
            # Stop on transport failure; reconnecting creates a new session and
            # fails the old session's pending/running jobs.
            finished = relay.request('/v1/host/complete', dict(id=job['id'], session=session,
                receipt=job['receipt'], result=result, error=error))
            print(json.dumps({'job': job['id'], 'status': finished['status']}), flush=True)
    finally:
        stop.set(); thread.join(timeout=1)
        try:
            relay.request('/v1/host/unregister', dict(phone_id=phone_id, session=session), timeout=5)
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8768')
    parser.add_argument('--phone-id', required=True); parser.add_argument('--address', required=True)
    parser.add_argument('--host-token-file'); parser.add_argument('--ca-file')
    parser.add_argument('--local-url', default='http://127.0.0.1:8766'); parser.add_argument('--local-token-file')
    args = parser.parse_args()
    relay = RelayClient(args.url, args.host_token_file, host=True, ca_file=args.ca_file)
    local = Client(args.local_url, args.local_token_file)
    try:
        run(relay, local, args.phone_id, args.address)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
