"""T03 removal contracts against disposable in-memory SQLite only."""
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
import csv
import io
import unittest
from datetime import date, timedelta
from unittest.mock import patch
from flask import g
from sqlalchemy import func
from app import app
from database import db
from models import User, Product, Inventory, Sale, InventoryAudit, Alert, Prediction


class ProductArchivingTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='t03-disposable-tests')
        self.ctx = app.app_context(); self.ctx.push()
        self.assertEqual(str(db.engine.url), 'sqlite:///:memory:')
        db.create_all()
        db.session.add_all([User(user_id=i, email=role+'@test.invalid', password_hash='unused', role=role)
                            for i, role in [(1, 'admin'), (2, 'staff')]])
        db.session.add(Product(product_id=1, name='Full Cream Milk', category='Dairy', supplier='Nestle',
                               unit_price=2, expiry_date=date.today()+timedelta(days=30), reorder_level=5))
        db.session.flush()
        db.session.add_all([Inventory(product_id=1, quantity=0), Sale(product_id=1, quantity=7, sale_date=date.today()),
                            Alert(product_id=1, message='Retained alert', status='active'),
                            Prediction(product_id=1, predicted_demand=4),
                            InventoryAudit(actor_id=1, product_id=1, action='sale', old_value={'quantity': 7},
                                           new_value={'quantity': 0}, timestamp=date.today())])
        from auth_fixtures import grant_fixture_scopes
        grant_fixture_scopes()
        db.session.commit()
        self.client = app.test_client(); self.token = 't03-csrf-token'
        self.client.environ_base['HTTP_X_CSRF_TOKEN'] = self.token
        self.login()

    def login(self, role='admin'):
        g.pop('_login_user', None)
        with self.client.session_transaction() as session:
            session.clear()
            session['_user_id'] = db.session.get(User, 1 if role == 'admin' else 2).get_id()
            session['_fresh'] = True
            session['active_branch_id'] = 1
            session['_csrf_token'] = self.token

    def tearDown(self):
        db.session.remove(); db.drop_all(); db.engine.dispose(); self.ctx.pop()

    def remove(self, **values):
        return self.client.delete('/products/1', json=dict(expected_version=1, **values))

    def test_archive_preserves_historical_totals_references_and_identity(self):
        old_audit = InventoryAudit.query.one().old_value
        before = (Sale.query.count(), db.session.query(func.sum(Sale.quantity)).scalar())
        self.assertEqual(self.remove().get_json()['action'], 'archived')
        db.session.expire_all()
        self.assertTrue(db.session.get(Product, 1).archived)
        self.assertEqual((Sale.query.count(), db.session.query(func.sum(Sale.quantity)).scalar()), before)
        self.assertEqual((Inventory.query.count(), Alert.query.count(), Prediction.query.count()), (1, 1, 1))
        self.assertEqual(InventoryAudit.query.filter_by(action='sale').one().old_value, old_audit)
        self.assertEqual(InventoryAudit.query.filter_by(action='archive').count(), 1)
        self.assertIn('Full Cream Milk', self.client.get('/sales').get_data(as_text=True))
        self.assertIn('Archived', self.client.get('/sales').get_data(as_text=True))
        self.assertIn('Full Cream Milk', self.client.get('/archived-products').get_data(as_text=True))
        export = list(csv.DictReader(io.StringIO(self.client.get('/admin-settings/export-data').get_data(as_text=True))))
        self.assertEqual(export[0]['Status'], 'Archived')
        self.assertEqual(self.client.get('/alerts').get_json()[0]['product_archived'], True)

    def test_archive_excluded_from_all_new_operations_and_forecasts(self):
        self.assertEqual(self.remove().status_code, 200)
        for path in ['/products', '/inventory', '/api/predictions']:
            self.assertEqual(self.client.get(path).get_json(), [], path)
        self.assertNotIn('data-id="1"', self.client.get('/admin-inventory').get_data(as_text=True))
        self.assertEqual(self.client.post('/sales', json={'product_id': 1, 'quantity': 1}).status_code, 409)
        payload=dict(name='Full Cream Milk', category='Dairy', supplier='Nestle', unit_price=2, quantity=10,
                     expiry_date=(date.today()+timedelta(days=30)).isoformat(), reorder_level=5, expected_version=2)
        self.assertEqual(self.client.put('/products/1', json=payload).status_code, 409)
        self.assertEqual(self.client.get('/admin-edit-product/1').status_code, 409)
        # Exact archived batches must not be silently reactivated by receipt.
        form=dict(product='Milk', variant='Full Cream Milk', name='Full Cream Milk', category='Dairy', supplier='Nestle', unit_price='2', quantity='3',
                  expiry_date=payload['expiry_date'], reorder_level='5')
        self.assertEqual(self.client.post('/admin-add-product', data=form).status_code, 409)
        self.assertEqual(Inventory.query.one().quantity, 0)

    def test_nonzero_stock_and_stale_revision_have_zero_effect(self):
        Inventory.query.update({'quantity': 5}); db.session.commit()
        for mode in ('archive', 'permanent'):
            self.assertEqual(self.remove(mode=mode).status_code, 409)
        db.session.expire_all()
        self.assertEqual((db.session.get(Product, 1).version, Inventory.query.one().quantity), (1, 5))
        self.assertFalse(db.session.get(Product, 1).archived)
        self.assertEqual(InventoryAudit.query.count(), 1)
        self.assertEqual(self.client.delete('/products/1', json={'expected_version': 99}).status_code, 409)

    def test_all_dependencies_block_permanent_delete_including_zero_stock_and_audits(self):
        for dependent in (Inventory, Sale, Alert, Prediction, InventoryAudit):
            with self.subTest(dependent=dependent.__name__):
                # Keep only this dependency in a rolled-back savepoint.
                savepoint = db.session.begin_nested()
                for other in (Inventory, Sale, Alert, Prediction, InventoryAudit):
                    if other != dependent: other.query.delete()
                from inventory_transactions import delete_product
                from security import MutationError
                with app.test_request_context():
                    from flask_login import login_user
                    login_user(db.session.get(User, 1))
                    from flask import session
                    session['active_branch_id'] = 1
                    with self.assertRaises(MutationError) as caught:
                        delete_product(1, 1, 'permanent')
                    self.assertEqual(caught.exception.status, 409)
                savepoint.rollback()

    def test_unused_definition_permanent_delete_audited_without_identifier_reuse(self):
        for model in (Inventory, Sale, Alert, Prediction, InventoryAudit): model.query.delete()
        db.session.commit()
        # The T04 mapping itself is retained reconciliation evidence.
        self.assertEqual(self.remove(mode='permanent').status_code, 409)
        self.assertIsNotNone(db.session.get(Product, 1))
        db.session.add(Product(name='Next definition')); db.session.commit()
        self.assertGreater(Product.query.order_by(Product.product_id.desc()).first().product_id, 1)

    def test_authorization_csrf_and_mode_validation(self):
        self.assertEqual(self.client.delete('/products/1', json={'expected_version': 1}, headers={'X-CSRF-Token': ''}).status_code, 400)
        for mode in ('invalid', [], None):
            self.assertEqual(self.remove(mode=mode).status_code, 400)
        self.login('staff')
        self.assertEqual(self.remove(mode='permanent').status_code, 403)
        self.assertEqual(self.client.post('/admin-delete-product/1', data={'expected_version': '1'}).status_code, 403)
        self.assertEqual(self.remove().status_code, 403)
        self.client = app.test_client(); g.pop('_login_user', None)
        self.assertEqual(self.client.delete('/products/1', json={'expected_version': 2}).status_code, 302)

    def test_archive_rollback_and_repeat_rejection(self):
        with self.assertLogs('app', level='ERROR'), patch.object(db.session, 'commit', side_effect=RuntimeError('injected')):
            self.assertEqual(self.remove().status_code, 500)
        db.session.expire_all()
        self.assertFalse(db.session.get(Product, 1).archived)
        self.assertEqual(db.session.get(Product, 1).version, 1)
        self.assertEqual(InventoryAudit.query.count(), 1)
        self.assertEqual(self.remove().status_code, 200)
        self.assertEqual(self.remove().status_code, 409)
        self.assertEqual(self.client.delete('/products/1', json={'expected_version': 2}).status_code, 409)
        self.assertEqual(InventoryAudit.query.count(), 2)

    def test_placeholder_operations_unavailable_never_flash_success(self):
        for path in ('/notify-manager', '/reassign-tasks', '/request-replacement'):
            self.assertEqual(self.client.post(path).status_code, 403)
        self.login('staff')
        for path in ('/notify-manager', '/reassign-tasks', '/request-replacement'):
            response = self.client.post(path)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('success', response.get_data(as_text=True).lower())
            self.assertEqual(self.client.post(path, headers={'X-CSRF-Token': ''}).status_code, 400)
        with self.client.session_transaction() as session:
            self.assertFalse(session.get('_flashes'))
