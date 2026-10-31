"""T10 disposable authorization/state/ledger integration and SQLite contention."""
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from datetime import datetime
from sqlalchemy import select,func,update,delete
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Forbidden
import test_catalog_receiving as fixtures
from database import db
from models import (StockTransfer,StockTransferLine,StockTransferAllocation,StockTransferEvent,
    BranchStock,Branch,BranchProduct,StockLot,SKU,User,UserHQScope,UserBranch,InventoryOperation,InventoryMovement)
import transfer_services as transfers
import inventory_ledger as ledger
from expiry import UTC,canonical_expiry


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.CatalogReceivingTests();self.fixture.setUp()
        self.client=self.fixture.client;self.sku=self.fixture.sku
        self.clock=patch('inventory_ledger.utcnow',return_value=fixtures.NOW);self.clock.start()
        self.fixture.fixture.login(3);self.receipt=self.fixture.receive();self.lot=self.receipt['lot_id']
    def tearDown(self):self.clock.stop();self.fixture.tearDown()
    def login(self,uid=3,branch=1):self.fixture.fixture.login(uid,branch)
    def post(self,path,data,status=200):return self.fixture.post(path,data,status)
    def payload(self,**changes):return dict(dict(destination_branch_id=2,planned_arrival_utc='2030-01-02T00:00:00+00:00',
        lines=[dict(sku_id=self.sku,quantity=3)],request_key='CREATE',business_reference='TRANSFER-1',reason='Observed branch need'),**changes)
    def create(self,**changes):return self.post('/api/transfers',self.payload(**changes))
    def action(self,t,action,status=200,**changes):
        data=dict(expected_version=t['version'],request_key=action.upper(),business_reference='TRANSFER-1',reason='Reviewed '+action)
        data.update(changes)
        return self.post(f"/api/transfers/{t['transfer_id']}/{action}",data,status)
    def submitted(self):return self.action(self.create(),'submit')
    def approved(self):
        t=self.submitted();self.login(1);return self.action(t,'approve')
    def stock(self):return db.session.get(BranchStock,(1,self.lot),populate_existing=True)
    def count(self,model):return db.session.scalar(select(func.count()).select_from(model))
    @contextmanager
    def context(self,uid=1,branch=1):
        ctx=self.fixture.fixture.context(uid,branch)
        try:yield
        finally:ctx.pop()
    def check(self):
        with self.context():self.assertTrue(all(r['balanced'] for r in ledger.reconcile() if r['sku_id']==self.sku))

    def test_complete_approval_cancel_balances_events_and_replay(self):
        draft=self.create();self.assertEqual((draft['state'],draft['version']),('Draft',1));self.assertEqual(self.stock().reserved,0)
        submitted=self.action(draft,'submit');self.assertEqual(self.stock().reserved,0)
        self.login(1);approved=self.action(submitted,'approve')
        self.assertEqual((approved['state'],approved['version'],approved['approver_id']),('Approved',3,1))
        self.assertEqual((self.stock().on_hand,self.stock().reserved),(8,3))
        self.assertEqual(approved['reservations'][0]['remaining'],3)
        self.assertTrue(self.action(submitted,'approve')['replayed'])
        cancelled=self.action(approved,'cancel');self.assertEqual((cancelled['state'],cancelled['version']),('Cancelled',4))
        self.assertEqual(self.stock().reserved,0);self.assertEqual(cancelled['reservations'][0]['remaining'],0)
        self.assertTrue(self.action(approved,'cancel')['replayed'])
        self.action(cancelled,'cancel',409,request_key='NEW-CANCEL')
        self.assertEqual(self.count(StockTransferEvent),4);self.assertEqual(self.count(StockTransferAllocation),1)
        self.assertTrue(self.action(submitted,'approve')['replayed']) # original result after later cancellation
        self.assertEqual(self.stock().reserved,0);self.check()

    def test_draft_and_submitted_cancellation_and_rejection_have_no_stock_effect(self):
        for state in ('Draft','Submitted','Rejected'):
            with self.subTest(state=state):
                self.login(3);t=self.create(request_key='CREATE-'+state)
                if state!='Draft':t=self.action(t,'submit',request_key='SUBMIT-'+state)
                if state=='Rejected':self.login(1);t=self.action(t,'reject',request_key='REJECT')
                else:t=self.action(t,'cancel',request_key='CANCEL-'+state)
                self.assertIn(t['state'],('Cancelled','Rejected'));self.assertEqual(self.stock().reserved,0)
        self.assertEqual(self.count(InventoryMovement),1);self.check()

    def test_forbidden_state_transition_matrix(self):
        for state in ('Draft','Submitted','Approved','Rejected','Cancelled'):
            for action in ('submit','approve','reject','cancel','unsupported'):
                allowed={'Draft':{'submit','cancel'},'Submitted':{'approve','reject','cancel'},'Approved':{'cancel'},'Rejected':set(),'Cancelled':set()}
                if action in allowed[state]:continue
                with self.subTest(state=state,action=action):
                    self.login(3);t=self.create(request_key=state+action)
                    if state in ('Submitted','Approved','Rejected'):t=self.action(t,'submit',request_key='SUB-'+state+action)
                    self.login(1)
                    if state=='Approved':t=self.action(t,'approve',request_key='APP-'+action)
                    if state=='Rejected':t=self.action(t,'reject',request_key='REJ-'+action)
                    if state=='Cancelled':t=self.action(t,'cancel',request_key='CAN-'+action)
                    self.action(t,action,400 if action=='unsupported' else 409,request_key='INVALID-'+state+action)
                    if state=='Approved':self.action(t,'cancel',request_key='CLEAN-'+action)

    def test_hq_requester_cannot_self_approve_but_independent_hq_can(self):
        self.login(1);t=self.submitted();self.action(t,'approve',403)
        db.session.add(UserHQScope(user_id=5,hq_role='admin'));db.session.commit();self.login(5)
        approved=self.action(t,'approve');self.assertEqual(approved['approver_id'],5)

    def test_roles_branch_scope_and_direct_service_denial(self):
        t=self.submitted()
        for uid in (2,4,7):
            self.login(uid)
            self.post('/api/transfers',self.payload(request_key='DENY-'+str(uid)),403)
            self.action(t,'approve',403)
            with self.context(uid):
                with self.assertRaises(Forbidden):transfers.create(self.payload())
                with self.assertRaises(Forbidden):transfers.transition(t['transfer_id'],'approve',dict(expected_version=2,request_key='X',business_reference='X',reason='X'))
        self.login(3);self.action(t,'approve',403)
        self.login(6,2);self.assertEqual(self.client.get(f"/api/transfers/{t['transfer_id']}").status_code,403)
        db.session.get(UserBranch,(6,2)).branch_role='manager';db.session.commit();self.login(6,2)
        self.assertEqual(self.client.get(f"/api/transfers/{t['transfer_id']}").status_code,404)
        self.action(t,'submit',404)
        self.login(2,branch=None);self.assertEqual(self.client.get('/api/transfers').status_code,200)
        self.assertEqual(self.client.get(f"/api/transfers/{t['transfer_id']}").status_code,200)

    def test_only_hq_may_cancel_approved_and_generic_apis_cannot_release_hold(self):
        t=self.approved();self.login(3);self.action(t,'cancel',403)
        self.post('/api/stock-reservations/release',dict(sku_id=self.sku,quantity=3,reservation_id=t['approval_operation_id'],
            request_key='STEAL',business_reference='STEAL',reason='Attempt generic release'),404)
        self.assertEqual(self.stock().reserved,3)

    def test_version_conflicts_changed_keys_and_canonical_multiline_replay(self):
        t=self.create();self.assertTrue(self.create()['replayed'])
        self.post('/api/transfers',self.payload(reason='changed'),409)
        self.action(t,'submit',409,expected_version=99)
        submitted=self.action(t,'submit');self.action(t,'submit',409,request_key='OTHER')
        self.login(1);self.action(submitted,'approve')
        self.action(submitted,'approve',409,reason='Changed facts')

    def test_invalid_inputs_and_client_identity_balance_injection(self):
        for changes in (dict(destination_branch_id=1),dict(lines=[]),dict(lines=[dict(sku_id=self.sku,quantity=0)]),
            dict(lines=[dict(sku_id=self.sku,quantity=True)]),dict(lines=[dict(sku_id=self.sku,quantity=1)]*2),
            dict(planned_arrival_utc='2030-01-02'),dict(planned_arrival_utc='2020-01-01T00:00:00Z'),
            dict(actor_id=1),dict(approved=True),dict(source_branch_id=2),dict(reserved=0)):
            response=self.client.post('/api/transfers',json=self.payload(**changes))
            self.assertIn(response.status_code,(400,403,409),response.get_data(as_text=True))
        self.assertEqual(self.count(StockTransfer),0)

    def test_shortage_rollback_and_injected_audit_failure(self):
        t=self.submitted();self.login(1)
        self.post('/api/sku-sales',dict(sku_id=self.sku,quantity=7,request_key='SALE',business_reference='SALE',reason='Actual sale'))
        before=self.count(InventoryOperation);self.action(t,'approve',409)
        self.assertEqual(self.count(InventoryOperation),before);self.assertEqual(self.stock().reserved,0)
        self.assertEqual(db.session.get(StockTransfer,t['transfer_id']).state,'Submitted')
        self.post('/api/stock-adjustments',dict(sku_id=self.sku,lot_id=self.lot,quantity=7,direction='increase',expected_revision=self.stock().version,
            request_key='ADD',business_reference='ADD',reason='Verified count'))
        with patch('inventory_ledger.finish',side_effect=RuntimeError('Synthetic audit failure')):self.action(t,'approve',500)
        self.assertEqual(self.stock().reserved,0);self.assertEqual(db.session.get(StockTransfer,t['transfer_id']).state,'Submitted')
        self.assertEqual(self.count(StockTransferAllocation),0);self.action(t,'approve');self.check()

    def test_expired_quarantined_reserved_and_destination_life_excluded(self):
        t=self.submitted();self.login(1)
        lot=db.session.get(StockLot,self.lot);original=lot.expires_at_utc
        for mode in ('expired','quarantine','life'):
            lot.validation_state='quarantine' if mode=='quarantine' else 'validated'
            lot.expires_at_utc=fixtures.NOW.replace(tzinfo=None) if mode=='expired' else original
            db.session.commit()
            if mode=='life':
                row=db.session.get(StockTransfer,t['transfer_id']);row.planned_arrival_utc=datetime(2030,1,8,12);db.session.commit()
            self.action(t,'approve',409)
            self.assertEqual(self.stock().reserved,0)
        self.check()

    def test_catalog_branch_and_requester_revocation_rechecked(self):
        t=self.submitted();self.login(1)
        for model,key,field in ((SKU,self.sku,'active'),(Branch,2,'active'),(BranchProduct,(2,self.sku),'enabled'),(UserBranch,(3,1),'active')):
            row=db.session.get(model,key);setattr(row,field,False);db.session.commit();self.action(t,'approve',409)
            setattr(row,field,True);db.session.commit()
        self.action(t,'approve')

    def test_permission_revoked_before_commit_rolls_back(self):
        t=self.submitted();self.login(1)
        original=ledger.finish
        def revoke(*args,**kwargs):
            db.session.get(UserHQScope,1).active=False;db.session.flush()
            return original(*args,**kwargs)
        with patch('inventory_ledger.finish',side_effect=revoke):self.action(t,'approve',403)
        self.assertEqual(self.stock().reserved,0);self.assertTrue(db.session.get(UserHQScope,1).active)
        self.assertEqual(db.session.get(StockTransfer,t['transfer_id']).state,'Submitted')

    def test_fefo_multiple_lots_and_cancellation_after_expiry_or_archive(self):
        early=self.fixture.receive(self.fixture.delivery(lot_code='EARLY',expiry_date='2030-01-04',external_reference='EARLY',quantity=2))
        t=self.approved();self.assertEqual({a['lot_id']:a['quantity'] for a in t['reservations']},{early['lot_id']:2,self.lot:1})
        db.session.get(SKU,self.sku).active=False;db.session.get(Branch,2).active=False
        db.session.get(StockLot,self.lot).validation_state='quarantine';db.session.commit()
        self.action(t,'cancel');self.assertEqual(self.stock().reserved,0);self.check()

    def test_allocations_events_lines_immutable_and_fk_enforced(self):
        t=self.approved()
        for model in (StockTransferLine,StockTransferAllocation,StockTransferEvent):
            for statement in (delete(model),update(model).values(**({'quantity':999} if model!=StockTransferEvent else {'reason':'rewrite'}))):
                with self.assertRaises(IntegrityError):db.session.execute(statement)
                db.session.rollback()
        with self.assertRaises(IntegrityError):
            db.session.add(StockTransferLine(transfer_id=99999,sku_id=self.sku,quantity=1));db.session.flush()
        db.session.rollback();self.check()

    def test_empty_scoped_pagination_html_and_csrf(self):
        self.assertEqual(self.client.get('/api/transfers').get_json()['transfers'],[])
        t=self.create();page=self.client.get('/transfers?transfer_id='+str(t['transfer_id']))
        self.assertEqual(page.status_code,200);self.assertIn(b'Create draft',page.data)
        with self.client.session_transaction() as state:state.pop('_csrf_token',None)
        response=self.client.post('/api/transfers',json=self.payload(request_key='NO-CSRF'))
        self.assertEqual(response.status_code,400);self.assertEqual(self.count(StockTransfer),1)

    def test_multiline_approval_all_or_nothing_and_destination_policy_boundary(self):
        self.login(1,branch=None)
        second=self.fixture.post('/api/skus',self.fixture.sku_payload(code='OTHER',name='Other variant',variant_attributes={'volume':'1L'}))['sku_id']
        for branch in (1,2):self.fixture.put(f'/api/assortments/{branch}/{second}',dict(enabled=True,expected_version=0,reason='Approved'))
        self.login(3);t=self.create(lines=[dict(sku_id=self.sku,quantity=2),dict(sku_id=second,quantity=1)])
        t=self.action(t,'submit');self.login(1);self.action(t,'approve',409)
        self.assertEqual(self.stock().reserved,0);self.assertEqual(self.count(StockTransferAllocation),0)
        self.login(3);self.fixture.receive(self.fixture.delivery(sku_id=second,lot_code='OTHER',external_reference='OTHER'))
        self.login(1);result=self.action(t,'approve');self.assertEqual(len(result['reservations']),2);self.check()

    def test_arrival_timezone_minimum_exact_boundary_and_no_invented_expiry(self):
        from models import CategoryExpiryPolicy
        policy=db.session.get(CategoryExpiryPolicy,self.fixture.cat)
        policy.min_remaining_days_on_transfer_receipt=1;db.session.commit()
        # Last valid destination date Jan 8; arrival Jan 7 has exactly one day.
        t=self.submitted() # replace with separate reviewed future-arrival draft below
        self.action(t,'cancel');t=self.create(request_key='BOUNDARY',planned_arrival_utc='2030-01-07T00:00:00+08:00')
        t=self.action(t,'submit',request_key='BOUNDARY-SUB');self.login(1)
        approved=self.action(t,'approve',request_key='BOUNDARY-APP');self.assertEqual(self.stock().reserved,3)
        self.action(approved,'cancel',request_key='BOUNDARY-CAN')
        self.login(3);late=self.create(request_key='LATE',planned_arrival_utc='2030-01-08T00:00:00+08:00')
        late=self.action(late,'submit',request_key='LATE-SUB');self.login(1);self.action(late,'approve',409,request_key='LATE-APP')
        lot=db.session.get(StockLot,self.lot);lot.expiry_date=None;lot.expiry_timezone=None;lot.expires_at_utc=None;db.session.commit()
        self.action(late,'approve',409,request_key='MISSING-DATE');self.assertEqual(self.stock().reserved,0)

    def test_lot_expiry_at_commit_rolls_back_and_cancel_failure_retains_hold(self):
        t=self.submitted();self.login(1)
        with patch('transfer_services._arrival_eligible',wraps=transfers._arrival_eligible) as eligibility:
            original=ledger.finish
            def crossed(*args,**kwargs):
                with patch('inventory_ledger.utcnow',return_value=datetime(2030,1,9,tzinfo=UTC)):
                    result=original(*args,**kwargs);db.session.commit();return result
            with patch('inventory_ledger.finish',side_effect=crossed):self.action(t,'approve',409)
        self.assertEqual(self.stock().reserved,0);self.assertEqual(self.count(StockTransferAllocation),0)
        approved=self.action(t,'approve')
        with patch('inventory_ledger.finish',side_effect=RuntimeError('Synthetic cancellation audit failure')):self.action(approved,'cancel',500)
        self.assertEqual(self.stock().reserved,3);self.assertEqual(db.session.get(StockTransfer,t['transfer_id']).state,'Approved')
        self.action(approved,'cancel');self.check()


    def test_transition_authority_matrix_read_rechecks_and_invalid_query_contract(self):
        t=self.submitted()
        for uid in (2,3,4,7):
            for action in ('submit','approve','reject','cancel'):
                self.login(uid)
                # Managers may cancel submitted source proposals, so exercise
                # their forbidden transitions without consuming this fixture.
                if uid==3 and action=='cancel':continue
                self.action(t,action,409 if uid==3 and action=='submit' else 403,request_key=f'MATRIX-{uid}-{action}')
        self.login(3)
        for path in ('/api/transfers?page=0','/api/transfers?page=1&page=2','/api/transfers?actor_id=1','/api/transfers?scope=all'):
            self.assertIn(self.client.get(path).status_code,(400,403))
        self.action(t,'submit',403,actor_id=1)
        self.assertEqual(self.stock().reserved,0)
        self.login(1,branch=None)
        self.post('/api/transfers',self.payload(request_key='CONSOLIDATED-WRITE'),403)
        self.login(3);db.session.get(UserBranch,(3,1)).active=False;db.session.commit()
        self.assertEqual(self.client.get('/api/transfers').status_code,403)


