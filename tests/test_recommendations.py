"""T21 calculations and authorized services on new, aligned synthetic targets only."""
from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from sqlalchemy import select,update,func,event
from werkzeug.exceptions import Forbidden
from database import db
from models import BranchStock,StockLot,UserBranch,PurchaseOrder,StockTransfer,InventoryOperation,InventoryMovement
from security import MutationError
from services.recommendation_demo import prepare,actor,policy,SCENARIO
from services.recommendations import report
from services.recommendation_math import calculate,project,number
from services.recommendation_policy import validate,APPROVAL
from services.recommendation_facts import expected_inputs
from scripts.measure_t16 import client


def lot(quantity=20,expires=None,key=1):
    return dict(key=key,available=quantity,expires_at=expires,first_received='2001-03-31T04:00:00+00:00',source='on_hand')


class ProjectionTests(unittest.TestCase):
    def test_documented_35_unit_example_and_pack_rounding(self):
        p=validate(policy(1));r=calculate([lot()],[5]*7,'2001-03-31','Asia/Singapore',p)
        self.assertEqual(r['raw_need'],25);self.assertEqual(r['purchase_quantity'],30)
        donor=calculate([lot(80)],[5]*7,'2001-03-31','Asia/Singapore',p)
        self.assertEqual(donor['potential_excess'],30)

    def test_zero_need_does_not_order_a_minimum(self):
        r=calculate([lot(100)],[5]*7,'2001-03-31','Asia/Singapore',validate(policy(1)))
        self.assertEqual(r['raw_need'],0);self.assertEqual(r['purchase_quantity'],0)

    def test_horizon_and_unknown_policy_boundaries(self):
        for update in (dict(lead_days=None),dict(review_days=6),dict(pack_units=0),dict(minimum_order_units=0),
            dict(safety_units=-1),dict(approval_reference='not-approved'),dict(version=True)):
            with self.subTest(update=update),self.assertRaises(MutationError):validate(policy(1,**update))
        self.assertEqual(validate(policy(1,lead_days=7,review_days=0))['lead_days'],7)

    def test_missing_is_not_zero_and_nonfinite_rejected(self):
        for value in (None,True,-1,'nan','inf','unknown'):
            with self.assertRaises(MutationError):number(value)
        self.assertEqual(number(0),Decimal(0))

    def test_fefo_and_expiry_quantity_conservation(self):
        source=[lot(10,'2001-04-02T16:00:00+00:00',1),lot(20,None,2)]
        before=deepcopy(source);r=project(source,[5]*7,'2001-03-31','Asia/Singapore')
        self.assertEqual(source,before);self.assertEqual(r['expiry'],0)
        self.assertEqual(r['unmet'],5);self.assertEqual(r['ending'],0)
        r=project([lot(20,'2001-04-02T16:00:00+00:00')],[1]*7,'2001-03-31','Asia/Singapore')
        self.assertEqual(r['expiry'],18);self.assertEqual(r['unmet'],5)

    def test_expiry_timezone_conservative_day_end(self):
        r=project([lot(4,'2001-04-01T12:00:00+00:00')],[2],'2001-03-31','Asia/Singapore')
        self.assertEqual(r['expiry'],4);self.assertEqual(r['unmet'],2)

    def test_equal_expiry_and_receipt_use_numeric_lot_id_like_ledger(self):
        r=project([lot(4,key=10),lot(4,key=2)],[2],'2001-03-31','Asia/Singapore')
        remaining={item['key']:item['remaining'] for item in r['lots']}
        self.assertEqual(remaining,{10:Decimal(4),2:Decimal(2)})

    def test_inbound_only_available_on_explicit_ready_day(self):
        arrival=dict(lot(10,key='PO-1'),ready_date='2001-04-03')
        r=project([lot(1)],[2]*7,'2001-03-31','Asia/Singapore',[arrival])
        self.assertEqual(r['days'][0]['unmet'],'1');self.assertEqual(r['days'][1]['unmet'],'2')
        self.assertEqual(r['unmet'],3);self.assertEqual(r['ending'],0)

    def test_decimal_demand_is_rounded_only_at_final_quantity(self):
        r=calculate([lot(10)],[.1]*7,'2001-03-31','UTC',validate(policy(1,minimum_order_units=1,pack_units=1)))
        self.assertEqual(r['raw_need'],1);self.assertEqual(r['purchase_quantity'],1)

    def test_receipt_expectations_unknown_overdue_outside_and_perishable(self):
        now=__import__('datetime').datetime.fromisoformat(SCENARIO['as_of']);today=now.date()
        for due,perishable,reason in ((None,False,'PO_ARRIVAL_UNKNOWN'),('2001-03-30',False,'PO_OVERDUE'),
            ('2001-04-01',True,'PO_EXPIRY_ELIGIBILITY_UNKNOWN')):
            facts=dict(purchase_orders=[dict(expected_date=due,perishable=perishable,quantity=10,line_id=1,reference=1)],transfers=[])
            rows,reasons=expected_inputs(facts,today,7,'Asia/Singapore',now)
            self.assertFalse(rows);self.assertIn(reason,reasons)
        facts=dict(purchase_orders=[dict(expected_date='2001-04-10',perishable=True,quantity=10,line_id=1,reference=1)],transfers=[])
        self.assertEqual(expected_inputs(facts,today,7,'Asia/Singapore',now),([],[]))


class RecommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.temp=TemporaryDirectory();cls.root,cls.app=prepare(cls.temp.name)
    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():db.session.remove();db.engine.dispose()
        cls.temp.cleanup()
    def test_verified_fixture_calculations_and_transfers(self):
        with actor(self.app):r=report()
        by={row['branch_id']:row for row in r['items']}
        self.assertEqual(by[2]['calculation']['raw_need'],11);self.assertEqual(by[2]['calculation']['purchase_quantity'],12)
        self.assertEqual(by[1]['calculation']['potential_excess'],44)
        self.assertEqual(r['transfer_recommendations']['items'][0]['suggested_quantity'],11)
        self.assertFalse(r['actions_enabled']);self.assertFalse(r['production_numeric_enabled'])

    def test_no_policy_or_missing_lead_has_no_numeric_default(self):
        with actor(self.app),patch.dict(self.app.config,RECOMMENDATION_POLICIES={}):
            r=report();self.assertTrue(all(x['calculation'] is None for x in r['items']))
            self.assertTrue(all('POLICY_NOT_CONFIGURED' in x['reason_codes'] for x in r['items']))
        policies=deepcopy(self.app.config['RECOMMENDATION_POLICIES']);policies[(2,1)]['lead_days']=None
        with actor(self.app),patch.dict(self.app.config,RECOMMENDATION_POLICIES=policies):
            self.assertIn('POLICY_INVALID_OR_UNAPPROVED',report()['items'][1]['reason_codes'])

    def test_reserved_and_quarantined_are_excluded(self):
        with actor(self.app):
            row=db.session.scalar(select(BranchStock).where(BranchStock.branch_id==1,BranchStock.sku_id==1))
            # Counter-only unit mutation is rolled back; supported write paths are exercised separately below.
            row.reserved=20;row.quarantined_quantity=10;db.session.flush()
            r=report();own=r['items'][0]
            self.assertEqual(own['inputs']['available_stock'],50);self.assertEqual(own['calculation']['potential_excess'],14)
            self.assertEqual(own['inputs']['reserved'],20);self.assertEqual(own['inputs']['quarantined'],10)

    def test_expired_review_and_missing_opening_never_donors(self):
        with actor(self.app):
            lotrow=db.session.scalar(select(StockLot).join(BranchStock).where(BranchStock.branch_id==1))
            lotrow.validation_state='quarantine';db.session.flush()
            r=report();self.assertIsNone(r['items'][0]['calculation'])
            self.assertFalse(any(t['source_branch_id']==1 for t in r['transfer_recommendations']['items']))

    def test_actually_expired_stock_remains_physical_but_is_not_usable(self):
        from datetime import datetime
        with actor(self.app):
            lotrow=db.session.scalar(select(StockLot).join(BranchStock).where(BranchStock.branch_id==1))
            # Controlled fixture correction is rolled back at actor teardown.
            lotrow.expiry_date=date(2001,3,30);lotrow.expiry_timezone='Asia/Kuala_Lumpur'
            lotrow.expires_at_utc=datetime(2001,3,30,16);db.session.flush()
            r=report();own=r['items'][0]
            self.assertEqual(own['inputs']['physical_stock'],80)
            self.assertEqual(own['inputs']['available_stock'],0)
            self.assertEqual(own['recommendations'][2]['observed_expired_units'],80)
            self.assertFalse(any(t['source_branch_id']==1 for t in r['transfer_recommendations']['items']))

    def test_sparse_or_unavailable_forecast_withheld(self):
        import services.recommendations as service
        original=service.forecast_page
        def stale(values):
            value=original(values);value.update(state='stale',freshness='stale');return value
        with actor(self.app),patch('services.recommendations.forecast_page',side_effect=stale):
            r=report();self.assertTrue(all(x['calculation'] is None for x in r['items']))
            self.assertTrue(all('FORECAST_STALE' in x['reason_codes'] for x in r['items']))

    def test_provenance_and_qualification_are_required(self):
        import services.recommendations as service
        original=service.forecast_page
        def bad(values):value=original(values);value['qualification']='production-qualified';return value
        with actor(self.app),patch('services.recommendations.forecast_page',side_effect=bad):
            r=report();self.assertTrue(all('FORECAST_QUALIFICATION_UNSUPPORTED' in x['reason_codes'] for x in r['items']))
            self.assertTrue(all(x['calculation'] is None for x in r['items']))

    def test_routes_and_receiver_expiry_life_gate(self):
        with actor(self.app),patch.dict(self.app.config,RECOMMENDATION_ROUTES={}):
            self.assertEqual(report()['transfer_recommendations']['items'],[])
        with actor(self.app):
            row=db.session.scalar(select(BranchStock).where(BranchStock.branch_id==1));row.reserved=60;db.session.flush()
            r=report();self.assertFalse(any(t['source_branch_id']==1 for t in r['transfer_recommendations']['items']))

    def test_transfer_expiry_and_multiple_donor_budgets(self):
        from services.recommendations import transfers
        from authz import resolve_scope
        with actor(self.app):
            rows=report()['items']
            for row in rows:row['business_date']=date.fromisoformat(row['business_date'])
            rows[0]['calculation']['donor_remaining'][0]['expires_at']='2001-04-03T16:00:00+00:00'
            result=transfers(rows,resolve_scope(),__import__('datetime').datetime.fromisoformat(SCENARIO['as_of']))
            self.assertFalse(any(t['source_branch_id']==1 for t in result['items']))
            self.assertLessEqual(sum(t['suggested_quantity'] for t in result['items']),11)
            self.assertTrue(all(t['suggested_quantity']<=t['donor_surplus_before'] for t in result['items']))

    def test_receiver_minimum_life_is_inclusive(self):
        from services.recommendations import transfers
        from authz import resolve_scope
        from datetime import datetime
        with actor(self.app):
            rows=report()['items']
            for row in rows:row['business_date']=date.fromisoformat(row['business_date'])
            rows[0]['calculation']['donor_remaining'][0]['expires_at']='2001-04-09T16:00:00+00:00'
            receiver=next(r for r in rows if r['branch_id']==2)
            receiver['expiry_policy']['min_remaining_days_on_transfer_receipt']=8
            allowed=transfers(rows,resolve_scope(),datetime.fromisoformat(SCENARIO['as_of']))
            self.assertTrue(any(t['source_branch_id']==1 for t in allowed['items']))
            receiver['expiry_policy']['min_remaining_days_on_transfer_receipt']=9
            denied=transfers(rows,resolve_scope(),datetime.fromisoformat(SCENARIO['as_of']))
            self.assertFalse(any(t['source_branch_id']==1 for t in denied['items']))

    def test_explicit_marker_required_and_clock_cannot_be_extended_by_http(self):
        from simulation_clock import at_time
        with actor(self.app):
            with self.assertRaises(ValueError):
                with at_time(self.app,'2001-04-02T00:00:00+00:00'):pass
        marker=self.root/'t21-scenario.json';original=marker.read_text()
        try:
            marker.write_text('{}')
            with self.app.app_context():
                with self.assertRaises(ValueError):
                    with at_time(self.app,SCENARIO['as_of']):pass
        finally:marker.write_text(original)

    def test_staff_isolation_and_clerk_purchasing_privacy(self):
        for uid,branch in ((911,1),(912,2),(921,1),(922,2)):
            with actor(self.app,uid,branch):
                r=report();self.assertEqual({x['branch_id'] for x in r['items']},{branch})
                self.assertEqual(r['transfer_recommendations']['items'],[])
                self.assertTrue(all(x['calculation'] is None for x in r['items']))
                if uid>=920:
                    self.assertTrue(all(x['inputs']['purchase_orders'] is None and x['inputs']['transfers'] is None for x in r['items']))
                    self.assertTrue(all(x['policy'] is None and x['inputs']['supplier'] is None for x in r['items']))
                with self.assertRaises(Forbidden):report({'branch_id':3})

    def test_hq_analyst_read_only_consolidation(self):
        with actor(self.app,903):
            r=report();self.assertTrue(r['scope']['consolidated']);self.assertEqual(len(r['items']),3)
            self.assertTrue(all(x['calculation'] is not None for x in r['items']))

    def test_revoked_assignments_rechecked_same_service_context(self):
        with actor(self.app,911,1):
            report();db.session.execute(update(UserBranch).where(UserBranch.user_id==911).values(active=False))
            with self.assertRaises(Forbidden):report()

    def test_reproducibility_and_no_unintended_writes(self):
        with actor(self.app):
            def counts():return [(name,db.session.scalar(select(func.count()).select_from(model))) for name,model in
                (('operations',InventoryOperation),('movements',InventoryMovement),('po',PurchaseOrder),('transfers',StockTransfer))]
            before=counts();a=report();b=report();self.assertEqual(a,b);self.assertEqual(before,counts())
            self.assertFalse(db.session.dirty);self.assertFalse(db.session.new)

    def test_api_ui_version_calculation_parity_and_injections(self):
        with actor(self.app):r=report()
        u=client(self.app);data=u.get('/api/recommendations').get_json()
        self.assertEqual(data['decision_set_id'],r['decision_set_id'])
        for path in ('/ai-predictions',):
            response=u.get(path);self.assertEqual(response.status_code,200)
            for row in r['items']:self.assertIn(row['decision_id'],response.get_data(as_text=True))
        from services.planning_presentation import review_cards
        dashboard=u.get('/admin-dashboard')
        self.assertEqual(dashboard.status_code,200)
        for card in review_cards(r,{}):self.assertIn(card['decision_id'],dashboard.get_data(as_text=True))
        analytics=u.get('/reports')
        self.assertEqual(analytics.status_code,200)
        self.assertNotIn('data-decision-id',analytics.get_data(as_text=True))
        for extra in ('?actor_id=901','?as_of=2031-01-01','?page_size=11','?sku_id=1&sku_id=2','?approved=true'):
            self.assertIn(u.get('/api/recommendations'+extra).status_code,(400,403))
        self.assertEqual(self.app.test_client().get('/api/recommendations').status_code,302)
        self.assertEqual(u.post('/api/recommendations',json={}).status_code,405)

    def test_stock_revision_changes_decision_and_no_cache(self):
        with actor(self.app):
            a=report();stock=db.session.scalar(select(BranchStock).where(BranchStock.branch_id==2))
            stock.on_hand+=1;stock.version+=1;db.session.flush();b=report()
            self.assertNotEqual(a['decision_set_id'],b['decision_set_id']);self.assertEqual(b['items'][1]['calculation']['raw_need'],10)

    def test_unknown_stock_cannot_be_filled_with_zero(self):
        with actor(self.app),patch('services.recommendation_facts.lots',return_value={}):
            r=report();self.assertTrue(all(x['inputs']['available_stock'] is None for x in r['items']))
            self.assertTrue(all('STOCK_NOT_RECONCILED' in x['reason_codes'] for x in r['items']))

    def test_read_queries_scope_and_no_fit(self):
        sql=[]
        with actor(self.app,921,1):
            def collect(conn,cursor,statement,*args):sql.append(statement)
            event.listen(db.engine,'before_cursor_execute',collect)
            try:
                with patch('sklearn.linear_model.LinearRegression.fit',side_effect=AssertionError('fit')):report()
            finally:event.remove(db.engine,'before_cursor_execute',collect)
        self.assertFalse(any('FROM purchase_orders' in q for q in sql));self.assertFalse(any('FROM stock_transfers' in q for q in sql))
        self.assertFalse(any('FROM suppliers' in q for q in sql))
        self.assertLessEqual(len(sql),30)


