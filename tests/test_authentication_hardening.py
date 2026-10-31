"""T02 auth tests: disposable schemas; no existing configuration/database access."""
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
import json
import multiprocessing
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
from werkzeug.security import generate_password_hash
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import OperationalError
from database import db
from models import User, LoginThrottle
from authentication import consume_login_attempt, LoginThrottled, AuthenticationUnavailable, session_token
from configuration import application_config, ConfigurationError


def consume_in_worker(path):
    """Independent processes/engines, same persistent counters and stable secret."""
    from flask import Flask
    app = Flask('throttle-process')
    app.config.update(application_config({'DATABASE_URL': 'sqlite:///' + path,
        'SECRET_KEY': 't02-shared-synthetic-secret-for-workers', 'AUTH_ACCOUNT_LIMIT': '2', 'AUTH_IP_LIMIT': '100'}))
    db.init_app(app)
    try:
        with app.app_context():
            try:
                consume_login_attempt('worker@example.invalid', '192.0.2.1')
                return 'allowed'
            except LoginThrottled:
                return 'blocked'
    finally:
        with app.app_context():
            db.session.remove(); db.engine.dispose()


class AuthenticationHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = 't02-only-synthetic-password'
        cls.password_hash = generate_password_hash(cls.password)

    def setUp(self):
        from app import app
        self.app = app
        self.previous_config = dict(app.config)
        app.config.update(TESTING=True, SECRET_KEY='t02-only-test-session-secret', AUTH_THROTTLE_BACKEND='database',
                          AUTH_ACCOUNT_LIMIT=10, AUTH_IP_LIMIT=50, AUTH_THROTTLE_WINDOW_SECONDS=900,
                          AUTH_THROTTLE_SLOTS=16384, AUTH_ABSOLUTE_SECONDS=28800,
                          PERMANENT_SESSION_LIFETIME=timedelta(minutes=30), HTTPS_REQUIRED=False)
        with app.app_context():
            self.assertEqual(str(db.engine.url), 'sqlite:///:memory:')
            db.create_all()
            db.session.add_all([User(user_id=1, name='Admin', email='admin@test.invalid', password_hash=self.password_hash, role='admin'),
                                User(user_id=2, name='Staff', email='staff@test.invalid', password_hash=self.password_hash, role='staff')])
            from auth_fixtures import grant_fixture_scopes
            grant_fixture_scopes()
            db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove(); db.drop_all(); db.engine.dispose()
        self.app.config.clear(); self.app.config.update(self.previous_config)

    def csrf(self, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            return session.get('_csrf_token', '')

    def login(self, email='admin@test.invalid', password=None, role='admin', client=None, address=None):
        client = client or self.client
        client.get('/login/' + role)
        return client.post('/login/' + role, data={'email': email, 'password': password or self.password,
                           'csrf_token': self.csrf(client)}, environ_overrides={'REMOTE_ADDR': address or '192.0.2.1'})

    def save_profile(self, name='Admin', email='admin@test.invalid'):
        self.client.get('/admin-settings')
        return self.client.post('/admin-settings', data={'action': 'update_profile', 'name': name,
                                'email': email, 'csrf_token': self.csrf()})

    def stolen_client(self, value):
        client = self.app.test_client(); client.set_cookie('session', value)
        return client

    def test_disabled_account_and_reenable_cannot_restore_old_session(self):
        self.assertEqual(self.login().status_code, 302)
        cookie = self.client.get_cookie('session').value
        with self.app.app_context():
            user = db.session.get(User, 1); user.enabled = False; db.session.commit()
        self.assertEqual(self.client.get('/admin-dashboard').status_code, 302)
        self.assertEqual(self.stolen_client(cookie).post('/sales', json={'product_id': 1, 'quantity': 1}).status_code, 302)
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Invalid admin credentials.', response.get_data(as_text=True))
        with self.app.app_context():
            user = db.session.get(User, 1); user.enabled = True; db.session.commit()
        self.assertEqual(self.stolen_client(cookie).get('/admin-dashboard').status_code, 302)
        self.assertEqual(self.login().status_code, 302)

    def test_explicit_revocation_and_logout_reject_replayed_cookies(self):
        self.login(); cookie = self.client.get_cookie('session').value
        with self.app.app_context():
            db.session.get(User, 1).revoke_sessions(); db.session.commit()
        self.assertEqual(self.stolen_client(cookie).get('/admin-dashboard').status_code, 302)
        self.login(); self.client.get('/admin-dashboard')
        with self.client.session_transaction() as state: state['active_branch_id'] = 1
        cookie = self.client.get_cookie('session').value
        response = self.client.post('/logout', data={'csrf_token': self.csrf()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stolen_client(cookie).get('/admin-dashboard').status_code, 302)

    def test_password_and_role_changes_revoke_existing_tokens(self):
        for field in ('password', 'role'):
            self.login(); cookie = self.client.get_cookie('session').value
            with self.app.app_context():
                user = db.session.get(User, 1)
                if field == 'password': user.set_password('changed-synthetic-password')
                else: user.role = 'staff'
                db.session.commit()
            self.assertEqual(self.stolen_client(cookie).get('/admin-dashboard').status_code, 302)
            with self.app.app_context():
                user = db.session.get(User, 1); user.password_hash = self.password_hash; user.role = 'admin'; db.session.commit()

    def test_email_change_revokes_other_sessions_and_requires_login(self):
        self.login(); cookie = self.client.get_cookie('session').value
        response = self.save_profile(email='new-admin@test.invalid')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login/admin'))
        self.assertEqual(self.stolen_client(cookie).get('/admin-dashboard').status_code, 302)
        self.assertEqual(self.login(email='new-admin@test.invalid').status_code, 302)

    def test_invalid_profile_fields_never_persist(self):
        self.login()
        cases = [('', 'admin@test.invalid'), ('x'*101, 'admin@test.invalid'), ('Bad\nName', 'admin@test.invalid'),
                 ('Admin', ''), ('Admin', 'bad'), ('Admin', 'a..b@example.invalid'),
                 ('Admin', 'a@-bad.invalid'), ('Admin', 'a@domain'), ('Admin', 'a\r\n@example.invalid'),
                 ('Admin', 'x'*90+'@example.invalid')]
        for name, email in cases:
            with self.subTest(case=cases.index((name,email))):
                self.assertEqual(self.save_profile(name, email).status_code, 400)
                with self.app.app_context():
                    user = db.session.get(User, 1)
                    self.assertEqual((user.name, user.email), ('Admin', 'admin@test.invalid'))

    def test_valid_max_length_profile_and_duplicate_email(self):
        self.login()
        self.assertEqual(self.save_profile(name='N'*100).status_code, 200)
        self.assertEqual(self.save_profile(email='staff@test.invalid').status_code, 400)
        self.assertEqual(self.client.get('/admin-dashboard').status_code, 200)

    def test_profile_email_accepts_model_boundary(self):
        self.login()
        email = 'a'*64 + '@' + 'b'*27 + '.invalid'
        self.assertEqual(len(email), 100)
        self.assertEqual(self.save_profile(email=email).status_code, 302)
        self.assertEqual(self.login(email=email).status_code, 302)

    def test_idle_session_expiration(self):
        self.login()
        now = time.time()
        with patch('itsdangerous.timed.time.time', return_value=now + 1801):
            self.assertEqual(self.client.get('/admin-dashboard').status_code, 302)

    def test_absolute_expiration_even_with_a_fresh_outer_cookie(self):
        with self.app.app_context():
            with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=int(time.time()) - 28801):
                expired = session_token(db.session.get(User, 1))
        with self.client.session_transaction() as session:
            session['_user_id'] = expired; session['_permanent'] = True
        self.assertEqual(self.client.get('/admin-dashboard').status_code, 302)

    def test_csrf_rotation_tampered_and_legacy_id_rejection(self):
        self.client.get('/login/admin'); old = self.csrf()
        self.assertEqual(self.client.post('/login/admin', data={'email':'admin@test.invalid','password':self.password}).status_code, 400)
        with self.app.app_context(): self.assertEqual(LoginThrottle.query.count(), 0)
        self.assertEqual(self.login().status_code, 302)
        self.client.get('/admin-dashboard'); self.assertNotEqual(self.csrf(), old)
        self.assertEqual(self.client.post('/logout', data={'csrf_token': old}).status_code, 400)
        for token in ('1', 'tampered.session.token'):
            client = self.app.test_client()
            with client.session_transaction() as session: session['_user_id'] = token
            self.assertEqual(client.get('/admin-dashboard').status_code, 302)

    def test_account_throttle_across_addresses_and_login_roles(self):
        self.app.config['AUTH_ACCOUNT_LIMIT'] = 2
        self.assertEqual(self.login(password='wrong', address='192.0.2.2').status_code, 200)
        self.assertEqual(self.login(password='wrong', role='staff', address='192.0.2.3').status_code, 200)
        response = self.login(address='192.0.2.4')
        self.assertEqual(response.status_code, 429)
        self.assertTrue(1 <= int(response.headers['Retry-After']) <= 900)
        with self.app.app_context():
            db.session.execute(update(LoginThrottle).values(window_start=LoginThrottle.window_start - 900)); db.session.commit()
        self.assertEqual(self.login().status_code, 302)

    def test_address_throttle_unknown_accounts_and_success_not_reset(self):
        self.app.config['AUTH_IP_LIMIT'] = 2
        self.assertEqual(self.login().status_code, 302)
        other = self.app.test_client()
        self.assertEqual(self.login(email='absent@test.invalid', client=other).status_code, 200)
        self.assertEqual(self.login(email='another@test.invalid', client=other).status_code, 429)

    def test_login_failures_do_not_reveal_account_or_disabled_state(self):
        with self.app.app_context():
            user = db.session.get(User, 1); user.enabled=False; db.session.commit()
        for email in ('admin@test.invalid', 'absent@test.invalid', 'staff@test.invalid'):
            response = self.login(email=email)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Invalid admin credentials.', response.get_data(as_text=True))

    def test_malformed_stored_hash_does_not_disclose_account_through_an_error(self):
        with self.app.app_context():
            user = db.session.get(User, 1); user.password_hash = 'unsupported:algorithm$salt$hash'; db.session.commit()
        with self.assertLogs('app', level='ERROR'):
            response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Invalid admin credentials.', response.get_data(as_text=True))

    def test_missing_throttle_table_fails_closed(self):
        with self.app.app_context(): LoginThrottle.__table__.drop(db.engine)
        with self.assertLogs('app', level='ERROR'):
            response = self.login()
        self.assertEqual(response.status_code, 503)
        self.assertIn('temporarily unavailable', response.get_data(as_text=True))
        self.assertEqual(self.client.get('/admin-dashboard').status_code, 302)

    def test_database_outage_and_logs_are_sanitized_and_correlated(self):
        error = OperationalError('SELECT secret-password', {'password':'secret-password'}, Exception('mysql://user:secret-password@private/db'))
        with self.assertLogs('app', level='ERROR') as records, patch('app.consume_login_attempt', side_effect=error):
            response = self.login()
        self.assertEqual(response.status_code, 503)
        reference = response.headers['X-Request-ID']
        self.assertRegex(reference, r'^[a-f0-9]{32}$')
        self.assertIn(reference, response.get_data(as_text=True))
        for value in ('secret-password', 'mysql://', self.password, 'admin@test.invalid'):
            self.assertNotIn(value, response.get_data(as_text=True) + '\n'.join(records.output))
        payload = json.loads(records.records[-1].getMessage())
        self.assertEqual(payload['event'], 'database_unavailable')
        self.assertEqual(payload['correlation_id'], reference)

    def test_authenticated_database_outage_is_503_not_anonymous_or_traceback(self):
        self.login()
        error = OperationalError('private-query', {}, Exception('private-connection'))
        with self.assertLogs('app', level='ERROR'), patch('authentication.db.session.get', side_effect=error):
            response = self.client.get('/admin-dashboard')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private-', response.get_data(as_text=True))

    def test_write_database_outage_is_unavailable_and_logs_no_sql_parameters(self):
        self.login(); self.client.get('/admin-dashboard')
        with self.client.session_transaction() as state: state['active_branch_id'] = 1
        error = OperationalError('secret-query', {'password':'secret-param'}, Exception('secret-driver-message'))
        with self.assertLogs('app', level='ERROR') as records, patch('inventory_transactions.deduct_stock', side_effect=error):
            response = self.client.post('/sales', json={'product_id':1, 'quantity':1}, headers={'X-CSRF-Token':self.csrf()})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret-', response.get_data(as_text=True) + '\n'.join(records.output))

    def test_transaction_exception_log_omits_payload_and_exception_message(self):
        self.login()
        with self.assertLogs('app', level='ERROR') as records, patch('app.inventory_summaries', side_effect=RuntimeError('private-runtime-secret')):
            response = self.client.get('/admin-dashboard', headers={'X-Request-ID':'private-caller-secret'})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('private-', '\n'.join(records.output) + response.get_data(as_text=True))
        self.assertNotEqual(response.headers['X-Request-ID'], 'private-caller-secret')


class SharedThrottleTests(unittest.TestCase):
    def test_nontransactional_mysql_throttle_fails_closed_without_connecting(self):
        from flask import Flask
        app = Flask('mysql-throttle-guard')
        app.config.update(application_config({'DATABASE_URL':'sqlite:///:memory:'}))
        for engine_name, autocommit in [('MyISAM', 0), ('InnoDB', 1)]:
            engine = MagicMock()
            engine.dialect.name = 'mysql'
            connection = engine.begin.return_value.__enter__.return_value
            connection.exec_driver_sql.return_value.scalar_one_or_none.return_value = engine_name
            connection.exec_driver_sql.return_value.scalar_one.return_value = autocommit
            with app.app_context(), patch('authentication.db') as extension:
                extension.engine = engine
                with self.assertRaises(AuthenticationUnavailable):
                    consume_login_attempt('unknown@test.invalid', '192.0.2.1')
            connection.execute.assert_not_called()

    def test_separate_worker_processes_share_atomic_limits(self):
        with tempfile.TemporaryDirectory(prefix='inventraiq-throttle-workers-') as directory:
            path = (Path(directory) / 'throttle.db').as_posix()
            engine = create_engine('sqlite:///' + path)
            try: LoginThrottle.__table__.create(engine)
            finally: engine.dispose()
            with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context('spawn')) as executor:
                results = list(executor.map(consume_in_worker, [path]*6))
            self.assertEqual(results.count('allowed'), 2, results)
            self.assertEqual(results.count('blocked'), 4, results)
            engine = create_engine('sqlite:///' + path)
            try:
                with engine.connect() as connection:
                    rows = connection.execute(select(LoginThrottle)).all()
                    self.assertEqual(len(rows), 2)
                    self.assertTrue(all(0 <= row.slot < 16384 for row in rows))
            finally: engine.dispose()

    def test_invalid_or_disabled_throttle_configuration_rejected(self):
        for changes in ({'AUTH_THROTTLE_BACKEND':'memory'}, {'AUTH_ACCOUNT_LIMIT':'0'},
                        {'AUTH_IP_LIMIT':'bad'}, {'AUTH_ABSOLUTE_SECONDS':'60'},
                        {'AUTH_THROTTLE_SLOTS':'999999999'}):
            with self.subTest(keys=sorted(changes)), self.assertRaises(ConfigurationError):
                application_config(dict(DATABASE_URL='sqlite:///:memory:', **changes))
