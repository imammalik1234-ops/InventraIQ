"""Mutation tests use isolated SQLite; CSRF remains enabled throughout."""
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
import unittest
from datetime import date, timedelta
from unittest.mock import patch
from flask import g
from sqlalchemy.exc import IntegrityError
from app import app
from database import db
from models import User, Product, Inventory, Sale, Alert, Prediction, InventoryAudit


class SecurityTransactionTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='security-test-only')
        self.ctx = app.app_context(); self.ctx.push()
        self.assertEqual(str(db.engine.url), 'sqlite:///:memory:')
        db.create_all()
        self.expiry = (date.today()+timedelta(days=30)).isoformat()
        db.session.add_all([User(user_id=1, name='Staff', email='staff@test.invalid', password_hash='unused', role='staff'),
                            User(user_id=2, name='Admin', email='admin@test.invalid', password_hash='unused', role='admin'),
                            Product(product_id=1, name='Full Cream Milk', category='Dairy', supplier='Nestle',
                                    unit_price=3.50, expiry_date=date.fromisoformat(self.expiry), reorder_level=10)])
        db.session.flush()
        db.session.add(Inventory(product_id=1, quantity=10))
        from auth_fixtures import grant_fixture_scopes
        grant_fixture_scopes()
        db.session.commit()
        self.client = app.test_client()
        self.token = 'session-specific-token'
        self.client.environ_base['HTTP_X_CSRF_TOKEN'] = self.token
        self.payload = dict(name='Full Cream Milk', category='Dairy', supplier='Nestle', unit_price=3.50,
                            expiry_date=self.expiry, reorder_level=10, quantity=15, expected_version=1)
        self.form = dict(product='Milk', variant='Full Cream Milk', category='Dairy', supplier='Nestle',
                         unit_price='3.50', expiry_date=self.expiry, reorder_level='10', quantity='15', expected_version='1')

    def tearDown(self):
        db.session.remove(); db.drop_all(); db.engine.dispose(); self.ctx.pop()

    def login(self, role='staff'):
        g.pop('_login_user', None)
        with self.client.session_transaction() as session:
            session['_user_id'] = db.session.get(User, 2 if role == 'admin' else 1).get_id()
            session['_fresh'] = True
            session['active_branch_id'] = 1
            session['_csrf_token'] = self.token

    def stock(self):
        db.session.expire_all()
        return Inventory.query.one().quantity

    def test_csrf_required_for_all_mutations(self):
        self.login('admin')
        requests = [('post', '/products', self.payload), ('put', '/products/1', self.payload),
                     ('post', '/sales', {'product_id': 1, 'quantity': 1}),
                    ('post', '/add-product', None), ('post', '/admin-add-product', None),
                    ('post', '/edit-product/1', None), ('post', '/admin-edit-product/1', None),
                    ('post', '/delete-product/1', None), ('post', '/admin-delete-product/1', None),
                    ('post', '/remove-stock/1', None), ('post', '/admin-settings', None),
                    ('post', '/logout', None), ('post', '/check-alerts', None),
                    ('post', '/notify-manager', None), ('post', '/reassign-tasks', None),
                    ('post', '/request-replacement', None)]
        for method, path, payload in requests:
            for token in ('', 'wrong-token'):
                response = getattr(self.client, method)(path, json=payload, headers={'X-CSRF-Token': token})
                self.assertEqual(response.status_code, 400, path)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(InventoryAudit.query.count(), 0)

    def test_login_csrf_and_session_rotation(self):
        user = db.session.get(User, 1); user.set_password('test-password'); db.session.commit()
        page = self.client.get('/login/staff')
        self.assertIn('name="csrf_token"', page.get_data(as_text=True))
        credentials = {'email': 'staff@test.invalid', 'password': 'test-password'}
        self.assertEqual(self.client.post('/login/staff', data=credentials).status_code, 400)
        with self.client.session_transaction() as session: token = session['_csrf_token']
        response = self.client.post('/login/staff', data=dict(credentials, csrf_token=token), headers={'X-CSRF-Token': ''})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            from authentication import serializer
            self.assertEqual(serializer().loads(session['_user_id'])['id'], 1)
            self.assertNotIn('_csrf_token', session)

    def test_anonymous_and_role_permissions(self):
        for method, path in [('post', '/products'), ('put', '/products/1'), ('delete', '/products/1'),
                             ('post', '/sales'), ('post', '/remove-stock/1')]:
            self.assertEqual(getattr(self.client, method)(path, json=self.payload).status_code, 302)
        self.login('staff')
        for path in ['/add-product', '/edit-product/1', '/delete-product/1',
                     '/admin-add-product', '/admin-edit-product/1', '/admin-delete-product/1', '/admin-settings']:
            self.assertEqual(self.client.post(path, data=self.form).status_code, 403)
        self.assertEqual(self.stock(), 10)

    def test_form_tokens_and_gets_do_not_mutate(self):
        self.login('admin')
        for path in ['/inventory-page', '/edit-product/1', '/add-product', '/dashboard']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('name="csrf_token"', response.get_data(as_text=True))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        for path in ['/logout', '/check-alerts', '/notify-manager', '/reassign-tasks', '/request-replacement']:
            self.assertEqual(self.client.get(path).status_code, 405)
        response = self.client.post('/remove-stock/1', data={'quantity_to_remove': '2', 'expected_version': '1', 'reason': 'fixture correction', 'csrf_token': self.token},
                                    headers={'X-CSRF-Token': ''})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stock(), 8)
        audit = InventoryAudit.query.one()
        self.assertEqual((audit.actor_id, audit.product_id, audit.action), (2, 1, 'remove'))
        self.assertEqual((audit.old_value['quantity'], audit.new_value['quantity']), (10, 8))
        self.assertIsNotNone(audit.timestamp)

    def test_invalid_json_inputs(self):
        self.login('admin')
        cases = [('quantity', value) for value in [-1, True, 1.5, '2', 2147483648]]
        cases += [('unit_price', value) for value in [0, -1, True, 'NaN', 'Infinity', '1.001', '100000000']]
        cases += [('name', ''), ('name', 'x'*101), ('category', []), ('supplier', None),
                  ('expiry_date', '2026-02-30'), ('reorder_level', -1)]
        for key, value in cases:
            response = self.client.put('/products/1', json=dict(self.payload, **{key: value}))
            self.assertEqual(response.status_code, 400, (key, value))
        for payload in [None, [], 'invalid']:
            self.assertEqual(self.client.post('/products', json=payload).status_code, 400)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(InventoryAudit.query.count(), 0)

    def test_stale_edit_delete_and_remove_are_rejected(self):
        self.login('admin')
        self.assertEqual(self.client.post('/sales', json={'product_id': 1, 'quantity': 3}).status_code, 200)
        self.assertEqual(self.client.put('/products/1', json=self.payload).status_code, 409)
        self.assertEqual(self.client.delete('/products/1', json={'expected_version': 1}).status_code, 409)
        self.assertEqual(self.client.post('/remove-stock/1', data={'expected_version': '1', 'quantity_to_remove': '1', 'reason': 'fixture correction'}).status_code, 409)
        self.assertEqual(self.stock(), 7)
        self.assertEqual(InventoryAudit.query.count(), 1)
        response = self.client.put('/products/1', json=dict(self.payload, expected_version=2))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stock(), 15)
        self.assertEqual(InventoryAudit.query.order_by(InventoryAudit.audit_id.desc()).first().old_value['quantity'], 7)

    def test_revision_required_and_over_removal_rejected(self):
        self.login('admin')
        payload = dict(self.payload); payload.pop('expected_version')
        self.assertEqual(self.client.put('/products/1', json=payload).status_code, 400)
        self.assertEqual(self.client.delete('/products/1', json={}).status_code, 400)
        for qty in ['11', '-1', '1.5', '2147483648']:
            self.assertEqual(self.client.post('/remove-stock/1', data={'expected_version': '1', 'quantity_to_remove': qty, 'reason': 'fixture correction'}).status_code, 400)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(db.session.get(Product, 1).version, 1)

    def test_create_receive_and_edit_forms(self):
        self.login('staff')
        self.assertEqual(self.client.post('/edit-product/1', data=self.form).status_code, 403)
        self.assertEqual(self.client.post('/add-product', data=self.form).status_code, 403)
        self.login('admin')
        for prefix in ('', 'admin-'):
            form = dict(self.form, name=self.form['variant'], expected_version=str(db.session.get(Product, 1).version))
            self.assertEqual(self.client.post('/'+prefix+'edit-product/1', data=form).status_code, 302)
            self.assertEqual(self.client.post('/'+prefix+'add-product', data=dict(self.form, quantity='2')).status_code, 302)
        self.assertEqual(self.stock(), 17)
        self.assertEqual([row.action for row in InventoryAudit.query.order_by(InventoryAudit.audit_id)], ['edit', 'receive', 'edit', 'receive'])
        self.assertEqual(self.client.post('/products', json=dict(self.payload, name='New item')).status_code, 409)
        self.assertEqual(Inventory.query.count(), 1)

    def test_failed_commits_rollback_all_related_rows(self):
        self.login('admin')
        actions = [('post', '/sales', {'product_id': 1, 'quantity': 2}),
                   ('put', '/products/1', self.payload)]
        for method, path, payload in actions:
            with self.assertLogs('app', level='ERROR'), patch.object(db.session, 'commit', side_effect=RuntimeError('injected')):
                response = getattr(self.client, method)(path, json=payload)
            self.assertEqual(response.status_code, 500)
            self.assertEqual(self.stock(), 10)
            self.assertEqual(Product.query.count(), 1)
            self.assertEqual(db.session.get(Product, 1).version, 1)
            self.assertEqual((Sale.query.count(), Alert.query.count(), InventoryAudit.query.count()), (0, 0, 0))

    def test_audit_failure_rolls_back_stock(self):
        self.login()
        with self.assertLogs('app', level='ERROR'), patch('inventory_transactions._audit', side_effect=RuntimeError('injected')):
            response = self.client.post('/sales', json={'product_id': 1, 'quantity': 2})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(Sale.query.count(), 0)

    def test_archive_preserves_audit_and_predictions(self):
        self.login('admin')
        db.session.add(Prediction(product_id=1, predicted_demand=5)); db.session.commit()
        self.assertEqual(self.client.post('/admin-delete-product/1', data={'expected_version': '1'}).status_code, 409)
        self.assertEqual(self.client.post('/sales', json={'product_id': 1, 'quantity': 10}).status_code, 200)
        self.assertEqual(self.client.post('/admin-delete-product/1', data={'expected_version': '2'}).status_code, 302)
        self.assertEqual((Product.query.count(), Inventory.query.count(), Prediction.query.count()), (1, 1, 1))
        self.assertTrue(db.session.get(Product, 1).archived)
        audit = InventoryAudit.query.filter_by(action='archive').one()
        self.assertEqual((audit.actor_id, audit.product_id), (2, 1))
        self.assertEqual(audit.old_value['quantity'], 0)
        self.assertTrue(audit.new_value['archived'])

    def test_csrf_is_session_bound(self):
        self.login()
        other = app.test_client()
        with other.session_transaction() as session:
            session['_user_id'] = db.session.get(User, 1).get_id()
            session['_csrf_token'] = 'a-different-session-token'
        response = other.post('/sales', json={'product_id': 1, 'quantity': 1}, headers={'X-CSRF-Token': self.token})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stock(), 10)

    def test_form_validation_and_creation_rollback(self):
        self.login('admin')
        for key, value in [('quantity', '1.5'), ('quantity', '-1'), ('unit_price', 'NaN'),
                           ('unit_price', '0'), ('unit_price', '3.501'), ('reorder_level', '2147483648')]:
            response = self.client.post('/add-product', data=dict(self.form, **{key: value}))
            self.assertEqual(response.status_code, 400, (key, value))
        new_batch = dict(self.form, expiry_date=(date.today()+timedelta(days=31)).isoformat())
        response = self.client.post('/add-product', data=new_batch)
        self.assertEqual(response.status_code, 409)
        self.assertEqual((Product.query.count(), Inventory.query.count(), InventoryAudit.query.count()), (1, 1, 0))
        self.assertEqual(self.stock(), 10)

    def test_database_constraints(self):
        with self.assertRaises(IntegrityError):
            Inventory.query.update({'quantity': -1})
        db.session.rollback()
        db.session.add(Inventory(product_id=1, quantity=2))
        with self.assertRaises(IntegrityError): db.session.commit()
        db.session.rollback()
        self.assertEqual(self.stock(), 10)
