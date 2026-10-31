"""T07 contracts; every fixture is disposable and explicitly selected."""
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from sqlalchemy import select, update, delete, func, text
from sqlalchemy.exc import IntegrityError
import test_catalog_receiving as catalog_fixture
import test_concurrent_receiving as concurrent_fixture
from app import app
from database import db
from models import (InventoryOperation, InventoryMovement, InventoryDocument, InventoryReservation,
                    BranchStock, StockLot, StockReceipt, Inventory, LegacyProductMapping, Sale, UserBranch)
from security import MutationError
from expiry import UTC, canonical_expiry
import inventory_ledger as ledger

NOW=catalog_fixture.NOW


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=catalog_fixture.CatalogReceivingTests();self.fixture.setUp()
        self.client=self.fixture.client;self.sku=self.fixture.sku
        self.clock=patch('inventory_ledger.utcnow',return_value=NOW);self.clock.start()
        self.fixture.fixture.login(3)
    def tearDown(self):
        self.clock.stop();self.fixture.tearDown()
    @contextmanager
    def context(self,uid=1,branch=1):
        ctx=self.fixture.fixture.context(uid,branch)
        try:yield
        finally:ctx.pop()
    def receive(self,**changes):return self.fixture.receive(self.fixture.delivery(**changes))
    def post(self,path,data,status=200):return self.fixture.post(path,data,status)
    def payload(self,**changes):
        return dict(dict(sku_id=self.sku,quantity=2,request_key='SALE-1',business_reference='CUSTOMER-1',reason='Recorded quantity sale'),**changes)
    def count(self,model):return db.session.scalar(select(func.count()).select_from(model))
    def stock(self,lot):return db.session.get(BranchStock,(1,lot),populate_existing=True)
    def check(self):
        with self.context(1,1):
            rows=[r for r in ledger.reconcile() if r['sku_id']==self.sku]
        self.assertTrue(rows);self.assertTrue(all(r['balanced'] for r in rows),rows)

    def test_receipt_sale_retry_and_changed_payload_conflict(self):
        first=self.receive();self.assertEqual(self.count(InventoryMovement),1)
        result=self.post('/api/sku-sales',self.payload())
        retry=self.post('/api/sku-sales',dict(reversed(list(self.payload().items()))))
        self.assertTrue(retry['replayed']);self.assertEqual(result['operation_id'],retry['operation_id'])
        self.post('/api/sku-sales',self.payload(quantity=3),409)
        self.assertEqual(self.stock(first['lot_id']).on_hand,6)
        self.assertEqual(self.count(InventoryDocument),2);self.assertEqual(self.count(InventoryMovement),2);self.check()

    def test_fefo_spans_lots_even_when_id_lock_order_is_opposite(self):
        late=self.receive(expiry_date='2030-01-20',quantity=3)
        early=self.receive(lot_code='EARLY',supplier_lot_code='EARLY',external_reference='EARLY',quantity=4,expiry_date='2030-01-05')
        result=self.post('/api/sku-sales',self.payload(quantity=5))
        self.assertEqual([r['lot_id'] for r in result['allocations']],[early['lot_id'],late['lot_id']])
        self.assertEqual([r['quantity_delta'] for r in result['allocations']],[-4,-1]);self.check()

    def test_equal_expiry_fifo_then_lot_id_tie_break(self):
        first=self.receive(quantity=2)
        second=self.receive(lot_code='SECOND',supplier_lot_code='SECOND',external_reference='SECOND',quantity=2)
        result=self.post('/api/sku-sales',self.payload(quantity=3))
        self.assertEqual([r['lot_id'] for r in result['allocations']],[first['lot_id'],second['lot_id']]);self.check()

    def test_undated_nonperishables_sort_after_dated_lots(self):
        self.fixture.fixture.login(1,branch=None)
        self.fixture.put(f'/api/expiry-policies/category/{self.fixture.cat}',dict(policy=dict(catalog_fixture.DEFAULT,require_expiry=False),expected_revision=1,reason='Nonperishable'))
        self.fixture.fixture.login(3)
        undated=self.receive(expiry_date=None,quantity=2)
        dated=self.receive(lot_code='DATED',supplier_lot_code='DATED',external_reference='DATED',quantity=2)
        result=self.post('/api/sku-sales',self.payload(quantity=3))
        self.assertEqual([r['lot_id'] for r in result['allocations']],[dated['lot_id'],undated['lot_id']]);self.check()

    def test_insufficient_eligible_stock_rolls_back_every_fact(self):
        receipt=self.receive(quantity=2)
        self.receive(lot_code='HOLD',supplier_lot_code='HOLD',external_reference='HOLD',quantity=100,receipt_kind='quarantine')
        self.post('/api/sku-sales',self.payload(quantity=3),409)
        self.assertEqual(self.count(InventoryOperation),2);self.assertEqual(self.stock(receipt['lot_id']).on_hand,2);self.check()

    def test_expiry_instant_and_commit_boundary_crossing_rollback(self):
        first=self.receive()
        expires=canonical_expiry('2030-01-08','Asia/Singapore')
        with patch('inventory_ledger.utcnow',return_value=expires):
            self.post('/api/sku-sales',self.payload(),409)
        with patch('inventory_ledger.utcnow',side_effect=[expires-timedelta(seconds=1),expires-timedelta(seconds=1),expires]):
            self.post('/api/sku-sales',self.payload(),409)
        self.assertEqual(self.stock(first['lot_id']).on_hand,8);self.assertEqual(self.count(InventoryMovement),1)

    def test_last_valid_second_sale_and_branch_business_date(self):
        self.receive()
        now=canonical_expiry('2030-01-08','Asia/Singapore')-timedelta(seconds=1)
        with patch('inventory_ledger.utcnow',return_value=now):result=self.post('/api/sku-sales',self.payload())
        operation=db.session.get(InventoryOperation,result['operation_id'])
        self.assertEqual(operation.business_date.isoformat(),'2030-01-08');self.check()

    def test_reservations_excluded_from_sales_then_audited_release(self):
        receipt=self.receive()
        reservation=self.post('/api/stock-reservations',self.payload(quantity=7,request_key='RESERVE'))
        self.assertEqual(self.stock(receipt['lot_id']).reserved,7)
        self.post('/api/sku-sales',self.payload(),409)
        release=self.payload(quantity=7,request_key='RELEASE',reservation_id=reservation['operation_id'])
        self.post('/api/stock-reservations/release',release)
        self.assertTrue(self.post('/api/stock-reservations/release',release)['replayed'])
        self.post('/api/sku-sales',self.payload());self.check()

    def test_reserved_sale_revalidates_expiry_and_can_release_expired_hold(self):
        receipt=self.receive()
        reserve=self.post('/api/stock-reservations',self.payload(request_key='RESERVE'))
        payload=self.payload(reservation_id=reserve['operation_id'],request_key='FULFIL')
        with patch('inventory_ledger.utcnow',return_value=canonical_expiry('2030-01-08','Asia/Singapore')):
            self.post('/api/stock-reservations/fulfil',payload,409)
            self.post('/api/stock-reservations/release',dict(payload,request_key='RELEASE'))
        self.assertEqual((self.stock(receipt['lot_id']).on_hand,self.stock(receipt['lot_id']).reserved),(8,0));self.check()

    def test_reserved_sale_business_document_and_replay(self):
        receipt=self.receive();reserve=self.post('/api/stock-reservations',self.payload(request_key='RESERVE'))
        payload=self.payload(reservation_id=reserve['operation_id'],request_key='FULFIL')
        self.post('/api/stock-reservations/fulfil',payload)
        self.assertTrue(self.post('/api/stock-reservations/fulfil',payload)['replayed'])
        self.assertEqual((self.stock(receipt['lot_id']).on_hand,self.stock(receipt['lot_id']).reserved),(6,0));self.check()

    def test_adjustment_reason_revision_and_reserved_guard(self):
        receipt=self.receive();stock=self.stock(receipt['lot_id'])
        payload=self.payload(lot_id=stock.lot_id,expected_revision=stock.version,kind='adjustment')
        self.post('/api/stock-adjustments',dict(payload,reason=''),400)
        self.post('/api/stock-adjustments',dict(payload,expected_revision=99),409)
        self.post('/api/stock-adjustments',payload)
        self.assertTrue(self.post('/api/stock-adjustments',payload)['replayed'])
        self.post('/api/stock-adjustments',dict(payload,request_key='STALE'),409)
        self.assertEqual(self.stock(stock.lot_id).on_hand,6);self.check()

    def test_quarantine_writeoff_conserves_physical_and_does_not_touch_reservations(self):
        receipt=self.receive(receipt_kind='quarantine');stock=self.stock(receipt['lot_id'])
        self.post('/api/stock-adjustments',self.payload(lot_id=stock.lot_id,expected_revision=stock.version,kind='write_off',quantity=3))
        self.assertEqual((stock.on_hand,stock.quarantined_quantity),(5,5));self.check()

    def test_audit_failure_rolls_back_business_and_allocation(self):
        receipt=self.receive()
        with patch('catalog_services._record',side_effect=RuntimeError('Synthetic audit failure')):
            self.post('/api/sku-sales',self.payload(),500)
        self.assertEqual(self.stock(receipt['lot_id']).on_hand,8)
        self.assertEqual((self.count(InventoryOperation),self.count(InventoryMovement),self.count(InventoryDocument)),(1,1,1));self.check()

    def test_direct_service_scope_injection_and_role_matrix(self):
        receipt=self.receive()
        for uid,allowed in ((1,True),(2,False),(3,True),(4,True),(5,False),(7,False)):
            self.fixture.fixture.login(uid)
            self.post('/api/sku-sales',self.payload(request_key=f'ROLE-{uid}',quantity=1),200 if allowed else 403)
        self.fixture.fixture.login(4)
        self.post('/api/stock-reservations',self.payload(),403)
        self.post('/api/stock-adjustments',self.payload(lot_id=receipt['lot_id'],expected_revision=2),403)
        self.fixture.fixture.login(3)
        for changes in ({'branch_id':2},{'actor_id':1},{'items':[{'branch_id':2}]}):
            self.post('/api/sku-sales',self.payload(**changes),403)
        with self.context(3,2):
            from werkzeug.exceptions import Forbidden
            with self.assertRaises(Forbidden):ledger.allocate(self.payload())

    def test_assignment_revocation_rechecked_on_retry(self):
        self.receive();self.post('/api/sku-sales',self.payload())
        grant=db.session.get(UserBranch,(3,1));grant.active=False;db.session.commit()
        self.post('/api/sku-sales',self.payload(),403)

    def test_cross_branch_guessed_lot_and_reservation_ids_do_not_change_stock(self):
        receipt=self.receive()
        reserve=self.post('/api/stock-reservations',self.payload(request_key='OWN-HOLD'))
        self.fixture.fixture.login(1,branch=2)
        self.post('/api/stock-adjustments',self.payload(lot_id=receipt['lot_id'],expected_revision=3),404)
        self.post('/api/stock-reservations/release',self.payload(reservation_id=reserve['operation_id']),404)
        self.assertEqual((self.stock(receipt['lot_id']).on_hand,self.stock(receipt['lot_id']).reserved),(8,2))

    def test_disabled_assortment_and_review_lot_are_unavailable(self):
        receipt=self.receive()
        from models import BranchProduct
        offer=db.session.get(BranchProduct,(1,self.sku));offer.enabled=False;db.session.commit()
        self.post('/api/sku-sales',self.payload(),409)
        offer.enabled=True;lot=db.session.get(StockLot,receipt['lot_id']);lot.validation_state='review';db.session.commit()
        self.post('/api/sku-sales',self.payload(),409)
        self.assertEqual(self.count(InventoryMovement),1)

    def test_positive_count_adjustment_and_reconciliation_detect_tampering(self):
        receipt=self.receive();stock=self.stock(receipt['lot_id'])
        self.post('/api/stock-adjustments',self.payload(lot_id=stock.lot_id,expected_revision=stock.version,direction='increase',quantity=4))
        self.assertEqual(stock.on_hand,12);self.check()
        db.session.execute(update(BranchStock).where(BranchStock.branch_id==1,BranchStock.lot_id==stock.lot_id).values(on_hand=13))
        db.session.commit()
        with self.context():
            self.assertFalse(next(r for r in ledger.reconcile() if r['lot_id']==stock.lot_id)['balanced'])

    def test_request_key_shared_namespace_conflicts_across_operation_types(self):
        first=self.receive(request_key='SHARED-KEY')
        self.post('/api/sku-sales',self.payload(request_key='SHARED-KEY'),409)
        self.assertEqual(self.stock(first['lot_id']).on_hand,8)

    def test_receipt_key_conflict_across_delivery_references_rolls_back_new_lot(self):
        self.receive(request_key='ONE-RECEIPT')
        self.fixture.receive(self.fixture.delivery(lot_code='OTHER',supplier_lot_code='OTHER',external_reference='OTHER',request_key='ONE-RECEIPT'),409)
        self.assertEqual(self.count(StockReceipt),1);self.assertEqual(self.count(InventoryOperation),1)

    def test_opening_wrong_source_list_or_count_fails_without_adopting(self):
        manifest=dict(request_key='BAD-OPEN',business_reference='REVIEW',reason='Synthetic source check',lot_id=11,
            expected_revision=1,on_hand=999,reserved=0,quarantined_quantity=0,source_receipt_ids=[],approval_evidence='Synthetic sign-off')
        with self.context():
            with self.assertRaises(MutationError):ledger.opening(manifest)
            db.session.rollback()
        self.assertEqual(self.count(InventoryOperation),0)

    def test_replay_remains_exact_after_later_adjustment(self):
        receipt=self.receive();result=self.post('/api/sku-sales',self.payload())
        stock=self.stock(receipt['lot_id'])
        self.post('/api/stock-adjustments',self.payload(lot_id=stock.lot_id,expected_revision=stock.version,request_key='COUNT',direction='increase'))
        replay=self.post('/api/sku-sales',self.payload())
        self.assertEqual(dict(replay,replayed=False),result);self.check()

    def test_committed_facts_are_immutable_even_through_sql(self):
        self.receive()
        for statement in (update(InventoryMovement).values(on_hand_delta=999),delete(InventoryMovement),
                          update(InventoryOperation).values(business_reference='rewritten'),delete(InventoryOperation),
                          update(InventoryDocument).values(kind='rewritten'),delete(InventoryDocument)):
            with self.assertRaises(IntegrityError):db.session.execute(statement)
            db.session.rollback()
        operation=db.session.scalar(select(InventoryOperation))
        with self.assertRaises(IntegrityError):
            db.session.execute(text("INSERT INTO inventory_documents(operation_id,kind,facts) VALUES (:id,'forged','{}')"),{'id':operation.operation_id})
        db.session.rollback();self.check()

    def test_unopened_legacy_native_gate_and_default_legacy_writer_gate(self):
        self.fixture.fixture.login(3)
        self.post('/api/sku-sales',dict(self.payload(),sku_id=1),409)
        before=Inventory.query.filter_by(product_id=11).one().quantity
        app.config['LEGACY_STOCK_REHEARSAL']=False
        self.post('/sales',dict(product_id=11,quantity=1),409)
        self.assertEqual(Inventory.query.filter_by(product_id=11).one().quantity,before)

    def test_reviewed_legacy_opening_preserves_source_and_is_idempotent(self):
        self.fixture.fixture.login(1)
        mapping=db.session.get(LegacyProductMapping,11);stock=self.stock(mapping.lot_id)
        before=(self.count(Sale),Inventory.query.filter_by(product_id=11).one().quantity)
        manifest=dict(request_key='OPEN',business_reference='REVIEWED-11',reason='Reviewed frozen source',lot_id=stock.lot_id,
            expected_revision=stock.version,on_hand=stock.on_hand,reserved=0,quarantined_quantity=stock.quarantined_quantity,
            source_receipt_ids=[],approval_evidence='Synthetic owner sign-off')
        with self.context(1,1):
            ledger.opening(manifest);db.session.commit()
            self.assertTrue(ledger.opening(manifest)['replayed']);db.session.commit()
            self.assertTrue(next(row for row in ledger.reconcile() if row['lot_id']==stock.lot_id)['balanced'])
        self.assertEqual((self.count(Sale),Inventory.query.filter_by(product_id=11).one().quantity),before)

    def test_preledger_receipt_opening_adopts_ids_without_reapplying_quantities(self):
        lot=StockLot(sku_id=self.sku,lot_code='PRELEDGER',validation_state='review',provenance={'actual':'old evidence'})
        db.session.add(lot);db.session.flush()
        stock=BranchStock(branch_id=1,lot_id=lot.lot_id,sku_id=self.sku,on_hand=5,reserved=0,quarantined_quantity=5,version=1)
        db.session.add(stock);db.session.flush()
        receipt=StockReceipt(branch_id=1,lot_id=lot.lot_id,sku_id=self.sku,actor_id=3,external_reference='T06-OLD',payload_hash='a'*64,
            quantity=5,quarantined_quantity=5,received_at_utc=NOW.replace(tzinfo=None),policy_snapshot={},evidence={'original':True})
        db.session.add(receipt);db.session.commit()
        self.fixture.fixture.login(1)
        manifest=dict(request_key='ADOPT',business_reference='T06-REVIEW',reason='Reconcile receipt totals',lot_id=lot.lot_id,
            expected_revision=1,on_hand=5,reserved=0,quarantined_quantity=5,source_receipt_ids=[receipt.receipt_id],approval_evidence='Synthetic sign-off')
        with self.context(1,1):
            ledger.opening(manifest);db.session.commit();self.check()
        self.assertEqual(stock.on_hand,5);self.assertEqual(self.count(StockReceipt),1)


class LedgerConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture=concurrent_fixture.ConcurrentReceivingTests();self.fixture.setUp()
        self.app=self.fixture.app
        client=self.fixture.client(self.fixture.tokens[0])
        response=client.post('/api/receipts',json=self.fixture.payload(),headers={'X-CSRF-Token':'token'})
        self.assertEqual(response.status_code,200);self.lot=response.get_json()['lot_id']
    def tearDown(self):self.fixture.tearDown()
    def compete(self,requests,same_actor=False):
        barrier=Barrier(2)
        def run(pair):
            i,(path,payload)=pair;client=self.fixture.client(self.fixture.tokens[0 if same_actor else i]);barrier.wait(timeout=10)
            response=client.post(path,json=payload,headers={'X-CSRF-Token':'token'})
            return response.status_code,response.get_json()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,enumerate(requests)))
    def payload(self,**changes):return dict(dict(sku_id=self.fixture.sku,quantity=3,request_key='SALE',business_reference='SALE',reason='Synthetic competing sale'),**changes)
    def test_competing_fefo_sales_never_oversell(self):
        results=self.compete([('/api/sku-sales',self.payload()),('/api/sku-sales',self.payload(request_key='OTHER'))])
        self.assertEqual(sorted(r[0] for r in results),[200,409],results)
        with self.app.app_context():self.assertEqual(db.session.get(BranchStock,(1,self.lot)).on_hand,1)
    def test_same_actor_same_key_concurrency_and_crash_after_commit_retry(self):
        results=self.compete([('/api/sku-sales',self.payload()),('/api/sku-sales',self.payload())],same_actor=True)
        self.assertTrue(all(r[0] in (200,409) for r in results));self.assertIn(200,[r[0] for r in results])
        retry=self.fixture.client(self.fixture.tokens[0]).post('/api/sku-sales',json=self.payload(),headers={'X-CSRF-Token':'token'})
        self.assertEqual(retry.status_code,200);self.assertTrue(retry.get_json()['replayed'])
        with self.app.app_context():
            self.assertEqual(db.session.get(BranchStock,(1,self.lot)).on_hand,1)
            self.assertEqual(db.session.scalar(select(func.count()).select_from(InventoryMovement)),2)
    def test_competing_receipt_and_adjustment_conserve_successful_deltas(self):
        receipt=dict(mode='existing_lot',sku_id=self.fixture.sku,lot_id=self.lot,quantity=5,expiry_date='2090-01-01',external_reference='NEXT',reason='Synthetic receipt',evidence='Actual fixture label')
        adjust=self.payload(lot_id=self.lot,expected_revision=2,kind='adjustment',quantity=2)
        results=self.compete([('/api/receipts',receipt),('/api/stock-adjustments',adjust)])
        self.assertTrue(all(r[0] in (200,409) for r in results),results)
        with self.app.app_context():
            expected=4+(5 if results[0][0]==200 else 0)-(2 if results[1][0]==200 else 0)
            self.assertEqual(db.session.get(BranchStock,(1,self.lot)).on_hand,expected)
