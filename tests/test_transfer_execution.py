"""T11 disposable transfer execution, authority and conservation."""
import unittest
from unittest.mock import patch
from sqlalchemy import select,func,delete,update
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Forbidden
import test_transfers as base
from database import db
from models import (BranchStock,StockLot,UserBranch,UserHQScope,StockTransfer,InventoryMovement,
    TransferExecution,TransferTransit,TransferTransitFact,TransferDiscrepancy,TransferResolution,TransferReturnLink)
import transfer_execution as execution
import inventory_ledger as ledger
from datetime import datetime
from expiry import UTC


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.f=base.TransferTests();self.f.setUp();self.client=self.f.client;self.t=self.f.approved();self.lot=self.f.lot
        db.session.get(UserBranch,(6,2)).branch_role='manager';db.session.commit()
    def tearDown(self):self.f.tearDown()
    def login(self,uid=1,branch=1):self.f.login(uid,branch)
    def stock(self,branch=1):return db.session.get(BranchStock,(branch,self.lot),populate_existing=True)
    def payload(self,t,key='DISPATCH',**fields):return dict(expected_version=t['version'],request_key=key,business_reference='T11',reason='Verified custody and evidence',**fields)
    def post(self,t,action,data,status=200):return self.f.post(f"/api/transfers/{t['transfer_id']}/{action}",data,status)
    def dispatch_data(self,t=None):
        t=t or self.t
        return self.payload(t,lines=[dict(allocation_id=a['allocation_id'],expected_revision=db.session.get(BranchStock,(t['source_branch_id'],a['lot_id'])).version) for a in t['reservations']])
    def dispatch(self):return self.post(self.t,'dispatch',self.dispatch_data())
    def receipt_data(self,t,qty=1,key='RECEIVE',disposition='saleable'):
        stock=self.stock(2)
        return self.payload(t,key,lines=[dict(allocation_id=t['transit'][0]['allocation_id'],quantity=qty,expected_revision=stock.version if stock else 0,disposition=disposition)])
    def receive(self,t,qty=1,key='RECEIVE',disposition='saleable'):
        self.login(6,2);return self.post(t,'receive',self.receipt_data(t,qty,key,disposition))
    def check(self):
        from authentication import serializer
        with self.client.session_transaction() as state:
            uid=serializer().loads(state['_user_id'])['id'];branch=state.get('active_branch_id')
        with self.f.context(1,1):self.assertTrue(all(r['balanced'] for r in ledger.reconcile() if r['sku_id']==self.f.sku))
        with self.f.context(1,2):self.assertTrue(all(r['balanced'] for r in ledger.reconcile() if r['sku_id']==self.f.sku))
        with self.f.context(1,None):self.assertTrue(all(r['balanced'] for r in execution.reconcile()))
        transit=db.session.scalar(select(func.coalesce(func.sum(TransferTransit.remaining),0)))
        losses=db.session.scalar(select(func.coalesce(func.sum(TransferTransit.lost),0)))
        onhand=db.session.scalar(select(func.sum(BranchStock.on_hand)).where(BranchStock.sku_id==self.f.sku)) or 0
        self.assertEqual(onhand+transit+losses,8)
        self.login(uid,branch)

    def test_full_dispatch_receipt_and_original_lot_expiry(self):
        original=db.session.get(StockLot,self.lot).expires_at_utc
        t=self.dispatch();self.assertEqual((t['state'],t['transit'][0]['remaining']),('Dispatched',3))
        self.assertEqual((self.stock().on_hand,self.stock().reserved),(5,0));self.assertIsNone(self.stock(2));self.check()
        t=self.receive(t,3);self.assertEqual(t['state'],'Received');self.assertEqual(self.stock(2).on_hand,3)
        self.assertEqual(db.session.get(StockLot,self.lot).expires_at_utc,original);self.check()

    def test_multiple_partial_receipts_and_over_receipt(self):
        t=self.receive(self.dispatch(),1);self.assertEqual(t['state'],'PartiallyReceived');self.check()
        self.post(t,'receive',self.receipt_data(t,3,'TOO-MUCH'),409)
        t=self.receive(t,1,'SECOND');self.assertEqual(t['transit'][0]['remaining'],1)
        t=self.receive(t,1,'THIRD');self.assertEqual(t['state'],'Received');self.check()

    def test_idempotency_changed_payload_and_cancel_after_dispatch(self):
        payload=self.dispatch_data();t=self.dispatch()
        self.assertTrue(self.post(self.t,'dispatch',payload)['replayed'])
        self.post(t,'dispatch',dict(payload,reason='Changed'),409)
        self.f.action(t,'cancel',409,request_key='POST-DISPATCH-CANCEL')
        self.login(6,2);data=self.receipt_data(t,2);r=self.post(t,'receive',data)
        self.assertTrue(self.post(t,'receive',data)['replayed'])
        self.post(r,'receive',dict(data,reason='Changed'),409);self.assertEqual(self.stock(2).on_hand,2);self.check()

    def test_role_branch_and_direct_service_matrix(self):
        for uid,branch in ((2,1),(4,1),(3,2),(6,2),(7,1)):
            self.login(uid,branch);self.post(self.t,'dispatch',self.dispatch_data(),403 if uid!=6 else 404)
        self.login();t=self.dispatch()
        for uid,branch in ((1,1),(3,1),(2,2),(4,1),(7,1)):
            self.login(uid,branch);self.post(t,'receive',self.receipt_data(t),404 if uid in (1,3) else 403)
        with self.f.context(4,1):
            with self.assertRaises(Forbidden):execution.dispatch(t['transfer_id'],self.dispatch_data())
        self.login(6,2);self.assertEqual(self.client.get(f"/api/transfers/{t['transfer_id']}").status_code,200)
        detail=self.client.get(f"/api/transfers/{t['transfer_id']}").get_json()
        self.assertNotIn('creation_operation_id',detail);self.assertNotIn('balances',detail)
        self.assertTrue(all(e['to_state']!='Draft' for e in detail['events']))

    def test_dispatch_expired_quarantined_stale_or_missing_allocation_fails(self):
        data=self.dispatch_data();data['lines'][0]['expected_revision']=99;self.post(self.t,'dispatch',data,409)
        self.post(self.t,'dispatch',self.payload(self.t,lines=[]),400)
        lot=db.session.get(StockLot,self.lot);lot.validation_state='quarantine';db.session.commit()
        self.post(self.t,'dispatch',self.dispatch_data(),409);lot.validation_state='validated';lot.expires_at_utc=datetime(2029,1,1);db.session.commit()
        self.post(self.t,'dispatch',self.dispatch_data(),409)
        self.assertEqual((self.stock().on_hand,self.stock().reserved),(8,3));self.assertIsNone(db.session.get(TransferExecution,self.t['transfer_id']))

    def test_late_or_damaged_arrivals_are_quarantined_with_discrepancy(self):
        t=self.dispatch()
        with patch('inventory_ledger.utcnow',return_value=datetime(2030,1,9,tzinfo=UTC)):t=self.receive(t,2)
        self.assertEqual((self.stock(2).on_hand,self.stock(2).quarantined_quantity),(2,2));self.assertEqual(t['state'],'Exception')
        self.assertEqual(t['discrepancies'][0]['location'],'quarantine');self.check()
        t=self.receive(t,1,'DAMAGED','quarantine');self.assertEqual(t['state'],'Exception');self.check()
        self.login(1,2)
        for d in t['discrepancies']:
            t=self.post(t,'resolve',self.payload(t,'ACK-'+str(d['discrepancy_id']),discrepancy_id=d['discrepancy_id'],decision='quarantine_acknowledged'))
        self.assertEqual(t['state'],'Received');self.assertEqual(self.stock(2).quarantined_quantity,3);self.check()

    def test_shortage_remains_transit_found_then_received(self):
        t=self.dispatch();self.login(6,2)
        t=self.post(t,'discrepancy',self.payload(t,'MISSING',allocation_id=t['transit'][0]['allocation_id'],quantity=2))
        self.assertEqual(t['state'],'Exception');self.assertEqual(t['transit'][0]['remaining'],3);self.check()
        self.post(t,'receive',self.receipt_data(t,2),409)
        self.login(1,2);d=t['discrepancies'][0]
        t=self.post(t,'resolve',self.payload(t,'FOUND',discrepancy_id=d['discrepancy_id'],decision='found'))
        self.assertEqual(t['state'],'Dispatched');t=self.receive(t,3);self.check()

    def test_only_hq_explicit_loss_resolution_no_silent_writeoff(self):
        t=self.dispatch();self.login(6,2)
        t=self.post(t,'discrepancy',self.payload(t,'MISSING',allocation_id=t['transit'][0]['allocation_id'],quantity=1))
        data=self.payload(t,'LOSS',discrepancy_id=t['discrepancies'][0]['discrepancy_id'],decision='loss')
        self.post(t,'resolve',data,403);self.login(1,2);t=self.post(t,'resolve',data)
        self.assertEqual((t['transit'][0]['remaining'],t['transit'][0]['lost']),(2,1))
        self.assertTrue(self.post(t,'resolve',data)['replayed']);self.check();t=self.receive(t,2);self.assertEqual(t['state'],'Received');self.check()

    def test_dispatch_and_receipt_failure_rollback(self):
        with patch('inventory_ledger.finish',side_effect=RuntimeError('Synthetic dispatch failure')):self.post(self.t,'dispatch',self.dispatch_data(),500)
        self.assertEqual(self.stock().on_hand,8);self.assertIsNone(db.session.get(TransferExecution,self.t['transfer_id']))
        t=self.dispatch();self.login(6,2)
        with patch('inventory_ledger.finish',side_effect=RuntimeError('Synthetic receive failure')):self.post(t,'receive',self.receipt_data(t,3),500)
        self.assertIsNone(self.stock(2));self.assertEqual(db.session.get(TransferTransit,t['transit'][0]['allocation_id']).remaining,3);self.check()

    def test_revision_revocation_and_payload_injection(self):
        self.post(self.t,'dispatch',dict(self.dispatch_data(),actor_id=1),403)
        t=self.dispatch();self.login(6,2);data=self.receipt_data(t)
        data['lines'][0]['expected_revision']=88;self.post(t,'receive',data,409)
        db.session.get(UserBranch,(6,2)).active=False;db.session.commit();self.post(t,'receive',self.receipt_data(t),403)
        self.assertIsNone(self.stock(2));self.check()

    def test_linked_return_full_workflow_uses_original_lot_and_independent_approval(self):
        t=self.receive(self.dispatch(),3)
        original=t['transit'][0]['allocation_id']
        child=self.post(t,'return',self.payload(t,'RETURN',lines=[dict(allocation_id=original,quantity=2)],planned_arrival_utc='2030-01-03T00:00:00Z'))
        self.assertEqual((child['source_branch_id'],child['destination_branch_id']),(2,1));self.assertEqual(self.stock(2).on_hand,3)
        child=self.f.action(child,'submit',request_key='RETURN-SUBMIT');self.login(1,2)
        child=self.f.action(child,'approve',request_key='RETURN-APPROVE')
        self.assertEqual(child['reservations'][0]['lot_id'],self.lot)
        data=self.payload(child,'RETURN-DISPATCH',lines=[dict(allocation_id=child['reservations'][0]['allocation_id'],expected_revision=self.stock(2).version)])
        child=self.post(child,'dispatch',data);self.check();self.login(3,1)
        child=self.post(child,'receive',self.payload(child,'RETURN-RECEIVE',lines=[dict(allocation_id=child['transit'][0]['allocation_id'],quantity=2,expected_revision=self.stock().version,disposition='saleable')]))
        self.assertEqual(child['state'],'Received');self.assertEqual((self.stock().on_hand,self.stock(2).on_hand),(7,1));self.check()

    def test_excessive_or_unsaleable_return_rejected(self):
        t=self.receive(self.dispatch(),2,disposition='quarantine')
        self.post(t,'return',self.payload(t,'RETURN',lines=[dict(allocation_id=t['transit'][0]['allocation_id'],quantity=1)],planned_arrival_utc='2030-01-03T00:00:00Z'),409)
        self.post(t,'return',self.payload(t,'OVER',lines=[dict(allocation_id=t['transit'][0]['allocation_id'],quantity=3)],planned_arrival_utc='2030-01-03T00:00:00Z'),409)
        self.check()

    def test_immutable_transit_facts_and_reconciliation_detects_tampering(self):
        t=self.dispatch()
        with self.assertRaises(IntegrityError):db.session.execute(delete(TransferTransitFact))
        db.session.rollback()
        transit=db.session.get(TransferTransit,t['transit'][0]['allocation_id']);transit.remaining=2;transit.received=1;db.session.commit()
        with self.f.context(1,None):self.assertFalse(execution.reconcile()[0]['balanced'])

    def test_dashboard_csv_include_transit_once_and_no_revenue_change(self):
        t=self.dispatch();self.login(1,None)
        report=self.client.get('/api/operations').get_json()
        self.assertEqual(report['total']['outgoing_transit'],3);self.assertEqual(report['total']['incoming_transit'],3)
        self.assertEqual(report['total']['company_owned_physical'],report['total']['on_hand']+3)
        self.assertEqual(report['total']['recorded_gross'],'0.00')
        csv=self.client.get('/operations/export.csv').get_data(as_text=True);self.assertIn('outgoing_transit',csv)
        import csv as csv_module,io
        rows=list(csv_module.reader(io.StringIO(csv)));header=next(r for r in rows if r and r[0]=='branch_id')
        for values in rows[rows.index(header)+1:]:self.assertEqual(len(values),len(header))
        parsed=[dict(zip(header,values)) for values in rows[rows.index(header)+1:]]
        self.assertEqual(sum(int(r['outgoing_transit']) for r in parsed),3)
        self.login(6,2);report=self.client.get('/api/operations').get_json()
        self.assertEqual(report['total']['outgoing_transit'],0);self.assertEqual(report['total']['incoming_transit'],3)
        self.check()


class ExecutionConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.f=base.TransferConcurrencyTests();self.f.setUp();self.app=self.f.app
        with self.app.app_context():
            db.session.add_all([UserBranch(user_id=i,branch_id=2,branch_role='manager') for i in (2,3)]);db.session.commit()
        self.t=self.f.submitted()
        self.approved=self.f.write(self.f.admin,f"/api/transfers/{self.t['transfer_id']}/approve",self.f.action_data(self.t,'APPROVE'))
    def tearDown(self):self.f.tearDown()
    def client(self,token,branch):
        client=self.f.fixture.client(token)
        with client.session_transaction() as state:state['active_branch_id']=branch
        return client
    def data(self,t,key,**fields):return dict(self.f.action_data(t,key),**fields)
    def dispatch_data(self):
        with self.app.app_context():version=db.session.get(BranchStock,(1,self.f.lot)).version
        return self.data(self.approved,'DISPATCH',lines=[dict(allocation_id=self.approved['reservations'][0]['allocation_id'],expected_revision=version)])
    def dispatch(self):return self.f.write(self.f.admin,f"/api/transfers/{self.t['transfer_id']}/dispatch",self.dispatch_data())
    def compete(self,action,t,payloads,branch=2):
        from threading import Barrier
        from concurrent.futures import ThreadPoolExecutor
        barrier=Barrier(2)
        def run(pair):
            i,data=pair;client=self.client(self.f.fixture.tokens[i] if branch==2 else self.f.fixture.admin_token,branch)
            barrier.wait(timeout=10);r=client.post(f"/api/transfers/{t['transfer_id']}/{action}",json=data,headers={'X-CSRF-Token':'token'})
            return r.status_code,r.get_json()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,enumerate(payloads)))
    def test_concurrent_receipts_cannot_overcredit(self):
        t=self.dispatch();payload=self.data(t,'RECEIVE',lines=[dict(allocation_id=t['transit'][0]['allocation_id'],quantity=2,expected_revision=0,disposition='saleable')])
        results=self.compete('receive',t,[payload,dict(payload,request_key='OTHER')])
        self.assertEqual(sorted(r[0] for r in results),[200,409],results)
        with self.app.app_context():
            self.assertEqual(db.session.get(BranchStock,(2,self.f.lot)).on_hand,2)
            transit=db.session.get(TransferTransit,t['transit'][0]['allocation_id']);self.assertEqual((transit.remaining,transit.received),(1,2))
    def test_same_key_receipt_lost_response_and_duplicate_dispatch(self):
        data=self.dispatch_data();results=self.compete('dispatch',self.approved,[data,data],1)
        self.assertIn(200,[r[0] for r in results]);self.assertTrue(all(r[0] in (200,409) for r in results),results)
        t=self.f.write(self.f.admin,f"/api/transfers/{self.t['transfer_id']}/dispatch",data);self.assertTrue(t['replayed'])
        client=self.client(self.f.fixture.tokens[0],2)
        payload=self.data(t,'RECEIVE',lines=[dict(allocation_id=t['transit'][0]['allocation_id'],quantity=3,expected_revision=0,disposition='saleable')])
        path=f"/api/transfers/{t['transfer_id']}/receive"
        response=client.post(path,json=payload,headers={'X-CSRF-Token':'token'});self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        retry=client.post(path,json=payload,headers={'X-CSRF-Token':'token'})
        self.assertEqual(retry.status_code,200);self.assertTrue(retry.get_json()['replayed'])
        with self.app.app_context():
            self.assertEqual(db.session.get(BranchStock,(1,self.f.lot)).on_hand,1)
            self.assertEqual(db.session.get(BranchStock,(2,self.f.lot)).on_hand,3)
            self.assertEqual(db.session.scalar(select(func.count()).select_from(TransferTransitFact)),2)
