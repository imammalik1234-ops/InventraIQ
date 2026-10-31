"""Local headless browser verification on a NEW attested T15 only; no Gemini."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from werkzeug.serving import make_server,WSGIRequestHandler
from sqlalchemy import select,func
from scripts.measure_t16 import fixture,client
from database import db
from models import BranchStock,InventoryOperation
from simulation.verify import verify


class Quiet(WSGIRequestHandler):
    def log(self,*args,**kwargs):pass


def main():
    from playwright.sync_api import sync_playwright
    output=Path(__file__).resolve().parents[1]/'data/private/t27/browser'
    output.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='inventraiq-t27-browser-') as parent:
        root,app=fixture(parent,'small');app.config['ASSISTANT_PROPOSALS_ENABLED']=True
        server=make_server('127.0.0.1',0,app,threaded=True,request_handler=Quiet)
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        before=verify(app)
        cases=[]
        try:
            with sync_playwright() as playwright:
                executable=next((p for p in ('C:/Program Files/Google/Chrome/Application/chrome.exe',
                    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe') if Path(p).is_file()),None)
                if executable is None:raise RuntimeError('Existing browser required; no install permitted')
                browser=playwright.chromium.launch(executable_path=executable,headless=True)
                try:
                    caller=client(app,901,1)
                    # Existing test session helper supplies an authenticated,
                    # server-signed fixture session; never print its cookie.
                    cookie=caller.get_cookie('session').value
                    context=browser.new_context()
                    context.add_cookies([dict(name='session',value=cookie,url=origin)])
                    context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
                    page=context.new_page()
                    with app.app_context():
                        lot=db.session.scalar(select(BranchStock.lot_id).where(BranchStock.branch_id==1,BranchStock.sku_id==121,BranchStock.on_hand>10))
                    for width in (1440,320):
                        page.set_viewport_size(dict(width=width,height=1000))
                        assert page.goto(origin+'/assistant/proposals').status==200
                        page.get_by_label('Exact local proposal question').fill(f'propose write-off SKU 121 lot {lot} quantity 1')
                        page.get_by_role('button',name='Create proposal for review',exact=True).click()
                        page.wait_for_url('**/assistant/proposals/*')
                        assert verify(app)==before
                        checkbox=page.get_by_role('checkbox')
                        assert not checkbox.is_checked()
                        page.get_by_role('button',name='Confirm exact proposal',exact=True).click()
                        assert not checkbox.is_checked()  # Native required validation stops submission.
                        assert verify(app)==before
                        checkbox.focus();assert checkbox.evaluate('e=>e===document.activeElement')
                        assert page.locator('input[name=csrf_token]').count()>=2
                        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                        page.screenshot(path=str(output/f'review-{width}.png'),full_page=True)
                        if width==1440:
                            page.get_by_role('button',name='Reject / cancel proposal',exact=True).click()
                            page.wait_for_url('**/assistant/proposals/*')
                            page.get_by_text('Cancelled. No business action was initiated.',exact=True).wait_for()
                            assert verify(app)==before
                        else:
                            checkbox.check()
                            page.get_by_role('button',name='Confirm exact proposal',exact=True).click()
                            page.get_by_text('Confirmed once.',exact=False).wait_for()
                            page.reload()
                            with app.app_context():
                                count=db.session.scalar(select(func.count()).select_from(InventoryOperation))
                            page.reload()
                            with app.app_context():
                                assert db.session.scalar(select(func.count()).select_from(InventoryOperation))==count
                        cases.append(dict(width=width,explicit_checkbox=True,review_no_business_effect=True,
                            keyboard=True,no_overflow=True))
                    context.close()
                    result=dict(browser=browser.version,cases=cases,confirmation_once=True,external_requests=0)
                    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
                    print(json.dumps(result))
                finally:browser.close()
        finally:
            server.shutdown();worker.join(5);server.server_close()
            with app.app_context():db.session.remove();db.engine.dispose()


if __name__=='__main__':main()
