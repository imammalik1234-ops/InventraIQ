"""T24 value, scope and workflow contracts on new synthetic SQLite only."""
import csv
import io
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlsplit, parse_qs

from test_design_accessibility import Document


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ingestion.target import ACK
        from simulation.runtime import initialize, create_app
        from simulation.runner import run
        from simulation.verify import verify
        cls.temp = TemporaryDirectory(prefix='inventraiq-t24-test-')
        cls.root = initialize(cls.temp.name, acknowledgment=ACK)
        cls.app = create_app(cls.root)
        run(cls.app)
        cls.before = verify(cls.app)

    @classmethod
    def tearDownClass(cls):
        from database import db
        from simulation.verify import verify
        try:
            if verify(cls.app) != cls.before:
                raise AssertionError('Presentation reads changed business facts')
        finally:
            with cls.app.app_context():
                db.session.remove();db.engine.dispose()
            cls.temp.cleanup()

    def client(self, uid=901, branch=None):
        from scripts.measure_t16 import client
        return client(self.app, uid, branch)

    def document(self, client, path):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        return Document(response.get_data(as_text=True))

    def test_overview_prioritizes_risk_and_does_not_repeat_detail_lists(self):
        document = self.document(self.client(), '/admin-dashboard')
        headings = [' '.join(n.text.split()) for n in document.elements('h2')]
        self.assertEqual(headings[0], 'Review the evidence. Choose the next move.')
        self.assertIn('Stock requiring attention',headings)
        self.assertNotIn('Lot inventory', headings)
        self.assertNotIn('Recorded priced transactions', headings)
        self.assertFalse(any('Synthetic planning preview' in n.text for n in document.elements('summary')))
        self.assertNotIn('data-decision-set', self.client().get('/admin-dashboard').get_data(as_text=True))

    def test_reports_is_a_compatibility_alias_for_the_single_analytics_workspace(self):
        client = self.client()
        for path in ('/analytics','/reports'):
            document = self.document(client, path)
            self.assertEqual(document.elements('h1')[0].text.strip(), 'Analytics')
            links = [n for n in document.elements('a') if n.attrs.get('aria-current')=='page']
            self.assertEqual([n.text.strip() for n in links], ['Analytics'])
            self.assertTrue(any(n.attrs.get('href','').startswith('/operations/export.csv') for n in document.elements('a')))
            self.assertTrue(any('data-operational-summary' in n.attrs for n in document.elements('tr')))
            nav = next(n for n in document.elements('nav') if n.attrs.get('aria-label')=='Main navigation')
            self.assertNotIn('Reports', [n.text.strip() for n in document.descendants(nav) if n.tag=='a'])

    def test_generated_retry_tokens_stay_in_payloads_without_editable_fields(self):
        client = self.client(912,2)
        for path in ('/branch-sales','/sku-inventory','/transfers'):
            document = self.document(client,path)
            tokens = [n for n in document.elements('input') if n.attrs.get('name')=='request_key']
            self.assertTrue(tokens,path)
            self.assertTrue(all(n.attrs.get('type')=='hidden' and n.attrs.get('value') for n in tokens),path)

    def test_workspace_omits_backend_definitions_and_repeated_marketing(self):
        client = self.client()
        for path in ('/admin-dashboard','/analytics','/admin-settings'):
            html = client.get(path).get_data(as_text=True)
            self.assertNotIn('Data definitions and reconciliation',html)
            self.assertNotIn('Evidence informs. People decide.',html)
            self.assertNotIn('CSRF protection',html)

    def test_report_cells_and_export_match_scoped_backend_values(self):
        client = self.client()
        report = client.get('/api/operations').json
        document = self.document(client, '/reports')
        rows = {int(n.attrs['data-branch-fact']): n for n in document.elements('tr') if 'data-branch-fact' in n.attrs}
        for branch in report['branches']:
            cells = [n.text.strip() for n in document.descendants(rows[branch['branch_id']]) if n.tag == 'td']
            self.assertEqual(cells, [str(branch['sold_units']), str(branch['unknown_revenue_units']),
                                    branch['recorded_gross'], branch['recorded_refunds'], branch['recorded_net']])
        footer = next(n for n in document.elements('tr') if 'data-operational-summary' in n.attrs)
        self.assertEqual(footer.attrs['data-net'], report['total']['recorded_net'])
        response = client.get('/operations/export.csv')
        try:
            rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        finally:
            response.close()
        header = next(i for i,r in enumerate(rows) if r[:2] == ['branch_id','code'])
        self.assertEqual(len(rows[header+1:]), len(report['branches']))

    def test_branch_and_clerk_html_do_not_contain_other_branch_or_purchase_data(self):
        for uid in (912,922):
            client = self.client(uid,2)
            for path in ('/dashboard','/inventory-page','/reports','/analytics'):
                document = self.document(client,path)
                main = document.elements('main')[0]
                self.assertIn('Eastside', main.text)
                self.assertNotIn('DEMO-01', main.text)
                self.assertNotIn('DEMO-03', main.text)
                self.assertNotIn('Central', main.text)
                self.assertNotIn('Westside', main.text)
                if uid == 922:
                    self.assertNotIn('Approved PO outstanding', main.text)
                    self.assertFalse(any(n.tag=='form' and n.attrs.get('method','').upper()=='POST'
                                         for n in document.descendants(main)))
            self.assertEqual(client.get('/api/operations?branch_id=1').status_code,403)

    def test_low_stock_next_action_opens_matching_sku_in_same_branch(self):
        client = self.client(912,2)
        document = self.document(client,'/dashboard')
        links = [n for n in document.elements('a') if 'Inspect stock' in n.text]
        self.assertTrue(links)
        for link in links:
            target = urlsplit(link.attrs['href'])
            query = parse_qs(target.query)
            self.assertEqual(query['branch_id'],['2'])
            self.assertNotEqual(query['search'],['All lots of SKU'])
            response = client.get('/api/operations?'+target.query)
            self.assertGreater(response.json['inventory']['total'],0)

    def test_selected_workflow_record_precedes_queue_and_supplier_maintenance(self):
        client = self.client(912,2)
        for path, detail, queue in (('/purchasing?order_id=2','PO #2','Purchase order queue'),
                                    ('/transfers?transfer_id=1','Transfer #1','Transfer queue')):
            document = self.document(client,path)
            headings = [' '.join(n.text.split()) for n in document.elements('h2')]
            self.assertLess(next(i for i,h in enumerate(headings) if h.startswith(detail)),headings.index(queue))
        analyst = self.client(903)
        document = self.document(analyst,'/purchasing')
        self.assertFalse(any(n.attrs.get('aria-label','').startswith(('Create purchase','Edit supplier','Create supplier'))
                             for n in document.elements('form')))
        supplier = next(n for n in document.elements('summary') if 'Supplier directory' in n.text)
        self.assertNotIn('open', supplier.parent.attrs)
        document = self.document(self.client(903,2),'/sales')
        self.assertFalse(any('New priced sale' in n.text for n in document.elements('a')))

    def test_purchase_and_transfer_posts_keep_recoverable_submission_and_security_fields(self):
        for path in ('/purchasing?order_id=2','/transfers?transfer_id=1'):
            document = self.document(self.client(912,2),path)
            forms = [f for f in document.elements('form') if
                     f.attrs.get('action','').startswith(('/purchasing/','/transfers/'))]
            self.assertTrue(forms)
            for form in forms:
                self.assertIn('data-enhance-form',form.attrs)
                names = {n.attrs.get('name') for n in document.descendants(form) if n.tag=='input'}
                self.assertIn('csrf_token',names)
                self.assertTrue('payload' in names or 'request_key' in names)
                if 'payload' not in names and '/create' not in form.attrs['action']:
                    self.assertIn('expected_version',names)

    def test_catalog_controls_are_disclosed_and_inventory_and_sales_navigation_stay_current(self):
        document = self.document(self.client(),'/catalog')
        definitions = next(n for n in document.elements('h2') if 'SKU definitions' in n.text)
        administration = next(n for n in document.elements('summary') if 'Catalog administration' in n.text)
        self.assertLess(document.nodes.index(definitions),document.nodes.index(administration))
        self.assertNotIn('open',administration.parent.attrs)
        for form in document.elements('form'):
            if form.attrs.get('action')=='/catalog/actions':
                node=form.parent
                while node is not None and node.tag!='details':node=node.parent
                self.assertIsNotNone(node)
                self.assertIn('data-enhance-form',form.attrs)
        for path,expected in (('/sku-inventory','Inventory'),('/branch-sales','Sales')):
            document=self.document(self.client(912,2),path)
            active=[n.text.strip() for n in document.elements('a') if n.attrs.get('aria-current')=='page']
            self.assertEqual(active,[expected])
            nav=next(n for n in document.elements('nav') if n.attrs.get('aria-label')=='Main navigation')
            self.assertNotIn('Lot inventory',nav.text)
        for uid,branch,label in ((901,None,'HQ administrator'),(903,None,'HQ analyst'),(912,2,'Branch manager'),(922,2,'Clerk')):
            document=self.document(self.client(uid,branch),'/catalog')
            profile=next(n for n in document.elements('div') if n.attrs.get('class')=='profile-summary')
            self.assertIn(label,profile.text)

    def test_po_lot_suggestions_are_bounded_to_authorized_branch_supplier_and_line(self):
        from database import db
        from models import BranchStock,StockLot,PurchaseOrder,PurchaseOrderLine
        from sqlalchemy import select
        document=self.document(self.client(912,2),'/purchasing?order_id=2')
        lists=document.elements('datalist');self.assertTrue(lists)
        with self.app.app_context():
            order=db.session.get(PurchaseOrder,2)
            for choices in lists:
                line_id=int(choices.attrs['id'].split('-line-')[1].split('-')[0])
                line=db.session.get(PurchaseOrderLine,line_id)
                options=[n for n in document.descendants(choices) if n.tag=='option']
                self.assertLessEqual(len(options),100)
                self.assertTrue(options)
                for option in options:
                    lot=db.session.get(StockLot,int(option.attrs['value']))
                    self.assertEqual((lot.sku_id,lot.supplier_id),(line.sku_id,order.supplier_id))
                    self.assertIsNotNone(db.session.scalar(select(BranchStock).where(BranchStock.branch_id==2,BranchStock.lot_id==lot.lot_id)))
        self.assertEqual(self.client(912,2).get('/purchasing?order_id=1').status_code,404)
        analyst=self.document(self.client(903,2),'/purchasing?order_id=2')
        self.assertFalse(analyst.elements('datalist'))

    def test_optional_lines_preserve_payloads_and_offer_revisions_are_record_bound(self):
        for path,label in (('/branch-sales','Create priced sale'),('/purchasing?branch_id=2','Create purchase order'),('/transfers?branch_id=2','Create transfer draft')):
            document=self.document(self.client(901,2),path)
            form=next(n for n in document.elements('form') if n.attrs.get('aria-label')==label)
            controls=document.descendants(form)
            self.assertEqual(sum(n.attrs.get('name')=='sku_id' for n in controls),3)
            self.assertEqual(sum(n.attrs.get('name')=='quantity' for n in controls),3)
            extra=next(n for n in controls if n.tag=='details' and n.attrs.get('class')=='full additional-lines')
            self.assertNotIn('open',extra.attrs)
        document=self.document(self.client(),'/catalog')
        offers=[f for f in document.elements('form') if f.attrs.get('aria-label','').startswith('Edit assortment')]
        self.assertTrue(offers)
        for form in offers:
            revisions=[n for n in document.descendants(form) if n.attrs.get('name')=='expected_version']
            self.assertEqual(len(revisions),1)
            self.assertEqual(revisions[0].attrs.get('type'),'hidden')
            self.assertGreater(int(revisions[0].attrs['value']),0)
        policy=next(n for n in document.elements('form') if n.attrs.get('aria-label')=='Create expiry policy')
        self.assertTrue(any(n.tag=='select' and n.attrs.get('name')=='key' for n in document.descendants(policy)))
        self.assertFalse(any(n.tag=='input' and n.attrs.get('name')=='key' for n in document.descendants(policy)))


if __name__ == '__main__':
    unittest.main()
