from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
from openphone import export as export_source


class ExportTests(unittest.TestCase):
    def test_runtime_database_is_excluded_even_with_a_source_file_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'open-phone';root.mkdir()
            (root/'original.py').write_text('print("original source")\n')
            with closing(sqlite3.connect(root/'runtime.json')) as db, db:
                db.execute('CREATE TABLE fixture(value TEXT)');db.execute("INSERT INTO fixture VALUES('private runtime fixture')")
            out=Path(tmp)/'source.tar.gz'
            with patch.object(export_source,'ROOT',root):meta=export_source.export(out)
            with tarfile.open(out) as archive:
                self.assertEqual(archive.getnames(),['open-phone/original.py'])
            self.assertEqual(meta['files'],1)


if __name__=='__main__':unittest.main()
