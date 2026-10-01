"""Original single-writer SQLite relay ledger. No live host credentials persist.

Phone/app metadata and bounded job results survive restart; unfinished jobs are
restored as failed by RelayStore, never as work to execute. Commit precedes job
acceptance, claim and completion. SQLite's synchronous DELETE journal is used.
"""
import fcntl
import json
import os
from pathlib import Path
import sqlite3

APPLICATION_ID = 0x4F50484E


class RelayState:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = None; self.db = None
        self.encoded = {'phones': {}, 'jobs': {}}; self.images = {}
        try:
            self.lock = self._file(str(self.path) + '.lock')
            try: fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise RuntimeError('Another relay owns this state file') from None
            fd = self._file(self.path); os.close(fd)
            self.db = sqlite3.connect(str(self.path), timeout=5, check_same_thread=False)
            application = self.db.execute('PRAGMA application_id').fetchone()[0]
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if (tables and (application != APPLICATION_ID or version != 1 or tables != {'phones', 'jobs', 'images'})) or version > 1:
                raise ValueError('Unsupported or unrelated relay state database')
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA secure_delete=ON')
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('PRAGMA application_id=' + str(APPLICATION_ID))
                self.db.execute('PRAGMA user_version=1')
                for name in ('phones', 'jobs'):
                    self.db.execute(f'CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, data BLOB NOT NULL, mime TEXT NOT NULL)')
        except Exception:
            self.close(); raise

    @staticmethod
    def _file(path):
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_uid != os.getuid(): raise ValueError('Relay state files must belong to this user')
            os.fchmod(fd, 0o600)
            return fd
        except Exception: os.close(fd); raise

    def load(self):
        data = {}
        for table in ('phones', 'jobs'):
            self.encoded[table] = dict(self.db.execute(f'SELECT id, data FROM {table}'))
            data[table] = {identifier: json.loads(value) for identifier, value in self.encoded[table].items()}
        self.images = {identifier: (bytes(raw), mime) for identifier, raw, mime in self.db.execute('SELECT id, data, mime FROM images')}
        data['images'] = dict(self.images)
        return data

    def save(self, phones, jobs, images):
        encoded = {table: {key: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                           for key, value in entries.items()}
                   for table, entries in (('phones', phones), ('jobs', jobs))}
        changes = any(encoded[table] != self.encoded[table] for table in encoded)
        changes = changes or images.keys() != self.images.keys() or any(value is not self.images.get(key) for key, value in images.items())
        if not changes: return
        with self.db:
            for table in ('phones', 'jobs'):
                for key in self.encoded[table].keys() - encoded[table].keys():
                    self.db.execute(f'DELETE FROM {table} WHERE id=?', (key,))
                for key, value in encoded[table].items():
                    if self.encoded[table].get(key) != value:
                        self.db.execute(f'INSERT INTO {table}(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (key, value))
            for key in self.images.keys() - images.keys():
                self.db.execute('DELETE FROM images WHERE id=?', (key,))
            for key, (raw, mime) in images.items():
                if images[key] is not self.images.get(key):
                    self.db.execute('INSERT INTO images(id,data,mime) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,mime=excluded.mime', (key, raw, mime))
        # Update comparison caches only after the transaction actually commits.
        self.encoded = encoded; self.images = dict(images)

    def close(self):
        if self.db is not None: self.db.close(); self.db = None
        if self.lock is not None: os.close(self.lock); self.lock = None
