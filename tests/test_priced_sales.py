"""T08 on explicitly disposable fixtures, including real competing requests."""
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from sqlalchemy import select,func,update,delete
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Forbidden
import test_catalog_receiving as fixture_module
import test_concurrent_receiving as concurrent_module
from database import db
from models import (PricedSale,SaleLine,SaleAllocation,SaleRefund,RefundLine,CatalogAudit,
                    InventoryOperation,InventoryMovement,BranchStock,SKU,BranchProduct,UserBranch,Sale)
import sales_services as sales
import inventory_ledger as ledger
from expiry import canonical_expiry


class PricedSalesTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.CatalogReceivingTests();self.fixture.setUp()
        self.client=self.fixture.client;self.sku=self.fixture.sku
        self.clock=patch('inventory_ledger.utcnow',return_value=fixture_module.NOW);self.clock.start()
        self.second=self.fixture.post('/api/skus',self.fixture.sku_payload(code='OTHER',name='Another SKU',variant_attributes={'size':'other'},default_price='0.10'))['sku_id']
        self.fixture.put(f'/api/assortments/1/{self.second}',dict(enabled=True,expected_version=0,reason='Approved second SKU'))
        self.fixture.fixture.login(3)
        self.first_receipt=self.fixture.receive()
        self.second_receipt=self.fixture.receive(self.fixture.delivery(sku_id=self.second,lot_code='SECOND',external_reference='SECOND'))
    def tearDown(self):self.clock.stop();self.fixture.tearDown()
    @contextmanager
    def context(self,uid=1,branch=1):
        ctx=self.fixture.fixture.context(uid,branch)
        try:yield
        finally:ctx.pop()
    def post(self,path,data,status=200):return self.fixture.post(path,data,status)
    def payload(self,**changes):return dict(dict(lines=[dict(sku_id=self.sku,quantity=2),dict(sku_id=self.second,quantity=3)],
        request_key='PRICED-1',business_reference='COUNTER-1',reason='Verified sale'),**changes)
    def create(self,**changes):return self.post('/api/priced-sales',self.payload(**changes))
    def refund_payload(self,sale,**changes):
        allocation=sale['lines'][0]['allocations'][0]['allocation_id']
        return dict(dict(lines=[dict(allocation_id=allocation,quantity=1,disposition='no_restock')],
            request_key='REFUND-1',business_reference='RETURN-1',reason='Customer returned item'),**changes)
    def refund(self,sale,data=None,status=200,reversal=False):
        return self.post(f"/api/priced-sales/{sale['sale_id']}/"+('reversal' if reversal else 'refunds'),data or self.refund_payload(sale),status)
    def count(self,model):return db.session.scalar(select(func.count()).select_from(model))
    def check(self):
        with self.context():
            self.assertTrue(all(r['balanced'] for r in ledger.reconcile() if r['sku_id'] in (self.sku,self.second)))
            self.assertTrue(all(r['balanced'] for r in sales.reconcile_sales()))

    def test_multiline_sale_exact_amounts_business_date_audit_and_ledger(self):
        result=self.create()
        self.assertEqual((result['currency'],result['total']),('MYR','5.30'))
        self.assertEqual([r['unit_price'] for r in result['lines']],['2.50','0.10'])
        row=db.session.get(PricedSale,result['sale_id']);self.assertEqual(row.cashier_id,3)
        self.assertEqual(row.business_date.isoformat(),'2030-01-01');self.assertEqual(row.total,Decimal('5.30'))
        self.assertIsNotNone(db.session.get(CatalogAudit,row.audit_id));self.assertEqual(self.count(SaleLine),2)
        self.assertEqual(self.count(SaleAllocation),2);self.check()

    def test_duplicate_sale_original_result_even_after_price_change(self):
        result=self.create();sku=db.session.get(SKU,self.sku);sku.default_price=Decimal('9.99');db.session.commit()
        retry=self.create(lines=list(reversed(self.payload()['lines'])))
        self.assertTrue(retry['replayed']);self.assertEqual(dict(retry,replayed=False),result)
        self.post('/api/priced-sales',self.payload(reason='changed'),409)
        self.assertEqual(self.count(PricedSale),1);self.check()

    def test_branch_override_snapshot_and_later_price_changes_never_rewrite(self):
        offer=db.session.get(BranchProduct,(1,self.sku));offer.sale_price_override=Decimal('3.33');db.session.commit()
        result=self.create();self.assertEqual(result['total'],'6.96')
        offer.sale_price_override=Decimal('5.55');db.session.commit()
        line=db.session.scalar(select(SaleLine).where(SaleLine.sku_id==self.sku))
        self.assertEqual((line.unit_price,line.price_snapshot['source']),(Decimal('3.33'),'branch_override'))
        refund=self.refund(result);self.assertEqual(refund['total'],'3.33')

    def test_all_lines_validate_before_any_deduction_insufficient_rollback(self):
        self.post('/api/priced-sales',self.payload(lines=[dict(sku_id=self.sku,quantity=1),dict(sku_id=self.second,quantity=99)]),409)
        self.assertEqual(self.count(PricedSale),0);self.assertEqual(self.count(InventoryMovement),2)
        self.assertEqual(db.session.get(BranchStock,(1,self.first_receipt['lot_id'])).on_hand,8);self.check()

    def test_invalid_multiline_inputs_and_untrusted_prices_currency_tax(self):
        for lines in ([],None,[dict(sku_id=self.sku,quantity=True)],[dict(sku_id=self.sku,quantity=0)],
                      [dict(sku_id=self.sku,quantity=1)]*2,[dict(sku_id=self.sku,quantity=1,unit_price='0.01')]):
            with self.subTest(lines=lines):self.post('/api/priced-sales',self.payload(lines=lines),400)
        for field,value in [('currency','USD'),('tax','0.10'),('discount','1.00'),('unit_price','0.01')]:
            self.post('/api/priced-sales',self.payload(**{field:value}),400)
        self.assertEqual(self.count(PricedSale),0)

    def test_unknown_price_rejected_zero_price_allowed_without_inference(self):
        sku=db.session.get(SKU,self.sku);sku.default_price=None;db.session.commit()
        self.post('/api/priced-sales',self.payload(),409)
        sku.default_price=Decimal('0.00');db.session.commit()
        result=self.create();self.assertEqual(result['total'],'0.30')

    def test_partial_then_full_refund_original_remaining_amount(self):
        sale=self.create();first=self.refund(sale)
        self.assertEqual((first['total'],first['remaining_refundable_quantity']),('2.50',4))
        full=self.refund(sale,dict(mode='full',request_key='FULL',business_reference='RETURN',reason='Refund remainder'))
        self.assertEqual((full['total'],full['remaining_refundable_quantity']),('2.80',0))
        self.assertTrue(self.refund(sale,dict(mode='full',request_key='FULL',business_reference='RETURN',reason='Refund remainder'))['replayed'])
        self.refund(sale,dict(mode='full',request_key='OTHER',business_reference='RETURN',reason='No remaining'),409)
        with self.context():detail=sales.sale_detail(sale['sale_id'])
        self.assertEqual((detail['refunded_total'],detail['net_total']),('5.30','0.00'));self.check()

    def test_excessive_and_changed_duplicate_refund_rejected(self):
        sale=self.create();data=self.refund_payload(sale);data['lines'][0]['quantity']=3
        self.refund(sale,data,409);data['lines'][0]['quantity']=1
        first=self.refund(sale,data);self.assertTrue(self.refund(sale,data)['replayed'])
        self.refund(sale,dict(data,reason='Changed reason'),409)
        self.assertEqual(self.count(SaleRefund),1)

    def test_full_reversal_links_compensation_without_deleting_original(self):
        sale=self.create()
        result=self.refund(sale,dict(mode='full',request_key='REVERSE',business_reference='VOID-1',reason='Authorized cancellation'),reversal=True)
        self.assertEqual((result['kind'],result['total']),('reversal','5.30'))
        self.assertEqual(result['original_operation_id'],sale['operation_id'])
        self.assertEqual((self.count(PricedSale),self.count(SaleLine),self.count(SaleAllocation)),(1,2,2));self.check()

    def test_partial_reversal_not_allowed(self):
        sale=self.create();self.refund(sale,self.refund_payload(sale),409,reversal=True)

    def test_financial_only_refund_has_no_stock_effect(self):
        sale=self.create();before=self.count(InventoryMovement);self.refund(sale)
        self.assertEqual(self.count(InventoryMovement),before)
        self.assertEqual(db.session.get(BranchStock,(1,self.first_receipt['lot_id'])).on_hand,6);self.check()

    def test_verified_saleable_return_uses_original_lot_and_preserves_expiry(self):
        sale=self.create();data=self.refund_payload(sale);data['lines'][0].update(disposition='saleable',saleable_verified=True,inspection_evidence='Sealed label inspected')
        result=self.refund(sale,data);self.assertEqual(result['lines'][0]['disposition'],'saleable')
        stock=db.session.get(BranchStock,(1,self.first_receipt['lot_id']));self.assertEqual((stock.on_hand,stock.quarantined_quantity),(7,0));self.check()

    def test_unverified_saleable_return_denied(self):
        sale=self.create();data=self.refund_payload(sale);data['lines'][0]['disposition']='saleable'
        self.refund(sale,data,400);self.assertEqual(self.count(SaleRefund),0)

    def test_expired_return_is_refunded_but_quarantined_not_available(self):
        sale=self.create();data=self.refund_payload(sale);data['lines'][0].update(disposition='saleable',saleable_verified=True,inspection_evidence='Inspected original returned label')
        with patch('inventory_ledger.utcnow',return_value=canonical_expiry('2030-01-08','Asia/Singapore')):result=self.refund(sale,data)
        self.assertEqual(result['lines'][0]['disposition'],'quarantine')
        stock=db.session.get(BranchStock,(1,self.first_receipt['lot_id']));self.assertEqual((stock.on_hand,stock.quarantined_quantity),(7,1));self.check()

    def test_damaged_quarantine_return_does_not_increase_available_quantity(self):
        sale=self.create();data=self.refund_payload(sale);data['lines'][0].update(disposition='quarantine',inspection_evidence='Damaged sealed package')
        self.refund(sale,data);stock=db.session.get(BranchStock,(1,self.first_receipt['lot_id']))
        self.assertEqual((stock.on_hand,stock.quarantined_quantity),(7,1));self.check()

    def test_refund_role_branch_and_direct_service_matrix(self):
        sale=self.create()
        for uid in (2,4,5,7):
            self.fixture.fixture.login(uid);self.refund(sale,status=403)
        self.fixture.fixture.login(1,branch=2);self.refund(sale,status=404)
        with self.context(3,2):
            with self.assertRaises(Forbidden):sales.refund_sale(sale['sale_id'],self.refund_payload(sale))
        self.fixture.fixture.login(1,branch=1);self.refund(sale)

    def test_cross_sale_allocation_and_branch_body_injection_denied(self):
        first=self.create();second=self.create(request_key='SECOND-SALE')
        self.refund(first,self.refund_payload(second),404)
        self.post('/api/priced-sales',self.payload(branch_id=2),403)
        self.post('/api/priced-sales',self.payload(actor_id=1),403)

    def test_creation_and_refund_audit_failure_roll_back_every_effect(self):
        with patch('catalog_services._record',side_effect=RuntimeError('Synthetic operation audit failure')):
            self.post('/api/priced-sales',self.payload(),500)
        self.assertEqual(self.count(PricedSale),0);self.assertEqual(self.count(InventoryMovement),2)
        sale=self.create();data=self.refund_payload(sale);data['lines'][0].update(disposition='saleable',saleable_verified=True,inspection_evidence='Inspected')
        with patch('catalog_services._record',side_effect=RuntimeError('Synthetic refund audit failure')):self.refund(sale,data,500)
        self.assertEqual(self.count(SaleRefund),0);self.assertEqual(self.count(RefundLine),0);self.check()

    def test_immutable_sale_refund_and_audit_even_through_sql(self):
        sale=self.create();self.refund(sale)
        for statement in (update(PricedSale).values(total_minor=1),delete(PricedSale),update(SaleLine).values(unit_price_minor=1),
                          delete(SaleAllocation),update(SaleRefund).values(total_minor=1),delete(RefundLine),
                          update(CatalogAudit).where(CatalogAudit.audit_id==db.session.get(PricedSale,sale['sale_id']).audit_id).values(reason='changed')):
            with self.assertRaises(IntegrityError):db.session.execute(statement)
            db.session.rollback()

    def test_historical_unknown_revenue_is_not_current_catalog_price(self):
        self.create()
        result=self.client.get('/api/priced-sales').get_json()
        self.assertTrue(result['historical_unknown'])
        self.assertTrue(all(row['total'] is None and row['currency'] is None for row in result['historical_unknown']))
        original=db.session.scalar(select(Sale));self.assertFalse(hasattr(original,'total_minor'))

    def test_fefo_price_line_keeps_both_lot_allocations(self):
        early=self.fixture.receive(self.fixture.delivery(lot_code='EARLY',supplier_lot_code='EARLY',external_reference='EARLY',expiry_date='2030-01-04',quantity=1))
        sale=self.create(lines=[dict(sku_id=self.sku,quantity=2)])
        self.assertEqual([a['lot_id'] for a in sale['lines'][0]['allocations']],[early['lot_id'],self.first_receipt['lot_id']]);self.check()

    def test_decimal_rounding_and_large_amount_storage_are_exact(self):
        sku=db.session.get(SKU,self.sku);sku.default_price=Decimal('99999999.99');db.session.commit()
        sale=self.create(lines=[dict(sku_id=self.sku,quantity=3)])
        self.assertEqual(sale['total'],'299999999.97')
        self.assertEqual(db.session.scalar(select(SaleLine.unit_price_minor)),9999999999)
        self.assertEqual(self.refund(sale)['total'],'99999999.99')

    def test_refund_replay_denied_after_assignment_revocation(self):
        sale=self.create();self.refund(sale)
        grant=db.session.get(UserBranch,(3,1));grant.active=False;db.session.commit()
        self.refund(sale,status=403)

    def test_csrf_and_scoped_receipt_reads(self):
        sale=self.create()
        self.assertEqual(self.client.post('/api/priced-sales',json=self.payload(),headers={'X-CSRF-Token':''}).status_code,400)
        self.fixture.fixture.login(1,branch=2)
        self.assertEqual(self.client.get('/api/priced-sales?sale_id='+str(sale['sale_id'])).status_code,404)
        self.assertNotIn(sale['sale_id'],[r['sale_id'] for r in self.client.get('/api/priced-sales').get_json()['sales']])

    def test_single_sku_compatibility_route_now_records_priced_sale(self):
        data=dict(sku_id=self.sku,quantity=2,request_key='COMPAT',business_reference='COUNTER',reason='Compatible cashier')
        result=self.post('/api/sku-sales',data)
        self.assertEqual((result['kind'],result['total']),('priced_sale','5.00'))
        self.assertEqual(self.count(PricedSale),1);self.assertTrue(self.post('/api/sku-sales',data)['replayed'])

    def test_pre_t08_committed_quantity_key_replays_without_inventing_price(self):
        data=dict(sku_id=self.sku,quantity=2,request_key='OLD-QUANTITY',business_reference='COUNTER',reason='Old quantity evidence')
        with self.context(3):
            original=ledger.allocate(data);db.session.commit()
        replay=self.post('/api/sku-sales',data)
        self.assertEqual(dict(replay,replayed=False),original)
        self.assertEqual(self.count(PricedSale),0)

    def test_reserved_fulfilment_captures_current_authorized_price(self):
        data=dict(sku_id=self.sku,quantity=2,request_key='HOLD',business_reference='HOLD',reason='Customer hold')
        hold=self.post('/api/stock-reservations',data)
        sku=db.session.get(SKU,self.sku);sku.default_price=Decimal('3.00');db.session.commit()
        fulfilled=self.post('/api/stock-reservations/fulfil',dict(data,reservation_id=hold['operation_id'],request_key='FULFIL'))
        self.assertEqual((fulfilled['kind'],fulfilled['total']),('priced_reserved_sale','6.00'))
        stock=db.session.get(BranchStock,(1,self.first_receipt['lot_id']))
        self.assertEqual((stock.on_hand,stock.reserved),(6,0));self.check()

    def test_supported_amount_ceiling_rejects_without_partial_facts(self):
        sku=db.session.get(SKU,self.sku);sku.default_price=Decimal('99999999.99');db.session.commit()
        self.post('/api/priced-sales',self.payload(lines=[dict(sku_id=self.sku,quantity=1000000)]),400)
        self.assertEqual(self.count(PricedSale),0);self.check()


