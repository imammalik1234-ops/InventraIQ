"""The enhanced form transport preserves one-use feedback and security guards."""
import unittest
from flask import Flask, flash, get_flashed_messages, redirect
from flask_login import LoginManager
from security import init_security


class FormRedirectTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='isolated-form-transport-test')
        manager = LoginManager(self.app)
        manager.user_loader(lambda key: None)
        init_security(self.app, manager)

        @self.app.post('/login', endpoint='admin_login')
        def write():
            flash('Recorded once.', 'success')
            return redirect('/target', code=303)

        @self.app.get('/target')
        def target():
            return dict(messages=get_flashed_messages(with_categories=True))

        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state['_csrf_token'] = 'test-csrf'

    def test_enhanced_post_does_not_consume_flash_before_navigation(self):
        response = self.client.post('/login', data=dict(csrf_token='test-csrf'),
                                    headers={'X-InventraIQ-Form': '1'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), dict(redirect_to='/target'))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertNotIn('Location', response.headers)
        self.assertEqual(self.client.get('/target').get_json()['messages'], [['success', 'Recorded once.']])
        self.assertEqual(self.client.get('/target').get_json()['messages'], [])

    def test_native_post_preserves_http_redirect(self):
        response = self.client.post('/login', data=dict(csrf_token='test-csrf'))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, '/target')

    def test_enhancement_header_cannot_bypass_csrf(self):
        response = self.client.post('/login', headers={'X-InventraIQ-Form': '1'})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('redirect_to', response.get_json())
        self.assertEqual(self.client.get('/target').get_json()['messages'], [])

    def test_external_redirect_is_not_promoted_to_enhanced_navigation(self):
        self.app.view_functions['admin_login'] = lambda: redirect('https://example.invalid/target')
        response = self.client.post('/login', data=dict(csrf_token='test-csrf'),
                                    headers={'X-InventraIQ-Form': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(response.is_json)


if __name__ == '__main__':
    unittest.main()
