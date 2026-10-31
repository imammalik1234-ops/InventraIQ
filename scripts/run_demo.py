"""Run or verify a local demo without opening configured/existing databases."""
import argparse
from contextlib import contextmanager
from getpass import getpass
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@contextmanager
def demo_application(password):
    # Must select the disposable database before app.py initializes SQLAlchemy.
    if 'app' in sys.modules:
        raise RuntimeError('Run this launcher in a fresh Python process.')
    with tempfile.TemporaryDirectory(prefix='inventraiq-demo-') as directory:
        path = Path(directory).resolve() / 'demo.db'
        os.environ.update(
            PYTHON_DOTENV_DISABLED='1', DATABASE_URL='sqlite:///' + path.as_posix(),
            APP_ENV='development', HTTPS_REQUIRED='false', FLASK_DEBUG='false',
            SECRET_KEY=secrets.token_hex(32), SESSION_COOKIE_SECURE='false',
            OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
        )
        from app import app
        from database import db
        from models import User, UserHQScope
        from sqlalchemy import inspect
        try:
            with app.app_context():
                assert db.engine.url.get_backend_name() == 'sqlite'
                assert Path(db.engine.url.database).resolve() == path
                assert inspect(db.engine).get_table_names() == []
                db.create_all()
                from branch_foundation import seed_demo_branches
                seed_demo_branches(db.session)
                admin = User(name='Demo Administrator', email='demo-admin@example.invalid', role='admin')
                admin.set_password(password)
                db.session.add(admin)
                db.session.flush()
                db.session.add(UserHQScope(user_id=admin.user_id, hq_role='admin'))
                db.session.commit()
                email = admin.email
            yield app, email
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


def check_login(app, email, password):
    from database import db
    from sqlalchemy import inspect
    with app.app_context():
        schema = inspect(db.engine)
        for table in db.metadata.sorted_tables:
            assert schema.has_table(table.name)
            assert set(table.columns.keys()) == {c['name'] for c in schema.get_columns(table.name)}
    client = app.test_client()
    assert client.get('/admin-dashboard').status_code == 302
    assert client.get('/login/admin').status_code == 200
    with client.session_transaction() as session:
        token = session['_csrf_token']
    assert client.post('/login/admin', data={'email': email, 'password': password}).status_code == 400
    response = client.post('/login/admin', data={'email': email, 'password': password, 'csrf_token': token})
    assert response.status_code == 302 and response.location.endswith('/admin-dashboard')
    assert client.get(response.location).status_code == 200
    with client.session_transaction() as session:
        assert session['_user_id']
    return {'schema': 'latest model tables/columns', 'csrf_rejection': 400,
            'login': 302, 'protected_dashboard': 200, 'database': 'disposable SQLite'}


def verify_browser(app, email, password):
    import threading
    from werkzeug.serving import make_server, WSGIRequestHandler
    from playwright.sync_api import sync_playwright
    executable = next((p for p in (
        Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
        Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
    ) if p.exists()), None)
    if executable is None:
        raise RuntimeError('Chrome/Edge not found; browser verification not performed.')

    class QuietHandler(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):
            pass

    server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = 'http://127.0.0.1:' + str(server.server_port)
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            response = page.goto(origin + '/admin-dashboard')
            assert response.status == 200 and not page.url.endswith('/admin-dashboard')
            page.goto(origin + '/login/admin')
            page.get_by_label('Email address').fill(email)
            page.locator('#password').fill(password)
            page.get_by_role('button', name='Sign in to workspace').click()
            page.wait_for_url('**/admin-dashboard')
            assert page.reload().status == 200
            assert page.get_by_role('heading', name='Inventory overview', exact=True).is_visible()
            assert page.get_by_text('Start with your first product', exact=True).is_visible()
            screenshot = Path(tempfile.gettempdir()) / 'inventraiq-login-dashboard.png'
            page.screenshot(path=str(screenshot), full_page=True)
            page.get_by_role('button', name='Sign out').click()
            page.goto(origin + '/admin-dashboard')
            assert not page.url.endswith('/admin-dashboard')
            assert not errors, errors
            browser.close()
        return {'browser': 'Chrome/Edge headless', 'login_dashboard_reload_logout': 'passed',
                'javascript_errors': len(errors), 'screenshot': str(screenshot)}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Verify schema, CSRF, login and dashboard, then delete demo data.')
    mode.add_argument('--verify-browser', action='store_true', help='Also verify login in local Chrome; requires optional Playwright.')
    parser.add_argument('--port', type=int, default=5000)
    args = parser.parse_args()
    password = secrets.token_urlsafe(32) if args.check or args.verify_browser else getpass('Choose a private demo admin password (not displayed): ')
    if not password:
        parser.error('A nonempty password is required.')
    with demo_application(password) as (app, email):
        result = check_login(app, email, password)
        if args.verify_browser:
            result.update(verify_browser(app, email, password))
        if args.check or args.verify_browser:
            print(json.dumps(result, indent=2))
        else:
            print('Local disposable demo ready. Sign in with the demo email documented in README and your chosen password.')
            print('Data is removed when this process stops. Existing database settings are unchanged.')
            app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
