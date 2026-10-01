"""Serialized RPC and lifecycle for the native macOS helper processes."""
import collections
import json
import subprocess
import sys
import threading
import time


class NativeRPC:
    def __init__(self, executable):
        self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=sys.stderr, text=True, bufsize=1)
        self.lock = threading.Lock()
        self.pending = {}
        self.condition = threading.Condition()
        self.counter = 0
        self.events = collections.deque(maxlen=100)
        self.ready = threading.Event()
        self.dead = False
        threading.Thread(target=self._read, daemon=True).start()
        if not self.ready.wait(10):
            self.close(); raise RuntimeError('Native helper did not start')

    def _read(self):
        try:
            for line in self.process.stdout:
                try: value = json.loads(line)
                except ValueError: continue
                with self.condition:
                    if 'event' in value:
                        self.events.append(value)
                        if value['event'] in ('ready', 'fatal'): self.ready.set()
                    elif value.get('id') in self.pending:
                        self.pending[value['id']] = value
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.dead = True; self.condition.notify_all()

    def call(self, method, timeout=20, **params):
        # Serialize requests: each helper owns one capture session or HID service.
        with self.lock:
            with self.condition:
                if self.dead: raise RuntimeError('Native helper exited')
                self.counter += 1; identifier = self.counter
                self.pending[identifier] = None
            try:
                self.process.stdin.write(json.dumps(dict(params, id=identifier, method=method))+'\n')
                self.process.stdin.flush()
                deadline = time.monotonic()+timeout
                with self.condition:
                    while self.pending[identifier] is None:
                        if self.dead: raise RuntimeError('Native helper exited during request')
                        left = deadline-time.monotonic()
                        if left <= 0: raise TimeoutError(f'{method} timed out')
                        self.condition.wait(left)
                    value = self.pending[identifier]
                if not value.get('ok'): raise RuntimeError(value.get('error', f'{method} failed: {value}'))
                value.pop('id', None)
                return value
            finally:
                with self.condition: self.pending.pop(identifier, None)

    def close(self):
        if self.process.poll() is None:
            try: self.process.stdin.close(); self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.process.stdout.close()
