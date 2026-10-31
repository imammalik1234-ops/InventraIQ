"""T05 role x capability x resource isolation on disposable SQLite only."""
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
import csv
import io
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from flask import g, session
from flask_login import login_user
from sqlalchemy import select, update, func
from werkzeug.exceptions import Forbidden, NotFound
from app import app
from database import db
from models import (User, Branch, UserBranch, UserHQScope, Category, SKU, Product, Inventory,
                    Sale, Alert, StockLot, BranchProduct, BranchStock, LegacyProductMapping,
                    AuthorizationAudit, InventoryAudit)
from authz import resolve_scope, CAPABILITIES, Scope, READ_ENDPOINTS, WRITE_ENDPOINTS, ADMIN_ENDPOINTS, PUBLIC
import inventory_transactions as stock_tx
import branch_services
from intelligence import inventory_intelligence
from security import MutationError


class BranchAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.ctx=app.app_context(); self.ctx.push()
        app.config.update(TESTING=True, LEGACY_STOCK_REHEARSAL=True, SECRET_KEY='t05-disposable-authorization')
        self.assertEqual(str(db.engine.url), 'sqlite:///:memory:')
        db.create_all()
        for b in range(1,4):
            db.session.add(Branch(branch_id=b, code='BR-'+str(b), display_name='Branch '+str(b), timezone='Asia/Singapore'))
        for uid in range(1,8):
            db.session.add(User(user_id=uid, name='Fixture '+str(uid), email=f'fixture-{uid}@test.invalid',
                                password_hash='unused', role='admin' if uid in (1,5) else 'staff'))
        db.session.add(Category(category_id=1, code='TEST', display_name='Test'))
        db.session.flush()
        db.session.add(SKU(sku_id=1, code='SHARED-SKU', name='Shared sellable SKU', category_id=1))
        db.session.add_all([UserHQScope(user_id=1, hq_role='admin'), UserHQScope(user_id=2, hq_role='analyst'),
                           UserBranch(user_id=3, branch_id=1, branch_role='manager'),
                           UserBranch(user_id=4, branch_id=1, branch_role='clerk'),
                           UserBranch(user_id=6, branch_id=1, branch_role='manager'),
                           UserBranch(user_id=6, branch_id=2, branch_role='clerk'),
                           UserBranch(user_id=7, branch_id=1, branch_role='manager', active=False)])
        db.session.flush()
        self.expiry=date.today()+timedelta(days=30)
        for b in range(1,4):
            pid=b*11
            db.session.add(Product(product_id=pid, name=f'BRANCH-{b}-PRIVATE', category='Dairy', supplier='Fixture',
                                   unit_price=b+1, expiry_date=self.expiry, reorder_level=2))
            db.session.add(BranchProduct(branch_id=b, sku_id=1))
            db.session.add(StockLot(lot_id=pid, sku_id=1, lot_code='LOT-'+str(b), provenance={'branch_private': b}))
            db.session.flush()
            inv=Inventory(product_id=pid, quantity=pid); db.session.add(inv); db.session.flush()
            db.session.add(BranchStock(branch_id=b, sku_id=1, lot_id=pid, on_hand=pid))
            db.session.add(LegacyProductMapping(legacy_product_id=pid, legacy_inventory_id=inv.inventory_id,
                                               branch_id=b, sku_id=1, lot_id=pid, evidence={'synthetic': True}))
            db.session.add(Alert(product_id=pid, message=f'BRANCH-{b}-SECRET-ALERT', alert_type='fixture'))
            for day in range(14):
                db.session.add(Sale(product_id=pid, quantity=pid, sale_date=date.today()-timedelta(days=day)))
        db.session.add(Product(product_id=99, name='UNMAPPED-PRIVATE', unit_price=999))
        db.session.flush(); db.session.add(Inventory(product_id=99, quantity=999))
        db.session.add(Sale(product_id=99, quantity=999, sale_date=date.today()))
        db.session.commit()
        self.client=app.test_client()
        self.client.environ_base['HTTP_X_CSRF_TOKEN']='t05-csrf'

    def tearDown(self):
        db.session.remove(); db.drop_all(); db.engine.dispose(); self.ctx.pop()

    def login(self, uid=3, branch=1, client=None):
        client=client or self.client
        g.pop('_login_user',None)
        with client.session_transaction() as state:
            state.clear(); state['_user_id']=db.session.get(User,uid).get_id()
            state['_fresh']=True; state['_csrf_token']='t05-csrf'
            if branch is not None: state['active_branch_id']=branch

    def context(self, uid=3, branch=1):
        ctx=app.test_request_context(); ctx.push()
        g.pop('_login_user',None)
        login_user(db.session.get(User,uid))
        if branch is not None: session['active_branch_id']=branch
        return ctx

    def payload(self, pid=11):
        return dict(name=f'BRANCH-{pid//11}-PRIVATE', category='Dairy', supplier='Fixture',
                    unit_price=2, expiry_date=self.expiry.isoformat(), reorder_level=2,
                    quantity=11, expected_version=1)

    def test_role_capability_matrix_resolves_database_grants(self):
        for uid, role in [(1,'hq_admin'),(2,'hq_analyst'),(3,'manager'),(4,'clerk')]:
            for capability in set().union(*CAPABILITIES.values()) | {'unknown.capability'}:
                with self.subTest(role=role, capability=capability):
                    ctx=self.context(uid)
                    try:
                        if capability in CAPABILITIES[role]:
                            scope=resolve_scope(capability)
                            self.assertEqual(scope.role,role); self.assertEqual(scope.branches,(1,))
                        else:
                            with self.assertRaises(Forbidden): resolve_scope(capability)
                    finally: ctx.pop()

    def test_all_legacy_read_routes_are_isolated_for_every_branch_role(self):
        paths=['/products','/inventory','/alerts','/api/predictions','/dashboard','/admin-dashboard',
               '/sales','/reports','/analytics','/admin-inventory','/inventory-page','/inventory/all-products',
               '/inventory/low-stock','/inventory/reorder-needed','/alerts/expiring-soon','/expiry-alerts',
               '/admin-alerts','/ai-predictions','/archived-products','/admin-settings/export-data']
        for uid in (1,2,3,4):
            self.login(uid)
            for path in paths:
                with self.subTest(uid=uid,path=path):
                    response=self.client.get(path)
                    self.assertEqual(response.status_code,200)
                    text=response.get_data(as_text=True)
                    for secret in ('BRANCH-2-PRIVATE','BRANCH-3-PRIVATE','BRANCH-2-SECRET-ALERT','BRANCH-3-SECRET-ALERT','UNMAPPED-PRIVATE'):
                        self.assertNotIn(secret,text)
                    self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_cross_branch_reads_and_guessed_lot_ids_non_disclosing(self):
        self.login(3)
        for path in ['/products','/inventory','/sales','/analytics','/reports','/alerts','/api/predictions','/api/branch-stock']:
            self.assertEqual(self.client.get(path+'?branch_id=2').status_code,403,path)
        self.assertEqual(self.client.get('/api/stock-lots/11').status_code,200)
        for lot_id in (22,33,999):
            response=self.client.get('/api/stock-lots/'+str(lot_id),json={})
            self.assertEqual(response.status_code,404)
            self.assertEqual(response.get_json(),{'error':'Resource not found.'})
        self.assertNotIn('provenance',self.client.get('/api/stock-lots/11').get_json())

    def test_cross_branch_writes_and_unmapped_guesses_leave_all_balances_unchanged(self):
        for uid in (1,2,3,4):
            self.login(uid)
            for pid in (22,33,99,999):
                for method,path,payload in [('post','/sales',{'product_id':pid,'quantity':1}),
                    ('put',f'/products/{pid}',self.payload(pid)),
                    ('delete',f'/products/{pid}',{'expected_version':1})]:
                    with self.subTest(uid=uid,pid=pid,method=method):
                        self.assertIn(getattr(self.client,method)(path,json=payload).status_code,(403,404))
        self.assertEqual([i.quantity for i in Inventory.query.order_by(Inventory.product_id)],[11,22,33,999])
        self.assertEqual(InventoryAudit.query.count(),0)

    def test_hq_consolidated_is_explicit_capability_and_filters_unmapped(self):
        for uid in (1,2):
            self.login(uid,branch=None)
            rows=self.client.get('/inventory').get_json()
            self.assertEqual({r['product_id'] for r in rows},{11,22,33})
            self.assertEqual({r['branch_id'] for r in self.client.get('/api/branch-stock').get_json()},{1,2,3})
            self.assertEqual(self.client.post('/sales',json={'product_id':11,'quantity':1}).status_code,403)
            self.assertEqual(self.client.post('/api/scope',json={'branch_id':2}).status_code,200)
            self.assertEqual({r['product_id'] for r in self.client.get('/inventory').get_json()},{22})
            self.assertEqual(self.client.post('/api/scope',json={'consolidated':True}).status_code,200)
            self.assertEqual(len(self.client.get('/inventory').get_json()),3)

    def test_analyst_read_only_and_staff_cannot_mutate_catalog(self):
        for uid in (2,3,4):
            self.login(uid)
            for method,path,data in [('post','/products',self.payload()),('put','/products/11',self.payload()),
                ('delete','/products/11',{'expected_version':1}),
                ('put','/api/admin/assignments',{'user_id':3,'branch_id':2,'branch_role':'manager','active':True}),
                ('post','/api/admin/branches',{'code':'FOUR','display_name':'Fourth','timezone':'UTC'})]:
                self.assertEqual(getattr(self.client,method)(path,json=data).status_code,403)
            for path in ['/add-product','/edit-product/11','/admin-edit-product/11','/admin-add-product','/branch-administration']:
                self.assertEqual(self.client.get(path).status_code,403)
        self.login(2)
        self.assertEqual(self.client.post('/sales',json={'product_id':11,'quantity':1}).status_code,403)
        self.assertEqual(self.client.post('/remove-stock/11',data={'quantity_to_remove':'1','expected_version':'1','reason':'test'}).status_code,403)

    def test_clerk_sales_manager_adjustments_require_reason_and_catalog_stays_hq(self):
        self.login(4)
        self.assertEqual(self.client.post('/sales',json={'product_id':11,'quantity':1}).status_code,200)
        self.assertEqual(self.client.post('/remove-stock/11',data={'quantity_to_remove':'1','expected_version':'2','reason':'damage'}).status_code,403)
        self.login(3)
        self.assertEqual(self.client.post('/remove-stock/11',data={'quantity_to_remove':'1','expected_version':'2'}).status_code,400)
        self.assertEqual(self.client.post('/remove-stock/11',data={'quantity_to_remove':'1','expected_version':'2','reason':'damaged fixture'}).status_code,302)
        self.assertEqual(Inventory.query.filter_by(product_id=11).one().quantity,9)
        self.assertEqual(InventoryAudit.query.filter_by(action='remove').one().new_value['reason'],'damaged fixture')

    def test_direct_mutation_services_check_role_and_scope(self):
        for uid in (2,3,4):
            ctx=self.context(uid)
            try:
                for call in [lambda:stock_tx.create_product(self.payload()),
                             lambda:stock_tx.edit_product(11,self.payload(),1),
                             lambda:stock_tx.delete_product(11,1)]:
                    with self.assertRaises(Forbidden):call()
                for pid in (22,999):
                    with self.assertRaises((Forbidden,NotFound)):stock_tx.receive_stock(pid,1)
                    with self.assertRaises((Forbidden,NotFound)):stock_tx.deduct_stock(pid,1,sale=True)
            finally: ctx.pop(); db.session.rollback()
        ctx=self.context(3)
        try:
            stock_tx.receive_stock(11,1)
            self.assertEqual(Inventory.query.filter_by(product_id=11).one().quantity,12)
        finally: ctx.pop(); db.session.rollback()
        ctx=self.context(4)
        try:
            with self.assertRaises(Forbidden):stock_tx.receive_stock(11,1)
            with self.assertRaises(Forbidden):stock_tx.deduct_stock(11,1,reason='damage',expected_version=1)
        finally:ctx.pop(); db.session.rollback()

    def test_direct_read_services_require_server_actor_and_ignore_forged_scope(self):
        with self.assertRaises(Forbidden):inventory_intelligence()
        with self.assertRaises(Forbidden):branch_services.stock_rows()
        with self.assertRaises(Forbidden):branch_services.manage_branch()
        ctx=self.context(3)
        try:
            self.assertEqual({r['product_id'] for r in inventory_intelligence()},{11})
            self.assertEqual({r['branch_id'] for r in branch_services.stock_rows()},{1})
            with self.assertRaises(NotFound):branch_services.lot_detail(22)
            with self.assertRaises(Forbidden):resolve_scope(branch_id=Scope(1,(1,2,3),'hq_admin',True))
            with self.assertRaises(Forbidden):branch_services.select_scope(2)
        finally:ctx.pop()

    def test_injected_and_bulk_scopes_fail_before_mutation(self):
        self.login(3)
        payloads=[{'product_id':11,'quantity':1,'branch_id':2},
                  {'product_id':11,'quantity':1,'actor_id':1},
                  {'product_id':11,'quantity':1,'scope':{'branches':[1,2,3]}},
                  {'product_id':11,'quantity':1,'items':[{'product_id':22,'quantity':1}]},
                  {'product_id':11,'quantity':1,'branch_ids':[1,2]},
                  [{'product_id':11,'quantity':1},{'product_id':22,'quantity':1}]]
        for payload in payloads:
            self.assertIn(self.client.post('/sales',json=payload).status_code,(400,403))
        for query in ['branch_id=1&branch_id=2','branch_id=-1','branch_id=true','scope=all','branch_ids=1,2','consolidated=true']:
            self.assertEqual(self.client.get('/inventory?'+query).status_code,403)
        self.login(1)
        self.assertEqual(self.client.put('/products/11',json=dict(self.payload(),product_id=22)).status_code,404)
        self.assertEqual(InventoryAudit.query.count(),0)

    def test_selection_rechecks_multi_branch_roles_and_inactive_branches(self):
        self.login(6,branch=None)
        self.assertEqual(self.client.get('/inventory').status_code,403)
        self.assertEqual(self.client.get('/scope').status_code,200)
        self.assertEqual(self.client.post('/api/scope',json={'branch_id':2}).status_code,200)
        self.assertEqual({r['product_id'] for r in self.client.get('/inventory').get_json()},{22})
        self.assertEqual(self.client.post('/remove-stock/22',data={'quantity_to_remove':'1','expected_version':'1','reason':'test'}).status_code,403)
        self.assertEqual(self.client.post('/api/scope',json={'branch_id':3}).status_code,403)
        self.assertEqual(self.client.post('/api/scope',json={'consolidated':True}).status_code,403)
        db.session.get(Branch,2).active=False; db.session.commit()
        self.assertEqual(self.client.get('/inventory').status_code,403)
        self.assertEqual(self.client.post('/api/scope',json={'branch_id':1}).status_code,200)
        self.assertEqual(self.client.get('/inventory').status_code,200)

    def test_unassigned_legacy_admin_and_inactive_assignment_fail_closed(self):
        for uid in (5,7):
            self.login(uid,branch=None)
            for path in ['/inventory','/products','/analytics','/api/predictions','/api/admin/assignments']:
                self.assertEqual(self.client.get(path).status_code,403)
            self.assertEqual(self.client.get('/api/scope').get_json()['branches'],[])
            self.assertEqual(self.client.post('/sales',json={'product_id':11,'quantity':1}).status_code,403)

    def test_assignment_revocation_invalidates_sessions_and_cached_identity(self):
        target=app.test_client(); self.login(3,client=target)
        self.assertEqual(target.get('/inventory').status_code,200)
        self.login(1)
        response=self.client.put('/api/admin/assignments',json={'user_id':3,'branch_id':1,'branch_role':'manager','active':False})
        self.assertEqual(response.status_code,200)
        g.pop('_login_user',None)
        self.assertEqual(target.get('/inventory').status_code,302)
        self.login(3,client=target)
        self.assertEqual(target.get('/inventory').status_code,403)
        self.assertEqual(AuthorizationAudit.query.one().action,'assignment.set')
        self.assertEqual(User.query.count(),7)

    def test_raw_assignment_revocation_is_seen_in_same_request_without_session_revision(self):
        ctx=self.context(3)
        try:
            resolve_scope()
            db.session.execute(update(UserBranch).where(UserBranch.user_id==3).values(active=False)); db.session.flush()
            with self.assertRaises(Forbidden):inventory_intelligence()
            with self.assertRaises(Forbidden):stock_tx.deduct_stock(11,1,sale=True)
        finally:ctx.pop(); db.session.rollback()

    def test_security_changes_rechecked_even_after_user_was_loaded(self):
        for field,value in [('enabled',False),('role','admin'),('password_hash','changed'),('email','changed@test.invalid')]:
            ctx=self.context(3)
            try:
                resolve_scope()
                setattr(db.session.get(User,3),field,value); db.session.flush()
                with self.assertRaises(Forbidden):stock_tx.deduct_stock(11,1,sale=True)
            finally:ctx.pop(); db.session.rollback()

    def test_aggregate_export_and_analytics_scope_match_visible_rows(self):
        for uid,branch,expected in [(3,1,{11}),(6,2,{22}),(1,None,{11,22,33}),(2,3,{33})]:
            self.login(uid,branch)
            exported=list(csv.DictReader(io.StringIO(self.client.get('/admin-settings/export-data').get_data(as_text=True))))
            self.assertEqual({int(r['Product ID']) for r in exported},expected)
            with patch('app.render_template',return_value='rendered') as render:
                self.assertEqual(self.client.get('/analytics').status_code,200)
                self.assertEqual(render.call_args.kwargs['total_units'],sum(expected)*14)
                self.assertEqual({r['product_id'] for r in render.call_args.kwargs['intelligence_items']},expected)
            with patch('app.render_template',return_value='rendered') as render:
                self.client.get('/sales')
                self.assertEqual(render.call_args.kwargs['total_sales'],sum(expected)*14)

    def test_hq_admin_branch_and_assignment_administration_is_audited_and_atomic(self):
        self.login(1,branch=None)
        self.assertEqual(self.client.get('/branch-administration').status_code,200)
        response=self.client.post('/api/admin/branches',json={'code':'FOUR','display_name':'Fourth','timezone':'UTC'})
        self.assertEqual(response.status_code,200)
        bid=response.get_json()['branch_id']
        self.assertEqual(Branch.query.count(),4)
        self.assertEqual(self.client.patch(f'/api/admin/branches/{bid}',json={'active':False}).status_code,200)
        self.assertEqual(self.client.patch(f'/api/admin/branches/{bid}',json={'code':'CHANGED'}).status_code,400)
        self.assertEqual(self.client.post('/api/admin/branches',json={'code':'BAD','display_name':'Bad','timezone':'No/Such_Zone'}).status_code,400)
        self.assertEqual(self.client.put('/api/admin/assignments',json={'user_id':7,'branch_id':1,'branch_role':'clerk','active':True}).status_code,200)
        self.assertEqual(self.client.put('/api/admin/hq-scopes',json={'user_id':7,'hq_role':'analyst','active':True}).status_code,200)
        self.assertEqual(self.client.get('/api/admin/assignments').status_code,200)
        self.assertEqual(AuthorizationAudit.query.count(),4)
        self.assertEqual(User.query.count(),7)
        with self.assertLogs('app',level='ERROR'), patch.object(db.session,'commit',side_effect=RuntimeError('fixture')):
            response=self.client.put('/api/admin/assignments',json={'user_id':3,'branch_id':1,'branch_role':'clerk','active':False})
        self.assertEqual(response.status_code,500)
        self.assertTrue(db.session.get(UserBranch,(3,1)).active)
        self.assertEqual(AuthorizationAudit.query.count(),4)

    def test_admin_and_selection_csrf_remains_required(self):
        self.login(1)
        for method,path in [('post','/api/scope'),('post','/scope'),('post','/api/admin/branches'),
                            ('put','/api/admin/assignments'),('put','/api/admin/hq-scopes'),('post','/branch-administration')]:
            self.assertEqual(getattr(self.client,method)(path,json={},headers={'X-CSRF-Token':''}).status_code,400)

    def test_every_registered_endpoint_has_explicit_policy(self):
        special={'scope_selection','scope_page','branch_stock_api','lot_api'}
        for rule in app.url_map.iter_rules():
            self.assertIn(rule.endpoint,set(READ_ENDPOINTS)|set(WRITE_ENDPOINTS)|set(ADMIN_ENDPOINTS)|PUBLIC|special)

    def test_direct_admin_services_deny_non_hq_admins(self):
        for uid in (2,3,4,5):
            ctx=self.context(uid)
            try:
                for call in [lambda:branch_services.manage_branch(code='ILLEGAL',display_name='Illegal',timezone='UTC'),
                             lambda:branch_services.manage_assignment(3,2,'manager',True),
                             lambda:branch_services.manage_hq_scope(3,'admin',True),
                             lambda:branch_services.assignment_directory()]:
                    with self.assertRaises(Forbidden):call()
            finally:ctx.pop(); db.session.rollback()
        self.assertEqual(AuthorizationAudit.query.count(),0)

    def test_hq_revocation_and_branch_deactivation_do_not_leave_consolidated_cache(self):
        ctx=self.context(2,branch=None)
        try:
            self.assertEqual(set(resolve_scope().branches),{1,2,3})
            db.session.get(Branch,2).active=False; db.session.flush()
            self.assertEqual(set(resolve_scope().branches),{1,3})
            db.session.get(UserHQScope,2).active=False; db.session.flush()
            with self.assertRaises(Forbidden):inventory_intelligence()
        finally:ctx.pop(); db.session.rollback()

    def test_t05_migration_artifacts_compile_and_apply_only_to_disposable_sqlite(self):
        from pathlib import Path
        from sqlalchemy.schema import CreateTable
        from sqlalchemy.dialects import mysql, sqlite
        import sqlite3
        for name,dialect in [('mysql',mysql.dialect()),('sqlite',sqlite.dialect())]:
            artifact=Path('migrations/t05_v001_authorization_audit_'+name+'.sql').read_text(encoding='utf-8')
            compiled=str(CreateTable(AuthorizationAudit.__table__).compile(dialect=dialect)).strip()
            self.assertIn('\n'.join(line.rstrip() for line in compiled.splitlines())+';',artifact)
            self.assertNotIn('DROP ',artifact)
            self.assertNotIn('ALTER ',artifact)
        connection=sqlite3.connect(':memory:')
        try:
            connection.executescript(Path('migrations/t05_v001_authorization_audit_sqlite.sql').read_text())
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM authorization_audit').fetchone()[0],0)
        finally:connection.close()

    def test_duplicate_form_resource_identifiers_are_rejected(self):
        from werkzeug.datastructures import MultiDict
        self.login(3)
        response=self.client.post('/sales',data=MultiDict([('product_id','11'),('product_id','22'),('quantity','1')]))
        self.assertEqual(response.status_code,403)
        self.assertEqual(InventoryAudit.query.count(),0)

    def test_admin_invalid_identifiers_and_role_types_do_not_create_or_change_records(self):
        self.login(1)
        self.assertEqual(self.client.patch('/api/admin/branches/0',json={'code':'BAD','display_name':'Bad','timezone':'UTC'}).status_code,403)
        self.assertEqual(Branch.query.count(),3)
        self.assertEqual(self.client.put('/api/admin/assignments',json={'user_id':3,'branch_id':1,'branch_role':[],'active':True}).status_code,400)
        self.assertEqual(self.client.put('/api/admin/hq-scopes',json={'user_id':3,'hq_role':{},'active':True}).status_code,400)
        self.assertEqual(AuthorizationAudit.query.count(),0)
