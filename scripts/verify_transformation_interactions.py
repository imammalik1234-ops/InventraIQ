"""Additional interaction and timing checks on a fresh, disposable SYN-T15-V1.

Reuses the local-only acceptance harness. No existing database argument, external
requests, installs or provider access. Run after the main browser walkthrough.
"""
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from threading import Thread
import time

from scripts import verify_transformation as qa
from sqlalchemy import event, select
from models import BranchStock, StockLot
from scripts.measure_t16 import client


def main():
    from playwright.sync_api import sync_playwright
    output = qa.ROOT / 'data/private/transformation/browser'
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='inventraiq-interactions-') as parent:
        root, app = qa.fixture(parent, 'small')
        _, private = qa.attest(root)
        initial = qa.verify(app)
        history = qa.history_hash(root)
        server = qa.make_server('127.0.0.1', 0, app, threaded=True, request_handler=qa.Quiet)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        errors = []
        console_errors = []
        expected_console = []
        timings = {}
        try:
            with sync_playwright() as pw:
                executable = next(p for p in (
                    'C:/Program Files/Google/Chrome/Application/chrome.exe',
                    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe') if Path(p).is_file())
                browser = pw.chromium.launch(executable_path=executable, headless=True)
                try:
                    context, page = qa.login(browser, origin, private, 912, errors)
                    page.on('console', lambda message: console_errors.append(message.text)
                            if message.type == 'error' else None)
                    cases = qa.check_page(page, origin, '/scope', 'scope', output)
                    cases += qa.check_page(page, origin, '/catalog', 'catalog', output)
                    for summary in page.locator('.workflow-action > summary').all():
                        if summary.is_visible():
                            assert summary.evaluate('e=>e.scrollWidth<=e.clientWidth+1'), 'Clipped catalog action'
                    cases += qa.check_page(page, origin,
                        '/reports?start=2030-01-03&end=2031-01-03', 'annual-report', output, screenshots=False)
                    before_errors = len(console_errors)
                    cases += qa.check_page(page, origin,
                        '/reports?start=2020-01-01&end=2031-01-03', 'filter-error', output, expected_status=400)
                    page.get_by_role('link', name='Reset filters', exact=False).click()
                    assert page.locator('[data-view="reports"]').count() == 1
                    expected_console += console_errors[before_errors:]
                    del console_errors[before_errors:]
                    page.set_viewport_size(dict(width=768, height=1024))
                    page.goto(origin + '/dashboard')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    page.get_by_role('button', name='Open navigation', exact=True).click()
                    page.keyboard.press('Escape')
                    page.screenshot(path=str(output / 'branch-tablet-768.png'), full_page=True)
                    page.set_viewport_size(dict(width=320, height=844))
                    page.reload()
                    page.keyboard.press('Tab')
                    assert page.locator('.skip-link').evaluate('e=>e===document.activeElement')
                    page.keyboard.press('Enter')
                    assert page.locator('main').evaluate('e=>e===document.activeElement')
                    opener = page.locator('[data-assistant-open]').first
                    opener.focus(); page.keyboard.press('Enter')
                    panel = page.locator('#assistant-panel')
                    assert panel.evaluate('e=>e.open && e.contains(document.activeElement)')
                    for _ in range(12):
                        page.keyboard.press('Tab')
                        assert panel.evaluate('e=>e.contains(document.activeElement)')
                    page.keyboard.press('Escape')
                    assert opener.evaluate('e=>e===document.activeElement')
                    assert not console_errors, console_errors

                    # A rejected adjustment keeps the same form and lets the
                    # user correct it. Check the actual DB at each boundary.
                    with app.app_context():
                        lot = qa.db.session.scalar(select(StockLot.lot_id).join(
                            BranchStock, BranchStock.lot_id == StockLot.lot_id).where(
                            BranchStock.branch_id == 2, StockLot.sku_id == 122,
                            BranchStock.on_hand > 1))
                        original = qa.db.session.get(BranchStock, (2, lot)).on_hand
                    page.goto(origin + '/sku-inventory')
                    form = qa.open_form(page, f'Adjust native lot {lot}')
                    qa.fill(form, direction='decrease', kind='adjustment', quantity=1000000,
                            business_reference='SYN-TRANSFORM-REJECT', reason='Synthetic count rejection')
                    before_errors = len(console_errors)
                    form.get_by_role('button', name='Record stock adjustment', exact=True).click()
                    page.locator('#global-message:visible').wait_for()
                    assert form.get_by_role('button').is_enabled()
                    with app.app_context():
                        assert qa.db.session.get(BranchStock, (2, lot)).on_hand == original
                    rejection = page.locator('#global-message').inner_text()
                    assert rejection and '<script>' not in rejection
                    expected_console += console_errors[before_errors:]
                    del console_errors[before_errors:]
                    qa.fill(form, quantity=1, business_reference='SYN-TRANSFORM-ADJUST')
                    qa.submit(page, form, 'Committed operation #')
                    with app.app_context():
                        assert qa.db.session.get(BranchStock, (2, lot)).on_hand == original - 1
                    assert qa.verify(app) != initial
                    reconciled = qa.reconciliation(app, history)

                    # Simulate an unavailable assistant locally; no provider is
                    # contacted and the input is re-enabled for recovery.
                    page.goto(origin + '/dashboard')
                    page.route('**/api/assistant/conversations', lambda route: route.fulfill(
                        status=503, content_type='application/json',
                        body=json.dumps(dict(error=dict(code='provider_unavailable')))))
                    page.locator('[data-assistant-open]').first.click()
                    page.get_by_label('Ask about your authorized inventory').fill('inventory')
                    before_errors = len(console_errors)
                    page.get_by_role('button', name='Send', exact=True).click()
                    page.get_by_text('The planner is unavailable. No business records were changed.', exact=True).wait_for()
                    assert page.get_by_role('button', name='Send', exact=True).is_enabled()
                    page.keyboard.press('Escape')
                    page.unroute('**/api/assistant/conversations')
                    expected_console += console_errors[before_errors:]
                    del console_errors[before_errors:]
                    # Losing the session redirects to an actionable sign-in.
                    context.clear_cookies()
                    response = page.goto(origin + '/dashboard')
                    assert response.status == 200 and '/roles' in page.url
                    assert not page.locator('[data-workspace-charts]').count()
                    page.locator('a[href="/login/staff"]').click()
                    assert page.get_by_role('button', name='Sign in to workspace', exact=True).is_visible()
                    page.screenshot(path=str(output / 'session-expired-320.png'), full_page=True)
                    context.close()
                    assert not errors and not console_errors, (errors, console_errors)
                    version = browser.version
                finally:
                    browser.close()

            # In-process lab timings: one cold and five warm requests per path.
            # Include auth, query work, template rendering and response bytes.
            caller = client(app, 901)
            queries = []
            with app.app_context():
                engine = qa.db.engine
            def count(*args):
                queries.append(1)
            event.listen(engine, 'before_cursor_execute', count)
            try:
                for path in ('/admin-dashboard', '/catalog', '/reports', '/api/sku-stock?search=SYN&page_size=25'):
                    samples = []
                    for _ in range(6):
                        queries.clear(); start = time.perf_counter()
                        response = caller.get(path)
                        body = response.get_data()
                        assert response.status_code == 200, path
                        samples.append(dict(ms=round((time.perf_counter()-start)*1000, 3),
                                            queries=len(queries), bytes=len(body)))
                    timings[path] = dict(cold=samples[0], warm=samples[1:],
                                         warm_median_ms=median(s['ms'] for s in samples[1:]))
            finally:
                event.remove(engine, 'before_cursor_execute', count)
            assert qa.reconciliation(app, history) == reconciled
            result = dict(passed=True, browser=version, cases=cases,
                          tablet_width=768, keyboard_skip_and_dialog=True,
                          adjustment_rejection_retry=True, session_loss_recovery=True,
                          simulated_assistant_failure_recovery=True,
                          page_errors=errors, unexpected_console_errors=console_errors,
                          expected_rejection_console_messages=len(expected_console),
                          external_requests=0, timings=timings, reconciliation=reconciled,
                          performance_scope='small disposable SYN-T15-V1; local test-client laboratory; no load or field claims')
            (output / 'interactions.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
            print(json.dumps(dict(passed=True, cases=len(cases), timings=timings)))
        finally:
            server.shutdown(); worker.join(5); server.server_close()
            with app.app_context():
                qa.db.session.remove(); qa.db.engine.dispose()


if __name__ == '__main__':
    main()
