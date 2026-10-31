"""T15 original synthetic operations; only freshly initialized temporary targets."""
import copy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from sqlalchemy import select,func
from werkzeug.exceptions import Forbidden
from database import db
from models import (User,UserHQScope,UserBranch,InventoryOperation,InventoryMovement,BranchStock,
    StockLot,SKU,SaleLine,SaleAllocation,PricedSale,SaleRefund,PurchaseOrder,TransferTransit,StockTransfer)
from simulation.runtime import initialize,create_app,actor,history_hash
from simulation.runner import run
from simulation.verify import verify
from simulation.manifest import MANIFEST,validate
from simulation.schema import SimulationStep
from ingestion.target import ACK,initialize_target,connect


def dispose(app):
    with app.app_context():db.session.remove();db.engine.dispose()


class CompletedDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();cls.root=initialize(cls.temp.name,acknowledgment=ACK)
        cls.app=create_app(cls.root);cls.result=run(cls.app);cls.evidence=verify(cls.app)
    @classmethod
    def tearDownClass(cls):dispose(cls.app);cls.temp.cleanup()

    def client(self,uid=901,branch=None):
        from authentication import session_token
        with self.app.app_context():token=session_token(db.session.get(User,uid))
        client=self.app.test_client()
        with client.session_transaction() as state:
            state['_user_id']=token
            if branch:state['active_branch_id']=branch
        return client

    def test_all_reconciliation_scenarios_and_exact_financials(self):
        r=self.evidence
        self.assertTrue(r['ok']);self.assertEqual(self.result['completed_steps'],69)
        self.assertEqual(r['branch_codes'],['DEMO-01','DEMO-02','DEMO-03'])
        self.assertEqual([b['on_hand'] for b in r['branches']],[215,17,72])
        self.assertEqual(r['financial']['gross_minor'],32440);self.assertEqual(r['financial']['refund_minor'],900)
        self.assertEqual(r['financial']['net_minor'],31540)
        self.assertEqual(r['total']['approved_po_inbound'],15)
        self.assertEqual([t['state'] for t in r['transfer_states']],['PartiallyReceived','Received'])
        self.assertEqual([p['outstanding'] for p in r['purchasing']],[0,15])
        self.assertEqual(r['branches'][0]['warning_lots'],1);self.assertEqual(r['branches'][1]['low_stock_skus'],3)

    def test_full_rerun_changes_no_business_facts_or_history(self):
        before=verify(self.app);result=run(self.app);after=verify(self.app)
        self.assertEqual(result['new_steps'],0);self.assertEqual(before,after)

    def test_independent_run_is_deterministic_except_private_credentials(self):
        with TemporaryDirectory() as temp:
            root=initialize(temp,acknowledgment=ACK);app=create_app(root)
            try:
                run(app);other=verify(app)
                self.assertEqual(other,self.evidence)
                a=json.loads((self.root/'simulation-private.json').read_text())
                b=json.loads((root/'simulation-private.json').read_text())
                self.assertNotEqual(a['credentials'],b['credentials'])
            finally:dispose(app)

    def test_existing_roles_assignments_and_approval_separation(self):
        with actor(self.app,901):
            self.assertEqual(db.session.scalar(select(func.count()).select_from(User)),9)
            self.assertEqual(db.session.scalar(select(func.count()).select_from(UserBranch)),6)
            self.assertTrue(all(p.requester_id==901 and p.approver_id==902 for p in db.session.scalars(select(PurchaseOrder))))
            self.assertTrue(all(t.requester_id==911 and t.approver_id==902 for t in db.session.scalars(select(StockTransfer))))
        import catalog_services as catalog,purchasing_services as purchasing,sales_services as sales,transfer_services as transfers
        for uid,branch,fn in [(921,1,lambda:catalog.save_sku({})),(912,2,lambda:purchasing.create({})),
                              (903,1,lambda:sales.create_sale({})),(901,1,lambda:purchasing.transition(1,'approve',dict(expected_version=1,request_key='deny',business_reference='SYN-deny',reason='Synthetic denial'))),
                              (921,1,lambda:transfers.create({}))]:
            with self.subTest(uid=uid),actor(self.app,uid,branch),self.assertRaises(Forbidden):fn()

    def test_fefo_allocations_and_saleable_partial_refund_preserved(self):
        with actor(self.app,901):
            sale=db.session.scalar(select(PricedSale).where(PricedSale.branch_id==1).order_by(PricedSale.sale_id))
            line=db.session.scalar(select(SaleLine).where(SaleLine.sale_id==sale.sale_id).order_by(SaleLine.line_id))
            allocation=db.session.scalar(select(SaleAllocation).where(SaleAllocation.line_id==line.line_id))
            self.assertEqual(db.session.get(StockLot,allocation.lot_id).lot_code,'SYN-T15-DRINK-EARLY')
            self.assertEqual(sale.total_minor,5240)
            refund=db.session.scalar(select(SaleRefund));self.assertEqual(refund.total_minor,900)
            self.assertEqual(refund.sale_id,sale.sale_id)

    def test_quantity_conservation_openings_and_positive_effects_have_ledger(self):
        r=self.evidence['conservation'];self.assertTrue(r['balanced'])
        self.assertEqual((r['current_on_hand'],r['in_transit'],r['authorized_loss']),(304,6,1))
        self.assertEqual(self.evidence['operations']['opening'],12)
        self.assertEqual(self.evidence['operations']['adjustment'],12)
        with actor(self.app,901):
            for stock in db.session.scalars(select(BranchStock)):
                delta=db.session.scalar(select(func.sum(InventoryMovement.on_hand_delta)).where(InventoryMovement.branch_id==stock.branch_id,InventoryMovement.lot_id==stock.lot_id))
                self.assertEqual(delta,stock.on_hand)
            openings=db.session.scalars(select(InventoryOperation).where(InventoryOperation.operation_type=='opening')).all()
            self.assertTrue(all(op.business_date.isoformat()=='2031-01-01' for op in openings))

    def test_historical_observations_unchanged_and_write_protected(self):
        private=json.loads((self.root/'simulation-private.json').read_text())
        self.assertEqual(history_hash(self.root),private['history_hash'])
        with actor(self.app,901):
            from sqlalchemy import text
            with self.assertRaises(Exception):db.session.execute(text('UPDATE sales_observations SET units=999'))
            db.session.rollback()
        with connect(self.root) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM published_sales_observations').fetchone()[0],252)
            self.assertEqual(conn.execute('SELECT MAX(business_date) FROM published_sales_observations').fetchone()[0],'2001-01-14')

    def test_all_business_timestamps_are_fixture_dates_and_provenance_labels(self):
        with actor(self.app,901):
            for op in db.session.scalars(select(InventoryOperation)):
                self.assertTrue(op.business_reference.startswith(('SYNTHETIC-DEMO:','SYN-T15-')))
                self.assertTrue(op.occurred_at_utc.isoformat().startswith('2031-01-0'))
            for sku in db.session.scalars(select(SKU).where(SKU.code.like('SYN-T15-%'))):
                self.assertEqual(sku.variant_attributes['provenance'],'synthetic_demo')
                self.assertTrue(sku.name.startswith('Synthetic Demo'))

    def test_dashboard_api_exports_and_wrong_branch_scope(self):
        self.assertEqual(self.app.config['SESSION_COOKIE_SAMESITE'],'Lax')
        self.assertTrue(self.app.config['SESSION_COOKIE_HTTPONLY'])
        self.assertEqual(self.app.permanent_session_lifetime.total_seconds(),self.app.config['AUTH_IDLE_SECONDS'])
        client=self.client();response=client.get('/api/operations')
        self.assertEqual(response.status_code,200);data=response.get_json()
        self.assertEqual(data['total']['on_hand'],304);self.assertEqual(data['total']['recorded_net'],'315.40')
        self.assertIn('synthetic_demo',response.headers['X-InventraIQ-Provenance'])
        self.assertEqual(data['demo_provenance']['source'],'synthetic_demo')
        csv=client.get('/operations/export.csv');self.assertEqual(csv.status_code,200)
        self.assertIn('simulated, not real-world revenue',csv.get_data(as_text=True))
        manager=self.client(912,2);own=manager.get('/api/operations').get_json()
        self.assertEqual(own['total']['on_hand'],17);self.assertEqual(len(own['branches']),1)
        self.assertEqual(manager.get('/api/operations?branch_id=1').status_code,403)
        clerk=self.client(922,2);self.assertEqual(clerk.get('/purchasing').status_code,403)
        analyst=self.client(903);self.assertEqual(analyst.get('/api/operations').get_json()['total']['on_hand'],304)

    def test_existing_pages_render_with_honest_demo_banner(self):
        client=self.client()
        for path in ['/admin-dashboard','reports','/sales','/purchasing','/transfers','/sku-inventory']:
            response=client.get(path if path.startswith('/') else '/'+path)
            self.assertEqual(response.status_code,200,(path,response.status_code))
            self.assertIn('Simulated MYR totals are not real-world revenue',response.get_data(as_text=True))

    def test_changed_manifest_or_application_version_refuses_rerun(self):
        bad=copy.deepcopy(MANIFEST);bad['seed']=7
        with self.assertRaises(ValueError):run(self.app,bad)
        with patch('simulation.runner.code_hash',return_value='changed'),self.assertRaisesRegex(ValueError,'version changed'):run(self.app)

    def test_journal_is_immutable_and_contains_no_credentials(self):
        with actor(self.app,901):
            records=db.session.execute(select(SimulationStep)).mappings().all()
            self.assertEqual(len(records),69)
            serialized=json.dumps([dict(r) for r in records])
            for password in json.loads((self.root/'simulation-private.json').read_text())['credentials'].values():self.assertNotIn(password,serialized)
            from sqlalchemy import update
            with self.assertRaises(Exception):db.session.execute(update(SimulationStep).values(action='tampered'))
            db.session.rollback()


class DemoRecoveryAndSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)

    def make(self):
        root=initialize(self.temp.name,acknowledgment=ACK);app=create_app(root)
        self.addCleanup(dispose,app);return root,app

    def test_interruption_resume_and_mid_step_rollback(self):
        root,app=self.make();r=run(app,max_new_steps=25);self.assertEqual(r['status'],'interrupted')
        import simulation.runner as runner
        with (patch.object(runner.ledger,'adjust',side_effect=RuntimeError('Synthetic injected failure')),
              self.assertRaises(RuntimeError)):run(app)
        with app.app_context():
            self.assertEqual(db.session.scalar(select(func.count()).select_from(BranchStock)),0)
            self.assertEqual(db.session.scalar(select(func.count()).select_from(InventoryOperation)),0)
            self.assertEqual(db.session.scalar(select(func.count()).select_from(StockLot)),0)
        self.assertEqual(run(app)['status'],'complete');self.assertTrue(verify(app)['ok'])

    def test_lost_ack_after_commit_replays_step_not_business_operation(self):
        root,app=self.make()
        from simulation.runner import Runner
        original=Runner.step
        def after_commit(runner,*args):
            result=original(runner,*args)
            if args[0]=='po-partial-receipt':raise RuntimeError('Synthetic process lost acknowledgment')
            return result
        with patch.object(Runner,'step',after_commit),self.assertRaises(RuntimeError):run(app)
        self.assertEqual(run(app)['status'],'complete');self.assertTrue(verify(app)['ok'])
        self.assertEqual(verify(app)['operations']['receipt'],3)

    def test_non_disposable_unverified_t14_target_and_existing_users_refused(self):
        with self.assertRaises(ValueError):create_app(self.temp.name)
        t14=initialize_target(self.temp.name,acknowledgment=ACK)
        with self.assertRaises(ValueError):create_app(t14)
        root,app=self.make()
        with app.app_context():
            u=User(user_id=1,name='Synthetic existing sentinel',email='sentinel@demo.invalid',role='staff',enabled=True,session_version=1)
            u.set_password('Synthetic local sentinel only');db.session.add(u);db.session.commit()
        with self.assertRaisesRegex(ValueError,'existing users'):run(app)
        with app.app_context():self.assertEqual(db.session.scalar(select(func.count()).select_from(User)),1)

    def test_clock_is_context_local_and_unavailable_to_normal_app(self):
        from flask import Flask
        from simulation_clock import at_time,current_instant
        from expiry import utcnow
        root,app=self.make()
        with app.app_context(),at_time(app,MANIFEST['opening_at']):self.assertEqual(utcnow().isoformat(),MANIFEST['opening_at'])
        self.assertIsNone(current_instant())
        ordinary=Flask('ordinary')
        with ordinary.app_context(),self.assertRaises(ValueError):
            with at_time(ordinary,MANIFEST['opening_at']):pass

    def test_preexisting_operational_records_are_not_adopted_or_overwritten(self):
        root,app=self.make()
        from models import Product
        with app.app_context():
            db.session.add(Product(product_id=771,name='Synthetic existing record sentinel',version=1,archived=False));db.session.commit()
        with self.assertRaisesRegex(ValueError,'existing operational records'):run(app)
        with app.app_context():
            self.assertEqual(db.session.get(Product,771).name,'Synthetic existing record sentinel')
            self.assertEqual(db.session.scalar(select(func.count()).select_from(User)),0)

    def test_example_manifest_and_schema_artifacts_are_exact(self):
        from simulation.schema import render
        from sqlalchemy.dialects import sqlite,mysql
        root=Path(__file__).resolve().parents[1]
        self.assertEqual(json.loads((root/'data/t15_simulation.v1.json').read_text()),MANIFEST)
        for name,dialect in [('sqlite',sqlite.dialect()),('mysql',mysql.dialect())]:
            text=(root/'migrations'/f't15_v001_expand_{name}.sql').read_text()
            self.assertEqual(text,render(dialect));self.assertNotIn('DROP TABLE',text)
