"""Optional real Chrome verification. Creates only disposable synthetic SQLite data.
Requires local playwright and an installed Chrome/Edge. No .env/configured DB access.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['APP_ENV'] = 'testing'
os.environ['HTTPS_REQUIRED'] = 'false'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['FLASK_DEBUG'] = 'false'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import json
import secrets
import tempfile
import threading
from datetime import date, timedelta
from werkzeug.serving import make_server, WSGIRequestHandler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(Path(tempfile.gettempdir())/'inventraiq-ui-review'))
    args=parser.parse_args()
    output=Path(args.output).resolve(); output.mkdir(parents=True,exist_ok=True)
    browser_paths=[Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')]
    executable=next((p for p in browser_paths if p.exists()),None)
    if executable is None: raise RuntimeError('Installed Chrome/Edge not found; browser verification was not performed.')
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix='inventraiq-ui-db-') as directory:
        db_path=Path(directory).resolve()/'verification.db'
        os.environ['DATABASE_URL']='sqlite:///'+db_path.as_posix()
        os.environ['SECRET_KEY']=secrets.token_hex(32)
        from app import app
        from database import db
        from models import Product, Inventory, Sale, User, InventoryAudit
        password=secrets.token_urlsafe(20)
        app.config.update(TESTING=True)
        with app.app_context():
            assert Path(db.engine.url.database).resolve()==db_path
            db.create_all()
            for role in ('admin','staff'):
                user=User(name='Demo '+role.title(),email=role+'@demo.invalid',role=role)
                user.set_password(password);db.session.add(user)
            entries=[('Full Cream Milk','Dairy','Nestle',18,10,30),('White Bread','Bakery','Gardenia',42,8,30),('Coke 500ml','Beverages','Coca-Cola',108,12,90),('Plain Yogurt','Dairy','Dairy Best',12,6,5),('White Rice 5kg','Pantry','Golden Grain',78,9,90),('Fuji Apple','Produce','Fresh Farms Co.',20,None,30)]
            for i,(name,category,supplier,stock,qty,expiry) in enumerate(entries,1):
                db.session.add(Product(product_id=i,name=name,category=category,supplier=supplier,unit_price=3.50+i,
                                       reorder_level=15,expiry_date=date.today()+timedelta(days=expiry)))
                db.session.flush();db.session.add(Inventory(product_id=i,quantity=stock))
                if qty:
                    db.session.add_all([Sale(product_id=i,quantity=qty,sale_date=date.today()-timedelta(days=d)) for d in range(30)])
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tests'))
            from auth_fixtures import grant_fixture_scopes
            grant_fixture_scopes()
            db.session.commit()
        class QuietHandler(WSGIRequestHandler):
            def log_request(self,*a,**kw): pass
        server=make_server('127.0.0.1',0,app,threaded=True,request_handler=QuietHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        results=[];errors=[]
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=str(executable),headless=True)
                context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                context.route('**/*',lambda route: route.continue_() if route.request.url.startswith(origin) else route.abort())
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                def visit(path):
                    print('Checking '+path,flush=True)
                    response=page.goto(origin+path,wait_until='networkidle'); assert response.status==200,(path,response.status)
                    assert page.locator('h1').count()>=1,path
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), 'Page overflow: '+path
                def login(role):
                    visit('/login/'+role)
                    page.get_by_label('Email address').fill(role+'@demo.invalid')
                    page.locator('#password').fill(password)
                    page.get_by_role('button',name='Sign in to workspace').click()
                    page.wait_for_url('**/'+('admin-dashboard' if role=='admin' else 'dashboard'))
                    page.wait_for_load_state('networkidle')
                    visit('/scope')
                    page.locator('#branch-scope').select_option('1')
                    page.get_by_role('button',name='Select scope',exact=True).click()
                    page.wait_for_url('**/dashboard')
                visit('/roles');page.screenshot(path=str(output/'roles-desktop.png'),full_page=True)
                login('admin')
                for name,path in [('dashboard','/admin-dashboard'),('inventory','/admin-inventory'),('sales','/sales'),('intelligence','/ai-predictions'),('analytics','/analytics'),('reports','/reports'),('settings','/admin-settings'),('add-product','/admin-add-product')]:
                    visit(path);page.screenshot(path=str(output/(name+'-desktop.png')),full_page=True);results.append('desktop '+path)
                visit('/admin-inventory')
                page.get_by_label('Search inventory',exact=True).fill('rice')
                assert page.locator('#inventoryTable tbody tr[data-id]:visible').count()==1
                page.get_by_label('Search inventory',exact=True).fill('not a product')
                assert page.locator('#inventoryEmpty').is_visible()
                page.get_by_label('Search inventory',exact=True).fill('')
                page.get_by_label('Filter inventory status',exact=True).select_option('Review Required')
                assert page.locator('#inventoryTable tbody tr[data-id]:visible').count()==1
                results.append('inventory search, status filter, no results')
                visit('/admin-record-sale');page.locator('#productSelect').select_option('1');page.locator('#quantitySold').fill('2');page.get_by_role('button',name='Record sale',exact=True).click()
                page.get_by_role('status').filter(has_text='Sale recorded successfully').wait_for()
                with app.app_context():
                    assert Inventory.query.filter_by(product_id=1).one().quantity==16
                    assert InventoryAudit.query.filter_by(action='sale').count()==1
                results.append('browser sale records stock and audit')
                # Rejected stock deductions retain stock and render a useful error.
                page.locator('#quantitySold').fill('9999');page.get_by_role('button',name='Record sale',exact=True).click()
                page.locator('#message.danger').wait_for()
                with app.app_context(): assert Inventory.query.filter_by(product_id=1).one().quantity==16
                # A lost connection must not encourage an immediate duplicate submission.
                page.route('**/sales',lambda route:route.abort())
                page.locator('#quantitySold').fill('1');page.get_by_role('button',name='Record sale',exact=True).click()
                page.get_by_text('The outcome may be unknown.',exact=False).wait_for()
                assert page.locator('#recordSaleButton').is_disabled()
                page.unroute('**/sales')
                results.append('sale validation and unknown network outcome')
                visit('/admin-edit-product/1')
                page.get_by_label('On-hand quantity',exact=False).fill('17')
                page.get_by_role('button',name='Save changes').click();page.wait_for_url('**/admin-inventory*')
                with app.app_context(): assert Inventory.query.filter_by(product_id=1).one().quantity==17
                visit('/admin-edit-product/1')
                csrf=page.locator('#main-content input[name="csrf_token"]').input_value()
                response=page.request.post(origin+'/sales',data={'product_id':1,'quantity':1},headers={'X-CSRF-Token':csrf})
                assert response.status==200
                page.get_by_label('On-hand quantity',exact=False).fill('22');page.get_by_role('button',name='Save changes').click()
                page.locator('#global-message:not([hidden]),.validation-summary').first.wait_for()
                with app.app_context(): assert Inventory.query.filter_by(product_id=1).one().quantity==16
                results.append('edit saves stock; stale form cannot overwrite concurrent sale')
                visit('/admin-add-product')
                csrf=page.locator('#main-content input[name="csrf_token"]').input_value()
                response=page.request.post(origin+'/products',data=dict(name='Unavailable new SKU',category='Dairy',supplier='Nestle',quantity=3,unit_price=5.50,reorder_level=2,expiry_date=(date.today()+timedelta(days=30)).isoformat()),headers={'X-CSRF-Token':csrf})
                assert response.status==409
                with app.app_context(): assert Product.query.count()==6
                results.append('legacy batch-shaped creation remains disabled; shared SKU catalog is separate')
                # T03: lose the response AFTER the server commits a generic form.
                visit('/admin-edit-product/1')
                with app.app_context(): before_audits=InventoryAudit.query.count()
                commits=[]
                def lose_committed_response(route):
                    response=route.fetch()
                    commits.append(response.status)
                    route.abort()
                page.route('**/admin-edit-product/1',lose_committed_response)
                page.get_by_label('On-hand quantity',exact=False).fill('15')
                page.get_by_role('button',name='Save changes').click()
                page.get_by_text('The operation status is unknown.',exact=False).wait_for()
                assert page.get_by_role('button',name='Save changes').is_disabled()
                page.locator('form[data-enhance-form]').evaluate('(form)=>form.requestSubmit()')
                page.wait_for_timeout(100)
                assert len(commits)==1 and commits[0]==200,commits
                with app.app_context():
                    assert Inventory.query.filter_by(product_id=1).one().quantity==15
                    assert InventoryAudit.query.count()==before_audits+1
                page.unroute('**/admin-edit-product/1')
                results.append('generic form lost committed response locks duplicate requestSubmit')
                visit('/admin-inventory')
                csrf=page.locator('#main-content input[name="csrf_token"]').first.input_value()
                with app.app_context(): revision=db.session.get(Product,1).version
                rejected=page.request.delete(origin+'/products/1',data={'expected_version':revision},headers={'X-CSRF-Token':csrf})
                assert rejected.status==409
                sold=page.request.post(origin+'/sales',data={'product_id':1,'quantity':15},headers={'X-CSRF-Token':csrf})
                assert sold.status==200
                with app.app_context():
                    before_sales=[(sale.sale_id,sale.quantity) for sale in Sale.query.order_by(Sale.sale_id).all()]
                    before_audits=InventoryAudit.query.count()
                visit('/admin-inventory')
                row=page.locator('tr[data-id="1"]');row.locator('summary[aria-label]').click()
                page.once('dialog',lambda dialog:dialog.accept())
                row.get_by_role('button',name='Archive product',exact=True).click()
                page.wait_for_load_state('networkidle')
                page.locator('tr[data-id="1"]').wait_for(state='detached')
                with app.app_context():
                    assert db.session.get(Product,1).archived
                    assert [(sale.sale_id,sale.quantity) for sale in Sale.query.order_by(Sale.sale_id).all()]==before_sales
                    assert InventoryAudit.query.count()==before_audits+1
                visit('/archived-products');assert page.get_by_role('cell',name='Full Cream Milk',exact=True).count()==1
                visit('/sales');assert page.get_by_text('Archived',exact=True).count()>0
                visit('/admin-record-sale');assert page.locator('#productSelect option[value="1"]').count()==0
                # Staff edit regression later uses product 2 because product 1 is archived.
                results.append('archive browser confirmation, preserved sales/audits, history and sale exclusion')
                visit('/admin-dashboard');page.get_by_role('button',name='AI assistant').click();page.locator('#assistant-panel[data-assistant-state="unavailable"]').wait_for()
                assert page.locator('#assistant-question').is_disabled()
                assert page.get_by_text('Assistant unavailable',exact=True).is_visible()
                page.screenshot(path=str(output/'assistant-desktop.png'),full_page=True)
                page.keyboard.press('Escape');assert not page.locator('#assistant-panel').is_visible()
                results.append('assistant unavailable state, disabled messaging, Escape')
                for width,height in [(768,1024),(390,844)]:
                    page.set_viewport_size({'width':width,'height':height})
                    for name,path in [('dashboard','/admin-dashboard'),('inventory','/admin-inventory'),('analytics','/analytics'),('settings','/admin-settings')]:
                        visit(path);page.screenshot(path=str(output/(name+'-'+str(width)+'.png')),full_page=True)
                    page.get_by_role('button',name='Open navigation').click();assert page.get_by_role('navigation',name='Main navigation').is_visible()
                    page.keyboard.press('Escape');assert page.get_by_role('button',name='Open navigation').get_attribute('aria-expanded')=='false'
                    results.append('responsive layout and navigation '+str(width))
                page.set_viewport_size({'width':1440,'height':1000})
                visit('/admin-dashboard');page.get_by_role('button',name='Sign out').click();login('staff')
                for path in ['/dashboard','/inventory-page','/record-sale','/staff','/expiry-alerts','/analytics']:
                    visit(path);results.append('staff '+path)
                assert page.request.get(origin+'/edit-product/2').status==403
                assert page.request.get(origin+'/branch-administration').status==403
                results.append('staff catalog and branch administration denied')
                assert not errors,errors
                browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
            with app.app_context():db.session.remove();db.engine.dispose()
        print(json.dumps({'checks':results,'javascript_errors':errors,'screenshots':str(output),'database':'disposable SQLite, removed after run'},indent=2))

if __name__=='__main__':main()