class ScenarioSafetyTests(unittest.TestCase):
    def test_ordinary_t20_demo_date_mismatch_and_clock_boundaries(self):
        from forecasting.target import prepare as t20
        from forecasting.jobs import offline,publish
        from simulation.runtime import actor as old_actor
        from simulation_clock import at_time
        with TemporaryDirectory() as temp:
            root,app=t20(temp,'medium')
            try:
                with offline(app):publish(request_key='T21-mismatch',as_of='2001-03-31',skus=1)
                with old_actor(app,901):
                    r=report();self.assertTrue(all(x['calculation'] is None for x in r['items']))
                    self.assertTrue(all('FORECAST_OPERATIONAL_DATE_MISMATCH' in x['reason_codes'] for x in r['items']))
                    with self.assertRaises(ValueError):
                        with at_time(app,SCENARIO['as_of']):pass
            finally:
                with app.app_context():db.session.remove();db.engine.dispose()


class InboundReconciliationTests(unittest.TestCase):
    def setUp(self):self.temp=TemporaryDirectory();self.root,self.app=prepare(self.temp.name);self.index=0
    def tearDown(self):
        with self.app.app_context():db.session.remove();db.engine.dispose()
        self.temp.cleanup()
    def common(self):
        self.index+=1
        return dict(request_key='SYN-T21-TEST-'+str(self.index),business_reference='SYNTHETIC-T21',reason='Generated test record')
    def call(self,fn,uid=901,branch=2):
        with actor(self.app,uid,branch):
            value=fn();db.session.commit();return value
    def receiver(self):
        with actor(self.app):return next(r for r in report()['items'] if r['branch_id']==2)

    def test_concurrent_ledger_write_preserves_read_snapshot_and_next_read_refreshes(self):
        import threading
        import inventory_ledger as ledger
        from services import recommendation_facts as facts
        original=facts.lots;failures=[];completed=threading.Event()
        with actor(self.app):
            stock=db.session.scalar(select(BranchStock).where(BranchStock.branch_id==2))
            lot_id,revision=stock.lot_id,stock.version
        def write():
            try:
                with actor(self.app,901,2):
                    ledger.adjust(dict(request_key='SYN-T21-CONCURRENT',business_reference='SYN-T21',
                        reason='Controlled disposable concurrency test',sku_id=1,quantity=1,
                        lot_id=lot_id,expected_revision=revision,direction='decrease',kind='adjustment'))
                    db.session.commit()
            except Exception as exc:failures.append(exc)
            finally:completed.set()
        def overlap(*args):
            result=original(*args)
            worker=threading.Thread(target=write);worker.start()
            self.assertTrue(completed.wait(10),'Writer could not finish inside reader snapshot')
            worker.join(1);self.assertFalse(failures,failures)
            return result
        with actor(self.app),patch.object(facts,'lots',side_effect=overlap):
            first=next(r for r in report()['items'] if r['branch_id']==2)
        second=self.receiver()
        self.assertEqual(first['inputs']['physical_stock'],20)
        self.assertEqual(first['calculation']['raw_need'],11)
        self.assertEqual(second['inputs']['physical_stock'],19)
        self.assertEqual(second['calculation']['raw_need'],12)
        self.assertNotEqual(first['decision_id'],second['decision_id'])
        with actor(self.app):self.assertTrue(all(r['balanced'] for r in ledger.reconcile()))

    def test_approved_po_partial_receipt_cancel_and_physical_conservation(self):
        import purchasing_services as purchasing
        import inventory_ledger as ledger
        po=self.call(lambda:purchasing.create(dict(self.common(),supplier_id=1,expected_date='2001-04-01',
            lines=[dict(sku_id=1,quantity=12,unit_cost='1.00')])))
        self.assertEqual(self.receiver()['inputs']['expected_eligible_units'],0)
        po=self.call(lambda:purchasing.transition(po['order_id'],'submit',dict(self.common(),expected_version=po['version'])))
        self.assertEqual(self.receiver()['inputs']['expected_eligible_units'],0)
        po=self.call(lambda:purchasing.transition(po['order_id'],'approve',dict(self.common(),expected_version=po['version'])),902)
        row=self.receiver();self.assertEqual(row['inputs']['expected_eligible_units'],12);self.assertEqual(row['calculation']['raw_need'],0)
        po=self.call(lambda:purchasing.transition(po['order_id'],'receive',dict(self.common(),expected_version=po['version'],line_id=po['lines'][0]['line_id'],
            delivery=dict(mode='new_lot',lot_code='SYN-T21-PO',supplier_lot_code='SYN-T21-PO',quantity=5,expiry_date=None,
                external_reference='SYN-T21-DELIVERY',receipt_kind='normal',evidence='Generated nonperishable fixture'))),912)
        row=self.receiver();self.assertEqual(row['inputs']['physical_stock'],25);self.assertEqual(row['inputs']['expected_eligible_units'],7)
        self.assertEqual(row['calculation']['raw_need'],0)  # 25 physical + 7 remaining, never +12 again.
        po=self.call(lambda:purchasing.transition(po['order_id'],'cancel',dict(self.common(),expected_version=po['version'])))
        row=self.receiver();self.assertEqual(row['inputs']['expected_eligible_units'],0);self.assertEqual(row['inputs']['physical_stock'],25)
        self.assertEqual(row['calculation']['raw_need'],6)
        with actor(self.app):self.assertTrue(all(r['balanced'] for r in purchasing.reconcile()));self.assertTrue(all(r['balanced'] for r in ledger.reconcile()))

    def test_transfer_approval_dispatch_partial_receipt_not_double_counted(self):
        import transfer_services as transfer
        import inventory_ledger as ledger
        t=self.call(lambda:transfer.create(dict(self.common(),destination_branch_id=2,planned_arrival_utc='2001-04-01T04:00:00+00:00',
            lines=[dict(sku_id=1,quantity=10)])),branch=1)
        t=self.call(lambda:transfer.transition(t['transfer_id'],'submit',dict(self.common(),expected_version=t['version'])),branch=1)
        t=self.call(lambda:transfer.transition(t['transfer_id'],'approve',dict(self.common(),expected_version=t['version'])),902,1)
        row=self.receiver();self.assertEqual(row['inputs']['expected_eligible_units'],10);self.assertEqual(row['calculation']['raw_need'],1)
        self.assertEqual(row['inputs']['transfers'][0]['kind'],'approved_reservation')
        allocation=t['reservations'][0]
        with actor(self.app):revision=db.session.get(BranchStock,(1,allocation['lot_id'])).version
        t=self.call(lambda:transfer.transition(t['transfer_id'],'dispatch',dict(self.common(),expected_version=t['version'],
            lines=[dict(allocation_id=allocation['allocation_id'],expected_revision=revision)])),branch=1)
        row=self.receiver();self.assertEqual(row['inputs']['expected_eligible_units'],10)
        self.assertEqual(row['inputs']['transfers'][0]['kind'],'transit_remaining')
        t=self.call(lambda:transfer.transition(t['transfer_id'],'receive',dict(self.common(),expected_version=t['version'],
            lines=[dict(allocation_id=allocation['allocation_id'],quantity=4,expected_revision=0,disposition='saleable')])),912,2)
        row=self.receiver();self.assertEqual(row['inputs']['physical_stock'],24);self.assertEqual(row['inputs']['expected_eligible_units'],6)
        self.assertEqual(row['calculation']['raw_need'],1)  # Received four are already in physical, not a second arrival.
        with actor(self.app):self.assertTrue(all(r['balanced'] for r in ledger.reconcile()))
