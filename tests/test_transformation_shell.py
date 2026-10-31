"""Role-aware product navigation on a disposable populated SYN-T15 fixture."""
from tempfile import TemporaryDirectory
import unittest

from test_design_accessibility import Document


class ProductShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ingestion.target import ACK
        from simulation.runtime import initialize, create_app
        from simulation.runner import run
        from simulation.verify import verify
        cls.temp = TemporaryDirectory(prefix='inventraiq-product-shell-')
        cls.app = create_app(initialize(cls.temp.name, acknowledgment=ACK))
        run(cls.app)
        cls.before = verify(cls.app)

    @classmethod
    def tearDownClass(cls):
        from database import db
        from simulation.verify import verify
        try:
            if verify(cls.app) != cls.before:
                raise AssertionError('Shell reads changed synthetic business facts')
        finally:
            with cls.app.app_context():
                db.session.remove()
                db.engine.dispose()
            cls.temp.cleanup()

    def document(self, uid, path, branch=None):
        from scripts.measure_t16 import client
        response = client(self.app, uid, branch).get(path)
        self.assertEqual(response.status_code, 200, path)
        return Document(response.get_data(as_text=True))

    def navigation(self, document):
        nav = next(n for n in document.elements('nav') if n.attrs.get('aria-label') == 'Main navigation')
        return {n.text.strip(): n.attrs.get('href') for n in document.descendants(nav) if n.tag == 'a'}

    def test_analyst_navigation_is_explicitly_read_only_and_omits_write_workspaces(self):
        document = self.document(903, '/admin-dashboard')
        links = self.navigation(document)
        self.assertIn('Transfers', links)
        self.assertIn('Purchasing', links)
        self.assertIn('Analytics', links)
        self.assertNotIn('Reports', links)
        self.assertNotIn('Settings', links)
        self.assertNotIn('Receive stock', links)
        self.assertIn('Read only', next(n for n in document.elements('header') if n.attrs.get('class') == 'topbar').text)

    def test_branch_clerk_never_gets_manager_or_hq_navigation(self):
        document = self.document(922, '/dashboard', 2)
        links = self.navigation(document)
        for name in ('Transfers', 'Purchasing', 'Receive stock', 'Settings', 'Reports'):
            self.assertNotIn(name, links)
        self.assertIn('Sales', links)
        self.assertIn('My workspace', links)
        select = next(n for n in document.elements('select') if n.attrs.get('id') == 'shell-branch')
        options = [n for n in document.descendants(select) if n.tag == 'option']
        self.assertEqual([n.attrs['value'] for n in options], ['2'])

    def test_manager_and_administrator_keep_their_authorized_workspaces(self):
        manager = self.navigation(self.document(912, '/dashboard', 2))
        self.assertTrue({'Receive stock', 'Transfers', 'Purchasing'}.issubset(manager))
        self.assertNotIn('Settings', manager)
        administrator = self.navigation(self.document(901, '/admin-dashboard'))
        self.assertTrue({'Settings', 'Analytics', 'Receive stock', 'Transfers', 'Purchasing'}.issubset(administrator))

    def test_shell_keeps_single_current_page_and_csrf_protected_scope_and_logout(self):
        for path, label in (('/sku-inventory', 'Inventory'), ('/branch-sales', 'Sales'), ('/catalog', 'Shared catalog')):
            document = self.document(912, path, 2)
            self.assertEqual([n.text.strip() for n in document.elements('a') if n.attrs.get('aria-current') == 'page'], [label])
            for action in ('/scope', '/logout'):
                form = next(n for n in document.elements('form') if n.attrs.get('action') == action)
                self.assertEqual(form.attrs.get('method').upper(), 'POST')
                token = [n for n in document.descendants(form) if n.attrs.get('name') == 'csrf_token']
                self.assertEqual(len(token), 1)
                self.assertTrue(token[0].attrs.get('value'))

    def test_authentication_pages_preserve_semantic_and_password_manager_contracts(self):
        anonymous = self.app.test_client()
        for path in ('/roles', '/login/admin', '/login/staff'):
            response = anonymous.get(path)
            self.assertEqual(response.status_code, 200, path)
            document = Document(response.get_data(as_text=True))
            self.assertEqual(len(document.elements('h1')), 1)
            self.assertEqual(len(document.elements('main')), 1)
            self.assertTrue(any(n.attrs.get('href', '').endswith('/css/product.css') for n in document.elements('link')))
            if path != '/roles':
                inputs = {n.attrs.get('name'): n for n in document.elements('input')}
                self.assertEqual(inputs['email'].attrs.get('autocomplete'), 'username')
                self.assertEqual(inputs['password'].attrs.get('autocomplete'), 'current-password')
                self.assertTrue(inputs['csrf_token'].attrs.get('value'))


if __name__ == '__main__':
    unittest.main()
