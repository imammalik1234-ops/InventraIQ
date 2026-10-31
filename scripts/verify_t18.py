"""T18 clock/evidence browser checks on a NEW disposable synthetic T15 app."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.verify_t15 import main
from scripts.verify_t16 import checks as paging_checks


def checks(origin,admin,manager,clerk,results):
    paging_checks(origin,admin,manager,clerk,results)
    for page in (admin,manager,clerk):
        assert page.goto(origin+'/ai-predictions').status==200
        assert '2001-01-14' in page.locator('#forecast-historical-date').inner_text()
        assert 'simulated_demo' in page.locator('#forecast-operational-clock').inner_text()
        assert '2031-01-03' in page.locator('#forecast-operational-clock').inner_text()
        branch=1 if page is admin else 2
        response=page.request.get(origin+f'/api/forecast-data?branch_id={branch}&sku_id=1&as_of=2001-01-07&training_end=2001-01-05')
        assert response.status==200
        data=response.json();assert data['contract']['branch_id']==branch
        assert data['contract']['as_of']=='2001-01-07' and len(data['days'])==7
        assert len(data['training'])==5 and len(data['evaluation'])==2
        assert data['forecast']['predicted_demand'] is None and data['contract']['provenance']=='synthetic_fixture'
        assert page.goto(origin+f'/ai-predictions?branch_id={branch}&sku_id=1&as_of=2001-01-07').status==200
        assert '2001-01-07' in page.locator('#forecast-historical-date').inner_text()
        assert page.get_by_role('heading',name='Forecast snapshots',exact=True).count()==1  # T20 page reads snapshots, not histories.
        assert page.request.get(origin+'/api/predictions').json()==[]
        if page is not admin:
            assert page.request.get(origin+'/api/forecast-data?branch_id=1&sku_id=1&as_of=2001-01-07').status==403
    results.append('T18 HQ/manager/clerk historical date, separate operational clock, scoped data, no predictions and forged-branch denial')
    admin.set_viewport_size({'width':390,'height':844})
    assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    admin.set_viewport_size({'width':1440,'height':1000})
    results.append('T18 forecast clock cards preserve responsive layout')


if __name__=='__main__':main(checks)
