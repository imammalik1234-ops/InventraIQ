"""Integration checks against isolated SQLite, never the configured MySQL database."""
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from unittest.mock import patch
from flask import g, template_rendered
from app import app, inventory_intelligence
from database import db
from models import Inventory, Product, Sale, User


class IntelligenceRows(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = {}
        self.product = self.field = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr':
            self.product = attrs.get('data-intelligence-product-id', attrs.get('data-id'))
            if self.product:
                self.rows[int(self.product)] = {}
        if tag == 'td':
            self.field = attrs.get('data-field')

    def handle_data(self, text):
        if self.product and self.field:
            self.rows[int(self.product)][self.field] = text.strip()

    def handle_endtag(self, tag):
        if tag == 'td':
            self.field = None
        if tag == 'tr':
            self.product = None


class DashboardIntegrationTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='integration-only')
        self.ctx = app.app_context()
        self.ctx.push()
        self.assertEqual(str(db.engine.url), 'sqlite:///:memory:')
        db.create_all()
        db.session.add(User(user_id=1, email='review@example.com', name='Reviewer',
                            password_hash='unused', role='staff'))
        stocks = {1: 20, 2: 5, 3: 500, 4: 77, 5: 1, 6: None, 7: 100}
        for pid, stock in stocks.items():
            db.session.add(Product(product_id=pid, name=f'Item {pid}', unit_price=2,
                                   reorder_level=1000 if pid == 3 else 10,
                                   # grant_fixture_scopes assigns this fixture to a UTC branch.
                                   expiry_date=datetime.now(timezone.utc).date()-timedelta(days=1) if pid == 7 else None))
            db.session.flush()
            if stock is not None:
                db.session.add(Inventory(product_id=pid, quantity=stock))
            if pid != 2:
                for day in range(14):
                    db.session.add(Sale(product_id=pid, quantity=20 if pid == 5 else 10,
                                        sale_date=date.today()-timedelta(days=day)))
        from auth_fixtures import grant_fixture_scopes
        grant_fixture_scopes()
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def login(self, role='staff'):
        g.pop('_login_user', None)
        from auth_fixtures import grant_fixture_scopes
        db.session.get(User, 1).role = role
        grant_fixture_scopes()
        db.session.commit()
        with self.client.session_transaction() as session:
            session['_user_id'] = db.session.get(User, 1).get_id()
            session['_fresh'] = True
            session['active_branch_id'] = 1

    @contextmanager
    def rendered(self):
        contexts = []
        def capture(sender, template, context, **extra):
            contexts.append(context)
        template_rendered.connect(capture, app)
        try:
            yield contexts
        finally:
            template_rendered.disconnect(capture, app)

    def assert_html_consistency(self, html, api):
        rows = IntelligenceRows(html).rows
        self.assertEqual(set(rows), {row['product_id'] for row in api})
        for item in api:
            expected = {
                'available-stock': item['available_stock'],
                'predicted-demand': item['forecast']['predicted_demand'],
                'projected-shortfall': item['projected_shortfall'],
                'replenishment': item['recommended_reorder_qty'],
            }
            self.assertEqual(rows[item['product_id']],
                             {key: str(value) if value is not None else 'Withheld'
                              for key, value in expected.items()})

    def test_cross_page_consistency_for_both_roles(self):
        before = [(i.product_id, i.quantity) for i in Inventory.query.order_by(Inventory.product_id)]
        for role, dashboard, inventory in [('staff', '/dashboard', '/inventory-page'),
                                            ('admin', '/admin-dashboard', '/admin-inventory')]:
            self.login(role)
            api = self.client.get('/api/predictions').get_json()
            for path in [dashboard, inventory, '/ai-predictions']:
                with self.rendered() as contexts, patch('app.inventory_intelligence', wraps=inventory_intelligence) as engine:
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertEqual(engine.call_count, 1, path)
                self.assert_html_consistency(response.get_data(as_text=True), api)
                if path == dashboard:
                    items = contexts[-1]['intelligence_items']
                    self.assertEqual([item['product_id'] for item in items], [item['product_id'] for item in api])
                    self.assertEqual(contexts[-1]['stats']['reorder_needed'], 0)  # T20: no request-time inference.
                    self.assertEqual(contexts[-1]['stats']['low_stock'], 4)
        self.assertEqual(before, [(i.product_id, i.quantity) for i in Inventory.query.order_by(Inventory.product_id)])
        self.assertEqual(db.session.query(db.func.sum(Sale.quantity)).scalar(), 980)

    def test_priority_and_rule_values(self):
        self.login()
        rows = self.client.get('/api/predictions').get_json()
        self.assertEqual([row['product_id'] for row in rows], [1, 2, 3, 4, 5, 6, 7])
        by_id = {row['product_id']: row for row in rows}
        self.assertIsNone(by_id[1]['projected_shortfall'])
        self.assertIsNone(by_id[1]['recommended_reorder_qty'])
        self.assertIsNone(by_id[2]['recommended_reorder_qty'])
        self.assertIsNone(by_id[6]['recommended_reorder_qty'])
        self.assertEqual(by_id[4]['risk_status'], 'Review required')
        self.assertIsNone(by_id[4]['projected_shortfall'])
        self.assertEqual(by_id[7]['available_stock'], 0)
        self.assertEqual(by_id[3]['risk_status'], 'Review required')
        self.assertTrue(all(row['forecast']['status']=='unavailable' for row in rows))

    def test_authorization(self):
        paths = ['/dashboard', '/admin-dashboard', '/inventory-page', '/admin-inventory',
                 '/ai-predictions', '/api/predictions', '/reports']
        for path in paths: self.assertEqual(self.client.get(path).status_code, 302)
        for role in ('staff', 'admin'):
            self.login(role)
            for path in paths: self.assertEqual(self.client.get(path).status_code, 200)

    def test_low_stock_is_not_reorder_recommendation(self):
        self.login()
        with self.rendered() as contexts:
            self.client.get('/inventory/low-stock')
        low = {item['product_id'] for item in contexts[-1]['products']}
        with self.rendered() as contexts:
            self.client.get('/inventory/reorder-needed')
        reorder = {item['product_id'] for item in contexts[-1]['products']}
        self.assertIn(3, low)  # configured threshold, but model demand is covered
        self.assertNotIn(3, reorder)
        self.assertEqual(reorder,set())  # T20: no qualified snapshots, no live-fit reorder results.
        self.assertNotIn(1, low)
        self.assertNotIn(2, reorder)  # insufficient history is not zero demand
        for role, path in [('staff', '/expiry-alerts'), ('admin', '/admin-alerts')]:
            self.login(role)
            with self.rendered() as contexts:
                response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual({row['product_id'] for row in contexts[-1]['reorder_items']}, reorder)
            self.assertIn('Recommendation withheld.', response.get_data(as_text=True))
            self.assertNotIn('None', response.get_data(as_text=True))

    def test_empty_inventory_and_no_history(self):
        Sale.query.delete()
        Inventory.query.delete()
        db.session.commit()
        for role, path in [('staff', '/dashboard'), ('admin', '/admin-dashboard')]:
            self.login(role)
            api = self.client.get('/api/predictions').get_json()
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assert_html_consistency(response.get_data(as_text=True), api)
            self.assertTrue(all(row['recommended_reorder_qty'] is None for row in api))
            self.assertIn('Review required', response.get_data(as_text=True))
        from models import LegacyProductMapping
        LegacyProductMapping.query.delete()
        Product.query.delete()
        db.session.commit()
        for role, path in [('staff', '/dashboard'), ('admin', '/admin-dashboard')]:
            self.login(role)
            self.assertEqual(self.client.get('/api/predictions').get_json(), [])
            self.assertIn('No products available.', self.client.get(path).get_data(as_text=True))

    def test_unreliable_forecasts_across_pages(self):
        for sale in Sale.query.filter_by(product_id=1):
            if (date.today()-sale.sale_date).days < 3:
                sale.quantity = 1
        db.session.commit()
        self.login()
        api = self.client.get('/api/predictions').get_json()
        row = next(row for row in api if row['product_id'] == 1)
        self.assertEqual(row['forecast']['status'], 'unavailable')  # Missing snapshots never fit changed history.
        self.assertIsNone(row['recommended_reorder_qty'])
        for path in ['/dashboard', '/ai-predictions', '/inventory-page']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assert_html_consistency(response.get_data(as_text=True), api)
