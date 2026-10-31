"""The supported local launcher must not inherit a broken/existing database."""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from database import enforce_sqlite_foreign_keys


class DemoLoginTests(unittest.TestCase):
    def test_disposable_launcher_login_dashboard_and_existing_database_untouched(self):
        with tempfile.TemporaryDirectory(prefix='inventraiq-login-regression-') as directory:
            path = Path(directory) / 'existing.db'
            with closing(sqlite3.connect(path)) as connection:
                enforce_sqlite_foreign_keys(connection)
                connection.execute('CREATE TABLE preserve_me (value INTEGER)')
                connection.execute('INSERT INTO preserve_me VALUES (42)')
                connection.commit()
            original = path.read_bytes()
            environment = dict(os.environ, DATABASE_URL='sqlite:///' + path.as_posix(),
                               PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
            result = subprocess.run([sys.executable, 'scripts/run_demo.py', '--check'],
                                    cwd=Path(__file__).resolve().parents[1], env=environment,
                                    capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['login'], 302)
            self.assertEqual(report['protected_dashboard'], 200)
            self.assertEqual(report['csrf_rejection'], 400)
            self.assertEqual(report['database'], 'disposable SQLite')
            self.assertEqual(path.read_bytes(), original)
