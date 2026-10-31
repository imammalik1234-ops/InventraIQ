"""T15 real Chromium workflow checks on a new temporary synthetic demo only."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main(extra_checks=None,create_fixture=None):
    from simulation.runtime import initialize,create_app
    from simulation.runner import run
    from simulation.verify import verify
    from ingestion.target import ACK
    from database import db
    from flask import url_for
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server,WSGIRequestHandler
    with TemporaryDirectory(prefix='inventraiq-t15-browser-') as temp:
        if create_fixture:root,app=create_fixture(temp)
        else:root=initialize(temp,acknowledgment=ACK);app=create_app(root)
        run(app)
        evidence=verify(app);checks=[];errors=[]
        private=json.loads((root/'simulation-private.json').read_text())
        executable=next((p for p in [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')] if p.exists()),None)
        if executable is None:raise RuntimeError('No Chromium browser available')
        class Quiet(WSGIRequestHandler):
            def log_request(self,*args,**kwargs):pass
        server=make_server('127.0.0.1',0,app,threaded=False,request_handler=Quiet)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        try:
            with app.test_request_context():
                routes={key:url_for(key) for key in ['admin_dashboard','reports','sales','admin_inventory']}
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=str(executable),headless=True)
                def login(uid,portal):
                    context=browser.new_context(viewport={'width':1440,'height':1000})
                    context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
                    page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(origin+'/login/'+portal);page.get_by_label('Email address').fill(f'synthetic-{uid}@demo.invalid')
                    page.locator('#password').fill(private['credentials'][str(uid)])
                    page.get_by_role('button',name='Sign in to workspace',exact=True).click();page.wait_for_load_state('networkidle')
                    cookie=next(c for c in context.cookies() if c['name']==app.config['SESSION_COOKIE_NAME'])
                    assert cookie['httpOnly'] and cookie['sameSite']=='Lax' and cookie['expires']>0
                    return page
                admin=login(901,'admin')
                assert 'not real-world revenue' in admin.locator('#synthetic-demo-notice').inner_text()
                data=admin.request.get(origin+'/api/operations').json()
                assert data['total']['on_hand']==304 and data['total']['recorded_net']=='315.40'
                assert data['demo_provenance']['source']=='synthetic_demo'
                checks.append('Real login, HQ consolidation, fixed date and synthetic financial labels')
                for endpoint in routes.values():
                    assert admin.goto(origin+endpoint).status==200;admin.wait_for_load_state('networkidle')
                for path in ['/branch-sales?sale_id=1&branch_id=1','/purchasing?order_id=2&branch_id=2','/transfers?transfer_id=1&branch_id=2','/transfers?transfer_id=2&branch_id=3']:
                    assert admin.goto(origin+path).status==200;admin.wait_for_load_state('networkidle')
                    assert admin.locator('#synthetic-demo-notice').count()==1
                checks.append('Existing dashboard, inventory, reports, sales/refund, partial PO and transfer detail navigation')
                form=admin.get_by_role('form',name='Switch branch',exact=True)
                form.locator('select').select_option('1');form.get_by_role('button',name='Switch',exact=True).click();admin.wait_for_load_state('networkidle')
                assert admin.request.get(origin+'/api/operations').json()['total']['on_hand']==215
                checks.append('HQ validated branch switch shows excess branch stock')
                analyst=login(903,'admin');assert analyst.request.get(origin+'/api/operations').json()['total']['on_hand']==304
                csrf=analyst.locator('meta[name="csrf-token"]').get_attribute('content')
                assert analyst.request.post(origin+'/api/purchase-orders',data={},headers={'X-CSRF-Token':csrf}).status==403
                checks.append('HQ analyst consolidated read with service-route purchasing denial')
                manager=login(912,'staff');own=manager.request.get(origin+'/api/operations').json()
                assert own['total']['on_hand']==17 and own['total']['low_stock_skus']==3
                assert manager.goto(origin+'/purchasing').status==200
                assert 'PartiallyReceived' in manager.locator('main').inner_text()
                assert manager.request.get(origin+'/api/operations?branch_id=1').status==403
                checks.append('Destination manager sees shortages and partial PO, forged branch rejected')
                clerk=login(922,'staff');assert clerk.request.get(origin+'/api/operations').json()['total']['on_hand']==17
                assert clerk.request.get(origin+'/purchasing').status==403 and clerk.request.get(origin+'/transfers').status==403
                checks.append('Clerk scoped operations with purchasing and transfer gates intact')
                export=manager.request.get(origin+'/operations/export.csv')
                assert export.status==200 and 'simulated, not real-world revenue' in export.text()
                assert 'DEMO-01' not in export.text() and 'DEMO-03' not in export.text()
                checks.append('Branch CSV isolation and explicit simulated monetary provenance')
                if extra_checks:extra_checks(origin,admin,manager,clerk,checks)
                assert admin.goto(origin+routes['admin_dashboard']).status==200
                admin.set_viewport_size({'width':390,'height':844});admin.wait_for_timeout(100)
                assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
                admin.locator('[data-nav-toggle]').click();admin.wait_for_timeout(50)
                assert admin.locator('[data-nav-toggle]').get_attribute('aria-expanded')=='true'
                admin.keyboard.press('Escape');admin.keyboard.press('Tab')
                assert admin.locator(':focus').count()==1
                checks.append('Mobile layout, menu expansion and keyboard focus')
                assert not errors,errors
                browser.close()
                assert verify(app)==evidence,'Browser navigation changed simulation business facts'
        finally:
            server.shutdown();worker.join(timeout=5);server.server_close()
            with app.app_context():db.session.remove();db.engine.dispose()
        print(json.dumps(dict(checks=checks,javascript_errors=errors,evidence={key:evidence[key] for key in
            ['ok','business_sha256','completed_steps','historical_observations_unchanged','operations','financial',
             'conservation','branches','transfer_states','real_m5_ingestion','mysql']}),indent=2))


if __name__=='__main__':main()
