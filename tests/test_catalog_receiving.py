"""T06 catalog, receiving and policy contracts on disposable SQLite only."""
import unittest
from datetime import datetime, date, timedelta, timezone
from unittest.mock import patch
from sqlalchemy import select, func
from flask import g
from werkzeug.exceptions import Forbidden
import test_branch_authorization as authorization_fixture
from app import app
from database import db
from models import (SKU, SKUIdentity, Category, CategoryExpiryPolicy, ProductExpiryPolicyOverride,
                    BranchProduct, BranchStock, StockLot, StockReceipt, CatalogAudit, CompanyExpirySettings,
                    Product, Inventory, Sale, InventoryAudit, LegacyProductMapping)
from expiry import Policy, POLICY_FIELDS, validate_policy, canonical_expiry, classify, UTC
from security import MutationError
import receiving_services as receiving
import catalog_services as catalog

NOW=datetime(2030,1,1,tzinfo=UTC)
DEFAULT=dict(require_expiry=True,early_days=7,high_days=3,critical_days=1,
             expected_shelf_life_days=30,min_remaining_days_on_receipt=0,min_remaining_days_on_transfer_receipt=1)


class ExpirySemanticsTests(unittest.TestCase):
    def policy(self,**changes):
        return Policy(source='category',key=1,revision=1,effective_at='2026-01-01T00:00:00Z',**dict(DEFAULT,**changes))
    def test_policy_integer_bounds_order_and_complete_override(self):
        for changes in [dict(early_days=3),dict(high_days=1),dict(critical_days=-1),dict(early_days=3651),
                        dict(early_days=True),dict(critical_days=0.5),dict(expected_shelf_life_days=0),
                        dict(min_remaining_days_on_receipt=-1),dict(require_expiry='false')]:
            with self.subTest(changes=changes),self.assertRaises(MutationError):validate_policy(dict(DEFAULT,**changes))
        with self.assertRaises(MutationError):validate_policy({'require_expiry':False})
        self.assertFalse(validate_policy(dict(DEFAULT,require_expiry=False))['require_expiry'])
    def test_inclusive_warning_thresholds_and_exclusive_expiry_instant(self):
        expiry=date(2030,1,20);instant=canonical_expiry(expiry,'Asia/Singapore')
        for days,status in [(8,'healthy'),(7,'early'),(6,'early'),(4,'early'),(3,'high'),(2,'high'),(1,'critical'),(0,'critical')]:
            as_of=datetime(2030,1,20,tzinfo=UTC)-timedelta(days=days)
            result=classify(policy=self.policy(),expiry_date=expiry,expiry_timezone='Asia/Singapore',expires_at_utc=instant,
                            branch_timezone='Asia/Singapore',as_of=as_of)
            self.assertEqual((result['status'],result['days_remaining']),(status,days))
        for offset,status in [(-1,'critical'),(0,'expired'),(1,'expired')]:
            result=classify(policy=self.policy(),expiry_date=expiry,expiry_timezone='Asia/Singapore',expires_at_utc=instant,
                            branch_timezone='Asia/Singapore',as_of=instant+timedelta(microseconds=offset))
            self.assertEqual(result['status'],status)
    def test_nonperishables_optional_actual_dates_and_missing_policy(self):
        args=dict(expiry_date=None,expiry_timezone=None,expires_at_utc=None,branch_timezone='UTC',as_of=NOW)
        self.assertEqual(classify(policy=self.policy(require_expiry=False),**args)['status'],'not_tracked')
        self.assertEqual(classify(policy=self.policy(),**args)['status'],'review')
        self.assertEqual(classify(policy=None,**args)['status'],'review')
        args.update(expiry_date=date(2029,1,1),expiry_timezone='UTC',expires_at_utc=canonical_expiry(date(2029,1,1),'UTC'))
        self.assertEqual(classify(policy=self.policy(require_expiry=False),**args)['status'],'expired')
        self.assertEqual(classify(policy=self.policy(require_expiry=False),validation_state='quarantine',**args)['status'],'review')
    def test_branch_calendar_differs_at_same_instant_and_host_clock_is_irrelevant(self):
        expiry=date(2030,1,8);instant=canonical_expiry(expiry,'Asia/Singapore');now=datetime(2030,1,1,17,tzinfo=UTC)
        base=dict(policy=self.policy(),expiry_date=expiry,expiry_timezone='Asia/Singapore',expires_at_utc=instant,as_of=now)
        a=classify(branch_timezone='Asia/Singapore',**base)
        b=classify(branch_timezone='America/Los_Angeles',**base)
        self.assertEqual((a['days_remaining'],b['days_remaining']),(6,7))
        self.assertEqual(canonical_expiry(expiry,'Asia/Singapore'),datetime(2030,1,8,16,tzinfo=UTC))
    def test_dst_short_long_days_ambiguous_midnight_and_skipped_day(self):
        self.assertEqual(canonical_expiry(date(2026,3,8),'America/New_York')-canonical_expiry(date(2026,3,7),'America/New_York'),timedelta(hours=23))
        self.assertEqual(canonical_expiry(date(2026,11,1),'America/New_York')-canonical_expiry(date(2026,10,31),'America/New_York'),timedelta(hours=25))
        self.assertEqual(canonical_expiry(date(2020,10,31),'America/Havana'),datetime(2020,11,1,4,tzinfo=UTC))
        self.assertEqual(canonical_expiry(date(2018,11,3),'America/Sao_Paulo'),datetime(2018,11,4,3,tzinfo=UTC))
        with self.assertRaises(MutationError):canonical_expiry(date(2011,12,29),'Pacific/Apia')
        with self.assertRaises(MutationError):canonical_expiry('2030-02-30','UTC')
        with self.assertRaises(MutationError):canonical_expiry('2030-01-01','No/Zone')
        with self.assertRaises(ValueError):classify(policy=self.policy(),expiry_date=None,expiry_timezone=None,expires_at_utc=None,branch_timezone='UTC',as_of=datetime(2030,1,1))


class CatalogReceivingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=authorization_fixture.BranchAuthorizationTests();self.fixture.setUp();self.client=self.fixture.client
        self.fixture.login(1,branch=None)
        self.cat=self.post('/api/categories',dict(code='BEVERAGES',display_name='Beverages',reason='HQ approved identity'))['category_id']
        self.put(f'/api/expiry-policies/category/{self.cat}',dict(policy=DEFAULT,expected_revision=0,reason='HQ approved policy'))
        self.put('/api/expiry-settings',dict(label_timezone='Asia/Singapore',expected_version=0,reason='Label interpretation approved'))
        self.sku=self.post('/api/skus',self.sku_payload())['sku_id']
        for branch in (1,2,3):
            self.put(f'/api/assortments/{branch}/{self.sku}',dict(enabled=True,expected_version=0,reason='HQ approved assortment'))
    def tearDown(self):self.fixture.tearDown()
    def sku_payload(self,**changes):
        return dict(dict(code='COLA-500',name='Coca-Cola 500ml',category_id=self.cat,unit='unit',
                         variant_attributes={'brand':'Coca-Cola','volume':'500ml'},default_price='2.50',
                         reason='HQ approved SKU'),**changes)
    def request(self,method,path,data,status=200):
        response=getattr(self.client,method)(path,json=data)
        self.assertEqual(response.status_code,status,(path,response.get_data(as_text=True)))
        return response.get_json()
    def post(self,path,data,status=200):return self.request('post',path,data,status)
    def put(self,path,data,status=200):return self.request('put',path,data,status)
    def patch(self,path,data,status=200):return self.request('patch',path,data,status)
    def delivery(self,**changes):
        return dict(dict(mode='new_lot',sku_id=self.sku,lot_code='INTERNAL-1',supplier_lot_code='SUPPLIER-BATCH-7',
                         quantity=8,expiry_date='2030-01-08',external_reference='DELIVERY-1-LINE-1',
                         reason='Verified delivery',evidence='Actual manufacturer label and delivery note'),**changes)
    def receive(self,data=None,status=200):
        with patch('receiving_services.utcnow',return_value=NOW):
            return self.post('/api/receipts',data or self.delivery(),status)
    def own_stock(self):
        with patch('receiving_services.utcnow',return_value=NOW):
            response=self.client.get('/api/sku-stock');self.assertEqual(response.status_code,200)
            return [r for r in response.get_json() if r['sku_id']==self.sku]
    def test_hq_create_edit_archive_and_stable_variant_uniqueness(self):
        other=self.post('/api/skus',self.sku_payload(code='COLA-1000',name='Coca-Cola 1L',variant_attributes={'volume':'1L'},default_price='4.00'))
        self.assertNotEqual(other['sku_id'],self.sku)
        self.post('/api/skus',self.sku_payload(code='cola-500'),409)
        self.post('/api/skus',self.sku_payload(code='ANOTHER-CODE',name='  coca-cola 500ML  '),409)
        self.patch(f'/api/skus/{self.sku}',dict(name='Coca-Cola 500ml revised',expected_version=1,reason='Approved name correction'))
        self.patch(f'/api/skus/{self.sku}',dict(code='RENAMED',expected_version=2,reason='invalid'),400)
        self.patch(f'/api/skus/{self.sku}',dict(active=False,expected_version=2,reason='Archive variant'))
        self.assertNotIn(self.sku,{r['sku_id'] for r in self.client.get('/api/skus').get_json()})
        self.assertIsNotNone(db.session.get(SKUIdentity,self.sku))
    def test_staff_analyst_catalog_policy_price_and_forms_denied(self):
        for uid in (2,3,4):
            self.fixture.login(uid)
            for method,path,data in [('post','/api/skus',self.sku_payload()),
                ('patch',f'/api/skus/{self.sku}',dict(default_price='0.01',expected_version=1,reason='attack')),
                ('put',f'/api/assortments/1/{self.sku}',dict(enabled=True,sale_price_override='0.01',expected_version=1,reason='attack')),
                ('put',f'/api/expiry-policies/category/{self.cat}',dict(policy=dict(DEFAULT,require_expiry=False),expected_revision=1,reason='attack')),
                ('put','/api/expiry-settings',dict(label_timezone='UTC',expected_version=1,reason='attack'))]:
                self.request(method,path,data,403)
            self.assertEqual(self.client.post('/catalog/actions',data={'action':'sku','name':'Injected'}).status_code,403)
        self.assertEqual(str(db.session.get(SKU,self.sku).default_price),'2.50')
    def test_new_lot_receipt_is_usable_scoped_and_audited(self):
        self.fixture.login(3)
        result=self.receive();lot=db.session.get(StockLot,result['lot_id'])
        self.assertEqual((lot.expiry_date,lot.expiry_timezone,lot.expires_at_utc),(date(2030,1,8),'Asia/Singapore',datetime(2030,1,8,16)))
        receipt=db.session.get(StockReceipt,result['receipt_id'])
        self.assertEqual((receipt.actor_id,receipt.branch_id,receipt.quantity),(3,1,8))
        self.assertEqual(receipt.policy_snapshot['source'],'category')
        self.assertIn('tzdata_version',lot.provenance)
        self.assertEqual(self.own_stock()[0]['eligible_quantity'],8)
        self.assertEqual(CatalogAudit.query.filter_by(entity_type='receipt').count(),1)
    def test_multiple_dates_same_supplier_batch_distinct_lots_and_existing_lot(self):
        self.fixture.login(3);first=self.receive()
        second=self.receive(self.delivery(lot_code='INTERNAL-2',expiry_date='2030-01-09',external_reference='DELIVERY-2'))
        self.assertNotEqual(first['lot_id'],second['lot_id'])
        existing=dict(mode='existing_lot',sku_id=self.sku,lot_id=first['lot_id'],expiry_date='2030-01-08',quantity=3,
                      external_reference='DELIVERY-3',reason='New delivery same lot',evidence='Same actual batch label')
        before=db.session.get(BranchStock,(1,first['lot_id'])).first_received_at_utc
        self.receive(existing)
        self.assertEqual(db.session.get(BranchStock,(1,first['lot_id'])).on_hand,11)
        self.assertEqual(db.session.get(BranchStock,(1,first['lot_id'])).first_received_at_utc,before)
        self.receive(dict(existing,external_reference='CONFLICT-DATE',expiry_date='2030-01-10'),409)
        self.receive(self.delivery(external_reference='DUPLICATE-LOT'),409)
    def test_receipt_retry_has_one_effect_and_conflicting_reference_is_rejected(self):
        self.fixture.login(3);first=self.receive();second=self.receive()
        self.assertEqual(first['receipt_id'],second['receipt_id']);self.assertTrue(second['replayed'])
        self.receive(self.delivery(quantity=9),409)
        self.assertEqual(StockReceipt.query.count(),1);self.assertEqual(self.own_stock()[0]['on_hand'],8)
    def test_receiving_cannot_edit_identity_price_policy_clock_or_branch(self):
        self.fixture.login(3)
        for changes in [dict(default_price='0.01'),dict(name='New global product'),dict(require_expiry=False),dict(as_of='2020-01-01'),dict(branch_id=2),dict(label_timezone='UTC',zone_reason='attack')]:
            response=self.client.post('/api/receipts',json=self.delivery(**changes))
            self.assertIn(response.status_code,(400,403))
        self.assertEqual(StockReceipt.query.count(),0)
        self.assertEqual(str(db.session.get(SKU,self.sku).default_price),'2.50')
    def test_clerk_and_analyst_cannot_receive_or_use_direct_services(self):
        for uid in (2,4):
            self.fixture.login(uid);self.receive(status=403)
            self.assertEqual(self.client.get('/receiving').status_code,403)
            context=self.fixture.context(uid)
            try:
                with self.assertRaises(Forbidden):receiving.receive(self.delivery())
                with self.assertRaises(Forbidden):catalog.save_sku(self.sku_payload())
            finally:context.pop()
    def test_cross_branch_existing_lot_and_receipts_are_hidden(self):
        self.fixture.login(1,branch=2);second=self.receive(self.delivery(lot_code='BRANCH2-LOT'))
        self.fixture.login(3)
        self.receive(dict(mode='existing_lot',sku_id=self.sku,lot_id=second['lot_id'],expiry_date='2030-01-08',quantity=2,
            external_reference='GUESS',reason='Guess',evidence='Guess'),404)
        self.assertEqual(self.own_stock(),[])
        self.assertNotIn('BRANCH2-LOT',self.client.get('/receiving').get_data(as_text=True))
    def test_required_invalid_expired_and_minimum_life_receipts_do_not_partially_commit(self):
        self.fixture.login(3)
        for actual,status in [(None,400),('2030-02-30',400),('2029-12-31',409)]:
            self.receive(self.delivery(expiry_date=actual),status)
        self.assertEqual(StockReceipt.query.count(),0)
        self.assertEqual(StockLot.query.filter_by(sku_id=self.sku).count(),0)
        self.fixture.login(1,branch=None)
        self.put(f'/api/expiry-policies/category/{self.cat}',dict(policy=dict(DEFAULT,min_remaining_days_on_receipt=8),expected_revision=1,reason='Approved minimum life'))
        self.fixture.login(3);self.receive(status=409)
        self.receive(self.delivery(expiry_date='2030-01-09'))
    def test_last_valid_day_allowed_at_minimum_zero_and_expiry_boundary_rejected(self):
        self.fixture.login(3)
        result=self.receive(self.delivery(expiry_date='2030-01-01'))
        self.assertEqual(db.session.get(StockReceipt,result['receipt_id']).policy_snapshot['min_remaining_days_on_receipt'],0)
        with patch('receiving_services.utcnow',return_value=datetime(2030,1,1,16,tzinfo=UTC)):
            self.post('/api/receipts',self.delivery(lot_code='EXPIRED',expiry_date='2030-01-01',external_reference='AT-BOUNDARY'),409)
    def test_quarantine_arrival_conserves_physical_quantity_without_saleability(self):
        self.fixture.login(3)
        for i,actual in enumerate([None,'2029-01-01','unreadable-label']):
            result=self.receive(self.delivery(lot_code='QUAR-'+str(i),external_reference='QUAR-'+str(i),receipt_kind='quarantine',expiry_date=actual))
            stock=db.session.get(BranchStock,(1,result['lot_id']))
            self.assertEqual((stock.on_hand,stock.quarantined_quantity),(8,8))
        self.assertTrue(all(r['eligible_quantity']==0 and r['unusable_quantity']==8 for r in self.own_stock()))
    def test_nonperishable_undated_receipt_and_recorded_optional_date(self):
        self.put(f'/api/expiry-policies/category/{self.cat}',dict(policy=dict(DEFAULT,require_expiry=False),expected_revision=1,reason='Explicit non-perishable policy'))
        self.fixture.login(3);result=self.receive(self.delivery(expiry_date=None))
        lot=db.session.get(StockLot,result['lot_id']);self.assertIsNone(lot.expiry_date);self.assertIsNone(lot.expires_at_utc)
        self.assertEqual(self.own_stock()[0]['status'],'not_tracked')
        self.receive(self.delivery(lot_code='DATED',external_reference='DATED',expiry_date='2029-01-01'),409)
    def test_policy_override_full_replacement_removal_and_revision_history(self):
        override=dict(DEFAULT,early_days=14,high_days=5,critical_days=2,require_expiry=False)
        self.put(f'/api/expiry-policies/sku/{self.sku}',dict(policy=override,expected_revision=0,reason='HQ full SKU override'))
        row=next(r for r in self.client.get('/api/skus').get_json() if r['sku_id']==self.sku)
        self.assertEqual((row['policy']['source'],row['policy']['early_days']),('sku',14))
        self.put(f'/api/expiry-policies/category/{self.cat}',dict(policy=dict(DEFAULT,early_days=10),expected_revision=1,reason='Category revised'))
        self.assertEqual(catalog._effective_policy(db.session.get(SKU,self.sku)).early_days,14)
        self.request('delete',f'/api/expiry-policies/sku/{self.sku}',dict(policy=None,expected_revision=1,reason='Restore inheritance'))
        self.assertEqual(catalog._effective_policy(db.session.get(SKU,self.sku)).early_days,10)
        self.put(f'/api/expiry-policies/sku/{self.sku}',dict(policy=override,expected_revision=0,reason='New override revision'))
        self.assertEqual(db.session.get(ProductExpiryPolicyOverride,self.sku).revision,3)
        self.assertEqual(CatalogAudit.query.filter_by(entity_type='sku_policy',entity_key=str(self.sku)).count(),3)
    def test_catalog_policy_reads_do_not_grow_per_sku_and_keep_live_precedence(self):
        from sqlalchemy import event
        def read():
            statements=[]
            def count(conn,cursor,statement,parameters,context,many):
                if 'expiry_polic' in statement:statements.append(statement)
            event.listen(db.engine,'before_cursor_execute',count)
            try:
                response=self.client.get('/api/skus')
                self.assertEqual(response.status_code,200)
                return response.get_json(),len(statements)
            finally:event.remove(db.engine,'before_cursor_execute',count)
        initial,initial_count=read()
        for n in range(24):
            db.session.add(SKU(code=f'SYN-PERF-{n:02}',name=f'Synthetic catalog {n}',category_id=self.cat,
                unit='unit',default_price='1.00',active=True,version=1))
        db.session.commit()
        expanded,expanded_count=read()
        self.assertEqual(len(expanded),len(initial)+24)
        self.assertLessEqual(expanded_count,2,'Policy reads must remain bounded as the catalog grows')
        self.assertEqual(initial_count,expanded_count)
        override=dict(DEFAULT,early_days=14,high_days=5,critical_days=2,require_expiry=False)
        self.put(f'/api/expiry-policies/sku/{self.sku}',dict(policy=override,expected_revision=0,reason='Synthetic override'))
        current,_=read()
        row=next(r for r in current if r['sku_id']==self.sku)
        self.assertEqual((row['policy']['source'],row['policy']['early_days']),('sku',14))
        self.request('delete',f'/api/expiry-policies/sku/{self.sku}',dict(policy=None,expected_revision=1,reason='Synthetic restore'))
        restored,_=read()
        row=next(r for r in restored if r['sku_id']==self.sku)
        self.assertEqual((row['policy']['source'],row['policy']['early_days']),('category',DEFAULT['early_days']))
    def test_policy_enable_holds_existing_undated_stock_and_disable_never_releases(self):
        self.put(f'/api/expiry-policies/category/{self.cat}',dict(policy=dict(DEFAULT,require_expiry=False),expected_revision=1,reason='Optional label'))
        self.fixture.login(3);result=self.receive(self.delivery(expiry_date=None))
        self.fixture.login(1,branch=None)
        changed=self.put(f'/api/expiry-policies/category/{self.cat}',dict(policy=DEFAULT,expected_revision=2,reason='Require actual expiry'))
        self.assertEqual(changed['review_holdings'][0]['on_hand'],8)
        self.assertEqual(db.session.get(StockLot,result['lot_id']).validation_state,'review')
        self.put(f'/api/expiry-policies/category/{self.cat}',dict(policy=dict(DEFAULT,require_expiry=False),expected_revision=3,reason='Remove requirement'))
        self.fixture.login(3);self.assertEqual(self.own_stock()[0]['eligible_quantity'],0)
        self.assertEqual(db.session.get(StockReceipt,result['receipt_id']).policy_snapshot['revision'],2)
    def test_reserved_stock_policy_transition_is_gated_without_losing_evidence(self):
        self.put(f'/api/expiry-policies/category/{self.cat}',dict(policy=dict(DEFAULT,require_expiry=False),expected_revision=1,reason='Optional label'))
        self.fixture.login(3);result=self.receive(self.delivery(expiry_date=None))
        db.session.get(BranchStock,(1,result['lot_id'])).reserved=1;db.session.commit()
        self.fixture.login(1,branch=None)
        self.put(f'/api/expiry-policies/category/{self.cat}',dict(policy=DEFAULT,expected_revision=2,reason='Needs ledger'),409)
        self.assertEqual(db.session.get(CategoryExpiryPolicy,self.cat).revision,2)
        self.assertEqual(db.session.get(BranchStock,(1,result['lot_id'])).on_hand,8)
    def test_revision_conflicts_sku_policy_settings_and_assortment(self):
        self.patch(f'/api/skus/{self.sku}',dict(default_price='3.00',expected_version=1,reason='Price updated'))
        self.patch(f'/api/skus/{self.sku}',dict(default_price='4.00',expected_version=1,reason='Stale'),409)
        self.put(f'/api/expiry-policies/category/{self.cat}',dict(policy=DEFAULT,expected_revision=0,reason='Stale'),409)
        self.put('/api/expiry-settings',dict(label_timezone='UTC',expected_version=0,reason='Stale'),409)
        self.put(f'/api/assortments/1/{self.sku}',dict(enabled=True,expected_version=0,reason='Stale'),409)
    def test_branch_override_hq_default_and_legacy_history_are_preserved(self):
        before=[(s.sale_id,s.quantity,s.product_id) for s in Sale.query.all()]
        old_ids=[m.legacy_product_id for m in LegacyProductMapping.query.all()]
        self.put(f'/api/assortments/1/{self.sku}',dict(enabled=True,sale_price_override='3.75',expected_version=1,reason='Approved branch price'))
        self.patch(f'/api/skus/{self.sku}',dict(default_price='4.00',expected_version=1,reason='HQ current price'))
        context=self.fixture.context(3)
        try:self.assertEqual(next(r for r in receiving.receiving_choices() if r['sku_id']==self.sku)['effective_price'],'3.75')
        finally:context.pop()
        self.assertEqual([(s.sale_id,s.quantity,s.product_id) for s in Sale.query.all()],before)
        self.assertEqual([m.legacy_product_id for m in LegacyProductMapping.query.all()],old_ids)
        self.assertEqual(str(db.session.get(SKU,self.sku).default_price),'4.00')
    def test_archived_or_unapproved_sku_cannot_receive_but_old_receipts_survive(self):
        self.fixture.login(3);first=self.receive()
        self.fixture.login(1,branch=None)
        self.patch(f'/api/skus/{self.sku}',dict(active=False,expected_version=1,reason='Archive'))
        self.fixture.login(3);self.receive(self.delivery(lot_code='AFTER',external_reference='AFTER'),409)
        self.assertEqual(db.session.get(StockReceipt,first['receipt_id']).quantity,8)
        self.assertEqual(self.own_stock()[0]['eligible_quantity'],0)
    def test_audit_failure_rolls_back_lot_receipt_and_stock(self):
        self.fixture.login(3)
        with self.assertLogs('app',level='ERROR'),patch('catalog_services._record',side_effect=RuntimeError('injected')):
            self.receive(status=500)
        self.assertEqual(StockReceipt.query.count(),0);self.assertEqual(StockLot.query.filter_by(sku_id=self.sku).count(),0)
        self.assertEqual(BranchStock.query.filter_by(sku_id=self.sku).count(),0)
    def test_zone_changes_do_not_reinterpret_existing_lot_and_corrections_stay_held(self):
        self.fixture.login(3);first=self.receive();lot=db.session.get(StockLot,first['lot_id']);instant=lot.expires_at_utc
        self.fixture.login(1,branch=None)
        self.put('/api/expiry-settings',dict(label_timezone='UTC',expected_version=1,reason='Future labels approved UTC'))
        self.assertEqual(db.session.get(StockLot,lot.lot_id).expires_at_utc,instant)
        corrected=self.patch(f'/api/lot-corrections/{lot.lot_id}',dict(expected_version=1,expiry_date='2030-01-10',expiry_timezone='Asia/Singapore',reason='Genuine supplier correction',evidence='Signed replacement label'))
        self.assertEqual(corrected['validation_state'],'review');self.assertEqual(corrected['affected_holdings'][0]['branch_id'],1)
        self.fixture.login(3);self.assertEqual(self.own_stock()[0]['eligible_quantity'],0)
        self.patch(f'/api/lot-corrections/{lot.lot_id}',dict(expected_version=2,expiry_date='2031-01-01',expiry_timezone='UTC',reason='attack',evidence='attack'),403)
        self.assertEqual(db.session.get(StockReceipt,first['receipt_id']).evidence['request']['expiry_date'],'2030-01-08')
    def test_missing_policy_and_label_timezone_fail_closed(self):
        db.session.delete(db.session.get(CompanyExpirySettings,1));db.session.commit()
        self.fixture.login(3);self.receive(status=400)
        db.session.delete(db.session.get(CategoryExpiryPolicy,self.cat));db.session.commit()
        self.receive(self.delivery(expiry_date=None),409)
        self.receive(self.delivery(receipt_kind='quarantine',expiry_date=None))
        self.assertEqual(StockReceipt.query.one().policy_snapshot['source'],'unresolved')
    def test_native_sales_require_a_complete_keyed_request_and_csrf_is_retained(self):
        self.fixture.login(3);first=self.receive()
        self.post('/api/sku-sales',dict(sku_id=self.sku,quantity=1),400)
        self.assertEqual(db.session.get(BranchStock,(1,first['lot_id'])).on_hand,8)
        self.assertEqual(self.client.post('/api/receipts',json=self.delivery(),headers={'X-CSRF-Token':''}).status_code,400)

    def test_policy_managed_legacy_writes_are_gated_while_history_and_mappings_survive(self):
        self.put('/api/expiry-policies/category/1',dict(policy=DEFAULT,expected_revision=0,reason='Reviewed legacy policy cutover'))
        before=Inventory.query.filter_by(product_id=11).one().quantity
        self.fixture.login(3)
        self.post('/sales',dict(product_id=11,quantity=1),409)
        self.assertNotIn(11,{r['product_id'] for r in self.client.get('/products').get_json()})
        self.assertIn('BRANCH-1-PRIVATE',self.client.get('/sales').get_data(as_text=True))
        self.assertEqual(Inventory.query.filter_by(product_id=11).one().quantity,before)
        self.assertEqual(LegacyProductMapping.query.count(),3)

    def test_disabled_assortment_blocks_receipt_and_invalid_quantities_never_commit(self):
        self.put(f'/api/assortments/1/{self.sku}',dict(enabled=False,expected_version=1,reason='Suspend assortment'))
        self.fixture.login(3);self.receive(status=409)
        self.assertEqual(StockReceipt.query.count(),0)
        self.fixture.login(1,branch=None)
        self.put(f'/api/assortments/1/{self.sku}',dict(enabled=True,expected_version=2,reason='Resume assortment'))
        self.fixture.login(3)
        for quantity in (0,-1,True,1.5,'1.5',2147483648):self.receive(self.delivery(quantity=quantity),400)
        self.assertEqual(StockReceipt.query.count(),0)

    def test_one_identity_can_be_received_in_all_three_branches_without_stock_replication(self):
        first=None
        for branch,quantity in [(1,5),(2,8),(3,13)]:
            self.fixture.login(1,branch=branch)
            if first is None:
                first=self.receive(self.delivery(quantity=quantity))
            else:
                self.receive(dict(mode='existing_lot',sku_id=self.sku,lot_id=first['lot_id'],expiry_date='2030-01-08',
                    quantity=quantity,external_reference='BRANCH-'+str(branch),reason='Same manufacturer lot delivered to branch',evidence='Actual batch label'))
        self.assertEqual(SKU.query.filter_by(code='COLA-500').count(),1)
        self.assertEqual([(s.branch_id,s.on_hand) for s in BranchStock.query.filter_by(sku_id=self.sku).order_by(BranchStock.branch_id)],[(1,5),(2,8),(3,13)])
        self.fixture.login(3);self.assertEqual({r['branch_id'] for r in self.own_stock()},{1})

    def test_legacy_opening_balances_cannot_be_silently_treated_as_reconciled_receiving(self):
        self.fixture.login(3)
        self.receive(self.delivery(sku_id=1),409)
        self.assertEqual(StockReceipt.query.count(),0)
        self.assertEqual(Inventory.query.filter_by(product_id=11).one().quantity,11)
        self.assertEqual(BranchStock.query.filter_by(sku_id=1,branch_id=1).one().on_hand,11)

    def test_supplier_lot_alias_and_timezone_change_cannot_split_or_extend_same_batch(self):
        self.fixture.login(3);first=self.receive()
        self.receive(self.delivery(lot_code='ALIAS',external_reference='ALIAS'),409)
        self.fixture.login(1,branch=None)
        self.put('/api/expiry-settings',dict(label_timezone='UTC',expected_version=1,reason='New labels only'))
        self.fixture.login(3)
        self.receive(self.delivery(lot_code='NEW-ZONE-ALIAS',external_reference='NEW-ZONE-ALIAS'),409)
        self.assertEqual(db.session.get(StockLot,first['lot_id']).expires_at_utc,datetime(2030,1,8,16))
        self.assertEqual(StockReceipt.query.count(),1)

    def test_html_policy_boolean_is_explicit_not_silently_coerced_to_nonperishable(self):
        data=dict(DEFAULT,require_expiry='unexpected',catalog_action='policy',kind='category',key=str(self.cat),expected_revision='1',reason='Invalid boolean')
        self.assertEqual(self.client.post('/catalog/actions',data=data).status_code,400)
        self.assertTrue(db.session.get(CategoryExpiryPolicy,self.cat).require_expiry)
        self.assertEqual(db.session.get(CategoryExpiryPolicy,self.cat).revision,1)

    def test_invalid_branch_timezone_holds_eligibility_and_keeps_stock_visible(self):
        from models import Branch
        self.fixture.login(3);self.receive()
        db.session.get(Branch,1).timezone='No/Zone';db.session.commit()
        row=self.own_stock()[0]
        self.assertEqual((row['status'],row['eligible_quantity'],row['on_hand'],row['local_expiry']),('review',0,8,None))
        self.receive(self.delivery(lot_code='BAD-CLOCK',external_reference='BAD-CLOCK',expiry_date='2030-02-01'),409)

    def test_archiving_legacy_unknown_price_never_requires_a_fabricated_price(self):
        response=self.client.post('/catalog/actions',data=dict(catalog_action='archive',sku_id='1',expected_version='1',reason='Retire old definition without guessing its price'))
        self.assertEqual(response.status_code,302)
        sku=db.session.get(SKU,1)
        self.assertFalse(sku.active);self.assertIsNone(sku.default_price)
        self.assertEqual(LegacyProductMapping.query.count(),3)
        self.assertEqual(Sale.query.count(),43)
