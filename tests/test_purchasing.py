"""T12 disposable purchasing, exact money, role and ledger integration."""
import unittest
from unittest.mock import patch
from sqlalchemy import select,func,update,delete
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Forbidden
import test_catalog_receiving as fixtures
from database import db
from models import (Supplier,PurchaseOrder,PurchaseOrderLine,PurchaseReceiptLink,PurchasingEvent,UserHQScope,
    UserBranch,StockLot,BranchStock,StockReceipt,InventoryOperation,InventoryMovement,SKU,Product,Inventory,CatalogAudit)
import purchasing_services as purchases
import receiving_services as receiving
import inventory_ledger as ledger


class PurchasingTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CatalogReceivingTests();self.f.setUp();self.client=self.f.client;self.sku=self.f.sku
        self.clock=patch('inventory_ledger.utcnow',return_value=fixtures.NOW);self.clock.start()
        self.receipt_clock=patch('receiving_services.utcnow',return_value=fixtures.NOW);self.receipt_clock.start()
        db.session.add(UserHQScope(user_id=5,hq_role='admin'));db.session.commit()
        self.login(1)
        self.supplier=self.post('/api/suppliers',dict(code='REAL-01',display_name='Recorded supplier',active=True,expected_version=0,reason='Actual reviewed supplier'))['supplier_id']
    def tearDown(self):self.receipt_clock.stop();self.clock.stop();self.f.tearDown()
    def login(self,uid=1,branch=1):self.f.fixture.login(uid,branch)
    def post(self,path,data,status=200):return self.f.post(path,data,status)
    def payload(self,**changes):return dict(dict(supplier_id=self.supplier,expected_date=None,lines=[dict(sku_id=self.sku,quantity=8,unit_cost='1.25')],
        request_key='CREATE',business_reference='PO-A',reason='Authorized actual purchase'),**changes)
    def create(self,**changes):return self.post('/api/purchase-orders',self.payload(**changes))
    def action(self,t,action,status=200,**changes):
        data=dict(expected_version=t['version'],request_key=action.upper(),business_reference='PO-A',reason='Reviewed '+action);data.update(changes)
        return self.post(f"/api/purchase-orders/{t['order_id']}/{action}",data,status)
    def approved(self):
        self.login(1);t=self.action(self.create(),'submit');self.login(5);return self.action(t,'approve')
    def delivery(self,**changes):return dict(dict(mode='new_lot',lot_code='ACTUAL-LOT',supplier_lot_code='MANUFACTURER-BATCH',quantity=3,
        expiry_date='2030-01-08',external_reference='DELIVERY-A',receipt_kind='normal',evidence='Actual label and delivery evidence'),**changes)
    def receive(self,t,delivery=None,status=200,key='RECEIVE'):
        return self.action(t,'receive',status,request_key=key,line_id=t['lines'][0]['line_id'],delivery=delivery or self.delivery())
    def count(self,model):return db.session.scalar(select(func.count()).select_from(model))
    def check(self):
        ctx=self.f.fixture.context(1,1)
        try:
            self.assertTrue(all(r['balanced'] for r in purchases.reconcile()))
            self.assertTrue(all(r['balanced'] for r in ledger.reconcile() if r['sku_id']==self.sku))
        finally:ctx.pop()
        self.login(3)

    def test_supplier_identity_revision_archive_and_delete_protection(self):
        self.post('/api/suppliers',dict(code='real-01',display_name='Duplicate',active=True,expected_version=0,reason='Duplicate'),409)
        self.f.patch(f'/api/suppliers/{self.supplier}',dict(display_name='Revised supplier',active=False,expected_version=1,reason='Archive'))
        self.post('/api/purchase-orders',self.payload(),409)
        self.f.patch(f'/api/suppliers/{self.supplier}',dict(display_name='Revised supplier',active=True,expected_version=1,reason='Stale'),409)
        self.f.patch(f'/api/suppliers/{self.supplier}',dict(display_name='Revised supplier',active=True,expected_version=2,reason='Restore'))
        t=self.create()
        with self.assertRaises(IntegrityError):db.session.execute(delete(Supplier).where(Supplier.supplier_id==self.supplier))
        db.session.rollback();self.assertIsNotNone(db.session.get(PurchaseOrder,t['order_id']))
        self.assertEqual(self.client.delete(f'/api/suppliers/{self.supplier}').status_code,405)

    def test_hq_only_permission_matrix_and_independent_approval(self):
        t=self.action(self.create(),'submit');self.action(t,'approve',403)
        for uid in (2,3,4,6,7):
            self.login(uid)
            self.post('/api/purchase-orders',self.payload(request_key='DENY-'+str(uid)),403)
            self.post('/api/suppliers',dict(code='FORGED',display_name='X',active=True,expected_version=0,reason='X'),403)
            for action in ('submit','approve','reject','cancel'):self.action(t,action,403)
            ctx=self.f.fixture.context(uid,1)
            try:
                with self.assertRaises(Forbidden):purchases.create(self.payload())
                with self.assertRaises(Forbidden):purchases.save_supplier(dict(code='X',display_name='X',active=True,expected_version=0,reason='X'))
            finally:ctx.pop()
        self.login(5);t=self.action(t,'approve');self.assertEqual(t['approver_id'],5)

    def test_partial_complete_receipts_cost_and_supplier_lot_provenance(self):
        t=self.approved();self.assertEqual((t['ordered_cost'],t['expected_date']),('10.00',None))
        self.assertEqual(self.count(BranchStock),3) # only preserved legacy fixture holdings
        self.login(3);t=self.receive(t)
        self.assertEqual((t['state'],t['lines'][0]['received'],t['lines'][0]['outstanding']),('PartiallyReceived',3,5))
        self.assertEqual((t['received_cost'],t['outstanding_cost']),('3.75','6.25'))
        lot=t['receipt']['lot_id'];self.assertEqual(db.session.get(StockLot,lot).supplier_id,self.supplier)
        existing=dict(mode='existing_lot',lot_id=lot,quantity=5,expiry_date='2030-01-08',external_reference='DELIVERY-B',receipt_kind='normal',evidence='Same observed lot')
        t=self.receive(t,existing,key='SECOND');self.assertEqual((t['state'],t['received_cost']),('Received','10.00'))
        self.assertEqual(db.session.get(BranchStock,(1,lot)).on_hand,8);self.assertEqual(str(db.session.get(SKU,self.sku).default_price),'2.50');self.check()

    def test_duplicate_keys_changed_payload_delivery_reference_and_native_bypass(self):
        t=self.approved();self.login(3);first=self.receive(t)
        self.assertTrue(self.receive(t)['replayed']);self.receive(t,self.delivery(quantity=2),409)
        self.receive(first,self.delivery(),409,key='DIFFERENT-KEY')
        lot=first['receipt']['lot_id']
        native=dict(mode='existing_lot',sku_id=self.sku,lot_id=lot,quantity=1,expiry_date='2030-01-08',external_reference='BYPASS',reason='Bypass',evidence='Fake authority')
        self.post('/api/receipts',native,409)
        self.assertEqual(db.session.get(BranchStock,(1,lot)).on_hand,3);self.assertEqual(self.count(PurchaseReceiptLink),1);self.check()

    def test_unapproved_orders_receive_nothing_and_not_inbound(self):
        self.login(1);draft=self.create();self.login(3);self.receive(draft,status=409)
        self.login(1);submitted=self.action(draft,'submit');self.login(3);self.receive(submitted,status=409)
        self.assertEqual(self.count(StockReceipt),0);self.assertEqual(draft['lines'][0]['approved_inbound'],0)
        self.login(5);rejected=self.action(submitted,'reject');self.login(3);self.receive(rejected,status=409)
        self.assertEqual(self.count(StockReceipt),0)

    def test_cancel_remaining_retains_receipts_and_exact_cost_conservation(self):
        t=self.approved();self.login(3);t=self.receive(t);self.action(t,'cancel',403)
        self.login(1);cancelled=self.action(t,'cancel')
        self.assertEqual((cancelled['state'],cancelled['lines'][0]['received'],cancelled['lines'][0]['cancelled'],cancelled['lines'][0]['outstanding']),('Cancelled',3,5,0))
        self.assertEqual((cancelled['received_cost'],cancelled['cancelled_cost']),('3.75','6.25'));self.assertTrue(self.action(t,'cancel')['replayed'])
        self.login(3);self.receive(cancelled,status=409,key='AFTER-CANCEL');self.check()
        line=db.session.get(PurchaseOrderLine,t['lines'][0]['line_id']);line.cancelled=4;db.session.commit()
        ctx=self.f.fixture.context(1,1)
        try:self.assertFalse(purchases.reconcile()[0]['balanced'])
        finally:ctx.pop()

    def test_wrong_branch_guessed_order_and_inactive_assignment(self):
        t=self.approved();self.login(6,2)
        self.assertEqual(self.client.get(f"/api/purchase-orders/{t['order_id']}").status_code,403)
        db.session.get(UserBranch,(6,2)).branch_role='manager';db.session.commit();self.login(6,2)
        self.assertEqual(self.client.get(f"/api/purchase-orders/{t['order_id']}").status_code,404);self.receive(t,status=404)
        self.login(3);db.session.get(UserBranch,(3,1)).active=False;db.session.commit();self.receive(t,status=403)
        self.assertEqual(self.count(StockReceipt),0)

    def test_lot_expiry_missing_dates_and_quarantine(self):
        t=self.approved();self.login(3)
        self.receive(t,self.delivery(expiry_date=None),400);self.receive(t,self.delivery(expiry_date='2020-01-01'),409)
        self.receive(t,self.delivery(expiry_date='2030-02-30'),400)
        held=self.receive(t,self.delivery(expiry_date='2020-01-01',receipt_kind='quarantine'))
        stock=db.session.get(BranchStock,(1,held['receipt']['lot_id']));self.assertEqual((stock.on_hand,stock.quarantined_quantity),(3,3))
        self.assertEqual(held['receipt']['quarantined'],3);self.check()

    def test_over_receipt_stale_revision_invalid_transitions_and_archive_after_approval(self):
        t=self.approved();self.login(3);self.receive(t,self.delivery(quantity=9),409)
        self.action(t,'receive',409,expected_version=99,line_id=t['lines'][0]['line_id'],delivery=self.delivery())
        self.login(1);self.action(t,'submit',409);self.action(t,'reject',409);self.action(t,'dispatch',400)
        self.f.patch(f'/api/suppliers/{self.supplier}',dict(display_name='Archived supplier',active=False,expected_version=1,reason='No new orders'))
        self.login(3);self.receive(t);self.check() # already-approved actual delivery remains receivable

    def test_receipt_failure_rolls_back_native_and_po_all_effects(self):
        t=self.approved();self.login(3)
        original=ledger.finish
        def fail(scope,op,*args,**kwargs):
            if op.operation_type=='po_receive':raise RuntimeError('Synthetic purchasing audit failure')
            return original(scope,op,*args,**kwargs)
        with patch('inventory_ledger.finish',side_effect=fail):self.receive(t,status=500)
        self.assertEqual((self.count(StockReceipt),self.count(PurchaseReceiptLink)),(0,0))
        self.assertEqual(db.session.get(PurchaseOrderLine,t['lines'][0]['line_id']).received,0)
        self.assertEqual(self.count(StockLot),3);self.receive(t);self.check()

    def test_unknown_legacy_supplier_costs_and_mapping_are_not_invented(self):
        legacy=[(p.product_id,p.supplier) for p in db.session.scalars(select(Product))]
        self.assertTrue(all(l.supplier_id is None for l in db.session.scalars(select(StockLot))))
        self.assertEqual(self.count(PurchaseOrder),0);self.create()
        self.assertEqual([(p.product_id,p.supplier) for p in db.session.scalars(select(Product))],legacy)
        self.assertTrue(all(l.supplier_id is None for l in db.session.scalars(select(StockLot))))

    def test_exact_cost_currency_validation_and_immutable_original_lines(self):
        t=self.create(lines=[dict(sku_id=self.sku,quantity=3,unit_cost='0.10')]);self.assertEqual(t['ordered_cost'],'0.30')
        for value in (None,'1.001','-1.00','NaN','Infinity'):
            self.post('/api/purchase-orders',self.payload(request_key=str(value),lines=[dict(sku_id=self.sku,quantity=1,unit_cost=value)]),400)
        for field,value in (('currency','USD'),('actor_id',5),('branch_id',2),('total',0)):
            r=self.client.post('/api/purchase-orders',json=self.payload(**{field:value}));self.assertIn(r.status_code,(400,403))
        with self.assertRaises(IntegrityError):db.session.execute(update(PurchaseOrderLine).values(unit_cost_minor=1))
        db.session.rollback();self.assertEqual(db.session.get(PurchaseOrderLine,t['lines'][0]['line_id']).unit_cost_minor,10)

    def test_reference_collision_cannot_attach_existing_manual_receipt(self):
        self.login(3);self.f.receive(self.f.delivery(external_reference='MANUAL'))
        self.login(1);t=self.approved();self.login(3);self.receive(t,self.delivery(external_reference='MANUAL'),409)
        self.assertEqual(self.count(PurchaseReceiptLink),0);self.assertEqual(db.session.get(PurchaseOrderLine,t['lines'][0]['line_id']).received,0)

    def test_old_supplier_lot_cannot_be_assigned_or_used_by_other_supplier(self):
        self.login(3);manual=self.f.receive()
        t=self.approved();self.login(3)
        data=dict(mode='existing_lot',lot_id=manual['lot_id'],quantity=1,expiry_date='2030-01-08',external_reference='PO-OLD',evidence='Observed old label')
        self.receive(t,data,409);self.assertIsNone(db.session.get(StockLot,manual['lot_id']).supplier_id)

    def test_scoped_reads_analyst_clerk_csrf_and_html(self):
        t=self.approved();self.login(2,None)
        self.assertEqual(self.client.get('/purchasing?order_id='+str(t['order_id'])).status_code,200)
        self.login(3);html=self.client.get('/purchasing').get_data(as_text=True)
        self.assertNotIn('Create PO draft',html);self.assertNotIn('Create supplier</button>',html)
        self.login(4);self.assertEqual(self.client.get('/api/purchase-orders').status_code,403)
        self.login(1)
        with self.client.session_transaction() as state:state.pop('_csrf_token',None)
        self.post('/api/purchase-orders',self.payload(request_key='NO-CSRF'),400)

    def test_approved_inbound_reports_and_revocation_at_commit(self):
        t=self.create();self.assertEqual(self.client.get('/api/operations').get_json()['total']['approved_po_inbound'],0)
        t=self.action(t,'submit');self.login(5);t=self.action(t,'approve')
        self.assertEqual(self.client.get('/api/operations').get_json()['total']['approved_po_inbound'],8)
        self.login(3);t=self.receive(t);self.assertEqual(self.client.get('/api/operations').get_json()['total']['approved_po_inbound'],5)
        self.login(4);self.assertNotIn('approved_po_inbound',self.client.get('/api/operations').get_json()['total'])
        self.login(3);original=ledger.finish
        def revoke(scope,op,*args,**kwargs):
            if op.operation_type=='po_receive':db.session.get(UserBranch,(3,1)).active=False;db.session.flush()
            return original(scope,op,*args,**kwargs)
        existing=dict(mode='existing_lot',lot_id=t['receipt']['lot_id'],quantity=1,expiry_date='2030-01-08',external_reference='REVOKED',evidence='Same actual lot')
        with patch('inventory_ledger.finish',side_effect=revoke):self.receive(t,existing,403,key='REVOKED')
        self.assertEqual(db.session.get(PurchaseOrderLine,t['lines'][0]['line_id']).received,3);self.assertTrue(db.session.get(UserBranch,(3,1)).active)


