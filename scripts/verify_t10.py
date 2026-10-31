"""T10 role workflows on newly created disposable SQLite only."""
import os
os.environ.update(APP_ENV='testing',PYTHON_DOTENV_DISABLED='1',DATABASE_URL='sqlite:///:memory:',
    PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
import sys,json,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from werkzeug.serving import make_server,WSGIRequestHandler
from playwright.sync_api import sync_playwright
from test_transfers import TransferTests
from app import app
from database import db
from models import User,UserBranch,BranchStock


def main():
    fixture=TransferTests();fixture.setUp()
    app.config['LEGACY_STOCK_REHEARSAL']=False
    password='t10-disposable-browser-only'
    for user in db.session.query(User):user.set_password(password)
    db.session.commit();db.session.remove()
    executable=next((p for p in [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
        Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')] if p.exists()),None)
    if executable is None:raise RuntimeError('Chrome/Edge unavailable; browser verification not performed.')
    class Quiet(WSGIRequestHandler):
        def log_request(self,*args,**kwargs):pass
    server=make_server('127.0.0.1',0,app,threaded=False,request_handler=Quiet)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    origin='http://127.0.0.1:'+str(server.server_port);checks=[];errors=[]
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=str(executable),headless=True)
            def login(uid,portal):
                context=browser.new_context(viewport={'width':1440,'height':1000})
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(origin+'/login/'+portal);page.get_by_label('Email address').fill(f'fixture-{uid}@test.invalid')
                page.locator('#password').fill(password);page.get_by_role('button',name='Sign in to workspace',exact=True).click()
                page.wait_for_load_state('networkidle');return page
            def switch(page,branch):
                form=page.get_by_role('form',name='Switch branch',exact=True)
                form.locator('select').select_option(branch);form.get_by_role('button',name='Switch',exact=True).click();page.wait_for_load_state('networkidle')
            def action(page,label,reason):
                form=page.locator('form').filter(has=page.get_by_role('button',name=label,exact=True))
                form.locator('input[name=reason]').fill(reason);form.get_by_role('button',name=label,exact=True).click()
                page.wait_for_load_state('networkidle')
            manager=login(3,'staff');manager.get_by_role('link',name='Transfers',exact=True).click()
            form=manager.locator('form').filter(has=manager.get_by_role('button',name='Create draft',exact=True))
            form.locator('[name=destination_branch_id]').select_option('2')
            form.locator('[name=planned_arrival_utc]').fill('2030-01-02T00:00:00+00:00')
            form.locator('[name=sku_id]').first.select_option(str(fixture.sku));form.locator('[name=quantity]').first.fill('3')
            form.locator('[name=business_reference]').fill('BROWSER-T10');form.locator('[name=reason]').fill('Observed branch shortage')
            form.get_by_role('button',name='Create draft',exact=True).click();manager.wait_for_load_state('networkidle')
            assert 'Draft' in manager.locator('main').inner_text()
            key=manager.url.split('transfer_id=')[1].split('&')[0]
            action(manager,'Submit transfer','Source manager submitted')
            assert 'Submitted' in manager.locator('main').inner_text()
            assert manager.get_by_role('button',name='Approve transfer',exact=True).count()==0
            checks.append('source manager creates and submits a multi-line-capable draft; no approval control')
            admin=login(1,'admin');switch(admin,'1');admin.goto(origin+'/transfers?transfer_id='+key)
            action(admin,'Approve transfer','Independent HQ review')
            assert 'Approved' in admin.get_by_role('heading',name='Transfer #'+key+' ? Approved',exact=True).inner_text()
            assert '3 remaining' in admin.locator('main').inner_text()
            with app.app_context():assert db.session.get(BranchStock,(1,fixture.lot)).reserved==3
            checks.append('independent HQ approval shows exact state, original lot reservation and operation ID')
            analyst=login(2,'staff');analyst.goto(origin+'/transfers?transfer_id='+key)
            assert 'Approved' in analyst.locator('main').inner_text()
            assert analyst.get_by_role('button',name='Cancel transfer',exact=True).count()==0
            clerk=login(4,'staff');assert clerk.goto(origin+'/transfers').status==403
            assert clerk.request.get(origin+'/api/transfers/'+key).status==403
            checks.append('HQ analyst reads without actions; clerk transfer page and API denied')
            # One account has manager at source, clerk at branch 2: switching is no authority grant.
            dual=login(6,'staff');switch(dual,'2')
            assert dual.goto(origin+'/transfers?transfer_id='+key).status==403
            assert manager.request.get(origin+'/api/transfers/'+key+'?branch_id=2').status==403
            checks.append('wrong-branch selection and forged branch parameter fail closed')
            action(admin,'Cancel transfer','HQ cancels reviewed shipment before dispatch')
            assert 'Cancelled' in admin.locator('main').inner_text();assert '0 remaining' in admin.locator('main').inner_text()
            with app.app_context():assert db.session.get(BranchStock,(1,fixture.lot)).reserved==0
            checks.append('HQ approved cancellation releases once and shows linked immutable state history')
            for width in (768,390):
                admin.set_viewport_size({'width':width,'height':900});admin.reload();admin.wait_for_load_state('networkidle')
                assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),width
                assert admin.get_by_role('form',name='Switch branch',exact=True).is_visible()
            checks.append('transfer detail and persistent branch shell fit tablet/mobile without overflow')
            with app.app_context():db.session.get(UserBranch,(3,1)).active=False;db.session.commit();db.session.remove()
            assert manager.goto(origin+'/transfers').status==403
            checks.append('revoked source assignment blocks subsequent browser access')
            assert not errors,errors;browser.close()
    finally:
        server.shutdown();server.server_close();worker.join(timeout=5);fixture.tearDown()
    print(json.dumps(dict(checks=checks,javascript_errors=errors,database='disposable synthetic SQLite only'),indent=2))


if __name__=='__main__':main()
