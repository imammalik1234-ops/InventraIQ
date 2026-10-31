"""Focused T06 browser workflows using synthetic, disposable SQLite fixtures."""
import os
os.environ.update(APP_ENV='testing',PYTHON_DOTENV_DISABLED='1',DATABASE_URL='sqlite:///:memory:',
                  PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import json
import threading
from datetime import date,timedelta
from werkzeug.serving import make_server,WSGIRequestHandler
from playwright.sync_api import sync_playwright
from test_catalog_receiving import CatalogReceivingTests
from app import app
from database import db
from models import User,SKU,StockReceipt,BranchStock


def main(ledger_checks=None):
    fixture=CatalogReceivingTests();fixture.setUp()
    password='t06-disposable-browser-only'
    for user in User.query.all():user.set_password(password)
    db.session.commit();db.session.remove()
    executable=next((p for p in [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')] if p.exists()),None)
    if executable is None:raise RuntimeError('Chrome/Edge not installed; browser verification not performed.')
    class Quiet(WSGIRequestHandler):
        def log_request(self,*args,**kwargs):pass
    # The in-memory fixture has one StaticPool connection: serialize browser HTTP
    # requests. Real competing writers are tested separately on temporary files.
    server=make_server('127.0.0.1',0,app,threaded=False,request_handler=Quiet)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    checks=[];errors=[]
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=str(executable),headless=True)
            def login(uid,role):
                context=browser.new_context(viewport={'width':1440,'height':1000})
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(origin+'/login/'+role)
                page.get_by_label('Email address').fill(f'fixture-{uid}@test.invalid')
                page.locator('#password').fill(password);page.get_by_role('button',name='Sign in to workspace',exact=True).click()
                page.wait_for_url('**/'+('admin-dashboard' if role=='admin' else 'dashboard'));page.wait_for_load_state('networkidle')
                return page
            def csrf(page):return page.locator('meta[name="csrf-token"]').get_attribute('content')
            def form(page,label):return page.get_by_role('form',name=label,exact=True)
            def submit(page,element,button):
                element.get_by_role('button',name=button,exact=True).click();page.wait_for_load_state('networkidle')
                page.wait_for_function("!document.querySelector('button[data-pending]')")
            admin=login(1,'admin');assert admin.goto(origin+'/catalog').status==200
            create=form(admin,'Create SKU')
            for name,value in [('code','COLA-1000'),('name','Coca-Cola 1L'),('unit','unit'),('default_price','4.25'),('variant_attributes','{"volume":"1L","brand":"Coca-Cola"}'),('reason','Approved browser SKU')]:create.locator('[name="'+name+'"]').fill(value)
            create.locator('[name="category_id"]').select_option(str(fixture.cat))
            submit(admin,create,'Create SKU')
            admin.get_by_role('heading',name='COLA-1000 - Coca-Cola 1L',exact=True).wait_for()
            with app.app_context():sku_id=SKU.query.filter_by(code='COLA-1000').one().sku_id
            checks.append('HQ creates database-driven sellable variant through catalog form')
            edit=form(admin,'Edit SKU COLA-1000');edit.locator('[name="default_price"]').fill('5.00');edit.locator('[name="reason"]').fill('Approved current price')
            submit(admin,edit,'Save SKU COLA-1000');admin.wait_for_function("document.querySelector('form[aria-label=\"Edit SKU COLA-1000\"] input[name=\"expected_version\"]').value==='2'")
            checks.append('HQ edits SKU with revision and audit')
            offer=form(admin,'Branch assortment');offer.locator('[name="branch_id"]').select_option('1');offer.locator('[name="sku_id"]').select_option(str(sku_id))
            offer.locator('[name="expected_version"]').fill('0');offer.locator('[name="sale_price_override"]').fill('5.75');offer.locator('[name="reason"]').fill('Approved branch offer')
            submit(admin,offer,'Save assortment');admin.get_by_text(f'Branch 1, SKU {sku_id}: Approved, effective price 5.75, revision 1.',exact=True).wait_for()
            checks.append('HQ approves branch assortment and price override')
            policy=form(admin,'Create expiry policy');policy.locator('[name="kind"]').select_option('sku');policy.locator('[name="key"]').fill(str(sku_id));policy.locator('[name="require_expiry"]').select_option('true')
            for name,value in [('early_days','14'),('high_days','5'),('critical_days','2'),('min_remaining_days_on_receipt','0'),('min_remaining_days_on_transfer_receipt','1'),('expected_shelf_life_days','60'),('reason','Approved full override')]:policy.locator('[name="'+name+'"]').fill(value)
            submit(admin,policy,'Create expiry policy');form(admin,f'Policy sku {sku_id}').wait_for()
            checks.append('HQ saves complete SKU expiry override through form')
            manager=login(3,'staff');assert manager.goto(origin+'/receiving').status==200
            actual=(date.today()+timedelta(days=30)).isoformat()
            def new_lot(code,reference,actual_date,quantity):
                element=form(manager,'Receive new lot');element.locator('[name="sku_id"]').select_option(str(sku_id))
                for name,value in [('lot_code',code),('supplier_lot_code','ACTUAL-SUPPLIER-LABEL'),('expiry_date',actual_date),('quantity',str(quantity)),('external_reference',reference),('reason','Verified delivery'),('evidence','Observed synthetic actual label')]:element.locator('[name="'+name+'"]').fill(value)
                submit(manager,element,'Receive new lot');form(manager,'Receive lot '+code).wait_for()
            new_lot('BROWSER-1','BROWSER-DELIVERY-1',actual,9)
            retry=dict(mode='new_lot',sku_id=sku_id,lot_code='BROWSER-1',supplier_lot_code='ACTUAL-SUPPLIER-LABEL',expiry_date=actual,quantity=9,external_reference='BROWSER-DELIVERY-1',reason='Verified delivery',evidence='Observed synthetic actual label',receipt_kind='normal')
            response=manager.request.post(origin+'/api/receipts',data=retry,headers={'X-CSRF-Token':csrf(manager)})
            assert response.status==200 and response.json()['replayed']
            new_lot('BROWSER-2','BROWSER-DELIVERY-2',(date.today()+timedelta(days=45)).isoformat(),4)
            existing=form(manager,'Receive lot BROWSER-1')
            for name,value in [('quantity','3'),('external_reference','BROWSER-DELIVERY-3'),('reason','Same lot new delivery'),('evidence','Matching observed label')]:existing.locator('[name="'+name+'"]').fill(value)
            submit(manager,existing,'Receive lot BROWSER-1');manager.get_by_text('BROWSER-DELIVERY-3',exact=False).wait_for()
            rows=manager.request.get(origin+'/api/sku-stock').json();native=[r for r in rows if r['sku_id']==sku_id]
            assert sorted(r['on_hand'] for r in native)==[4,12]
            assert all(r['branch_id']==1 and r['effective_price']=='5.75' and r['policy']['source']=='sku' for r in native)
            checks.append('manager receives new lots and existing lot with actual dates; retry has one effect')
            assert manager.request.post(origin+'/api/receipts',data=dict(retry,external_reference='INJECT',branch_id=2),headers={'X-CSRF-Token':csrf(manager)}).status==403
            assert manager.request.put(origin+f'/api/assortments/1/{sku_id}',data={'sale_price_override':'0.01','expected_version':1,'reason':'attack'},headers={'X-CSRF-Token':csrf(manager)}).status==403
            assert manager.request.post(origin+'/api/sku-sales',data={'sku_id':sku_id,'quantity':1},headers={'X-CSRF-Token':csrf(manager)}).status==400
            checks.append('receiving branch/price injection denied; incomplete unkeyed sales denied')
            clerk=login(4,'staff');assert clerk.request.get(origin+'/receiving').status==403
            analyst=login(2,'staff');assert analyst.request.post(origin+'/api/skus',data={'name':'Injected'},headers={'X-CSRF-Token':csrf(analyst)}).status==403
            checks.append('clerk receiving and analyst catalog mutation denied')
            if ledger_checks:ledger_checks(manager,clerk,origin,sku_id,native,checks,csrf,form,submit)
            assert admin.goto(origin+'/catalog').status==200
            archive=form(admin,'Archive SKU COLA-1000');archive.locator('[name="reason"]').fill('Archive this variant')
            submit(admin,archive,'Archive SKU COLA-1000');admin.get_by_role('heading',name='COLA-1000 - Coca-Cola 1L (Archived)',exact=True).wait_for()
            assert manager.request.post(origin+'/api/receipts',data=dict(retry,lot_code='ARCHIVED',external_reference='ARCHIVED'),headers={'X-CSRF-Token':csrf(manager)}).status==409
            assert all(r['eligible_quantity']==0 for r in manager.request.get(origin+'/api/sku-stock').json() if r['sku_id']==sku_id)
            checks.append('archive retains receipts/holdings and blocks new receiving')
            for width in (768,390):
                admin.set_viewport_size({'width':width,'height':900});manager.set_viewport_size({'width':width,'height':900})
                for page,path in [(admin,'/catalog'),(manager,'/receiving'),(manager,'/sku-inventory')]:
                    assert page.goto(origin+path).status==200
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(width,path)
            checks.append('catalog, receiving and lot inventory remain responsive at 768/390 pixels')
            assert not errors,errors;browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5);fixture.tearDown()
    print(json.dumps({'checks':checks,'javascript_errors':errors,'database':'disposable SQLite only'},indent=2))


if __name__=='__main__':main()
