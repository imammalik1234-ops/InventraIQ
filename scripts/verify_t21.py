"""Real Chromium advisory checks on a NEW disposable synthetic T21 scenario only."""
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server,WSGIRequestHandler
    from sqlalchemy import select,update
    from database import db
    from models import UserBranch
    from simulation.runtime import history_hash
    from simulation.manifest import canonical
    from simulation.runner import normalized
    from services.recommendation_demo import prepare,actor
    import inventory_ledger as ledger

    with TemporaryDirectory(prefix='inventraiq-t21-browser-') as temp:
        root,app=prepare(temp);checks=[];errors=[]
        private=json.loads((root/'simulation-private.json').read_text())
        def evidence():
            with actor(app):
                names=['branch_stock','stock_lots','stock_receipts','inventory_operations','inventory_movements',
                    'inventory_documents','priced_sales','sale_lines','sale_refunds','refund_lines','suppliers',
                    'purchase_orders','purchase_order_lines','stock_transfers','stock_transfer_lines',
                    'stock_transfer_allocations','transfer_transit','catalog_audit','purchasing_events','stock_transfer_events']
                tables={name:[dict(row) for row in db.session.execute(select(db.metadata.tables[name])).mappings()] for name in names}
                for rows in tables.values():rows.sort(key=lambda row:canonical(normalized(row)))
                return dict(business_sha256=hashlib.sha256(canonical(normalized(tables)).encode()).hexdigest(),
                    history_sha256=history_hash(root),ledger_balanced=all(r['balanced'] for r in ledger.reconcile()))
        before=evidence()
        executable=next((p for p in [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
            Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')] if p.exists()),None)
        if executable is None:raise RuntimeError('No Chromium browser available')
        class Quiet(WSGIRequestHandler):
            def log_request(self,*args,**kwargs):pass
        server=make_server('127.0.0.1',0,app,threaded=False,request_handler=Quiet)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=str(executable),headless=True)
                def login(uid,portal):
                    context=browser.new_context(viewport={'width':1440,'height':1000})
                    context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
                    page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(origin+'/login/'+portal)
                    page.get_by_label('Email address').fill(f'synthetic-{uid}@demo.invalid')
                    page.locator('#password').fill(private['credentials'][str(uid)])
                    page.get_by_role('button',name='Sign in to workspace',exact=True).click()
                    page.wait_for_load_state('networkidle')
                    cookie=next(c for c in context.cookies() if c['name']==app.config['SESSION_COOKIE_NAME'])
                    assert cookie['httpOnly'] and cookie['sameSite']=='Lax'
                    return page
                admin=login(901,'admin');analyst=login(903,'admin')
                for page in (admin,analyst):
                    data=page.request.get(origin+'/api/recommendations').json()
                    assert data['scope']==dict(branches=[1,2,3],consolidated=True)
                    assert data['actions_enabled'] is False and data['production_numeric_enabled'] is False
                    receiver=next(r for r in data['items'] if r['branch_id']==2)
                    assert receiver['calculation']['raw_need']==11 and receiver['calculation']['purchase_quantity']==12
                    assert receiver['calculation']['demand']=='21.0'
                    assert data['transfer_recommendations']['items'][0]['suggested_quantity']==11
                    for path in ('/ai-predictions','/admin-dashboard','/reports','/analytics'):
                        assert page.goto(origin+path).status==200
                        card=page.locator('[data-decision-set]')
                        assert card.get_attribute('data-decision-set')==data['decision_set_id']
                        assert card.locator('article').count()==3
                        assert 'Synthetic, provisional and advisory' in card.inner_text()
                    details=page.locator('.recommendation-details').nth(1)
                    details.locator('summary').click()
                    assert '2001-03-31' in details.inner_text() and 'Daily forecast units' in details.inner_text()
                    assert 'raw=ceil' in details.inner_text() and '2001-04-07' in details.inner_text()
                checks.append('HQ admin/analyst: API and four views share decision identities, inputs, provisional labels and 12-unit purchase / 11-unit transfer previews')
                admin.set_viewport_size({'width':390,'height':844})
                assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
                summary=admin.locator('.recommendation-details summary').first
                summary.focus();admin.keyboard.press('Enter')
                assert summary.evaluate('(el) => el.parentElement.open')
                assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
                admin.set_viewport_size({'width':1440,'height':1000})
                checks.append('390px layout with expanded formula/details; keyboard-accessible native disclosure')
                form=admin.get_by_role('form',name='Switch branch',exact=True)
                form.locator('select').select_option('1');form.get_by_role('button',name='Switch',exact=True).click()
                admin.wait_for_load_state('networkidle')
                scoped=admin.request.get(origin+'/api/recommendations').json()
                assert scoped['scope']==dict(branches=[1],consolidated=False)
                assert scoped['transfer_recommendations']['items']==[]
                assert all(r['calculation'] is None for r in scoped['items'])
                checks.append('Branch selection restricts HQ context; incomplete inbound visibility withholds numbers')
                manager=login(912,'staff');clerk=login(922,'staff')
                for page in (manager,clerk):
                    data=page.request.get(origin+'/api/recommendations').json()
                    assert {r['branch_id'] for r in data['items']}=={2}
                    assert all(r['calculation'] is None for r in data['items'])
                    assert data['transfer_recommendations']['items']==[]
                    assert page.goto(origin+'/ai-predictions').status==200
                    assert page.locator('[data-decision-id]').count()==1
                    assert 'Inputs needed' in page.locator('[data-decision-set]').inner_text()
                    assert page.request.get(origin+'/api/recommendations?branch_id=1').status==403
                    assert page.request.get(origin+'/api/recommendations?actor_id=901').status in (400,403)
                assert clerk.request.get(origin+'/api/recommendations').json()['items'][0]['inputs']['purchase_orders'] is None
                assert clerk.request.get(origin+'/purchasing').status==403
                assert clerk.request.get(origin+'/transfers').status==403
                checks.append('Manager/clerk: own-branch facts only, forged scope denied, restricted inbound unknown and no cross-branch candidates')
                for page in (analyst,manager,clerk):
                    assert page.request.post(origin+'/api/recommendations',data={}).status==405
                checks.append('Read-only recommendation interface has no create/approve/reserve action')
                with actor(app):
                    db.session.execute(update(UserBranch).where(UserBranch.user_id==912).values(active=False));db.session.commit()
                assert manager.request.get(origin+'/api/recommendations').status==403
                assert manager.goto(origin+'/ai-predictions').status==403
                checks.append('Committed assignment revocation takes effect on next API and page request')
                assert not errors,errors
                browser.close()
            after=evidence();assert before==after and after['ledger_balanced']
            checks.append('Browser reads preserve operational fingerprints, historical observations and balanced ledger')
        finally:
            server.shutdown();worker.join(timeout=5);server.server_close()
            with app.app_context():db.session.remove();db.engine.dispose()
        print(json.dumps(dict(checks=checks,javascript_errors=errors,evidence=after,
            synthetic_only=True,production_numeric_enabled=False,mysql='Not verified'),indent=2))


if __name__=='__main__':
    with patch('sklearn.linear_model.LinearRegression.fit',side_effect=AssertionError('HTTP fit')),\
        patch('sklearn.ensemble.HistGradientBoostingRegressor.fit',side_effect=AssertionError('HTTP fit')):
        main()