class ConcurrentPricedSalesTests(unittest.TestCase):
    def setUp(self):
        from sales_routes import bp
        self.fixture=concurrent_module.ConcurrentReceivingTests();self.fixture.extra_blueprints=(bp,)
        self.fixture.setUp();self.app=self.fixture.app
        client=self.fixture.client(self.fixture.tokens[0])
        self.assertEqual(client.post('/api/receipts',json=self.fixture.payload(),headers={'X-CSRF-Token':'token'}).status_code,200)
    def tearDown(self):self.fixture.tearDown()
    def payload(self,**changes):return dict(dict(lines=[dict(sku_id=self.fixture.sku,quantity=3)],request_key='SALE',business_reference='COUNTER',reason='Synthetic sale'),**changes)
    def compete(self,requests,same_actor=False):
        barrier=Barrier(2)
        def run(pair):
            index,(path,data)=pair;client=self.fixture.client(self.fixture.tokens[0 if same_actor else index]);barrier.wait(timeout=10)
            response=client.post(path,json=data,headers={'X-CSRF-Token':'token'});return response.status_code,response.get_json()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,enumerate(requests)))
    def test_competing_final_units_one_sale_only(self):
        response=self.fixture.client(self.fixture.tokens[0]).post('/api/priced-sales',json=self.payload(request_key='PREVIOUS'),headers={'X-CSRF-Token':'token'})
        self.assertEqual(response.status_code,200)
        final=self.payload(lines=[dict(sku_id=self.fixture.sku,quantity=1)],request_key='FINAL')
        results=self.compete([('/api/priced-sales',final),('/api/priced-sales',dict(final,request_key='SECOND'))])
        self.assertEqual(sorted(r[0] for r in results),[200,409])
        with self.app.app_context():
            self.assertEqual(db.session.scalar(select(BranchStock.on_hand)),0)
            self.assertEqual(db.session.scalar(select(func.count()).select_from(PricedSale)),2)
    def test_same_sale_key_race_and_lost_response_retry(self):
        results=self.compete([('/api/priced-sales',self.payload())]*2,same_actor=True)
        self.assertTrue(all(r[0] in (200,409) for r in results));self.assertIn(200,[r[0] for r in results])
        retry=self.fixture.client(self.fixture.tokens[0]).post('/api/priced-sales',json=self.payload(),headers={'X-CSRF-Token':'token'})
        self.assertEqual(retry.status_code,200);self.assertTrue(retry.get_json()['replayed'])
    def test_competing_refunds_cannot_exceed_original_quantity(self):
        client=self.fixture.client(self.fixture.tokens[0]);sale=client.post('/api/priced-sales',json=self.payload(lines=[dict(sku_id=self.fixture.sku,quantity=4)]),headers={'X-CSRF-Token':'token'}).get_json()
        allocation=sale['lines'][0]['allocations'][0]['allocation_id'];path=f"/api/priced-sales/{sale['sale_id']}/refunds"
        data=dict(lines=[dict(allocation_id=allocation,quantity=3,disposition='no_restock')],request_key='RETURN',business_reference='RETURN',reason='Synthetic return')
        results=self.compete([(path,data),(path,dict(data,request_key='SECOND-RETURN'))])
        self.assertEqual(sorted(r[0] for r in results),[200,409])
        with self.app.app_context():self.assertEqual(db.session.scalar(select(func.sum(RefundLine.quantity))),3)
