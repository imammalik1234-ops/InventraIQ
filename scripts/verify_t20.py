"""Real Chromium T20 role checks; fresh synthetic target and offline job only."""
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.verify_t15 import main
from forecasting.target import prepare
from forecasting.jobs import offline,publish
from forecasting.monitoring import evaluate

prepared={}


def fixture(parent):
    root,app=prepare(parent,'medium');prepared['app']=app
    return root,app


def checks(origin,admin,manager,clerk,results):
    with offline(prepared['app']):
        monitored=publish(request_key='browser-monitoring',as_of='2001-03-24',skus=12)
        evaluate(monitored['run_id'],'2001-03-31')
        run=publish(request_key='browser-current',as_of='2001-03-31',skus=12)
    for page,branch in ((admin,1),(manager,2),(clerk,2)):
        data=page.request.get(origin+f'/api/forecast-snapshots?branch_id={branch}').json()
        assert data['run_id']==run['run_id'] and data['state']=='provisional'
        assert len(data['items'])==12 and {r['branch_id'] for r in data['items']}=={branch}
        assert page.goto(origin+f'/ai-predictions?branch_id={branch}').status==200
        page.get_by_text('Versioned forecast snapshots and coverage',exact=True).click()
        card=page.locator('[data-forecast-run]')
        assert card.is_visible()
        assert card.get_attribute('data-forecast-run')==str(run['run_id'])
        assert 'provisional_synthetic' in card.inner_text() and '2001-03-31' in card.inner_text()
        assert page.locator('[data-forecast-series]').count()==12
        assert page.request.get(origin+f'/api/predictions?branch_id={branch}').json()==data['items']
        history=page.request.get(origin+f'/api/forecast-data?branch_id={branch}&sku_id=1&as_of=2001-03-31').json()
        assert history['forecast_run_id']==run['run_id'] and history['forecast']==data['items'][0]['forecast']
        metrics=page.request.get(origin+f'/api/forecast-monitoring?branch_id={branch}&run_id={monitored["run_id"]}').json()
        assert metrics['status']=='evaluated' and {m['branch_id'] for m in metrics['metrics']}=={branch}
        if page is not admin:
            assert page.request.get(origin+'/api/forecast-snapshots?branch_id=1').status==403
    results.append('T20 current batch matches forecast HTML/JSON/legacy API for HQ, manager and clerk; metrics scoped')
    first=admin.request.get(origin+'/api/forecast-snapshots?branch_id=1&page_size=5').json()
    second=admin.request.get(origin+f'/api/forecast-snapshots?branch_id=1&page_size=5&page=2&run_id={run["run_id"]}').json()
    assert first['pagination']['total']==12
    assert not {r['snapshot_id'] for r in first['items']}&{r['snapshot_id'] for r in second['items']}
    results.append('T20 bounded stable snapshot pagination retains branch/run scope')
    assert admin.goto(origin+'/admin-dashboard?branch_id=1').status==200
    # The overview presents scoped decisions; batch markers belong to /ai-predictions.
    from services.planning_presentation import review_cards
    recommendations=admin.request.get(origin+'/api/recommendations?branch_id=1&page_size=3').json()
    panel=admin.locator('[data-planning-center]')
    assert panel.count()==1 and 'Demo evidence' in panel.inner_text() and 'human review' in panel.inner_text()
    assert 'Inputs needed' in panel.inner_text() and 'Not ready' in panel.inner_text()
    assert recommendations['items'] and not recommendations['actions_enabled'] and not recommendations['production_numeric_enabled']
    assert all(r['calculation'] is None and any(reason.startswith('FORECAST_') for reason in r['reason_codes']) for r in recommendations['items'])
    for card in review_cards(recommendations,{}):
        assert panel.locator('[data-decision-id="'+card['decision_id']+'"]').count()==1
    admin.set_viewport_size({'width':390,'height':844})
    assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    admin.set_viewport_size({'width':1440,'height':1000})
    results.append('T20 dashboard decision/API parity and mobile layout; demo labels and missing-input gates visible')
    with offline(prepared['app']):publish(request_key='browser-stale',as_of='2001-03-24',skus=12)
    assert manager.goto(origin+'/ai-predictions').status==200
    manager.get_by_text('Versioned forecast snapshots and coverage',exact=True).click()
    assert 'Stale' in manager.locator('[data-forecast-run]').inner_text()
    assert 'Unavailable' in manager.locator('[data-forecast-series]').first.inner_text()
    results.append('T20 historical stale batch explicitly withholds numerical output; no live fallback')


if __name__=='__main__':
    with patch('sklearn.linear_model.LinearRegression.fit',side_effect=AssertionError('HTTP fit')),\
        patch('sklearn.ensemble.HistGradientBoostingRegressor.fit',side_effect=AssertionError('HTTP fit')):
        main(checks,create_fixture=fixture)
