"""Scoped chart and queue contracts on populated disposable T15 data."""
from tempfile import TemporaryDirectory
import unittest
from sqlalchemy import select,func
from database import db
from models import PricedSale,SaleRefund,TransferExecution,StockReceipt
from scripts.measure_t16 import fixture,client
from simulation.runtime import actor
from simulation.verify import verify


class WorkspaceViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory(prefix='inventraiq-transformation-')
        cls.root,cls.app=fixture(cls.temp.name,'small')
        cls.before=verify(cls.app)

    @classmethod
    def tearDownClass(cls):
        try:
            if verify(cls.app)!=cls.before:raise AssertionError('Presentation reads changed business facts')
        finally:
            with cls.app.app_context():db.session.remove();db.engine.dispose()
            cls.temp.cleanup()

    def test_chart_headers_reconcile_to_report_and_do_not_infer_money(self):
        from operational_services import report
        from services.workspace_views import read
        with actor(self.app,901):
            data=report({},sections=())
            visuals=read(data)
            self.assertEqual(sum(d['gross_minor'] for d in visuals['sales_days']),
                db.session.scalar(select(func.sum(PricedSale.total_minor))))
            self.assertEqual(sum(d['refund_minor'] for d in visuals['sales_days']),
                db.session.scalar(select(func.sum(SaleRefund.total_minor))))
            self.assertEqual(sum(b['on_hand'] for b in visuals['stock_branches']),data['total']['on_hand'])
            self.assertLessEqual(len(visuals['sales_days']),90)
            self.assertTrue(visuals['synthetic'])

    def test_clerk_charts_and_queues_are_scoped_without_purchase_disclosure(self):
        from operational_services import report
        from services.workspace_views import read
        with actor(self.app,922,2):
            data=report({},sections=())
            visuals=read(data)
            self.assertEqual([b['branch_id'] for b in visuals['stock_branches']],[2])
            self.assertEqual(visuals['queues']['transfers'],[])
            self.assertEqual(visuals['queues']['purchases'],[])
        caller=client(self.app,922,2)
        html=caller.get('/dashboard').get_data(as_text=True)
        self.assertNotIn('DEMO-01',html.split('<main',1)[-1].split('</main>',1)[0])
        self.assertNotIn('DEMO-03',html.split('<main',1)[-1].split('</main>',1)[0])

    def test_full_supported_report_window_remains_available_with_charts(self):
        from operational_services import report
        from services.workspace_views import read
        values=dict(start='2030-01-03',end='2031-01-03')
        with actor(self.app,901):
            data=report(values,sections=())
            visuals=read(data)
            self.assertEqual(len(visuals['sales_days']),366)
            self.assertEqual(visuals['sales_days'][0]['date'],values['start'])
            self.assertEqual(visuals['sales_days'][-1]['date'],values['end'])
            self.assertEqual(sum(d['gross_minor'] for d in visuals['sales_days']),
                             sum(b['gross_minor'] for b in data['branches']))
        caller=client(self.app,901)
        for path in ('/admin-dashboard','/sales','/analytics','/reports'):
            response=caller.get(path+'?start='+values['start']+'&end='+values['end'])
            self.assertEqual(response.status_code,200,path)
            self.assertIn('2030-01-03',response.get_data(as_text=True))
        self.assertEqual(caller.get('/reports?start=2029-01-03&end=2031-01-03').status_code,400)

    def test_scoped_queues_link_only_visible_existing_documents(self):
        from operational_services import report
        from services.workspace_views import read
        with actor(self.app,912,2):
            visuals=read(report({},sections=()))
            self.assertLessEqual(len(visuals['queues']['purchases']),4)
            self.assertLessEqual(len(visuals['queues']['transfers']),4)
            for item in visuals['queues']['transfers']:
                self.assertIn(2,(item['source'],item['destination']))
                self.assertIn('branch_id=2',item['url'])

    def test_invalid_page_filters_recover_in_scope_while_API_errors_stay_JSON(self):
        caller=client(self.app,901)
        invalid='?branch_id=2&start=2020-01-01&end=2031-01-03'
        response=caller.get('/reports'+invalid)
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.mimetype,'text/html')
        html=response.get_data(as_text=True)
        self.assertIn('Review your filters.',html)
        self.assertIn('href="/reports?branch_id=2"',html)
        self.assertIn('Reset filters',html)
        self.assertNotIn('This area is restricted.',html)
        for path in ('/api/operations','/reports'):
            response=caller.get(path+invalid,headers={'Accept':'application/json'})
            self.assertEqual(response.status_code,400)
            self.assertIn('error',response.get_json())

    def test_assistant_review_shell_keeps_role_and_does_not_grant_readonly_actions(self):
        self.app.config['ASSISTANT_PROPOSALS_ENABLED']=True
        try:
            html=client(self.app,903).get('/assistant/proposals').get_data(as_text=True)
            self.assertIn('HQ analyst',html)
            self.assertNotIn('href="/assistant/proposals"',html)
            self.assertNotIn('Create proposal for review</button>',html)
        finally:self.app.config['ASSISTANT_PROPOSALS_ENABLED']=False

    def test_queue_excludes_completed_shipments_and_uses_execution_state(self):
        from operational_services import report
        from services.workspace_views import read
        with actor(self.app,901):
            executions=dict(db.session.execute(select(TransferExecution.transfer_id,TransferExecution.state)).all())
            self.assertIn('Received',executions.values())
            queue=read(report({},sections=()))['queues']
            for item in queue['transfers']:
                self.assertNotEqual(executions.get(item['id']),'Received')
                if item['id'] in executions:self.assertEqual(item['state'],executions[item['id']])
            self.assertNotIn(next(k for k,v in executions.items() if v=='Received'),[i['id'] for i in queue['transfers']])

    def test_receiving_feedback_requires_an_actual_scoped_receipt(self):
        with self.app.app_context():
            own=db.session.scalar(select(StockReceipt.receipt_id).where(StockReceipt.branch_id==2))
            other=db.session.scalar(select(StockReceipt.receipt_id).where(StockReceipt.branch_id==1))
        caller=client(self.app,912,2)
        self.assertIn(f'Receipt #{own} recorded:',caller.get(f'/receiving?receipt={own}').get_data(as_text=True))
        self.assertNotIn(f'Receipt #{other} recorded:',caller.get(f'/receiving?receipt={other}').get_data(as_text=True))
