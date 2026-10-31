"""Workspace integration contracts on a newly populated disposable T15 only."""
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import parse_qs, urlsplit

from test_design_accessibility import Document


class OperationalWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.measure_t16 import fixture
        from simulation.verify import verify
        cls.temp = TemporaryDirectory(prefix='inventraiq-workspace-redesign-')
        cls.root, cls.app = fixture(cls.temp.name, 'small')
        cls.before = verify(cls.app)

    @classmethod
    def tearDownClass(cls):
        from database import db
        from simulation.verify import verify
        try:
            if verify(cls.app) != cls.before:
                raise AssertionError('Workspace rendering changed synthetic business facts')
        finally:
            with cls.app.app_context():
                db.session.remove()
                db.engine.dispose()
            cls.temp.cleanup()

    def page(self, path, actor=901, branch=None):
        from scripts.measure_t16 import client
        response = client(self.app, actor, branch).get(path)
        self.assertEqual(response.status_code, 200, path)
        return Document(response.get_data(as_text=True))

    def test_catalog_rows_keep_search_identity_and_record_bound_edits(self):
        page = self.page('/catalog')
        rows = [row for row in page.elements('tr') if 'data-catalog-entry' in row.attrs]
        self.assertTrue(rows)
        for row in rows:
            self.assertIn(row.attrs['data-sku-code'], row.attrs['data-catalog-entry'])
            edit = next(form for form in page.descendants(row) if form.tag == 'form'
                        and form.attrs.get('aria-label', '').startswith('Edit SKU'))
            fields = {node.attrs.get('name'): node for node in page.descendants(edit)
                      if node.tag == 'input'}
            self.assertEqual(fields['code'].attrs['value'], row.attrs['data-sku-code'])
            self.assertEqual(fields['expected_version'].attrs['type'], 'hidden')
            self.assertTrue(fields['csrf_token'].attrs.get('value'))
        analyst = self.page('/catalog', 903)
        self.assertFalse(any(form.attrs.get('action') == '/catalog/actions'
                             for form in analyst.elements('form')))

    def test_branch_directory_and_planning_tables_retain_accessible_evidence(self):
        for path in ('/branch-administration', '/ai-predictions'):
            page = self.page(path)
            self.assertEqual(len(page.elements('h1')), 1)
            ids = [node.attrs['id'] for node in page.nodes if 'id' in node.attrs]
            self.assertEqual(len(ids), len(set(ids)))
            for table in page.elements('table'):
                self.assertTrue(any(node.tag == 'caption' and node.text.strip()
                                    for node in page.descendants(table)))
                self.assertEqual(table.parent.attrs.get('role'), 'region')
                self.assertEqual(table.parent.attrs.get('tabindex'), '0')
        directory = self.page('/branch-administration')
        actions = {node.attrs.get('value') for node in directory.elements('input')
                   if node.attrs.get('name') == 'action'}
        self.assertTrue({'branch', 'assignment', 'hq'} <= actions)
        for form in directory.elements('form'):
            if form.attrs.get('method', '').upper() == 'POST':
                self.assertTrue(any(node.attrs.get('name') == 'csrf_token'
                                    for node in directory.descendants(form)))

    def test_selected_scope_is_preserved_in_new_and_existing_receipt_forms(self):
        page = self.page('/receiving?branch_id=2')
        forms = [form for form in page.elements('form') if form.attrs.get('method') == 'POST'
                 and form.attrs.get('action', '').startswith('/receiving')]
        self.assertGreater(len(forms), 1)
        for form in forms:
            self.assertEqual(parse_qs(urlsplit(form.attrs['action']).query)['branch_id'], ['2'])
            names = {node.attrs.get('name') for node in page.descendants(form)}
            self.assertTrue({'csrf_token', 'mode', 'quantity', 'external_reference',
                             'receipt_kind', 'reason', 'evidence'} <= names)

    def test_sale_return_drilldown_preserves_original_allocation_and_refund_gate(self):
        page = self.page('/branch-sales?branch_id=1&sale_id=1')
        forms = [form for form in page.elements('form')
                 if form.attrs.get('aria-label', '').startswith('Refund allocation')]
        self.assertTrue(forms)
        for form in forms:
            self.assertEqual(form.parent.tag, 'details')
            fields = {node.attrs.get('name'): node for node in page.descendants(form)
                      if node.tag in ('input', 'select')}
            self.assertEqual(fields['mode'].attrs['value'], 'partial')
            self.assertEqual(fields['allocation_id'].attrs['type'], 'hidden')
            self.assertIn('saleable_verified', fields)
            self.assertIn('inspection_evidence', fields)
            self.assertIn('request_key', fields)
        readonly = self.page('/branch-sales?branch_id=1&sale_id=1', 903)
        self.assertFalse(any(form.attrs.get('aria-label', '').startswith(
            ('Refund allocation', 'Reverse remaining', 'Create priced sale'))
            for form in readonly.elements('form')))


if __name__ == '__main__':
    unittest.main()
