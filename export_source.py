#!/usr/bin/env python3
"""Export original source/docs, excluding helpers, vendor artifacts and secrets."""
import argparse
import gzip
import hashlib
import io
import os
import pathlib
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent
SKIP = {'node_modules', 'build', 'private', 'artifacts', '__pycache__', '.git', '.venv'}
SUFFIXES = {'.py', '.m', '.swift', '.mjs', '.js', '.html', '.css', '.json', '.plist', '.md'}


def export(destination):
    entries = []
    for directory, dirs, names in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in SKIP and not name.startswith('.')]
        for name in names:
            path = pathlib.Path(directory)/name
            if (name.startswith('.') and name != '.gitignore') or (path.suffix not in SUFFIXES and name not in ('LICENSE', '.gitignore', 'requirements-video.txt')):
                continue
            if path.is_symlink():
                raise ValueError('Source export refuses symlinks')
            with path.open('rb') as source:
                if source.read(16) == b'SQLite format 3\x00':
                    continue  # Runtime state stays private even with a source-like suffix.
            entries.append((path, 'open-phone/'+path.relative_to(ROOT).as_posix()))
    keys = [path.read_bytes().strip() for path in ROOT.iterdir() if path.name.startswith('.') and path.name.endswith('token') and path.is_file()]
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        for path, name in sorted(entries, key=lambda entry: entry[1]):
            content = path.read_bytes()
            private_key = any(line.startswith(b'-----BEGIN ') and b'PRIVATE KEY' in line for line in content.splitlines())
            if any(key and key in content for key in keys) or private_key:
                raise ValueError(f'Credential/private key detected in {name}; source export stopped')
            info = tarfile.TarInfo(name); info.size = len(content); info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('wb') as output:
        with gzip.GzipFile(filename='', fileobj=output, mode='wb', mtime=0) as compressed:
            compressed.write(data.getvalue())
    return {'path': str(destination), 'files': len(entries), 'bytes': destination.stat().st_size,
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=pathlib.Path, default=ROOT.parent/'open-phone-source.tar.gz')
    print(export(parser.parse_args().out))
