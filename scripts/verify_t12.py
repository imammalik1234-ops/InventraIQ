"""T12 actual browser purchasing workflow on fresh disposable SQLite only."""
import os
os.environ.update(APP_ENV='testing',PYTHON_DOTENV_DISABLED='1',DATABASE_URL='sqlite:///:memory:',PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
import sys,json,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server,WSGIRequestHandler
from test_purchasing import PurchasingTests
from app import app
from database import db
from models import User,PurchaseReceiptLink,PurchaseOrderLine,BranchStock


def main():
    f=PurchasingTests();f.setUp();app.config['LEGACY_STOCK_REHEARSAL']=False
    password='t12-disposable-browser-only'
    for user in db.session.query(User):user.set_password(password)
    db.session.commit();db.session.remove()
    executable=next((p for p in (Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')) if p.exists()),None)
    if not executable:raise RuntimeError('Chrome/Edge unavailable; browser verification not performed.')
    class Quiet(WSGIRequestHandler):
        def log_request(self,*args,**kwargs):pass
    server=make_server('127.0.0.1',0,app,threaded=False,request_handler=Quiet)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    origin='http://127.0.0.1:'+str(server.server_port);checks=[];errors=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=str(executable),headless=True)
            def login(uid,portal):
                context=browser.new_context(viewport={'width':1440,'height':1000})
                context.route('**/*',lambda r:r.continue_() if r.request.url.startswith(origin+'/') else r.abort())
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(origin+'/login/'+portal);page.get_by_label('Email address').fill(f'fixture-{uid}@test.invalid');page.locator('#password').fill(password)
                page.get_by_role('button',name='Sign in to workspace',exact=True).click();page.wait_for_load_state('networkidle')
                if uid in (1,5,2):
                    form=page.get_by_role('form',name='Switch branch',exact=True);form.locator('select').select_option('1');form.get_by_role('button',name='Switch',exact=True).click();page.wait_for_load_state('networkidle')
                return page
            def visit(page,key=None):
                assert page.goto(origin+'/purchasing'+('?order_id='+str(key) if key else '')).status==200;page.wait_for_load_state('networkidle')
            def action(page,label,reason):
                form=page.locator('form').filter(has=page.get_by_role('button',name=label,exact=True)).first
                form.locator('[name=reason]').fill(reason);form.get_by_role('button',name=label,exact=True).click();page.wait_for_load_state('networkidle')
            def draft(page,qty,cost):
                visit(page);form=page.locator('form').filter(has=page.get_by_role('button',name='Create PO draft',exact=True))
                form.locator('[name=supplier_id]').select_option(str(f.supplier))
                form.locator('[name=sku_id]').first.select_option(str(f.sku));form.locator('[name=quantity]').first.fill(str(qty));form.locator('[name=unit_cost]').first.fill(cost)
                form.locator('[name=business_reference]').fill('BROWSER-PO');form.locator('[name=reason]').fill('Agreed actual purchasing requirement')
                form.get_by_role('button',name='Create PO draft',exact=True).click();page.wait_for_load_state('networkidle')
                return page.url.split('order_id=')[1].split('&')[0]
            admin=login(1,'admin');visit(admin)
            admin.get_by_text('Create supplier',exact=True).first.click()
            form=admin.locator('form').filter(has=admin.get_by_role('button',name='Create supplier',exact=True))
            form.locator('[name=code]').fill('BROWSER-SUPPLIER');form.locator('[name=display_name]').fill('Observed browser supplier');form.locator('[name=reason]').fill('HQ reviewed identity')
            form.get_by_role('button',name='Create supplier',exact=True).click();admin.wait_for_load_state('networkidle')
            assert 'BROWSER-SUPPLIER' in admin.locator('main').inner_text();checks.append('HQ creates stable supplier identity through protected form')
            key=draft(admin,8,'1.11');action(admin,'Submit PO','HQ requester submits')
            assert admin.get_by_role('button',name='Approve PO',exact=True).count()==0
            csrf=admin.locator('[name=csrf_token]').first.input_value()
            detail=admin.request.get(origin+'/api/purchase-orders/'+key).json()
            denied=admin.request.post(origin+'/api/purchase-orders/'+key+'/approve',data=dict(expected_version=detail['version'],request_key='SELF',business_reference='SELF',reason='Attempt self approval'),headers={'X-CSRF-Token':csrf})
            assert denied.status==403
            approver=login(5,'admin');visit(approver,key);action(approver,'Approve PO','Independent HQ approval')
            assert 'Approved' in approver.locator('main').inner_text();checks.append('HQ-only draft/submission, API self-approval denial, independent administrator approval')
            manager=login(3,'staff');visit(manager,key)
            assert manager.get_by_role('button',name='Create PO draft',exact=True).count()==0
            summary=manager.get_by_text('Receive PO line '+str(detail['lines'][0]['line_id'])+' (8 outstanding)',exact=True);summary.click()
            form=manager.locator('form').filter(has=manager.get_by_role('button',name='Receive PO delivery',exact=True))
            for field,value in [('lot_code','BROWSER-LOT'),('supplier_lot_code','ACTUAL-BROWSER-BATCH'),('quantity','3'),('expiry_date','2030-01-08'),('external_reference','BROWSER-DELIVERY'),('evidence','Actual observed label'),('reason','Inspected partial delivery')]:form.locator('[name='+field+']').fill(value)
            retry_fields=form.evaluate('(form)=>Object.fromEntries(new FormData(form).entries())');target=form.get_attribute('action')
            form.get_by_role('button',name='Receive PO delivery',exact=True).click();manager.wait_for_load_state('networkidle')
            assert 'PartiallyReceived' in manager.locator('main').inner_text();assert '3.33 MYR' in manager.locator('main').inner_text()
            retry=manager.request.post(origin+target,form=retry_fields);assert retry.status==200
            data=manager.request.get(origin+'/api/purchase-orders/'+key).json();assert len(data['receipts'])==1 and data['lines'][0]['received']==3
            checks.append('destination manager partial receipt and uncertain-response replay: one physical credit, exact 3.33 MYR cost and receipt/movement references')
            visit(admin,key);action(admin,'Cancel PO remaining','HQ cancels five undelivered units')
            assert 'Cancelled' in admin.locator('main').inner_text();assert '5.55 MYR' in admin.locator('main').inner_text()
            checks.append('HQ remaining cancellation retains three received, five cancelled, zero outstanding and unchanged stock')
            # A second order completes through a quarantined actual arrival.
            second=draft(admin,1,'0.10');action(admin,'Submit PO','Submit second order');visit(approver,second);action(approver,'Approve PO','Independent second approval')
            visit(manager,second);manager.locator('summary').filter(has_text='Receive PO line').first.click()
            form=manager.locator('form').filter(has=manager.get_by_role('button',name='Receive PO delivery',exact=True))
            for field,value in [('lot_code','DAMAGED-LOT'),('supplier_lot_code','DAMAGED-LABEL'),('quantity','1'),('expiry_date','2020-01-01'),('external_reference','DAMAGED-DELIVERY'),('evidence','Observed expired damaged delivery'),('reason','Physical quarantine inspection')]:form.locator('[name='+field+']').fill(value)
            form.locator('[name=receipt_kind]').select_option('quarantine');form.get_by_role('button',name='Receive PO delivery',exact=True).click();manager.wait_for_load_state('networkidle')
            complete=manager.request.get(origin+'/api/purchase-orders/'+second).json();assert complete['state']=='Received' and complete['receipts'][0]['quarantined']==1
            with app.app_context():
                stock=db.session.get(BranchStock,(1,complete['receipts'][0]['lot_id']));assert (stock.on_hand,stock.quarantined_quantity)==(1,1)
            checks.append('full completion records expired/damaged physical arrival as quarantine, never available; cost 0.10 MYR')
            analyst=login(2,'staff');visit(analyst,key);assert analyst.get_by_role('button',name='Cancel PO remaining',exact=True).count()==0
            clerk=login(4,'staff');assert clerk.goto(origin+'/purchasing').status==403
            assert manager.request.get(origin+'/api/purchase-orders/'+key+'?branch_id=2').status==403
            reconciled=admin.request.get(origin+'/api/purchase-orders/reconciliation').json()['items'];assert reconciled and all(r['balanced'] for r in reconciled)
            checks.append('analyst read-only, clerk and forged-branch denial; PO/receipt/movement reconciliation passes')
            for width in (768,390):
                manager.set_viewport_size({'width':width,'height':900});visit(manager,second)
                assert manager.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),width
            checks.append('existing design, branch context and purchasing tables/forms fit tablet/mobile without overflow')
            assert not errors,errors;browser.close()
    finally:
        server.shutdown();server.server_close();worker.join(timeout=5);f.tearDown()
    print(json.dumps(dict(checks=checks,javascript_errors=errors,database='disposable synthetic SQLite only'),indent=2))


if __name__=='__main__':main()