class PurchasingConcurrencyTests(unittest.TestCase):
    def setUp(self):
        import test_concurrent_receiving as fixtures
        from purchasing_routes import bp
        self.f=fixtures.ConcurrentReceivingTests();self.f.extra_blueprints=(bp,);self.f.setUp();self.app=self.f.app
        with self.app.app_context():db.session.add(UserHQScope(user_id=2,hq_role='admin'));db.session.commit()
        self.admin=self.f.client(self.f.admin_token);self.approver=self.f.client(self.f.tokens[0])
        supplier=self.write(self.admin,'/api/suppliers',dict(code='ACTUAL',display_name='Actual supplier',active=True,expected_version=0,reason='Fixture'))
        self.t=self.write(self.admin,'/api/purchase-orders',dict(supplier_id=supplier['supplier_id'],expected_date=None,
            lines=[dict(sku_id=self.f.sku,quantity=4,unit_cost='1.11')],request_key='PO',business_reference='PO',reason='Actual agreed order'))
        self.t=self.write(self.admin,f"/api/purchase-orders/{self.t['order_id']}/submit",self.action(self.t,'SUBMIT'))
        self.t=self.write(self.approver,f"/api/purchase-orders/{self.t['order_id']}/approve",self.action(self.t,'APPROVE'))
    def tearDown(self):self.f.tearDown()
    def write(self,client,path,data):
        r=client.post(path,json=data,headers={'X-CSRF-Token':'token'});self.assertEqual(r.status_code,200,r.get_data(as_text=True));return r.get_json()
    def action(self,t,key):return dict(expected_version=t['version'],request_key=key,business_reference='PO',reason='Verified fixture transition')
    def payload(self,key='RECEIVE',**changes):
        delivery=dict(mode='new_lot',lot_code='LOT',supplier_lot_code='ACTUAL',quantity=3,expiry_date='2090-01-01',external_reference='DELIVERY',receipt_kind='normal',evidence='Actual observed label')
        delivery.update(changes)
        return dict(self.action(self.t,key),line_id=self.t['lines'][0]['line_id'],delivery=delivery)
    def compete(self,requests):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier=Barrier(2)
        def run(pair):
            i,(action,data)=pair;client=self.f.client(self.f.tokens[i]);barrier.wait(timeout=10)
            r=client.post(f"/api/purchase-orders/{self.t['order_id']}/{action}",json=data,headers={'X-CSRF-Token':'token'})
            return r.status_code,r.get_json()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,enumerate(requests)))
    def test_competing_receipts_do_not_exceed_outstanding(self):
        results=self.compete([('receive',self.payload()),('receive',self.payload('OTHER',lot_code='OTHER',supplier_lot_code='OTHER',external_reference='OTHER'))])
        self.assertEqual(sorted(r[0] for r in results),[200,409],results)
        with self.app.app_context():
            self.assertEqual(db.session.get(PurchaseOrderLine,self.t['lines'][0]['line_id']).received,3)
            self.assertEqual(db.session.scalar(select(func.sum(BranchStock.on_hand))),3)
    def test_duplicate_key_timeout_replay_and_cancel_race(self):
        manager=self.f.client(self.f.tokens[1]);path=f"/api/purchase-orders/{self.t['order_id']}/receive";data=self.payload()
        first=self.write(manager,path,data);retry=self.write(manager,path,data);self.assertTrue(retry['replayed'])
        self.assertEqual(first['receipt'],retry['receipt'])
        # Competing cancellation and final receipt both target the same version.
        self.t=first;existing=dict(mode='existing_lot',lot_id=first['receipt']['lot_id'],quantity=1,expiry_date='2090-01-01',external_reference='FINAL',receipt_kind='normal',evidence='Same label')
        receive=dict(self.action(first,'FINAL'),line_id=first['lines'][0]['line_id'],delivery=existing)
        results=self.compete([('cancel',self.action(first,'CANCEL')),('receive',receive)])
        self.assertEqual(sorted(r[0] for r in results),[200,409],results)
        with self.app.app_context():
            line=db.session.get(PurchaseOrderLine,first['lines'][0]['line_id']);self.assertEqual(line.received+line.cancelled,4)
            self.assertEqual(db.session.scalar(select(func.sum(BranchStock.on_hand))),line.received)
