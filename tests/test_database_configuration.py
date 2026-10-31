"""T01 uses only disposable SQLite or non-connecting MySQL URL objects."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import sys
import subprocess
import unittest
from unittest.mock import patch
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import NullPool
from configuration import application_config, ConfigurationError
from database import db, enforce_sqlite_foreign_keys
import models
from readiness import database_readiness, register_readiness


class ConfigurationTests(unittest.TestCase):
    def test_missing_and_malformed_configuration_is_redacted(self):
        cases = [{}, {'DATABASE_URL': ''}, {'DATABASE_URL': 'private-not-a-url'},
                 {'DATABASE_URL': 'mysql+pymysql://name:private@host:bad/db'},
                 {'DATABASE_URL': 'sqlite://hostname/db'}, {'DATABASE_URL': 'postgresql://host/db'},
                 {'DATABASE_URL': 'mysql+pymysql://host/db'},
                 {'DATABASE_URL': 'sqlite:///:memory:', 'APP_ENV': 'unknown'},
                 {'DATABASE_URL': 'sqlite:///:memory:', 'SESSION_COOKIE_SECURE': 'perhaps'}]
        for env in cases:
            with self.subTest(keys=sorted(env)):
                with self.assertRaises(ConfigurationError) as error:
                    application_config(env)
                self.assertNotIn('private', str(error.exception))
                self.assertNotIn('name:', str(error.exception))

    def test_mysql_credentials_are_not_interpolated(self):
        password = 'synthetic:@/#?% password'
        env = dict(DB_USER='synthetic@user', DB_PASSWORD=password, DB_HOST='127.0.0.1', DB_NAME='fixture')
        url = application_config(env)['SQLALCHEMY_DATABASE_URI']
        self.assertEqual(url.password, password)
        self.assertEqual(url.username, env['DB_USER'])
        self.assertEqual(url.host, '127.0.0.1')
        for port in ('bad', '0', '65536'):
            with self.assertRaises(ConfigurationError):
                application_config(dict(env, DB_PORT=port))

    def test_database_url_precedence_never_falls_back(self):
        config = application_config(dict(DATABASE_URL='sqlite:///:memory:', DB_HOST='not-used'))
        self.assertEqual(config['SQLALCHEMY_DATABASE_URI'].database, ':memory:')
        with self.assertRaises(ConfigurationError):
            application_config(dict(DATABASE_URL='', DB_USER='unused', DB_PASSWORD='unused',
                                    DB_HOST='127.0.0.1', DB_NAME='unused'))

    def test_startup_driver_option_errors_do_not_expose_values(self):
        environment = dict(os.environ, APP_ENV='testing', PYTHON_DOTENV_DISABLED='1',
                           DATABASE_URL='sqlite:///:memory:?timeout=private-t01-option',
                           PYTHONDONTWRITEBYTECODE='1', FLASK_DEBUG='false')
        result = subprocess.run([sys.executable, '-c', 'import app'], env=environment,
                                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                text=True, encoding='utf-8', timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Database driver/settings could not be initialized', result.stderr)
        self.assertNotIn('private-t01-option', result.stderr)

    def test_development_key_and_legacy_boolean_format(self):
        config = application_config({'DATABASE_URL': 'sqlite:///:memory:', 'FLASK_DEBUG': '0'})
        self.assertTrue(config['SECRET_KEY'])
        self.assertFalse(config['SESSION_COOKIE_SECURE'])

    def test_production_requires_secret_https_and_database_tls(self):
        with tempfile.TemporaryDirectory(prefix='inventraiq-config-') as directory:
            ca = Path(directory) / 'fixture-ca.pem'
            ca.write_text('not-a-certificate; configuration test only', encoding='utf-8')
            env = dict(APP_ENV='production', DATABASE_URL='mysql+pymysql://synthetic:unused@localhost/fixture',
                       SECRET_KEY='synthetic-production-test-key-32-characters', DB_SSL_CA=str(ca))
            config = application_config(env)
            self.assertEqual(config['SECRET_KEY'], env['SECRET_KEY'])
            self.assertEqual(application_config(env)['SECRET_KEY'], config['SECRET_KEY'])
            self.assertTrue(config['SESSION_COOKIE_SECURE'])
            self.assertTrue(config['HTTPS_REQUIRED'])
            connect = config['SQLALCHEMY_ENGINE_OPTIONS']['connect_args']
            self.assertTrue(connect['ssl_verify_identity'])
            self.assertTrue(connect['ssl_verify_cert'])
            for changes in ({'SECRET_KEY': ''}, {'SECRET_KEY': 'short'}, {'SESSION_COOKIE_SECURE': 'false'},
                            {'HTTPS_REQUIRED': 'false'}, {'FLASK_DEBUG': 'true'}, {'DB_SSL_CA': ''},
                            {'DATABASE_URL': 'sqlite:///:memory:'},
                            {'DATABASE_URL': env['DATABASE_URL'] + '?ssl_disabled=true'}):
                with self.subTest(keys=sorted(changes)), self.assertRaises(ConfigurationError):
                    application_config(dict(env, **changes))


class DatabaseIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='inventraiq-db-test-')
        self.path = Path(self.directory.name) / 'fixture.db'
        self.engine = create_engine('sqlite:///' + self.path.as_posix(), poolclass=NullPool)
        db.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_foreign_keys_enabled_across_connections_and_recreation(self):
        for _ in range(3):
            with self.engine.connect() as first, self.engine.connect() as second:
                for connection in (first, second):
                    self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one(), 1)
            self.engine.dispose()

    def test_orphans_rejected_for_every_product_reference(self):
        for table, values in [('inventory', dict(product_id=999, quantity=1)),
                              ('sales', dict(product_id=999, quantity=1)),
                              ('alerts', dict(product_id=999)), ('predictions', dict(product_id=999))]:
            with self.subTest(table=table), self.assertRaises(IntegrityError):
                with self.engine.begin() as connection:
                    connection.execute(db.metadata.tables[table].insert().values(**values))

    @unittest.skipUnless(sys.version_info >= (3, 12), 'Requires sqlite3 autocommit parameter')
    def test_nonlegacy_autocommit_connection_preserves_mode_and_enforces_fks(self):
        engine = create_engine('sqlite:///' + self.path.as_posix(), connect_args={'autocommit': False})
        try:
            with engine.connect() as connection:
                self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one(), 1)
                self.assertFalse(connection.connection.driver_connection.autocommit)
            with self.assertRaises(IntegrityError), engine.begin() as connection:
                connection.execute(models.Inventory.__table__.insert().values(product_id=999, quantity=1))
        finally:
            engine.dispose()

    def test_parent_delete_is_restricted(self):
        with self.engine.begin() as connection:
            connection.execute(models.Product.__table__.insert().values(product_id=1, name='Fixture'))
            connection.execute(models.Inventory.__table__.insert().values(product_id=1, quantity=2))
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(models.Product.__table__.delete())

    def test_latest_schema_readiness_is_read_only(self):
        before = self.path.read_bytes()
        # SQLite mode=ro protects the actual file independently of app settings.
        report = database_readiness(self.engine, db.metadata)
        self.assertEqual(report['status'], 'ready', report)
        self.assertEqual(report['foreign_keys'], 1)
        self.assertEqual(self.path.read_bytes(), before)
        with self.engine.begin() as connection:
            connection.execute(models.Product.__table__.insert().values(name='Writable afterward'))

    def test_missing_file_is_not_created(self):
        missing = self.path.parent / 'absent.db'
        engine = create_engine('sqlite:///' + missing.as_posix())
        try:
            self.assertEqual(database_readiness(engine, db.metadata)['status'], 'missing_database')
            self.assertFalse(missing.exists())
        finally:
            engine.dispose()

    def test_missing_schema_is_reported_without_creation(self):
        empty = self.path.parent / 'empty.db'
        with closing(sqlite3.connect(empty)) as connection:
            enforce_sqlite_foreign_keys(connection)
        before = empty.read_bytes()
        engine = create_engine('sqlite:///' + empty.as_posix())
        try:
            report = database_readiness(engine, db.metadata)
            self.assertEqual(report['status'], 'incompatible')
            self.assertIn('users', report['missing_tables'])
            self.assertEqual(empty.read_bytes(), before)
        finally:
            engine.dispose()

    def test_legacy_columns_and_constraints_are_diagnosed(self):
        legacy = self.path.parent / 'legacy.db'
        with closing(sqlite3.connect(legacy)) as connection:
            enforce_sqlite_foreign_keys(connection)
            connection.execute('CREATE TABLE inventory (inventory_id INTEGER PRIMARY KEY, product_id INTEGER, quantity INTEGER)')
            connection.commit()
        engine = create_engine('sqlite:///' + legacy.as_posix())
        try:
            gaps = database_readiness(engine, db.metadata)['schema_gaps']['inventory']
            self.assertIn('missing_column:last_updated', gaps)
            self.assertIn('nullable_column:quantity', gaps)
            self.assertIn('missing_unique:product_id', gaps)
            self.assertIn('missing_foreign_key:product_id', gaps)
            self.assertIn('missing_check:ck_inventory_nonnegative', gaps)
        finally:
            engine.dispose()

    def test_unavailable_diagnostic_is_generic_no_fallback(self):
        with patch.object(self.engine, 'connect', side_effect=AssertionError('File check must use separate read-only engine')):
            self.assertEqual(database_readiness(self.engine, db.metadata)['status'], 'ready')
        memory = create_engine('sqlite:///:memory:')
        try:
            with patch.object(memory, 'connect', side_effect=OperationalError('private-query', {}, Exception('private-secret'))):
                report = database_readiness(memory, db.metadata)
            self.assertEqual(report['status'], 'unavailable')
            self.assertNotIn('private', json.dumps(report))
            self.assertIn('no fallback', report['message'])
        finally:
            memory.dispose()

    def test_mysql_requires_explicit_read_only_opt_in(self):
        engine = create_engine('mysql+pymysql://synthetic:unused@127.0.0.1/not-connected')
        try:
            with patch.object(engine, 'connect', side_effect=AssertionError('Must not connect')):
                report = database_readiness(engine, db.metadata)
            self.assertEqual(report['status'], 'not_checked')
        finally:
            engine.dispose()

    def test_readiness_cli_exit_codes_and_memory_connection_restored(self):
        app = Flask('readiness-test')
        app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        db.init_app(app)
        register_readiness(app)
        try:
            response = app.test_cli_runner().invoke(args=['db-readiness'])
            self.assertEqual(response.exit_code, 1, response.output)
            self.assertIn('missing_tables', response.output)
            with app.app_context():
                db.create_all()  # Check did not leave the pooled connection read-only.
            response = app.test_cli_runner().invoke(args=['db-readiness'])
            self.assertEqual(response.exit_code, 0, response.output)
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_application_connections_and_https_guard(self):
        from app import app
        with app.app_context():
            self.assertEqual(db.session.execute(text('PRAGMA foreign_keys')).scalar_one(), 1)
        with patch.dict(app.config, {'HTTPS_REQUIRED': True}):
            client = app.test_client()
            self.assertEqual(client.get('/login/admin').status_code, 400)
            self.assertEqual(client.get('/login/admin', headers={'X-Forwarded-Proto': 'https'}).status_code, 400)
            self.assertEqual(client.get('/login/admin', base_url='https://localhost').status_code, 200)
