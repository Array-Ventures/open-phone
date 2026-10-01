"""Original SQLite OAuth snapshot storage and Unix lifetime writer lock.

Node retains a `hold` subprocess whose stdin closes when the Node owner exits.
Short synchronous load/save subprocesses make durable commits before success.
The payload contains token hashes, not bearer/refresh secrets or the owner key.
"""
import fcntl
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

APPLICATION_ID = 0x4F504F41
MAX_BYTES = 16 * 1024 * 1024


def private_file(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('OAuth state files must be regular files owned by this user with one link')
        os.fchmod(fd, 0o600)
        return fd
    except Exception:
        os.close(fd)
        raise


def open_db(path):
    fd = private_file(path)
    os.close(fd)
    db = sqlite3.connect(str(path), timeout=2)
    try:
        application = db.execute('PRAGMA application_id').fetchone()[0]
        version = db.execute('PRAGMA user_version').fetchone()[0]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if (tables or application or version) and (application != APPLICATION_ID or version != 1 or tables != {'state'}):
            raise ValueError('Unsupported or unrelated OAuth state database')
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA secure_delete=ON')
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('PRAGMA application_id=' + str(APPLICATION_ID))
            db.execute('PRAGMA user_version=1')
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
        return db
    except Exception:
        db.close()
        raise


@contextmanager
def operation(path):
    # A new lifetime owner cannot appear between a helper's owner check and
    # its commit, even if the old lock keeper dies during that transaction.
    fd = private_file(str(path) + '.operation.lock')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def run(command, value, owner=None):
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if command == 'hold':
        fd = private_file(str(path) + '.lock')
        try:
            with operation(path):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.ftruncate(fd, 0)
                os.write(fd, str(os.getpid()).encode())
            print(json.dumps({'ready': True}), flush=True)
            # EOF also releases ownership after SIGKILL of the Node parent.
            while sys.stdin.buffer.read(1):
                pass
        finally:
            os.close(fd)
        return
    if command not in ('load', 'save'):
        raise ValueError('Unknown command')
    with operation(path):
        # Reject writes if the lifetime owner died, even while Node is blocked
        # in spawnSync and has not yet observed the child exit event.
        fd = private_file(str(path) + '.lock')
        try:
            if os.read(fd, 64).decode() != owner:
                raise ValueError('Wrong OAuth state owner')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise ValueError('OAuth state owner is no longer holding its lock')
        finally:
            os.close(fd)
        db = open_db(path)
        try:
            if command == 'load':
                row = db.execute('SELECT data FROM state WHERE id=1').fetchone()
                print(json.dumps({'snapshot': row[0] if row else None}))
            else:
                raw = sys.stdin.buffer.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('OAuth snapshot too large')
                value = json.loads(raw)
                if not isinstance(value, dict) or set(value) != {'payload', 'mac'}:
                    raise ValueError('Invalid OAuth snapshot envelope')
                encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('INSERT INTO state(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (encoded,))
                print(json.dumps({'saved': True}))
        finally:
            db.close()


if __name__ == '__main__':
    try:
        run(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    except Exception:
        # Never echo the snapshot, tokens, owner key or request input.
        print(json.dumps({'error': 'OAuth state storage unavailable or already owned'}), flush=True)
        sys.exit(1)
