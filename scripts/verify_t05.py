"""T05 headless browser checks against the isolated authorization matrix fixture."""
import os
os.environ.update(APP_ENV='testing', PYTHON_DOTENV_DISABLED='1', DATABASE_URL='sqlite:///:memory:',
                  PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import json
import threading
from werkzeug.serving import make_server, WSGIRequestHandler
from playwright.sync_api import sync_playwright
from test_branch_authorization import BranchAuthorizationTests
from app import app
from database import db
from models import User


def main():
    fixture=BranchAuthorizationTests(); fixture.setUp()
    password='t05-browser-disposable-only'
    for user in User.query.all(): user.set_password(password)
    db.session.commit(); db.session.remove()
    executable=next((p for p in [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
        Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')] if p.exists()),None)
    if executable is None: raise RuntimeError('Chrome/Edge unavailable; browser checks not run.')
    class QuietHandler(WSGIRequestHandler):
        def log_request(self,*args,**kwargs): pass
    server=make_server('127.0.0.1',0,app,threaded=True,request_handler=QuietHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    checks=[]; errors=[]
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=str(executable),headless=True)
            def login(uid,role):
                context=browser.new_context(viewport={'width':1440,'height':1000})
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(origin+'/login/'+role)
                page.get_by_label('Email address').fill(f'fixture-{uid}@test.invalid')
                page.locator('#password').fill(password)
                page.get_by_role('button',name='Sign in to workspace',exact=True).click()
                page.wait_for_url('**/'+('admin-dashboard' if role=='admin' else 'dashboard'))
                page.wait_for_load_state('networkidle')
                return page
            def csrf(page):return page.locator('meta[name="csrf-token"]').get_attribute('content')
            def choose(page,value):
                assert page.goto(origin+'/scope').status==200
                page.locator('#branch-scope').select_option(value)
                page.get_by_role('button',name='Select scope',exact=True).click()
                page.wait_for_url('**/dashboard')
                page.wait_for_load_state('networkidle')
            admin=login(1,'admin')
            assert 'BRANCH-2-PRIVATE' in admin.locator('body').inner_text()
            checks.append('HQ consolidated dashboard includes authorized branches')
            choose(admin,'1')
            assert 'BRANCH-1-PRIVATE' in admin.locator('body').inner_text()
            assert 'BRANCH-2-PRIVATE' not in admin.locator('body').inner_text()
            checks.append('scope form selects branch and filters dashboard')
            assert admin.goto(origin+'/branch-administration').status==200
            assert admin.get_by_role('heading',name='Branches and assignments',exact=True).is_visible()
            checks.append('HQ administration forms render with CSRF')
            for width in (768,390):
                admin.set_viewport_size({'width':width,'height':900})
                for path in ('/scope','/branch-administration','/admin-dashboard'):
                    assert admin.goto(origin+path).status==200
                    assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth+1'),(width,path)
            checks.append('scope/admin/dashboard layouts at 768 and 390 pixels')
            analyst=login(2,'staff'); choose(analyst,'1')
            assert analyst.goto(origin+'/reports').status==200
            assert analyst.request.get(origin+'/branch-administration').status==403
            response=analyst.request.post(origin+'/sales',data={'product_id':11,'quantity':1},headers={'X-CSRF-Token':csrf(analyst)})
            assert response.status==403
            checks.append('HQ analyst reports allowed; business and administration writes denied')
            manager=login(3,'staff')
            assert 'BRANCH-2-PRIVATE' not in manager.locator('body').inner_text()
            assert manager.request.get(origin+'/inventory?branch_id=2').status==403
            export=manager.request.get(origin+'/admin-settings/export-data').text()
            assert 'BRANCH-1-PRIVATE' in export and 'BRANCH-2-PRIVATE' not in export
            assert manager.request.get(origin+'/api/stock-lots/22').status==404
            checks.append('manager cross-branch URL, lot guess, dashboard and export isolation')
            clerk=login(4,'staff')
            assert clerk.request.put(origin+'/products/11',data={'quantity':99},headers={'X-CSRF-Token':csrf(clerk)}).status==403
            assert clerk.request.post(origin+'/sales',data={'product_id':11,'quantity':1},headers={'X-CSRF-Token':csrf(clerk)}).status==200
            checks.append('clerk sales allowed; catalog mutation denied')
            assert admin.goto(origin+'/branch-administration').status==200
            assignment_form=admin.locator('form').filter(has=admin.locator('input[name="action"][value="assignment"]'))
            assignment_form.locator('[name="user_id"]').select_option('3')
            assignment_form.locator('[name="branch_id"]').select_option('1')
            assignment_form.locator('[name="branch_role"]').select_option('manager')
            assignment_form.locator('[name="active"]').select_option('false')
            assignment_form.get_by_role('button',name='Save assignment',exact=True).click()
            admin.wait_for_load_state('networkidle')
            assert manager.request.get(origin+'/inventory',max_redirects=0).status==302
            checks.append('HQ revocation form invalidates existing manager session')
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5);fixture.tearDown()
    print(json.dumps({'checks':checks,'javascript_errors':errors,'database':'disposable in-memory SQLite only'},indent=2))


if __name__=='__main__':main()
