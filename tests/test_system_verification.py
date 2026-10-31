"""Integrated HTTP workflows with real login and session CSRF on disposable SQLite."""
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
import csv
import io
import unittest
import warnings
import shutil
import subprocess
import re
import json
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from sqlalchemy.exc import LegacyAPIWarning
from sqlalchemy import event
from sklearn.exceptions import ConvergenceWarning
from app import app, get_product_movement_clusters
from database import db
from models import LegacyProductMapping, User, Product, Inventory, Sale, InventoryAudit, Alert


class PageTargets(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.targets = []; self.forms = []
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            # Read-only GET search forms must not put CSRF secrets in URLs.
            # Every mutating form still requires its hidden session token.
            self.forms.append(attrs.get('method','GET').upper() in ('GET','HEAD'))
            if attrs.get('action'): self.targets.append((attrs['action'], attrs.get('method', 'GET').upper(), False))
        if tag == 'input' and attrs.get('name') == 'csrf_token' and attrs.get('value') and self.forms:
            self.forms[-1] = True
        if tag == 'a' and attrs.get('href'): self.targets.append((attrs['href'], 'GET', False))
        if tag in ('img','script') and attrs.get('src'): self.targets.append((attrs['src'], 'GET', True))
        if tag == 'link' and attrs.get('href'): self.targets.append((attrs['href'], 'GET', True))


class SystemVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = generate_password_hash('system-test-password')
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='system-tests-only')
        self.expiry = (date.today()+timedelta(days=30)).isoformat()
        with app.app_context():
            self.assertEqual(str(db.engine.url), 'sqlite:///:memory:')
            db.create_all()
            db.session.add_all([User(user_id=i, name=role.title(), email=role+'@test.invalid',
                                    password_hash=self.password_hash, role=role) for i, role in [(1,'staff'),(2,'admin')]])
            db.session.add(Product(product_id=1, name='Full Cream Milk', category='Dairy', supplier='Nestle',
                                   unit_price=3.50, expiry_date=date.fromisoformat(self.expiry), reorder_level=10))
            db.session.flush()
            db.session.add(Inventory(product_id=1, quantity=10))
            db.session.add_all([Sale(product_id=1, quantity=10, sale_date=date.today()-timedelta(days=d)) for d in range(14)])
            from auth_fixtures import grant_fixture_scopes
            grant_fixture_scopes()
            db.session.commit()
        self.client = app.test_client()
    def tearDown(self):
        with app.app_context():
            db.session.remove(); db.drop_all(); db.engine.dispose()
    def token(self):
        with self.client.session_transaction() as session: return session.get('_csrf_token', '')
    def login(self, role='admin'):
        self.client.get('/login/'+role)
        response = self.client.post('/login/'+role, data={'email':role+'@test.invalid',
                                    'password':'system-test-password','csrf_token':self.token()})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/admin-dashboard' if role=='admin' else '/dashboard'))
        self.assertEqual(self.client.get(response.location).status_code, 200)
        with self.client.session_transaction() as state: state['active_branch_id'] = 1
    def write(self, method, path, **kwargs):
        return getattr(self.client, method)(path, headers={'X-CSRF-Token':self.token()}, **kwargs)

    def test_real_authentication_failures_logout_and_roles(self):
        for role in ('admin','staff'):
            self.client.get('/login/'+role)
            other = 'staff' if role=='admin' else 'admin'
            for email, password in [(role+'@test.invalid','wrong'),(other+'@test.invalid','system-test-password')]:
                response=self.client.post('/login/'+role, data={'email':email,'password':password,'csrf_token':self.token()})
                self.assertEqual(response.status_code,200)
                self.assertIn('Invalid',response.get_data(as_text=True))
                self.assertEqual(self.client.get('/api/predictions').status_code,302)
            self.login(role)
            self.assertEqual(self.client.post('/logout').status_code,400)
            self.assertEqual(self.write('post','/logout').status_code,302)
            self.assertEqual(self.client.get('/api/predictions').status_code,302)

    def test_every_get_route_templates_links_assets_and_no_writes(self):
        admin = {'sales','admin_dashboard','reports','admin_add_product','admin_record_sale','admin_inventory',
                 'admin_edit_product','admin_alerts','admin_settings','export_data'}
        staff = {'home','staff','inventory_page','all_products_page','low_stock_page','reorder_needed_page',
                 'expiring_soon_page','add_product','edit_product','record_sale_page','expiry_alerts_page'}
        redirects = {'index','role_selection','admin_login','staff_login'}
        adapter = app.url_map.bind('localhost')
        for role in ('admin','staff'):
            self.login(role)
            forbidden = staff if role=='admin' else admin
            for rule in app.url_map.iter_rules():
                if 'GET' not in rule.methods or rule.endpoint=='static': continue
                path=rule.rule.replace('<int:product_id>','1').replace('<int:lot_id>','1').replace('<int:transfer_id>','1').replace('<int:order_id>','1')
                response=self.client.get(path,buffered=True)  # Finish streamed CSV/request contexts before the next route.
                denied = {'add_product','edit_product','admin_add_product','admin_edit_product','admin_settings','branch_admin','assignment_admin','branch_administration'} if role == 'staff' else set()
                expected = 403 if rule.endpoint in denied else (302 if rule.endpoint in redirects else 200)
                if rule.endpoint == 'lot_api': expected = 404  # No foundation holding in this legacy-only fixture.
                if rule.endpoint in {'transfers.detail_api','purchasing.detail_api'}: expected = 404  # No transfer in this legacy-only fixture.
                if rule.endpoint in {'operations.history_api','operations.history_export','operations.history_summary'}:
                    expected = 503  # Explicit synthetic-target gate; never adopt this legacy database.
                if rule.endpoint=='operations.forecast_data':expected=400  # Explicit SKU/as_of required.
                if rule.endpoint=='operations.forecast_monitoring':expected=400  # Explicit snapshot run required.
                if rule.endpoint=='assistant_tools.download':expected=404  # An unowned placeholder is not an artifact; owned IDs are tested in T25.
                if rule.endpoint=='assistant_conversations.history':expected=404  # Owned scoped history is tested in T26.
                if rule.endpoint.startswith('assistant_proposals.'):expected=503  # T27 is disabled until schema/policy approval.
                self.assertEqual(response.status_code,expected,(role,path))
                if expected!=200 or not response.content_type.startswith('text/html'): continue
                page=PageTargets(response.get_data(as_text=True))
                self.assertTrue(all(page.forms),(role,path,'missing CSRF token'))
                for target, method, asset in page.targets:
                    parts=urlsplit(target)
                    if parts.scheme or parts.netloc or not parts.path: continue
                    resolved=urlsplit(urljoin(path,target)).path
                    try: adapter.match(resolved, method=method)
                    except Exception as exc: self.fail(f'{path}: broken {method} {resolved}: {exc}')
                    if asset:
                        asset_response = self.client.get(resolved)
                        try: self.assertEqual(asset_response.status_code,200,resolved)
                        finally: asset_response.close()
            self.write('post','/logout')
        with app.app_context():
            self.assertEqual((Inventory.query.one().quantity, Sale.query.count(), InventoryAudit.query.count(), Alert.query.count()),(10,14,0,0))

    def test_sales_overview_uses_recorded_units_and_admin_layout(self):
        self.login()
        html=self.client.get('/sales').get_data(as_text=True)
        self.assertIn('Units sold',html)
        self.assertIn('140',html)
        self.assertIn('Full Cream Milk',html)
        self.assertIn('data-workspace-role="hq_admin"',html)
        self.assertIn('HQ administrator',html)
        self.assertNotIn('Branch operations',html)
        self.assertEqual(self.write('post','/sales',json={'product_id':1,'quantity':3}).status_code,200)
        self.assertIn('143',self.client.get('/sales').get_data(as_text=True))

    def test_complete_inventory_sale_receipt_edit_remove_delete_workflow(self):
        self.login()
        payload=dict(name='Full Cream Milk',category='Dairy',supplier='Nestle',expiry_date=self.expiry,
                     unit_price=3.50,reorder_level=10,quantity=10)
        self.assertEqual(self.write('post','/products',json=dict(payload,name='New SKU')).status_code,409)
        pid=1
        self.assertEqual(self.write('post','/sales',json={'product_id':pid,'quantity':3}).status_code,200)
        self.assertEqual(self.write('put',f'/products/{pid}',json=dict(payload,expected_version=1)).status_code,409)
        self.assertEqual(self.write('put',f'/products/{pid}',json=dict(payload,quantity=20,expected_version=2)).status_code,200)
        receipt=dict(product='Milk',variant='Full Cream Milk',category='Dairy',supplier='Nestle',expiry_date=self.expiry,
                     unit_price='3.50',reorder_level='10',quantity='5')
        self.assertEqual(self.write('post','/admin-add-product',data=receipt).status_code,302)
        self.write('post','/logout'); self.login('staff')
        self.assertEqual(self.write('post','/add-product',data=receipt).status_code,403)
        self.assertEqual(self.write('post',f'/remove-stock/{pid}',data={'expected_version':'4','quantity_to_remove':'2','reason':'damaged fixture'}).status_code,302)
        recommendation=next(row for row in self.client.get('/api/predictions').get_json() if row['product_id']==pid)
        self.assertEqual(recommendation['current_stock'],23)
        self.write('post','/logout'); self.login()
        with app.app_context():
            audits=InventoryAudit.query.filter_by(product_id=pid).order_by(InventoryAudit.audit_id).all()
            self.assertEqual([a.action for a in audits],['sale','edit','receive','remove'])
            self.assertEqual([a.actor_id for a in audits],[2,2,2,1])
        self.assertEqual(self.write('delete',f'/products/{pid}',json={'expected_version':5}).status_code,409)
        self.assertEqual(self.write('put',f'/products/{pid}',json=dict(payload,quantity=0,expected_version=5)).status_code,200)
        self.assertEqual(self.write('delete',f'/products/{pid}',json={'expected_version':6}).status_code,200)
        with app.app_context():
            self.assertTrue(db.session.get(Product,pid).archived)
            self.assertEqual(Sale.query.filter_by(product_id=pid).count(),15)
            self.assertEqual(InventoryAudit.query.filter_by(product_id=pid).count(),6)

    def test_error_responses_rollback_without_exposing_exception(self):
        self.login()
        self.assertEqual(self.write('post','/sales',json={'product_id':999,'quantity':1}).status_code,404)
        self.assertEqual(self.client.get('/admin-edit-product/999').status_code,404)
        self.assertEqual(self.write('post','/sales',json=[]).status_code,400)
        with self.assertLogs('app',level='ERROR'), patch('inventory_transactions._audit',side_effect=RuntimeError('private-detail')):
            response=self.write('post','/sales',json={'product_id':1,'quantity':1})
        self.assertEqual(response.status_code,500)
        self.assertNotIn('private-detail',response.get_data(as_text=True))
        with app.app_context():
            self.assertEqual(Inventory.query.one().quantity,10)
            self.assertEqual(InventoryAudit.query.count(),0)
            self.assertEqual(Sale.query.count(),14)

    def test_movement_analysis_without_degenerate_cluster_warnings(self):
        with app.app_context():
            db.session.add_all([Product(product_id=i,name=f'Twin {i}',unit_price=3.50) for i in (2,3)])
            db.session.flush()
            db.session.add_all([Sale(product_id=i,quantity=140,sale_date=date.today()) for i in (2,3)])
            from auth_fixtures import grant_fixture_scopes
            grant_fixture_scopes(); db.session.commit()
        with app.test_request_context():
            from flask_login import login_user
            from flask import session
            login_user(db.session.get(User,2)); session['active_branch_id']=1
            with warnings.catch_warnings():
                warnings.simplefilter('error',ConvergenceWarning)
                movement=get_product_movement_clusters()
            self.assertEqual(movement,{1:'Stable',2:'Stable',3:'Stable'})

    def test_export_is_authorized_and_spreadsheet_safe(self):
        self.assertEqual(self.client.get('/admin-settings/export-data').status_code,302)
        self.login('staff')
        self.assertEqual(self.client.get('/admin-settings/export-data').status_code,200)
        self.write('post','/logout'); self.login()
        with app.app_context():
            product=db.session.get(Product,1); product.name='=2+2'; db.session.commit()
        response=self.client.get('/admin-settings/export-data')
        self.assertEqual(response.status_code,200)
        rows=list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(rows[1][1],"'=2+2")
        self.assertEqual(rows[1][5],'10')
        self.assertIn('attachment',response.headers['Content-Disposition'])

    def test_read_routes_without_legacy_sqlalchemy_warnings(self):
        self.login()
        with warnings.catch_warnings():
            warnings.simplefilter('error',LegacyAPIWarning)
            for path in ['/admin-dashboard','/inventory','/alerts','/admin-edit-product/1']:
                self.assertEqual(self.client.get(path).status_code,200)

    def test_dashboard_query_count_does_not_grow_per_product(self):
        self.login()
        with app.app_context():
            # Exercise the clustering path in both sizes (it needs three products).
            db.session.add_all([Product(product_id=i, name=f'Extra {i}', unit_price=2) for i in (2,3)])
            db.session.flush()
            db.session.add_all([Inventory(product_id=i, quantity=5) for i in (2,3)])
            from auth_fixtures import grant_fixture_scopes
            grant_fixture_scopes(); db.session.commit()
            engine=db.engine
        def query_count():
            statements=[]
            def capture(conn, cursor, statement, params, context, executemany): statements.append(statement)
            event.listen(engine,'before_cursor_execute',capture)
            try: self.assertEqual(self.client.get('/admin-dashboard').status_code,200)
            finally: event.remove(engine,'before_cursor_execute',capture)
            return len(statements)
        small_count=query_count()
        with app.app_context():
            db.session.add_all([Product(product_id=i, name=f'Extra {i}', unit_price=2) for i in range(4,27)])
            db.session.flush()
            db.session.add_all([Inventory(product_id=i, quantity=5) for i in range(4,27)])
            db.session.add_all([Sale(product_id=i, quantity=i, sale_date=date.today()) for i in range(4,27)])
            from auth_fixtures import grant_fixture_scopes
            grant_fixture_scopes(); db.session.commit()
        large_count=query_count()
        self.assertEqual(large_count,small_count)  # Fresh authorization adds constant queries, never per-product queries.
        self.assertLessEqual(large_count,48)

    def test_empty_system_sales_and_reports(self):
        with app.app_context():
            LegacyProductMapping.query.delete(); Sale.query.delete(); Inventory.query.delete(); Product.query.delete(); db.session.commit()
        self.login()
        self.assertIn('No sales recorded yet.',self.client.get('/sales').get_data(as_text=True))
        self.assertIn('No products available.',self.client.get('/reports').get_data(as_text=True))
        self.assertEqual(self.client.get('/api/predictions').get_json(),[])

    def test_staff_edit_initializes_catalog_product_from_variant(self):
        self.login('staff')
        self.assertEqual(self.client.get('/edit-product/1').status_code,403)
        self.write('post','/logout'); self.login('admin')
        html=self.client.get('/edit-product/1').get_data(as_text=True)
        match=re.search(r'const selectedValues = (.*);',html)
        self.assertIsNotNone(match)
        selected=json.loads(match.group(1))
        self.assertEqual(selected['product'],'Full Cream Milk')  # Database-defined family fallback.
        self.assertEqual(selected['variant'],'Full Cream Milk')
        self.assertEqual(self.write('post','/edit-product/1',data=dict(selected,quantity='12',expected_version='1')).status_code,302)
        with app.app_context(): self.assertEqual(Inventory.query.one().quantity,12)

    def test_redesigned_shell_roles_assistant_and_local_assets(self):
        for role, dashboard in [('admin','/admin-dashboard'),('staff','/dashboard')]:
            self.login(role)
            html=self.client.get(dashboard).get_data(as_text=True)
            self.assertIn('css/inventraiq.css',html)
            self.assertIn('Skip to content',html)
            self.assertIn('href="/analytics"',html)
            self.assertIn('Local demonstration — no live AI connected',html)
            self.assertRegex(html,r'id="assistant-question"[^>]*maxlength="2000"')
            self.assertNotRegex(html,r'id="assistant-question"[^>]*disabled')
            self.assertIn('js/assistant.js',html)
            self.assertIn('data-assistant-loading',html)
            self.assertIn('data-assistant-empty',html)
            self.assertNotIn('cdn.',html)
            self.assertEqual('href="/admin-settings"' in html,role=='admin')
            self.assertEqual('href="/reports"' in html,role=='admin')
            self.write('post','/logout')

    def test_analytics_authentication_recorded_dates_and_no_writes(self):
        self.assertEqual(self.client.get('/analytics').status_code,302)
        with app.app_context():
            db.session.add_all([Sale(product_id=1,quantity=900,sale_date=date.today()-timedelta(days=30)),
                                Sale(product_id=1,quantity=800,sale_date=date.today()+timedelta(days=1))])
            db.session.commit()
        for role in ('admin','staff'):
            self.login(role)
            with patch('app.render_template',return_value='analytics rendered') as render:
                self.assertEqual(self.client.get('/analytics').status_code,200)
                context=render.call_args.kwargs
                self.assertEqual(context['total_units'],140)
                self.assertEqual(len(context['trend']),30)
                self.assertEqual(context['trend'][-1]['date'],date.today().isoformat())
                self.assertEqual(context['trend'][0]['units'],0)
                self.assertEqual(context['intelligence_items'][0]['product_id'],1)
            self.write('post','/logout')
        with app.app_context():
            self.assertEqual(Inventory.query.one().quantity,10)
            self.assertEqual(InventoryAudit.query.count(),0)

    def test_redesigned_empty_analytics_dashboard_and_error_pages(self):
        with app.app_context():
            LegacyProductMapping.query.delete(); Sale.query.delete(); Inventory.query.delete(); Product.query.delete(); db.session.commit()
        self.login()
        self.assertIn('Start with your first product',self.client.get('/admin-dashboard').get_data(as_text=True))
        html=self.client.get('/analytics').get_data(as_text=True)
        self.assertIn('No sales recorded in the last 30 days',html)
        self.assertIn('Forecast comparison unavailable',html)
        response=self.client.get('/unknown-page')
        self.assertEqual(response.status_code,404)
        self.assertIn('InventraIQ',response.get_data(as_text=True))
        response=self.client.get('/unknown-page',json={})
        self.assertEqual(response.status_code,404)
        self.assertIsInstance(response.get_json()['error'],str)

    def test_receipt_form_exposes_required_price_and_sale_post_csrf(self):
        self.login()
        html=self.client.get('/admin-add-product').get_data(as_text=True)
        self.assertRegex(html,r'<input[^>]*name="unit_price"[^>]*required')
        html=self.client.get('/admin-record-sale').get_data(as_text=True)
        page=PageTargets(html)
        self.assertTrue(all(page.forms))
        self.assertIn(('/sales','POST',False),page.targets)
        self.assertIn('Loading available products',html)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional; generated JavaScript syntax check unavailable')
    def test_rendered_inline_and_local_javascript_parse(self):
        self.login()
        pages=[self.client.get(path).get_data(as_text=True) for path in ['/admin-inventory','/admin-record-sale','/admin-add-product']]
        self.write('post','/logout'); self.login('staff')
        pages += [self.client.get(path).get_data(as_text=True) for path in ['/inventory-page','/record-sale','/edit-product/1','/add-product']]
        scripts=[]
        for html in pages:
            scripts.extend(re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>',html,re.S))
        from pathlib import Path
        scripts.extend(path.read_text(encoding='utf-8') for path in Path(app.static_folder).glob('js/*.js'))
        for script in scripts:
            if not script.strip(): continue
            result=subprocess.run([shutil.which('node'),'--check'],input=script,text=True,encoding='utf-8',capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
