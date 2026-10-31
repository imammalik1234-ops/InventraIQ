"""T18 temporal contracts on original synthetic rows and NEW disposable targets."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from sqlalchemy import event,update
from werkzeug.exceptions import Forbidden,NotFound
from database import db
from models import UserBranch,Branch
from forecasting.clock import business_date,iso_date
from forecasting.data import prepare_history,load_series,view_context,MAX_DAYS
from forecasting.compat import withheld_forecast
from ingestion.synthetic import SOURCE
from scripts.measure_t16 import fixture,client
from simulation.runtime import actor
from security import MutationError


def contract(**kw):
    return dict(version='inventraiq-forecast-data-v1',branch_id=1,sku_id=1,timezone='UTC',
        source_id=SOURCE,source_revision='synthetic-r1',manifest_hash='a'*64,provenance='synthetic_fixture',
        **dict(dict(start='2001-01-01',as_of='2001-01-05',training_end='2001-01-03',
            source_start='2001-01-01',source_end='2001-01-14'),**kw))


def fact(day,units=0,coverage='observed',**kw):
    return dict(business_date='2001-01-'+str(day).zfill(2),units=units,coverage=coverage,
        **dict(dict(branch_id=1,sku_id=1,source_revision='synthetic-r1',provenance='synthetic_fixture'),**kw))


class TemporalContractTests(unittest.TestCase):
    def test_zero_missing_absent_are_distinct(self):
        r=prepare_history(contract(),[fact(1),fact(2,None,'missing'),fact(4,7)])
        self.assertTrue(r['days'][0]['observed_zero']);self.assertTrue(r['training'][0]['eligible'])
        self.assertEqual([d['coverage'] for d in r['days']],['observed','missing','missing_record','observed','missing_record'])
        self.assertIsNone(r['training'][1]['units']);self.assertFalse(r['training'][1]['eligible'])
        self.assertTrue(all(d['stock_available'] is None and d['exposure'] is None for d in r['days']))

    def test_future_withheld_and_training_labels_separated(self):
        rows=[fact(1,2),fact(4,100),fact(6,999)]
        a=prepare_history(contract(),rows);rows[1]['units']=200
        b=prepare_history(contract(),rows)
        self.assertEqual(a['training'],b['training']);self.assertEqual(a['training_identity'],b['training_identity'])
        self.assertNotEqual(a['history_identity'],b['history_identity'])
        self.assertEqual(len(a['days']),5);self.assertEqual(a['evaluation'][0]['units'],100)
        self.assertEqual(a['training'][0]['units'],2)

    def test_declared_launch_window_not_first_sale(self):
        window=dict(start='2001-01-02',end='2001-01-04',known_on='2000-12-01',reference='synthetic launch evidence')
        r=prepare_history(contract(),[fact(1,0),fact(2,0),fact(4,3)],exposure=window)
        self.assertEqual(r['days'][0]['coverage'],'not_yet_launched');self.assertFalse(r['training'][0]['eligible'])
        self.assertTrue(r['days'][0]['recorded_zero']);self.assertFalse(r['days'][0]['observed_zero'])
        self.assertEqual(r['days'][1]['coverage'],'observed');self.assertTrue(r['training'][1]['eligible'])
        self.assertEqual(r['days'][-1]['coverage'],'outside_exposure')

    def test_future_exposure_evidence_cannot_change_training(self):
        rows=[fact(1,4)];base=prepare_history(contract(),rows)
        future=dict(start='2001-01-04',end=None,known_on='2001-01-04',reference='future launch record')
        result=prepare_history(contract(),rows,exposure=future)
        self.assertEqual(base,result)

    def test_nonzero_prelaunch_conflict_rejects(self):
        with self.assertRaises(MutationError):prepare_history(contract(),[fact(1,4)],
            exposure=dict(start='2001-01-03',end=None,known_on='2000-12-01',reference='launch evidence'))

    def test_dates_strict_and_bounds(self):
        for v in ('2001-02-30','20010101','2001-01-01T00:00:00','',None,False):
            with self.subTest(v=v),self.assertRaises(MutationError):iso_date(v)
        for c in (contract(training_end='2001-01-06'),contract(start='2002-01-01'),contract(start='1990-01-01')):
            with self.assertRaises(MutationError):prepare_history(c,[])
        self.assertEqual(MAX_DAYS,1096)

    def test_duplicate_and_cross_identity_rows_rejected(self):
        for rows in ([fact(1),fact(1)], [fact(1,branch_id=2)], [fact(1,sku_id=2)],
                     [fact(1,source_revision='synthetic-r2')], [fact(1,provenance='synthetic_demo')]):
            with self.subTest(rows=rows),self.assertRaises(MutationError):prepare_history(contract(),rows)

    def test_source_and_invalid_coverage_rejected(self):
        bad=contract();bad['source_id']='m5'
        with self.assertRaises(MutationError):prepare_history(bad,[])
        for row in (fact(1,-1),fact(1,True),fact(1,None),fact(1,1,'missing')):
            with self.assertRaises(MutationError):prepare_history(contract(),[row])

    def test_outside_source_dates_preserved_unknown(self):
        r=prepare_history(contract(start='2000-12-31',source_end='2001-01-03'),[])
        self.assertEqual(r['days'][0]['date'],'2000-12-31')
        self.assertEqual(r['days'][0]['coverage'],'outside_source_coverage')
        self.assertEqual(r['days'][-1]['coverage'],'outside_source_coverage')

    def test_determinism_input_order_independent(self):
        rows=[fact(1,2),fact(3,0),fact(2,None,'missing')]
        self.assertEqual(prepare_history(contract(),rows),prepare_history(contract(),list(reversed(rows))))

    def test_timezone_midnight_and_dst(self):
        t=datetime(2026,1,1,16,tzinfo=timezone.utc)
        self.assertEqual(business_date(t,'Asia/Singapore').isoformat(),'2026-01-02')
        self.assertEqual(business_date(t,'America/New_York').isoformat(),'2026-01-01')
        for hour in (6,7,8):
            self.assertEqual(business_date(datetime(2026,3,8,hour,tzinfo=timezone.utc),'America/New_York').isoformat(),'2026-03-08')
        for hour in (5,6):
            self.assertEqual(business_date(datetime(2026,11,1,hour,tzinfo=timezone.utc),'America/New_York').isoformat(),'2026-11-01')
        with self.assertRaises(MutationError):business_date(t.replace(tzinfo=None),'UTC')

    def test_compatibility_has_original_keys_no_fitting(self):
        with patch('ai_model.forecast_demand',side_effect=AssertionError('fit')):
            r=withheld_forecast(prepare_history(contract(),[fact(1,0)]))
        self.assertEqual(set(r),{'status','reason','predicted_demand','horizon_days','mae','history_days','selling_days','method'})
        self.assertIsNone(r['predicted_demand']);self.assertIsNone(r['mae']);self.assertEqual(r['status'],'withheld')

    def test_row_bound_and_invalid_exposure(self):
        with self.assertRaises(MutationError):prepare_history(contract(),[fact(6)]*(MAX_DAYS+2))
        for e in ({},dict(start='2001-01-04',end='2001-01-01',known_on='2000-01-01',reference='x')):
            with self.assertRaises(MutationError):prepare_history(contract(),[],exposure=e)


class ScopedForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();cls.root,cls.app=fixture(cls.temp.name,'small',indexes=True)
    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():db.session.remove();db.engine.dispose()
        cls.temp.cleanup()
    def read(self,**kw):
        values=dict(branch_id=1,sku_id=1,as_of='2001-01-14');values.update(kw)
        return load_series(**values)

    def test_fixed_asof_query_future_excluded(self):
        with actor(self.app,901):r=self.read(as_of='2001-01-07',training_end='2001-01-05')
        self.assertEqual(len(r['days']),7);self.assertEqual(len(r['training']),5)
        self.assertEqual(len(r['evaluation']),2);self.assertEqual(r['contract']['clock'],'historical_dataset')
        self.assertTrue(all(d['date']<='2001-01-07' for d in r['days']))

    def test_roles_and_branch_isolation(self):
        for uid,branch in ((901,1),(903,3),(911,1),(921,1),(912,2),(922,2)):
            with self.subTest(uid=uid),actor(self.app,uid,branch):
                r=self.read(branch_id=branch)
                self.assertEqual(r['contract']['branch_id'],branch)
                self.assertEqual(r['contract']['provenance'],'synthetic_fixture')
                if uid not in (901,903):
                    with self.assertRaises(Forbidden):self.read(branch_id=3 if branch==1 else 1)

    def test_revoked_and_inactive_assignments(self):
        with actor(self.app,911,1):
            self.read();db.session.execute(update(UserBranch).where(UserBranch.user_id==911).values(active=False))
            with self.assertRaises(Forbidden):self.read()
        with actor(self.app,911,1):
            db.session.execute(update(Branch).where(Branch.branch_id==1).values(active=False))
            with self.assertRaises(Forbidden):self.read()

    def test_unknown_and_operational_only_sku_not_merged(self):
        with actor(self.app,901):
            for sku in (99999,121):
                with self.assertRaises(NotFound):self.read(sku_id=sku)
            r=self.read();self.assertTrue(all(d['date'].startswith('2001-') for d in r['days']))

    def test_future_demo_period_does_not_shift_history(self):
        with actor(self.app,901):r=self.read(start='2031-01-01',as_of='2031-01-03')
        self.assertEqual(r['coverage']['observed'],0)
        self.assertTrue(all(d['coverage']=='outside_source_coverage' and d['units'] is None for d in r['days']))

    def test_source_pin_and_reproducibility_api_service(self):
        with actor(self.app,901):a=self.read()
        u=client(self.app)
        b=u.get('/api/forecast-data?branch_id=1&sku_id=1&as_of=2001-01-14').get_json()
        self.assertEqual(a['history_identity'],b['history_identity']);self.assertEqual(a['training'],b['training'])
        page=u.get('/ai-predictions?branch_id=1&sku_id=1&as_of=2001-01-14')
        self.assertIn('Forecast snapshots',page.get_data(as_text=True))  # T20 page no longer loads history.
        self.assertEqual(page.status_code,200)
        with actor(self.app,901):
            for kw in (dict(source_revision='synthetic-wrong'),dict(manifest_hash='b'*64)):
                with self.assertRaises(MutationError):self.read(**kw)

    def test_http_injections_and_old_contract(self):
        u=client(self.app,911,1);base='/api/forecast-data?branch_id=1&sku_id=1&as_of=2001-01-14'
        for extra in ('&actor_id=901','&sku_id=2','&clock=live','&exposure=zero','&source_id=m5','&as_of=2031-01-03'):
            self.assertEqual(u.get(base+extra).status_code,403 if extra=='&actor_id=901' else 400,extra)
        self.assertEqual(u.get(base.replace('branch_id=1','branch_id=2')).status_code,403)
        self.assertEqual(u.get('/api/forecast-data').status_code,400)
        self.assertEqual(self.app.test_client().get(base).status_code,302)
        with patch('intelligence.forecast_demand',side_effect=AssertionError('Unexpected fit')):
            old=u.get('/api/predictions');page=u.get('/ai-predictions')
            self.assertEqual(old.get_json(),[]);self.assertIn('pending-T20',old.headers['X-InventraIQ-Feature-Gate'])
            self.assertEqual(page.status_code,200);self.assertIn('2001-01-14',page.get_data(as_text=True))
            self.assertIn('2031-01-03',page.get_data(as_text=True))

    def test_exposure_registry_is_source_bound_server_only(self):
        with actor(self.app,901):
            r=self.read();key=(r['contract']['manifest_hash'],1,1)
            evidence=dict(start='2001-01-03',end=None,known_on='2001-01-01',reference='Generated launch evidence')
            with patch.dict(self.app.config,FORECAST_EXPOSURES={key:evidence}):
                changed=self.read();self.assertEqual(changed['days'][1]['coverage'],'not_yet_launched')
            with patch.dict(self.app.config,FORECAST_EXPOSURES={('wrong',1,1):evidence}):
                self.assertEqual(self.read(),r)

    def test_bounded_query_budget_and_no_prices_features(self):
        queries=[]
        with actor(self.app,901):
            def before(conn,cursor,stmt,params,context,many):queries.append(stmt)
            event.listen(db.engine,'before_cursor_execute',before)
            try:r=self.read()
            finally:event.remove(db.engine,'before_cursor_execute',before)
        self.assertLessEqual(len(queries),20)
        fact_queries=[q for q in queries if 'sales_observations' in q]
        self.assertEqual(len(fact_queries),1);self.assertIn('LIMIT',fact_queries[0])
        self.assertNotIn('price_minor',fact_queries[0]);self.assertNotIn('price',r['days'][0])
        with actor(self.app,901),self.assertRaises(MutationError):self.read(start='1900-01-01')

    def test_clock_context_separates_historical_and_simulated(self):
        with actor(self.app,912,2):r=view_context()
        self.assertEqual(r['historical_as_of'],'2001-01-14');self.assertEqual(r['operational_clock'],'simulated_demo')
        self.assertEqual([b['branch_id'] for b in r['branches']],[2])
        self.assertTrue(r['live_utc'].startswith('2026-'));self.assertTrue(r['operational_utc'].startswith('2031-'))
        with actor(self.app,912,2):earlier=view_context(as_of='2001-01-07')
        self.assertEqual(earlier['historical_as_of'],'2001-01-07')

    def test_unprepared_target_refused(self):
        with actor(self.app,901),patch.object(self.app,'_t15_target',None),self.assertRaises(MutationError) as error:
            self.read()
        self.assertEqual(error.exception.status,503)


class SourceRevisionTests(unittest.TestCase):
    def test_independent_targets_same_manifest_same_history(self):
        results=[]
        with TemporaryDirectory() as temp:
            for _ in range(2):
                root,app=fixture(temp,'small',indexes=True)
                try:
                    with actor(app,901):results.append(load_series(branch_id=1,sku_id=1,as_of='2001-01-14'))
                finally:
                    with app.app_context():db.session.remove();db.engine.dispose()
        self.assertEqual(results[0],results[1])

    def test_atomic_source_replacement_invalidates_old_pin(self):
        from ingestion.pipeline import generate_fixture,load_manifest,ingest
        with TemporaryDirectory() as temp:
            root,app=fixture(temp,'small',indexes=True)
            try:
                with actor(app,901):old=load_series(branch_id=1,sku_id=1,as_of='2001-01-14')
                path=generate_fixture(root,profile='small',revision='synthetic-r2',seed=14)
                ingest(root,load_manifest(root,path))
                with actor(app,901):
                    with self.assertRaises(MutationError):load_series(branch_id=1,sku_id=1,as_of='2001-01-14',source_revision='synthetic-r1')
                    current=load_series(branch_id=1,sku_id=1,as_of='2001-01-14',source_revision='synthetic-r2')
                    self.assertEqual(len(current['days']),14);self.assertNotEqual(old['history_identity'],current['history_identity'])
            finally:
                with app.app_context():db.session.remove();db.engine.dispose()


class LegacyClockTests(unittest.TestCase):
    def test_legacy_product_dates_follow_authorized_branches(self):
        from test_branch_authorization import BranchAuthorizationTests
        from intelligence import inventory_intelligence
        fixture=BranchAuthorizationTests();fixture.setUp()
        try:
            db.session.get(Branch,1).timezone='Asia/Singapore'
            db.session.get(Branch,2).timezone='America/New_York'
            db.session.get(Branch,3).timezone='UTC';db.session.commit()
            context=fixture.context(uid=1,branch=None)
            def recorded(p,inventory,sales,today):
                return dict(product_id=p.product_id,date=today.isoformat(),risk_priority=0,
                    projected_shortfall=None,recommended_reorder_qty=None)
            try:
                with patch('expiry.utcnow',return_value=datetime(2026,1,1,16,tzinfo=timezone.utc)),patch('intelligence.withheld_legacy',side_effect=recorded):
                    rows=inventory_intelligence()
                self.assertEqual({r['product_id']:r['date'] for r in rows},{11:'2026-01-02',22:'2026-01-01',33:'2026-01-01'})
            finally:context.pop()
        finally:fixture.tearDown()
