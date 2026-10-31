"""Browser review on a fresh synthetic database; never opens an existing database.

Requires local Playwright and an installed Chrome/Edge. No browser download, live
AI, external requests, or business mutations. Screenshots are ignored private data.
"""
import argparse
import json
from pathlib import Path, PureWindowsPath
import sys
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def wsl_command(root, interpreter='python3', before=False):
    """Build an argv-only WSL launch using the release path and selected Python."""
    root = PureWindowsPath(root)
    if len(root.drive) != 2 or root.drive[1] != ':' or not root.is_absolute():
        raise ValueError('The WSL bridge requires an absolute Windows drive path.')
    if not interpreter or not interpreter.strip():
        raise ValueError('Select a WSL Python interpreter with the demo dependencies installed.')
    linux_root = '/mnt/' + root.drive[0].lower() + root.as_posix()[2:]
    command = ['wsl', '--cd', linux_root, '--exec', interpreter, '-B', 'scripts/ui_review_server.py']
    return command + (['--before'] if before else [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', action='store_true')
    parser.add_argument('--wsl', action='store_true', help='Use the existing WSL Python backend and local Windows browser')
    parser.add_argument('--wsl-python', default='python3', help='WSL interpreter with the project dependencies; default: python3 on WSL PATH')
    args = parser.parse_args()
    from scripts import verify_transformation as qa
    from playwright.sync_api import sync_playwright
    from scripts.verify_t22 import measure_contrast, form_semantics
    from jinja2 import ChoiceLoader, FileSystemLoader

    phase = 'before' if args.before else 'after'
    output = ROOT / 'data/private/ui-redesign' / phase
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='inventraiq-ui-redesign-') as parent:
        saved = ROOT / 'data/private/ui-redesign/before-source'
        remote = None
        if args.wsl:
            command = wsl_command(ROOT, args.wsl_python, args.before)
            remote = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, encoding='utf-8')
            ready = remote.stdout.readline()
            if not ready:
                raise RuntimeError('WSL review backend failed: ' + remote.stderr.read()[-1800:])
            handshake = json.loads(ready)
            origin, private = handshake['origin'], handshake['private']
            app = None
        else:
            root, app = qa.fixture(parent, 'small')
            if args.before:
                app.jinja_loader = ChoiceLoader([FileSystemLoader(saved / 'templates'), app.jinja_loader])
            _, private = qa.attest(root)
            initial = qa.verify(app)
            server = qa.make_server('127.0.0.1', 0, app, threaded=True, request_handler=qa.Quiet)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            origin = 'http://127.0.0.1:' + str(server.server_port)
        errors, cases, interactions = [], [], []
        try:
            with sync_playwright() as pw:
                executable = next(path for path in (
                    'C:/Program Files/Google/Chrome/Application/chrome.exe',
                    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe') if Path(path).is_file())
                browser = pw.chromium.launch(executable_path=executable, headless=True)
                try:
                    for uid in ((901,) if args.before else (901, 903, 912, 922)):
                        context, page = qa.login(browser, origin, private, uid, errors)
                        if args.before:
                            page.route('**/static/css/product.css', lambda route: route.fulfill(
                                path=str(saved / 'static/css/product.css'), content_type='text/css'))
                        overview = '/admin-dashboard' if uid < 910 else '/dashboard'
                        paths = [(overview, 'overview')]
                        if uid == 901:
                            paths += [('/admin-inventory', 'inventory'), ('/analytics', 'analytics'), ('/reports', 'analytics-alias'),
                                      ('/purchasing', 'purchasing'), ('/transfers', 'transfers'),
                                      ('/receiving?branch_id=2', 'receiving'), ('/catalog', 'catalog'),
                                      ('/branch-sales?branch_id=2', 'sales'), ('/admin-settings', 'settings'),
                                      ('/admin-alerts', 'attention'), ('/ai-predictions', 'intelligence'),
                                      ('/scope', 'scope'), ('/branch-administration', 'organization')]
                        if args.before:
                            paths = paths[:1]
                        for path, name in paths:
                            for width in ((1440, 390) if args.before else (1440, 1024, 768, 390, 320)):
                                page.set_viewport_size(dict(width=width, height=1000))
                                response = page.goto(origin + path)
                                page.wait_for_load_state('networkidle')
                                assert response.status == 200, (path, response.status)
                                assert page.locator('main h1').count() == 1, path
                                if not page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'):
                                    page.screenshot(path=str(output / f'overflow-{uid}-{name}-{width}.png'), full_page=True)
                                    overflow = page.evaluate("""() => Array.from(document.querySelectorAll('main,main *')).filter(el => {
                                        const r=el.getBoundingClientRect();return r.right>innerWidth+1 && r.width>0;
                                    }).slice(0,18).map(el => ({tag:el.tagName,cls:el.className.baseVal??el.className,
                                        width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right}))""")
                                    raise AssertionError((path, width, overflow))
                                assert not form_semantics(page), (path, form_semantics(page))
                                if not args.before and name == 'receiving':
                                    assert page.locator('.entry-requirement.is-required').count() > 0
                                    assert page.locator('.entry-purpose').count() > 0
                                    assert page.locator('[name="external_reference"]').first.get_attribute('aria-describedby')
                                if not args.before:
                                    measure_contrast(page)
                                if width in (1440, 390) and (uid == 901 or name == 'overview'):
                                    page.screenshot(path=str(output / f'{uid}-{name}-{width}.png'), full_page=True)
                                cases.append(dict(role=uid, path=path, width=width))
                        if not args.before:
                            page.set_viewport_size(dict(width=1440, height=1000))
                            page.goto(origin + overview)
                            button = page.locator('[data-command-open]')
                            button.click()
                            assert page.locator('#command-palette').evaluate('el=>el.open')
                            assert page.locator('#command-query').evaluate('el=>el===document.activeElement')
                            page.keyboard.press('ArrowDown')
                            assert page.locator('#command-query').get_attribute('aria-activedescendant') == 'command-result-1'
                            assert page.locator('#command-result-1').get_attribute('aria-selected') == 'true'
                            labels = page.locator('#command-results a').all_text_contents()
                            assert not any(label.strip() == 'Reports' for label in labels)
                            if uid == 922:
                                assert not any(word in ' '.join(labels) for word in ('Settings', 'Purchasing', 'Transfers', 'Receive stock'))
                            page.locator('#command-query').fill('no-such-workspace')
                            assert page.locator('[data-command-empty]').is_visible()
                            page.locator('#command-query').fill('inventory')
                            assert page.locator('#command-results a').count() == 1
                            page.keyboard.press('Enter')
                            page.wait_for_load_state('networkidle')
                            assert 'inventory' in page.url
                            page.keyboard.press('Control+k')
                            page.keyboard.press('Escape')
                            assert not page.locator('#command-palette').evaluate('el=>el.open')
                            button.focus()
                            button.click()
                            page.keyboard.press('Escape')
                            assert button.evaluate('el=>el===document.activeElement')
                            if uid == 901:
                                button.click()
                                page.screenshot(path=str(output / 'workspace-search.png'))
                                page.keyboard.press('Escape')
                            interactions.append(f'{uid}: role-scoped search, empty result, Enter navigation, Ctrl+K, Escape, focus return')
                            page.goto(origin + overview)
                            page.locator('.assistant-trigger').click()
                            assert page.locator('#assistant-panel').evaluate('el=>el.open')
                            page.keyboard.press('Escape')
                            page.set_viewport_size(dict(width=390, height=844))
                            page.locator('[data-nav-toggle]').click()
                            sidebar = page.locator('#primary-sidebar')
                            for _ in range(15):
                                page.keyboard.press('Tab')
                                assert sidebar.evaluate('el=>el.contains(document.activeElement)')
                            page.keyboard.press('Escape')
                            assert page.locator('[data-nav-toggle]').evaluate('el=>el===document.activeElement')
                            page.locator('.assistant-trigger').click()
                            assert page.locator('#assistant-panel').evaluate('el=>el.open')
                            assert not page.locator('body').evaluate("el=>el.classList.contains('nav-open')")
                            page.keyboard.press('Escape')
                            assert page.locator('.assistant-trigger').evaluate('el=>el===document.activeElement')
                            if uid in (901,903):
                                page.locator('main [data-assistant-prompt="recommendations"]').click()
                                assert page.locator('#assistant-question').input_value()=='recommendations'
                                page.locator('#assistant-send').click()
                                page.wait_for_function("document.getElementById('assistant-send').disabled===false && document.querySelector('.assistant-decision-card')")
                                assert page.locator('.assistant-decision-card').count()>0
                                assert not page.locator('#assistant-messages').get_by_text('Technical record',exact=True).count()
                                if uid==901:
                                    page.screenshot(path=str(output/'planning-assistant-390.png'))
                                    page.set_viewport_size(dict(width=1440,height=1000))
                                    page.screenshot(path=str(output/'planning-assistant-1440.png'))
                                page.keyboard.press('Escape')
                            interactions.append(f'{uid}: native assistant, mobile navigation focus trap, contextual planning brief and focus return')
                            if uid == 901:
                                page.goto(origin + '/analytics?branch_id=2&start=2025-01-01&end=2025-01-31')
                                page.get_by_role('button', name='Explain these figures').click()
                                assert page.locator('#assistant-question').input_value() == 'inventory and sales 2025-01-01 to 2025-01-31'
                                with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/messages')) as pending:
                                    page.locator('#assistant-send').click()
                                reply = pending.value.json()
                                evidence = reply['messages'][-1]['evidence']
                                assert len(evidence) == 2
                                for entry in evidence:
                                    result = entry['result']
                                    assert result['status'] == 'ok', result.get('error')
                                    assert result['scope']['branch_ids'] == [2]
                                    assert result['filters']['start'] == '2025-01-01'
                                    assert result['filters']['end'] == '2025-01-31'
                                page.wait_for_function("document.getElementById('assistant-send').disabled===false")
                                assert page.locator('.assistant-fact-grid').count() > 0
                                interactions.append('HQ: Analytics assistant reads the displayed date range and Eastside scope')
                        context.close()

                    context = browser.new_context()
                    context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
                    if args.before:
                        context.route('**/static/css/product.css', lambda route: route.fulfill(
                            path=str(saved / 'static/css/product.css'), content_type='text/css'))
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    for path, name in [('/roles', 'workspace-access'), ('/login/admin', 'sign-in')]:
                        for width in (1440, 390, 320):
                            page.set_viewport_size(dict(width=width, height=1000))
                            assert page.goto(origin + path).status == 200
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                            page.screenshot(path=str(output / f'{name}-{width}.png'), full_page=True)
                            cases.append(dict(role='anonymous', path=path, width=width))
                    context.close()
                    assert not errors, errors
                    if app is not None:
                        assert qa.verify(app) == initial, 'Read-only review changed business facts'
                    if remote:
                        remaining, backend_errors = remote.communicate(input='\n', timeout=30)
                        assert remote.returncode == 0, backend_errors[-1800:]
                        assert json.loads(remaining)['business_facts_unchanged']
                    result = dict(passed=True, phase=phase, browser=browser.version, cases=cases,
                                  interactions=interactions, javascript_errors=errors,
                                  business_facts_unchanged=True, external_requests=0)
                    (output / 'verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
                    print(json.dumps(dict(passed=True, phase=phase, cases=len(cases), interactions=len(interactions), evidence=str(output))))
                finally:
                    browser.close()
        finally:
            if remote:
                if remote.poll() is None:
                    remote.communicate(input='\n', timeout=30)
            else:
                server.shutdown()
                worker.join(5)
                server.server_close()
                with app.app_context():
                    qa.db.session.remove()
                    qa.db.engine.dispose()


if __name__ == '__main__':
    main()
