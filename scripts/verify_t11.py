"""T11 real browser lifecycle; fresh in-memory synthetic fixture only."""
import os
os.environ.update(APP_ENV='testing',PYTHON_DOTENV_DISABLED='1',DATABASE_URL='sqlite:///:memory:',PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
import sys,json,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server,WSGIRequestHandler
from test_transfer_execution import ExecutionTests
from app import app
from database import db
from models import User,BranchStock,TransferTransit


def main():
    f=ExecutionTests();f.setUp();app.config['LEGACY_STOCK_REHEARSAL']=False
    password='t11-disposable-browser-only'
    for u in db.session.query(User):u.set_password(password)
    db.session.commit();db.session.remove()
    executable=next((p for p in [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')] if p.exists()),None)
    if not executable:raise RuntimeError('No Chrome/Edge; browser verification unavailable.')
    class Quiet(WSGIRequestHandler):
        def log_request(self,*args,**kwargs):pass
    server=make_server('127.0.0.1',0,app,threaded=False,request_handler=Quiet)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    origin='http://127.0.0.1:'+str(server.server_port);checks=[];errors=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=str(executable),headless=True)
            def login(uid,portal,branch):
                context=browser.new_context(viewport={'width':1440,'height':1000})
                context.route('**/*',lambda r:r.continue_() if r.request.url.startswith(origin+'/') else r.abort())
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(origin+'/login/'+portal);page.get_by_label('Email address').fill(f'fixture-{uid}@test.invalid');page.locator('#password').fill(password)
                page.get_by_role('button',name='Sign in to workspace',exact=True).click();page.wait_for_load_state('networkidle')
                form=page.get_by_role('form',name='Switch branch',exact=True);form.locator('select').select_option(str(branch));form.get_by_role('button',name='Switch',exact=True).click()
                page.wait_for_load_state('networkidle');return page
            def visit(page,key=1):
                assert page.goto(origin+'/transfers?transfer_id='+str(key)).status==200;page.wait_for_load_state('networkidle')
            def action(page,label,reason,quantity=None,decision=None):
                form=page.locator('form').filter(has=page.get_by_role('button',name=label,exact=True)).first
                if quantity is not None:form.locator('[name=quantity]').fill(str(quantity))
                if decision:form.locator('[name=decision]').select_option(decision)
                form.locator('[name=reason]').fill(reason)
                payload=json.loads(form.locator('[name=payload]').input_value()) if form.locator('[name=payload]').count() else None
                if payload:
                    payload['reason']=reason
                    if quantity is not None:
                        if 'lines' in payload:payload['lines'][0]['quantity']=quantity
                        else:payload['quantity']=quantity
                    if label=='Receive shipment':payload['lines'][0]['disposition']=form.locator('[name=disposition]').input_value()
                form.get_by_role('button',name=label,exact=True).click();page.wait_for_load_state('networkidle');return payload
            source=login(3,'staff',1);visit(source);action(source,'Dispatch shipment','Source verified full approved lot')
            assert 'Dispatched' in source.locator('main').inner_text()
            assert source.get_by_role('button',name='Cancel transfer',exact=True).count()==0
            with app.app_context():assert (db.session.get(BranchStock,(1,f.lot)).on_hand,db.session.get(BranchStock,(1,f.lot)).reserved)==(5,0)
            checks.append('authorized source dispatch: physical 8 to 5, reserved 3 to 0, durable transit 3, no cancellation shortcut')
            dest=login(6,'staff',2);visit(dest);payload=action(dest,'Receive shipment','Inspected first package',1)
            assert 'PartiallyReceived' in dest.locator('main').inner_text()
            csrf=dest.locator('input[name=csrf_token]').first.input_value()
            retry=dest.request.post(origin+'/api/transfers/1/receive',data=payload,headers={'X-CSRF-Token':csrf})
            assert retry.status==200 and retry.json()['replayed']
            with app.app_context():assert db.session.get(BranchStock,(2,f.lot)).on_hand==1
            checks.append('destination partial receipt and lost-response retry credit once, preserving original lot')
            action(dest,'Discrepancy shipment','Verified missing package',1)
            assert 'Exception' in dest.locator('main').inner_text()
            admin=login(1,'admin',2);visit(admin);action(admin,'Resolve shipment','HQ documented carrier loss',decision='loss')
            visit(dest);form=dest.locator('form').filter(has=dest.get_by_role('button',name='Receive shipment',exact=True)).first
            form.locator('[name=disposition]').select_option('quarantine');action(dest,'Receive shipment','Damaged final package',1)
            visit(admin);action(admin,'Resolve shipment','HQ acknowledges held damaged goods')
            assert 'Received' in admin.locator('main').inner_text()
            with app.app_context():
                assert (db.session.get(BranchStock,(2,f.lot)).on_hand,db.session.get(BranchStock,(2,f.lot)).quarantined_quantity)==(2,1)
                transit=db.session.get(TransferTransit,f.t['reservations'][0]['allocation_id']);assert (transit.remaining,transit.lost)==(0,1)
            checks.append('shortage remains accountable until explicit HQ loss; damaged receipt remains quarantined after acknowledgment')
            visit(dest);form=dest.locator('form').filter(has=dest.get_by_role('button',name='Return shipment',exact=True)).first
            form.locator('[name=planned_arrival_utc]').fill('2030-01-03T00:00:00Z');action(dest,'Return shipment','Verified saleable return to source',1)
            assert 'Draft' in dest.locator('main').inner_text();child=dest.url.split('transfer_id=')[1].split('&')[0]
            action(dest,'Submit transfer','Source manager submits linked return')
            visit(admin,child);action(admin,'Approve transfer','Independent HQ approves reverse transfer');action(admin,'Dispatch shipment','Return custody verified')
            visit(source,child);action(source,'Receive shipment','Original branch inspected returned goods',1)
            assert 'Received' in source.locator('main').inner_text()
            checks.append('linked reverse draft, independent approval, original-lot dispatch and verified receipt; no negative shortcut')
            reconciliation=admin.request.get(origin+'/api/transfers/reconciliation').json()['items'];assert reconciliation and all(r['balanced'] for r in reconciliation)
            with app.app_context():
                assert sum(db.session.get(BranchStock,(b,f.lot)).on_hand for b in (1,2))==7
            checks.append('both forward and return transit chains reconcile; physical 7 plus authorized loss 1 equals opening 8')
            for width in (768,390):
                source.set_viewport_size({'width':width,'height':900});visit(source,child)
                assert source.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),width
            analyst=login(2,'staff',2);visit(analyst)
            assert analyst.get_by_role('button',name='Receive shipment',exact=True).count()==0
            checks.append('tablet/mobile shipment evidence has no overflow; HQ analyst remains read-only')
            assert not errors,errors;browser.close()
    finally:
        server.shutdown();server.server_close();worker.join(timeout=5);f.tearDown()
    print(json.dumps(dict(checks=checks,javascript_errors=errors,database='disposable synthetic SQLite'),indent=2))


if __name__=='__main__':main()
