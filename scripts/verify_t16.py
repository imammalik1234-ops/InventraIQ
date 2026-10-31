"""T16 browser navigation plus the unchanged T15 role workflow matrix.

Creates a fresh disposable T15 target; no existing demo URL/database argument.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.verify_t15 import main


def checks(origin,admin,manager,clerk,results):
    response=admin.goto(origin+'/inventory-page?branch_id=1&page_size=1')
    assert response.status==200
    first=admin.locator('article h3').first.inner_text()
    nav=admin.get_by_role('navigation',name='Report pagination')
    nav.get_by_role('link',name='Next page',exact=True).click()
    assert 'snapshot=' in admin.url and 'page=2' in admin.url
    assert admin.locator('article h3').first.inner_text()!=first
    admin.get_by_role('navigation',name='Report pagination').get_by_role('link',name='Previous page',exact=True).click()
    assert admin.locator('article h3').first.inner_text()==first
    results.append('SQL inventory next/previous pages preserve branch, filters and signed revision')
    admin.goto(origin+'/sales?branch_id=1&page_size=1')
    link=admin.locator('a[href*="sale_id="]').first.inner_text()
    admin.get_by_role('navigation',name='Report pagination').get_by_role('link',name='Next page',exact=True).click()
    assert 'cursor=' in admin.url
    assert admin.locator('a[href*="sale_id="]').first.inner_text()!=link
    response=admin.goto(origin+'/branch-sales?branch_id=1&choice_search=DRINK')
    assert response.status==200,(response.status,admin.locator('main').inner_text())
    assert admin.get_by_role('form',name='Find sale SKU').count()==1
    assert admin.locator('select[name="sku_id"]').first.locator('option').count()>=2
    admin.set_viewport_size({'width':390,'height':844})
    assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    admin.set_viewport_size({'width':1440,'height':1000})
    results.append('Sales keyset navigation and bounded searchable SKU choices render in existing shell')
    for page in (manager,clerk):
        response=page.request.get(origin+'/api/observations?start=2001-01-01&end=2001-01-14&page_size=1')
        assert response.status==200
        data=response.json();assert data['scope']['branch_ids']==[2]
        assert data['items'][0]['branch_id']==2 and data['provenance']=='synthetic_fixture'
        token=data['next_cursor']
        assert page.request.get(origin+'/api/observations?start=2001-01-01&end=2001-01-14&page_size=1&cursor='+token).status==200
        assert page.request.get(origin+'/api/observations?branch_id=1').status==403
    results.append('Manager/clerk historical continuation remains scoped; forged branch denied')
    export=manager.request.get(origin+'/operations/observations.csv?start=2001-01-01&end=2001-01-14')
    assert export.status==200 and 'export_complete,84,synthetic_fixture' in export.text()
    assert 'SYN-STORE-01' not in export.text() and 'SYN-STORE-03' not in export.text()
    assert admin.request.get(origin+'/api/operations?page_size=101').status==400
    gated=manager.request.get(origin+'/api/predictions')
    assert gated.json()==[] and 'pending-T20' in gated.headers['x-inventraiq-feature-gate']
    results.append('Scoped streaming observation CSV completes with provenance; invalid page size rejected')


if __name__=='__main__':main(checks)
