"""Real competing requests against a disposable file-backed SQLite database."""
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from flask import Flask
from flask_login import LoginManager, login_required
from app import app as source_app
from database import db
from models import User, Product, Inventory, Sale, InventoryAudit
from security import init_security, transaction
import inventory_transactions as stock_tx


class ConcurrentStockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='inventraiq-concurrency-')
        directory = Path(self.temp.name).resolve()
        self.database_path = directory / 'inventory-test.db'
        self.assertEqual(self.database_path.resolve().parent, directory)
        self.app = Flask('inventory-concurrency-tests')
        self.app.config.update(SECRET_KEY='concurrency-test-only', TESTING=True,
                               SQLALCHEMY_DATABASE_URI='sqlite:///'+self.database_path.as_posix(),
                               SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'timeout': 5}})
        self.app.config.update(AUTH_ABSOLUTE_SECONDS=28800)
        db.init_app(self.app)
        manager = LoginManager(self.app)
        manager.user_loader(source_app.login_manager._user_callback)
        init_security(self.app, manager)
        self.app.add_url_rule('/sales', 'record_sale', source_app.view_functions['record_sale'], methods=['POST'])
        self.app.add_url_rule('/products/<int:product_id>', 'update_product_api',
                              source_app.view_functions['update_product_api'], methods=['PUT'])
        self.app.add_url_rule('/products/<int:product_id>', 'delete_product_api',
                              source_app.view_functions['delete_product_api'], methods=['DELETE'])

        @self.app.post('/receive')
        @login_required
        @transaction
        def receive():
            product, inventory = stock_tx.receive_stock(1, 3)
            return {'quantity': inventory.quantity}

        with self.app.app_context():
            db.create_all()
            db.session.add_all([User(user_id=1, email='concurrency@test.invalid', password_hash='unused', role='admin'),
                                Product(product_id=1, name='Milk', category='Dairy', supplier='Test', unit_price=2, reorder_level=0)])
            db.session.flush()
            db.session.add(Inventory(product_id=1, quantity=5))
            from auth_fixtures import grant_fixture_scopes
            grant_fixture_scopes()
            db.session.commit()
            self.auth_token = db.session.get(User, 1).get_id()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def compete(self, calls):
        barrier = Barrier(len(calls))
        def run(call):
            method, path, payload = call
            with self.app.test_client() as client:
                with client.session_transaction() as session:
                    session['_user_id'] = self.auth_token
                    session['_fresh'] = True
                    session['active_branch_id'] = 1
                    session['_csrf_token'] = 'thread-token'
                barrier.wait(timeout=10)
                return getattr(client, method)(path, json=payload, headers={'X-CSRF-Token': 'thread-token'}).status_code
        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            return list(executor.map(run, calls))

    def test_competing_sales_cannot_oversell(self):
        statuses = self.compete([('post', '/sales', {'product_id': 1, 'quantity': 4})]*2)
        self.assertEqual(statuses.count(200), 1, statuses)
        self.assertTrue(all(code in (200, 400, 409) for code in statuses), statuses)
        with self.app.app_context():
            self.assertEqual(Inventory.query.one().quantity, 1)
            self.assertEqual(Sale.query.one().quantity, 4)
            audit = InventoryAudit.query.one()
            self.assertEqual((audit.old_value['quantity'], audit.new_value['quantity']), (5, 1))
            self.assertEqual(Product.query.one().version, 2)

    def test_competing_absolute_edits_cannot_overwrite(self):
        payload = dict(name='Milk', category='Dairy', supplier='Test', unit_price=2, expiry_date='2030-01-01',
                       reorder_level=0, expected_version=1)
        statuses = self.compete([('put', '/products/1', dict(payload, quantity=20)),
                                ('put', '/products/1', dict(payload, quantity=30))])
        self.assertEqual(sorted(statuses), [200, 409])
        with self.app.app_context():
            self.assertEqual(Inventory.query.one().quantity, 20 if statuses[0] == 200 else 30)
            self.assertEqual(InventoryAudit.query.count(), 1)
            self.assertEqual(Product.query.one().version, 2)

    def test_receipt_and_sale_preserve_every_successful_delta(self):
        statuses = self.compete([('post', '/receive', {}), ('post', '/sales', {'product_id': 1, 'quantity': 4})])
        self.assertTrue(all(code in (200, 409) for code in statuses), statuses)
        self.assertGreaterEqual(statuses.count(200), 1)
        expected = 5 + (3 if statuses[0] == 200 else 0) - (4 if statuses[1] == 200 else 0)
        with self.app.app_context():
            self.assertEqual(Inventory.query.one().quantity, expected)
            self.assertEqual(InventoryAudit.query.count(), statuses.count(200))
            previous = 5
            for audit in InventoryAudit.query.order_by(InventoryAudit.audit_id):
                self.assertEqual(audit.old_value['quantity'], previous)
                previous = audit.new_value['quantity']
            self.assertEqual(previous, expected)

    def test_archive_and_receipt_cannot_leave_archived_stock(self):
        with self.app.app_context():
            Inventory.query.update({'quantity': 0}); db.session.commit()
        statuses = self.compete([('delete', '/products/1', {'expected_version': 1}), ('post', '/receive', {})])
        self.assertTrue(all(code in (200, 409) for code in statuses), statuses)
        self.assertEqual(statuses.count(200), 1, statuses)
        with self.app.app_context():
            product = Product.query.one()
            self.assertEqual(Inventory.query.one().quantity, 0 if product.archived else 3)
            self.assertEqual(InventoryAudit.query.count(), 1)