class TransferConcurrencyTests(unittest.TestCase):
    def setUp(self):
        import test_concurrent_receiving as concurrent_fixture
        from transfer_routes import bp
        from sales_routes import bp as sales_bp
        self.fixture=concurrent_fixture.ConcurrentReceivingTests();self.fixture.extra_blueprints=(bp,sales_bp)
        self.fixture.setUp();self.app=self.fixture.app
        with self.app.app_context():
            db.session.add(Branch(branch_id=2,code='DEST',display_name='Destination',timezone='UTC'));db.session.flush()
            db.session.add(BranchProduct(branch_id=2,sku_id=self.fixture.sku,enabled=True));db.session.commit()
        self.admin=self.fixture.client(self.fixture.admin_token)
        self.manager=self.fixture.client(self.fixture.tokens[0])
        self.lot=self.write(self.manager,'/api/receipts',self.fixture.payload())['lot_id']
    def tearDown(self):self.fixture.tearDown()
    def write(self,client,path,data):
        response=client.post(path,json=data,headers={'X-CSRF-Token':'token'})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True));return response.get_json()
    def submitted(self,key='ONE'):
        t=self.write(self.manager,'/api/transfers',dict(destination_branch_id=2,planned_arrival_utc='2089-12-20T00:00:00Z',
            lines=[dict(sku_id=self.fixture.sku,quantity=3)],request_key='CREATE-'+key,business_reference=key,reason='Synthetic branch requirement'))
        return self.write(self.manager,f"/api/transfers/{t['transfer_id']}/submit",self.action_data(t,'SUB-'+key))
    def action_data(self,t,key):return dict(expected_version=t['version'],request_key=key,business_reference='TRANSFER',reason='Synthetic review')
    def compete(self,requests):
        from threading import Barrier
        from concurrent.futures import ThreadPoolExecutor
        barrier=Barrier(2)
        def run(item):
            token,path,data=item;client=self.fixture.client(token);barrier.wait(timeout=10)
            result=client.post(path,json=data,headers={'X-CSRF-Token':'token'})
            return result.status_code,result.get_json()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,requests))
    def check(self):
        with self.app.app_context():
            stock=db.session.get(BranchStock,(1,self.lot));self.assertGreaterEqual(stock.on_hand-stock.reserved-stock.quarantined_quantity,0)
            movements=db.session.scalars(select(InventoryMovement).where(InventoryMovement.lot_id==self.lot)).all()
            self.assertEqual(sum(m.on_hand_delta for m in movements),stock.on_hand)
            self.assertEqual(sum(m.reserved_delta for m in movements),stock.reserved)
    def test_transfer_approval_competes_with_priced_sale(self):
        t=self.submitted();results=self.compete([(self.fixture.admin_token,f"/api/transfers/{t['transfer_id']}/approve",self.action_data(t,'APPROVE')),
            (self.fixture.tokens[1],'/api/priced-sales',dict(lines=[dict(sku_id=self.fixture.sku,quantity=3)],request_key='SALE',business_reference='SALE',reason='Synthetic sale'))])
        self.assertEqual(sorted(r[0] for r in results),[200,409],results);self.check()
    def test_two_transfers_compete_for_same_lot(self):
        a=self.submitted();b=self.submitted('TWO')
        results=self.compete([(self.fixture.admin_token,f"/api/transfers/{t['transfer_id']}/approve",self.action_data(t,'APP-'+str(t['transfer_id']))) for t in (a,b)])
        self.assertEqual(sorted(r[0] for r in results),[200,409],results);self.check()
        with self.app.app_context():self.assertEqual(db.session.get(BranchStock,(1,self.lot)).reserved,3)
    def test_duplicate_approval_and_cancellation_races_replay_once(self):
        t=self.submitted();path=f"/api/transfers/{t['transfer_id']}/approve";data=self.action_data(t,'APPROVE')
        results=self.compete([(self.fixture.admin_token,path,data)]*2)
        self.assertIn(200,[r[0] for r in results]);self.assertTrue(all(r[0] in (200,409) for r in results),results)
        approved=self.write(self.admin,path,data);self.assertTrue(approved['replayed']);self.check()
        path=f"/api/transfers/{t['transfer_id']}/cancel";data=self.action_data(approved,'CANCEL')
        results=self.compete([(self.fixture.admin_token,path,data)]*2)
        self.assertIn(200,[r[0] for r in results]);self.assertTrue(all(r[0] in (200,409) for r in results),results)
        self.assertTrue(self.write(self.admin,path,data)['replayed']);self.check()
        with self.app.app_context():
            self.assertEqual(db.session.get(BranchStock,(1,self.lot)).reserved,0)

    def test_late_replay_after_competing_commit_returns_original_without_new_effects(self):
        """Both callers pass preflight; one commits before the other's begin lookup."""
        from threading import Barrier,Event,Lock,get_ident
        from concurrent.futures import ThreadPoolExecutor
        def race(path,data):
            ready=Barrier(2);committed=Event();claim=Lock();state={}
            original=ledger.begin
            def begin_after_competitor(*args,**kwargs):
                ready.wait(timeout=10)
                with claim:
                    if 'winner' not in state:state['winner']=get_ident()
                    winner=state['winner']==get_ident()
                if not winner:
                    self.assertTrue(committed.wait(timeout=10),'Competing commit did not complete')
                return original(*args,**kwargs)
            def request_one(_):
                client=self.fixture.client(self.fixture.admin_token)
                try:
                    response=client.post(path,json=data,headers={'X-CSRF-Token':'token'})
                    return response.status_code,response.get_json()
                finally:
                    if state.get('winner')==get_ident():committed.set()
            with patch('inventory_ledger.begin',side_effect=begin_after_competitor):
                with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(request_one,range(2)))
            self.assertEqual([r[0] for r in results],[200,200],results)
            self.assertEqual(sorted(r[1]['replayed'] for r in results),[False,True])
            self.check()
            return next(r[1] for r in results if not r[1]['replayed'])
        transfer=self.submitted()
        approved=race(f"/api/transfers/{transfer['transfer_id']}/approve",self.action_data(transfer,'LATE-APPROVE'))
        self.assertEqual(approved['state'],'Approved')
        race(f"/api/transfers/{transfer['transfer_id']}/cancel",self.action_data(approved,'LATE-CANCEL'))
        with self.app.app_context():
            self.assertEqual(db.session.get(BranchStock,(1,self.lot)).reserved,0)
            self.assertEqual(db.session.scalar(select(func.count()).select_from(InventoryMovement)),3)
    def test_transfer_approval_competes_with_generic_reservation(self):
        t=self.submitted();results=self.compete([(self.fixture.admin_token,f"/api/transfers/{t['transfer_id']}/approve",self.action_data(t,'APPROVE')),
            (self.fixture.tokens[1],'/api/stock-reservations',dict(sku_id=self.fixture.sku,quantity=3,request_key='HOLD',business_reference='HOLD',reason='Synthetic customer hold'))])
        self.assertEqual(sorted(r[0] for r in results),[200,409],results);self.check()
