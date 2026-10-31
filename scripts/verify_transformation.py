"""Connected local browser acceptance on newly created synthetic databases only.

No database URL is accepted. Existing browsers/Playwright are required; nothing is
installed. All browser requests and Python sockets are restricted to loopback.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.pop('GEMINI_API_KEY', None)
os.environ.update(DATABASE_URL='sqlite:///:memory:', ASSISTANT_PROVIDER='fake',
                  GEMINI_ENABLED='false', PYTHON_DOTENV_DISABLED='1')


def audit(event, values):
    if event == 'socket.connect':
        address = values[1]
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1', 'localhost'):
            raise RuntimeError('External networking is prohibited by this verifier')


sys.addaudithook(audit)
from werkzeug.serving import make_server, WSGIRequestHandler
from sqlalchemy import select, func
from database import db
from scripts.measure_t16 import fixture
from simulation.runtime import attest, actor, history_hash
from simulation.verify import verify
from models import BranchStock, InventoryOperation, InventoryMovement, PricedSale, SaleRefund, StockTransfer, TransferTransit


class Quiet(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def open_form(page, name):
    # A form inside a closed details element is intentionally absent from the
    # accessibility tree until its disclosure is opened.
    form = page.locator(f'form[aria-label="{name}"]')
    assert form.count() == 1, (name, page.locator('body').get_attribute('data-workspace-role'))
    form.evaluate("e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true}")
    return form


def fill(form, **values):
    for key, value in values.items():
        field = form.locator(f'[name="{key}"]').first
        if field.evaluate("e=>e.tagName==='SELECT'"):
            field.select_option(str(value))
        else:
            field.fill(str(value))


def submit(page, form, expected=None):
    assert form.evaluate('e=>e.reportValidity()'), form.get_attribute('aria-label')
    try:
        with page.expect_navigation(wait_until='networkidle',timeout=12000):
            form.locator('button[type="submit"],button:not([type])').last.click()
    except Exception:
        # These fixtures contain synthetic evidence only. Report only the safe
        # local rejection summary, never form fields, cookies or response bodies.
        message=page.locator('#global-message')
        reason=message.inner_text() if message.count() and message.is_visible() else 'No local rejection summary'
        raise AssertionError(reason) from None
    assert page.locator('main h1').count() == 1
    if expected:
        assert expected in page.locator('main').inner_text(), (expected, page.locator('main').inner_text()[:350])


def login(browser, origin, private, uid, errors):
    context = browser.new_context(viewport=dict(width=1440, height=1000), reduced_motion='reduce')
    context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/login/' + ('admin' if uid < 910 else 'staff'))
    page.get_by_label('Email address').fill(f'synthetic-{uid}@demo.invalid')
    page.locator('#password').fill(private['credentials'][str(uid)])
    page.get_by_role('button', name='Sign in to workspace', exact=True).click()
    page.wait_for_load_state('networkidle')
    assert page.locator('main h1').count() == 1
    return context, page


def check_page(page, origin, path, name, output, screenshots=True, expected_status=200):
    from scripts.verify_t22 import measure_contrast, form_semantics
    cases = []
    for width in (1440, 320):
        page.set_viewport_size(dict(width=width, height=1000))
        start = time.monotonic()
        response = page.goto(origin + path)
        page.wait_for_load_state('networkidle')
        assert response.status == expected_status, (path, response.status)
        assert page.locator('main h1').count() == 1, path
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), (path, width, 'overflow')
        ids = page.locator('[id]').evaluate_all('els=>els.map(e=>e.id)')
        assert len(ids) == len(set(ids)), (path, 'duplicate IDs')
        assert not form_semantics(page), (path, 'unlabelled form fields')
        contrast = measure_contrast(page)
        for region in page.locator('.table-scroll').all():
            assert region.get_attribute('tabindex') == '0', path
        for detail in page.locator('main details').all()[:2]:
            summary = detail.locator('summary').first
            if not summary.is_visible():
                continue
            was_open = detail.evaluate('e=>e.open')
            summary.focus(); page.keyboard.press('Enter')
            assert detail.evaluate('e=>e.open') != was_open
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), (path, width, 'disclosure overflow')
            summary.focus(); page.keyboard.press('Enter')
            assert detail.evaluate('e=>e.open') == was_open
        if screenshots:
            page.evaluate("document.activeElement.blur();scrollTo({top:0,left:0,behavior:'instant'})")
            page.locator('.table-scroll,.chart-plot').evaluate_all('els=>els.forEach(e=>e.scrollLeft=0)')
            page.screenshot(path=str(output / f'{name}-{width}.png'), full_page=True)
        perf = page.evaluate("""()=>{const n=performance.getEntriesByType('navigation')[0];return {
            response_ms:n.responseEnd-n.requestStart,dom_ms:n.domContentLoadedEventEnd-n.startTime,
            transfer_bytes:performance.getEntriesByType('resource').reduce((s,r)=>s+r.transferSize,0),
            nodes:document.querySelectorAll('*').length}}""")
        cases.append(dict(path=path, width=width, contrast=contrast,
                          local_wall_ms=round((time.monotonic()-start)*1000), **perf))
    return cases


def select_branch(page, branch):
    page.set_viewport_size(dict(width=1440, height=1000))
    form = page.get_by_role('form', name='Switch branch', exact=True)
    form.locator('select').select_option(str(branch))
    form.get_by_role('button', name='Switch', exact=True).click()
    page.wait_for_load_state('networkidle')
    from urllib.parse import urlsplit
    url=urlsplit(page.url)
    scope=page.request.get(url.scheme+'://'+url.netloc+'/api/operations').json()['scope']
    assert (scope['branch_ids']==[int(branch)]) if str(branch)!='all' else scope['consolidated']


def navigation_keyboard(page):
    page.set_viewport_size(dict(width=320, height=844))
    opener = page.get_by_role('button', name='Open navigation', exact=True)
    opener.focus(); page.keyboard.press('Enter')
    sidebar = page.locator('#primary-sidebar')
    for _ in range(24):
        page.keyboard.press('Tab')
        assert sidebar.evaluate('e=>e.contains(document.activeElement)')
    page.keyboard.press('Escape')
    assert opener.evaluate('e=>e===document.activeElement')
    page.set_viewport_size(dict(width=1440, height=1000))


def read_walkthrough(browser, app, origin, private, output):
    errors = []; cases = []; sessions = {}
    context=browser.new_context(viewport=dict(width=1440,height=1000),reduced_motion='reduce')
    context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
    page=context.new_page()
    for path,name in [('/roles','choose-workspace'),('/login/admin','login')]:
        cases+=check_page(page,origin,path,name,output)
    context.close()
    for uid in (901, 902, 903, 912, 922):
        sessions[uid] = login(browser, origin, private, uid, errors)
        context, page = sessions[uid]
        expected = [1, 2, 3] if uid < 910 else [2]
        assert page.request.get(origin + '/api/operations').json()['scope']['branch_ids'] == expected
        navigation_keyboard(page)
        if uid == 901:
            paths = [('/admin-dashboard','hq-overview'),('/admin-inventory','inventory'),('/sku-inventory','lots'),
                     ('/catalog','catalog'),('/sales','sales-summary'),('/analytics','analytics'),('/reports','reports'),
                     ('/admin-alerts','attention'),('/purchasing','purchasing'),('/transfers','transfers'),
                     ('/ai-predictions','intelligence'),('/branch-administration','branches'),('/admin-settings','settings'),('/staff','staff')]
        elif uid == 912:
            paths = [('/dashboard','branch-overview'),('/branch-sales','branch-sales'),('/receiving','receiving'),
                     ('/purchasing?order_id=2','po-detail'),('/transfers?transfer_id=1','transfer-detail')]
        else:
            paths = [('/dashboard',f'role-{uid}-overview')]
        for path, name in paths:
            cases += check_page(page, origin, path, name, output)
        if uid >= 910:
            assert page.request.get(origin + '/api/operations?branch_id=1').status == 403
            assert 'DEMO-01' not in page.locator('main').inner_text()
            assert 'DEMO-03' not in page.locator('main').inner_text()
        if uid == 922:
            assert page.request.get(origin + '/purchasing').status == 403
            assert page.request.get(origin + '/transfers').status == 403
            cases+=check_page(page,origin,'/transfers','restricted',output,expected_status=403)
        if uid == 903:
            select_branch(page, 2)
            page.goto(origin + '/branch-sales')
            assert page.get_by_role('form', name='Create priced sale', exact=True).count() == 0
            assert page.locator('main form[method="POST"]').count() == 0
            page.goto(origin+'/assistant/proposals')
            assert page.get_by_role('button',name='Create proposal for review',exact=True).count()==0
        # Every role: absent CSRF cannot mutate even a permitted endpoint.
        assert page.request.post(origin + '/api/priced-sales', data='{}', headers={'Content-Type':'application/json'}).status == 400
    assert not errors, errors
    cases+=check_page(sessions[901][1],origin,'/missing-transformation-resource','missing',output,expected_status=404)
    import csv,io
    for uid,expected in ((901,[1,2,3]),(912,[2])):
        page=sessions[uid][1]
        response=page.request.get(origin+'/operations/export.csv')
        assert response.status==200
        rows=list(csv.reader(io.StringIO(response.text())))
        heading=next(i for i,row in enumerate(rows) if row and row[0]=='branch_id')
        assert [int(row[0]) for row in rows[heading+1:]]==expected
        facts=page.request.get(origin+'/api/operations').json()
        for row,branch in zip(rows[heading+1:],facts['branches']):
            assert int(row[3])==branch['on_hand'] and row[9]==branch['recorded_gross'] and row[11]==branch['recorded_net']
    return sessions, cases, errors


def local_assistant(page, origin, output):
    page.goto(origin + '/admin-dashboard')
    page.locator('[data-assistant-open]').first.click()
    page.get_by_label('Ask about your authorized inventory').fill('Which branch has the most urgent inventory issues, and why?')
    page.get_by_role('button', name='Send', exact=True).click()
    page.get_by_text('Evidence ready. Review coverage and source references.', exact=True).wait_for()
    text = page.locator('#assistant-messages').inner_text()
    assert 'low stock' in text.lower() and ('expiry' in text.lower() or 'expiration' in text.lower())
    assert 'No live model generated' in text
    assert 'Source E' in text
    page.locator('.assistant-finding a').first.click()
    assert page.locator('.assistant-source[open]').count()>=1
    page.get_by_role('button',name='Close assistant',exact=True).click()
    page.locator('[data-assistant-open]').first.click()
    page.wait_for_function("document.querySelector('#assistant-messages').innerText.includes('Coverage:')")
    for width in (1440, 320):
        page.set_viewport_size(dict(width=width, height=1000))
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.screenshot(path=str(output / f'assistant-{width}.png'))
    page.get_by_role('button', name='Clear conversation', exact=True).click()
    page.get_by_text('Conversation cleared.',exact=True).wait_for()
    page.get_by_role('button', name='Close assistant', exact=True).click()
    page.set_viewport_size(dict(width=1440, height=1000))


def reconciliation(app, original_history):
    import inventory_ledger, sales_services, purchasing_services, transfer_execution
    with actor(app, 901):
        groups = dict(inventory=inventory_ledger.reconcile(), sales=sales_services.reconcile_sales(),
                      purchasing=purchasing_services.reconcile(), transfers=transfer_execution.reconcile())
        for key, rows in groups.items():
            assert rows and all(r['balanced'] for r in rows), key
        physical = db.session.scalar(select(func.sum(BranchStock.on_hand))) or 0
        deltas = db.session.scalar(select(func.sum(InventoryMovement.on_hand_delta)).join(InventoryOperation)
                                   .where(InventoryOperation.status=='committed')) or 0
        assert physical == deltas
        transit = db.session.scalar(select(func.sum(TransferTransit.remaining))) or 0
        assert history_hash(app._t15_target) == original_history
        gross = db.session.scalar(select(func.sum(PricedSale.total_minor))) or 0
        refunds = db.session.scalar(select(func.sum(SaleRefund.total_minor))) or 0
        return dict(balanced=True, row_counts={k:len(v) for k,v in groups.items()}, physical=physical,
                    committed_physical_deltas=deltas, in_transit=transit, gross_minor=gross,
                    refunds_minor=refunds, net_minor=gross-refunds, historical_data_unchanged=True)


def business_walkthrough(app, origin, sessions, output):
    from scripts.transformation_workflows import run
    return run(app, origin, sessions, output, open_form=open_form, fill=fill, submit=submit,
               select_branch=select_branch)


def empty_walkthrough(browser, parent, output):
    from simulation.runtime import initialize, create_app
    from simulation.runner import bootstrap
    from simulation.manifest import MANIFEST
    from ingestion.target import ACK
    root = initialize(parent, acknowledgment=ACK)
    app = create_app(root); bootstrap(app, MANIFEST)
    _, private = attest(root)
    server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=Quiet)
    worker = Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        origin = 'http://127.0.0.1:' + str(server.server_port)
        context, page = login(browser, origin, private, 901, [])
        cases = []
        for path, name in [('/admin-dashboard','empty-overview'),('/catalog','empty-catalog'),('/sku-inventory','empty-lots'),('/ai-predictions','empty-intelligence')]:
            cases += check_page(page, origin, path, name, output)
        context.close()
        return cases
    finally:
        server.shutdown(); worker.join(5); server.server_close()
        with app.app_context(): db.session.remove(); db.engine.dispose()


def advisory_walkthrough(browser,parent,output):
    from services.recommendation_demo import prepare
    from unittest.mock import patch
    root,app=prepare(parent)
    _,private=attest(root)
    def fingerprint():
        from simulation.runner import normalized
        return hashlib.sha256(json.dumps(normalized({t.name:[dict(r) for r in db.session.execute(select(t)).mappings()]
            for t in db.metadata.sorted_tables if t.name not in ('users','login_throttle')}),sort_keys=True).encode()).hexdigest()
    with app.app_context(): before=fingerprint()
    server=make_server('127.0.0.1',0,app,threaded=True,request_handler=Quiet)
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    cases=[];errors=[]
    try:
        origin='http://127.0.0.1:'+str(server.server_port)
        with patch('sklearn.linear_model.LinearRegression.fit',side_effect=AssertionError('No fitting during HTTP')),patch(
                'sklearn.ensemble.HistGradientBoostingRegressor.fit',side_effect=AssertionError('No fitting during HTTP')):
            for uid in (901,912):
                context,page=login(browser,origin,private,uid,errors)
                cases+=check_page(page,origin,'/ai-predictions',f'published-intelligence-{uid}',output)
                advice=page.request.get(origin+'/api/recommendations').json()
                assert advice['actions_enabled'] is False and advice['production_numeric_enabled'] is False
                panel=page.locator('[data-decision-set]')
                assert panel.get_attribute('data-decision-set')==advice['decision_set_id']
                for item in advice['items']:
                    article=page.locator('[data-decision-id="'+item['decision_id']+'"]')
                    actual=[' '.join(v.split()) for v in article.locator('.recommendation-outcomes dd strong').all_text_contents()]
                    expected=[(str(r['observed_expired_units'])+' units' if r['observed_expired_units'] is not None else 'Unknown') if r['type']=='expiry_risk' else str(r['suggested_quantity'])+' units' if r['suggested_quantity'] is not None else 'Inputs needed' for r in item['recommendations']]
                    assert actual==expected and article.locator('form').count()==0
                if uid==912: assert {i['branch_id'] for i in advice['items']}=={2}
                context.close()
        with app.app_context(): assert fingerprint()==before
        assert not errors
        return dict(cases=cases,business_history_unchanged=True,no_HTTP_model_fitting=True,
                    provenance='SYN-T21-V1 synthetic provisional advisory only')
    finally:
        server.shutdown();worker.join(5);server.server_close()
        with app.app_context():db.session.remove();db.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only', action='store_true')
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--workflows-only', action='store_true')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    output = ROOT / 'data/private/transformation' / ('before' if args.baseline else 'browser')
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with TemporaryDirectory(prefix='inventraiq-transformation-') as parent:
        root, app = fixture(parent, 'small')
        app.config['ASSISTANT_PROPOSALS_ENABLED'] = True
        if args.baseline:
            from jinja2 import FileSystemLoader
            checkpoint = ROOT/'data/private/transformation/checkpoint/source'
            app.jinja_loader = FileSystemLoader(str(checkpoint/'templates'))
            app.static_folder = str(checkpoint/'static')
        _, private = attest(root)
        before = verify(app); assert before['ok']
        history = history_hash(root)
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=Quiet)
        worker = Thread(target=server.serve_forever, daemon=True); worker.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with sync_playwright() as pw:
                executable = next((p for p in ('C:/Program Files/Google/Chrome/Application/chrome.exe',
                                  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe') if Path(p).is_file()), None)
                if executable is None: raise RuntimeError('No existing browser available; no installation attempted')
                browser = pw.chromium.launch(executable_path=executable, headless=True)
                try:
                    if args.baseline:
                        context, page = login(browser, origin, private, 901, [])
                        cases = []
                        for path, name in [('/admin-dashboard','hq-overview'),('/catalog','catalog'),('/analytics','analytics')]:
                            cases += check_page(page, origin, path, name, output)
                        context.close(); sessions = {}; errors = []
                    elif args.workflows_only:
                        errors=[];cases=[]
                        sessions={uid:login(browser,origin,private,uid,errors) for uid in (901,902,903,912,922)}
                    else:
                        sessions, cases, errors = read_walkthrough(browser, app, origin, private, output)
                        local_assistant(sessions[901][1], origin, output)
                    assert verify(app) == before, 'Read-only walkthrough altered business facts'
                    workflows = None
                    if not args.read_only and not args.baseline:
                        workflows = business_walkthrough(app, origin, sessions, output)
                    reconciled = reconciliation(app, history)
                    for context, page in sessions.values(): context.close()
                    empty = [] if args.baseline or args.workflows_only else empty_walkthrough(browser, parent, output)
                    advisory=None if args.baseline or args.workflows_only else advisory_walkthrough(browser,parent,output)
                    result = dict(passed=True, browser=browser.version, independent_engine=False,
                                  actual_screen_reader=False, external_requests=0, live_ai_requests=0,
                                  cases=cases, empty_cases=empty, advisory=advisory, javascript_errors=errors,
                                  workflows=workflows, reconciliation=reconciled,
                                  elapsed_seconds=round(time.monotonic()-started,3))
                    (output/('verification-workflows.json' if args.workflows_only else 'verification.json')).write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
                    print(json.dumps(dict(passed=True,cases=len(cases),empty_cases=len(empty),workflows=workflows,
                                         elapsed_seconds=result['elapsed_seconds'],evidence=str(output))))
                finally: browser.close()
        finally:
            server.shutdown(); worker.join(5); server.server_close()
            with app.app_context(): db.session.remove(); db.engine.dispose()


if __name__ == '__main__':
    main()
