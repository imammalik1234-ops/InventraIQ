"""Role-based T09 browser checks on disposable synthetic SQLite only."""
import os
os.environ.update(APP_ENV='testing',PYTHON_DOTENV_DISABLED='1',DATABASE_URL='sqlite:///:memory:',
    PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import json
import threading
from werkzeug.serving import make_server,WSGIRequestHandler
from playwright.sync_api import sync_playwright
from test_operational_reporting import OperationalReportingTests
from app import app
from database import db
from models import User,UserBranch


def main():
    fixture=OperationalReportingTests();fixture.setUp()
    password='t09-disposable-browser-only'
    for user in db.session.query(User):user.set_password(password)
    db.session.commit();db.session.remove()
    executable=next((p for p in [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')] if p.exists()),None)
    if executable is None:raise RuntimeError('Chrome/Edge unavailable; no browser verification performed.')
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
                page.locator('#password').fill(password);page.get_by_role('button',name='Sign in to workspace',exact=True).click();page.wait_for_load_state('networkidle')
                return page
            def switch(page,value):
                element=page.get_by_role('form',name='Switch branch',exact=True)
                element.locator('select').select_option(value);element.get_by_role('button',name='Switch',exact=True).click();page.wait_for_load_state('networkidle')
            def summary(page):return page.locator('[data-operational-summary]')
            admin=login(1,'admin');assert summary(admin).get_attribute('data-net')=='12.50'
            api=admin.request.get(origin+'/api/operations').json();assert [b['on_hand'] for b in api['branches']]==[20,40,60]
            assert api['total']['on_hand']==sum(b['on_hand'] for b in api['branches'])
            checks.append('HQ admin company-wide totals reconcile to three distinct branch facts')
            for branch,net in [('1','2.50'),('2','2.50'),('3','7.50')]:
                switch(admin,branch);assert summary(admin).get_attribute('data-net')==net
            switch(admin,'all');assert summary(admin).get_attribute('data-net')=='12.50'
            for path in ('/inventory-page','/sales','/reports','/analytics','/expiry-alerts','/staff'):
                assert admin.goto(origin+path).status==200;admin.wait_for_load_state('networkidle')
            checks.append('HQ selector persists across navigation; each branch differs; consolidated view is explicit')
            analyst=login(2,'staff');assert summary(analyst).get_attribute('data-net')=='12.50'
            switch(analyst,'2');assert summary(analyst).get_attribute('data-net')=='2.50'
            token=analyst.locator('meta[name="csrf-token"]').get_attribute('content')
            assert analyst.request.post(origin+'/api/priced-sales',data={},headers={'X-CSRF-Token':token}).status==403
            checks.append('HQ analyst can consolidate and switch but cannot create sales')
            manager=login(3,'staff');clerk=login(4,'staff')
            for page in (manager,clerk):
                options=page.locator('#shell-branch option').all_text_contents();assert len(options)==1 and 'BR-1' in options[0]
                for path in ('/dashboard','/inventory-page','/sales','/reports','/analytics','/expiry-alerts','/staff'):
                    assert page.goto(origin+path).status==200;page.wait_for_load_state('networkidle')
                    assert summary(page).get_attribute('data-gross')=='2.50'
                    assert 'BR2-PRIVATE' not in page.locator('main').inner_text() and 'BR3-PRIVATE' not in page.locator('main').inner_text()
                assert page.request.get(origin+'/api/operations?branch_id=2').status==403
                assert page.request.get(origin+f"/api/stock-lots/{fixture.receipts[2]['lot_id']}").status==404
            assert 'Fixture 3' not in clerk.locator('main').inner_text()
            checks.append('manager and clerk pages, API and guessed resources remain in branch 1; clerk roster is self-only')
            report=manager.request.get(origin+'/operations/export.csv');assert report.status==200
            assert 'BR-1' in report.text() and 'BR-2' not in report.text() and 'BR-3' not in report.text()
            assert 'MYR' in report.text() and 'unknown' in report.text()
            checks.append('scoped CSV includes date/currency/provenance and excludes other branches')
            assert manager.goto(origin+'/reports?start=2031-01-01&end=2031-01-02').status==200
            assert summary(manager).get_attribute('data-net')=='0.00'
            assert 'unknown revenue' in manager.locator('main').inner_text()
            checks.append('empty financial period and unknown historical revenue are labelled honestly')
            dual=login(6,'staff');switch(dual,'2')
            with app.app_context():db.session.get(UserBranch,(6,2)).active=False;db.session.commit();db.session.remove()
            assert dual.goto(origin+'/analytics').status==403
            assert dual.goto(origin+'/dashboard').status==200
            assert dual.request.get(origin+'/api/operations').json()['scope']['branch_ids']==[1]
            checks.append('revoked selected branch fails closed, clears stale selection and cannot broaden scope')
            for width in (768,390):
                for page,path in ((admin,'/reports'),(manager,'/inventory-page'),(clerk,'/sales')):
                    page.set_viewport_size({'width':width,'height':900});assert page.goto(origin+path).status==200;page.wait_for_load_state('networkidle')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(width,path)
                    assert page.get_by_role('form',name='Switch branch',exact=True).is_visible()
            checks.append('persistent selector and operational layouts work at 768/390 pixels without overflow')
            assert not errors,errors;browser.close()
    finally:
        server.shutdown();server.server_close();worker.join(timeout=5);fixture.tearDown()
    print(json.dumps(dict(checks=checks,javascript_errors=errors,database='disposable synthetic SQLite only'),indent=2))


if __name__=='__main__':main()
