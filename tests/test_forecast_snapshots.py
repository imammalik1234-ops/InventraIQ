"""T20 tests exclusively on newly attested, generated disposable targets."""
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from sqlalchemy import select,update,func,event
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Forbidden
from database import db
from models import UserBranch,Branch
from security import MutationError
from forecasting import jobs
from forecasting.target import prepare
from forecasting.reader import page
from forecasting.schema import ForecastRun as R,ForecastSnapshot as S,ForecastPublication as P,ForecastMonitor as M
from forecasting.artifacts import trusted,canonical,digest,validate
from forecasting.monitoring import evaluate,read,summarize,health
from simulation.runtime import actor
from scripts.measure_t16 import client


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();cls.root,cls.app=prepare(cls.temp.name,'medium')
    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():db.session.remove();db.engine.dispose()
        cls.temp.cleanup()
    def setUp(self):
        # Tables are immutable: each test uses a unique key and latest publication.
        self.key='TEST-'+self._testMethodName.replace('_','-')
    def publish(self,**values):
        return jobs.publish(**dict(dict(request_key=self.key,as_of='2001-03-31',skus=2),**values))

    def test_authorization_and_offline_only(self):
        with actor(self.app,901),self.assertRaises(MutationError):self.publish()
        for uid in (903,911,921):
            with jobs.offline(self.app,uid),self.assertRaises(Forbidden):self.publish()

    def test_target_and_unapproved_origins_refused(self):
        with jobs.offline(self.app):
            for values in (dict(as_of='2031-01-01'),dict(skus=121),dict(request_key='../unsafe')):
                with self.assertRaises(MutationError):self.publish(**values)
            old=self.app._t20_target;del self.app._t20_target
            try:
                with self.assertRaises(MutationError):self.publish()
            finally:self.app._t20_target=old

    def test_repeat_and_conflicting_key(self):
        with jobs.offline(self.app):
            a=self.publish();b=self.publish()
            self.assertEqual(a,b)
            with self.assertRaises(MutationError):self.publish(skus=1)
            count=db.session.scalar(select(func.count()).select_from(S).where(S.c.run_id==a['run_id']))
            self.assertEqual(count,6)

    def test_deterministic_batches_and_later_price_irrelevance(self):
        with jobs.offline(self.app):
            a=self.publish();b=self.publish(request_key=self.key+'-again')
            self.assertEqual(a['batch_hash'],b['batch_hash']);self.assertEqual(a['eligible_count'],6)

    def test_failed_generation_keeps_previous_batch(self):
        with jobs.offline(self.app):
            a=self.publish();original=jobs.snapshot;count=[0]
            def fail(history):
                count[0]+=1
                if count[0]==3:raise RuntimeError('injected private detail')
                return original(history)
            with patch.object(jobs,'snapshot',side_effect=fail),self.assertRaises(RuntimeError):self.publish(request_key=self.key+'-failed')
            value=page();self.assertEqual(value['run_id'],a['run_id'])
            failed=db.session.execute(select(R).where(R.c.request_key==self.key+'-failed')).mappings().one()
            self.assertEqual(failed['status'],'failed');self.assertEqual(failed['error_type'],'RuntimeError')
            self.assertEqual(db.session.scalar(select(func.count()).select_from(S).where(S.c.run_id==failed['run_id'])),0)

    def test_interruption_and_recovery(self):
        with jobs.offline(self.app):
            a=self.publish()
            with patch.object(jobs,'snapshot',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):self.publish(request_key=self.key+'-interrupt')
            run=db.session.execute(select(R).where(R.c.request_key==self.key+'-interrupt')).mappings().one()
            self.assertEqual(run['status'],'running');self.assertEqual(page()['run_id'],a['run_id'])
            jobs.recover(run['run_id'],'Supervisor confirmed stopped worker')
            self.assertEqual(db.session.scalar(select(R.c.status).where(R.c.run_id==run['run_id'])),'failed')

    def test_publication_failure_rolls_back_snapshots_and_pointer(self):
        with jobs.offline(self.app):
            a=self.publish();original=jobs.event
            def fail(run_id,actor,event_name,reason):
                if event_name=='published':raise RuntimeError('inject commit-boundary failure')
                return original(run_id,actor,event_name,reason)
            with patch.object(jobs,'event',side_effect=fail),self.assertRaises(RuntimeError):self.publish(request_key=self.key+'-failure')
            self.assertEqual(page()['run_id'],a['run_id'])
            failed=db.session.scalar(select(R.c.run_id).where(R.c.request_key==self.key+'-failure'))
            self.assertEqual(db.session.scalar(select(func.count()).select_from(S).where(S.c.run_id==failed)),0)

    def test_expected_version_and_controlled_rollback(self):
        with jobs.offline(self.app):
            a=self.publish();b=self.publish(request_key=self.key+'-new');version=page()['publication_version']
            with self.assertRaises(MutationError):jobs.rollback(a['run_id'],version-1,'stale request')
            jobs.rollback(a['run_id'],version,'Return to previously reviewed synthetic batch')
            self.assertEqual(page()['run_id'],a['run_id'])

    def test_hash_and_schema_compatibility(self):
        artifact=trusted();validate(canonical(artifact),digest(artifact))
        for field,value in (('model_version','future'),('qualification','qualified'),('feature_schema','other'),('implementation_sha256','a'*64)):
            bad=dict(artifact);bad[field]=value
            with self.assertRaises(MutationError):validate(canonical(bad),digest(bad))
        with self.assertRaises(MutationError):validate(canonical(artifact),'0'*64)
        from forecasting.artifacts import validate_run
        with self.assertRaises(MutationError):validate_run(dict(artifact_json=canonical(artifact),artifact_hash=digest(artifact),
            model_id='wrong',model_version=artifact['model_version'],qualification=artifact['qualification'],horizon=7))

    def test_publication_health_is_bounded_hq_only(self):
        with jobs.offline(self.app):
            a=self.publish();h=health()
            self.assertEqual(h['active_run_id'],a['run_id']);self.assertLessEqual(len(h['attempts']),20)
            self.assertFalse(h['production_publication'])
        with jobs.offline(self.app,903),self.assertRaises(Forbidden):health()

    def test_tampered_artifact_is_not_served(self):
        with jobs.offline(self.app):
            self.publish()
            with patch('forecasting.reader.validate',side_effect=MutationError('integrity mismatch',409)):
                value=page()
            self.assertEqual(value['state'],'unavailable');self.assertEqual(value['items'],[])

    def test_stale_generation_and_historical_age(self):
        with jobs.offline(self.app):
            old=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
            with patch('forecasting.jobs.now',return_value=old):self.publish()
            value=page();self.assertEqual(value['state'],'stale')
            self.assertTrue(all(r['forecast']['predicted_demand'] is None for r in value['items']))
            self.publish(request_key=self.key+'-historical',as_of='2001-03-24')
            self.assertEqual(page()['state'],'stale')

    def test_branch_isolation_roles_and_scoped_pagination(self):
        with jobs.offline(self.app):run=self.publish()
        for uid,branch in ((901,1),(903,3),(911,1),(921,1),(912,2)):
            with actor(self.app,uid,branch):
                value=page({'page_size':'1'})
                self.assertEqual(value['run_id'],run['run_id']);self.assertEqual(value['pagination']['total'],2)
                self.assertEqual({r['branch_id'] for r in value['items']},{branch})
                if uid not in (901,903):
                    with self.assertRaises(Forbidden):page({'branch_id':3 if branch==1 else 1})
        with actor(self.app,901):
            self.assertEqual(page()['pagination']['total'],6)

    def test_revocation_and_branch_deactivation_rechecked(self):
        with jobs.offline(self.app):self.publish()
        with actor(self.app,911,1):
            page();db.session.execute(update(UserBranch).where(UserBranch.user_id==911).values(active=False))
            with self.assertRaises(Forbidden):page()
        with actor(self.app,901,1):
            db.session.execute(update(Branch).where(Branch.branch_id==1).values(active=False))
            with self.assertRaises(Forbidden):page()

    def test_stable_pages_and_mixed_version_prevention(self):
        with jobs.offline(self.app):
            a=self.publish();first=page({'page_size':2});second=page({'page_size':2,'page':2,'run_id':a['run_id']})
            self.assertFalse({r['snapshot_id'] for r in first['items']}&{r['snapshot_id'] for r in second['items']})
            self.publish(request_key=self.key+'-new')
            with self.assertRaises(MutationError):page({'run_id':a['run_id']})
            for values in ({'page':0},{'page_size':101},{'actor_id':901},{'as_of':'2031-01-01'}):
                with self.assertRaises(MutationError):page(values)

    def test_http_dashboard_and_api_version_no_fits_or_histories(self):
        with jobs.offline(self.app):run=self.publish()
        u=client(self.app,901)
        with patch('sklearn.linear_model.LinearRegression.fit',side_effect=AssertionError('fit')),\
            patch('sklearn.ensemble.HistGradientBoostingRegressor.fit',side_effect=AssertionError('fit')),\
            patch('forecasting.data.load_series',side_effect=AssertionError('history')):
            data=u.get('/api/forecast-snapshots').get_json()
            old=u.get('/api/predictions');html=u.get('/ai-predictions');dashboard=u.get('/admin-dashboard')
            recommendations=u.get('/api/recommendations?page_size=3').get_json()
        self.assertEqual(data['run_id'],run['run_id']);self.assertEqual(old.get_json(),data['items'])
        self.assertEqual(html.status_code,200);self.assertEqual(dashboard.status_code,200)
        planning_html=html.get_data(as_text=True);dashboard_html=dashboard.get_data(as_text=True)
        self.assertIn('provisional_synthetic',planning_html)
        self.assertIn('not qualified production predictions',planning_html)
        self.assertIn('data-forecast-run="'+str(run['run_id'])+'"',planning_html)
        # The overview renders decision cards; batch evidence lives on /ai-predictions.
        from services.planning_presentation import review_cards
        self.assertIn('data-planning-center',dashboard_html)
        self.assertIn('Demo evidence',dashboard_html);self.assertIn('human review',dashboard_html)
        self.assertIn('Inputs needed',dashboard_html);self.assertIn('Not ready',dashboard_html)
        self.assertFalse(recommendations['actions_enabled'])
        self.assertFalse(recommendations['production_numeric_enabled'])
        self.assertTrue(recommendations['items'])
        for row in recommendations['items']:
            self.assertIsNone(row['calculation'])
            self.assertTrue(any(reason.startswith('FORECAST_') for reason in row['reason_codes']))
        for card in review_cards(recommendations,{}):
            self.assertIn('data-decision-id="'+card['decision_id']+'"',dashboard_html)
        self.assertEqual(old.headers['X-InventraIQ-Forecast-Run'],str(run['run_id']))

    def test_snapshot_hash_tampering_detected(self):
        with jobs.offline(self.app):
            self.publish()
            original=__import__('forecasting.reader',fromlist=['payload']).payload
            def damaged(row):
                row=dict(row);row['payload_json']=row['payload_json'].replace('eligible','unavailable')
                return original(row)
            with patch('forecasting.reader.payload',side_effect=damaged):value=page()
            self.assertEqual(value['state'],'unavailable');self.assertEqual(value['items'],[])

    def test_late_authorization_change_leaves_old_batch(self):
        with jobs.offline(self.app):
            a=self.publish();original=jobs.authority;calls=[0]
            def changed():
                calls[0]+=1
                if calls[0]==2:raise Forbidden('revoked')
                return original()
            with patch('forecasting.jobs.authority',side_effect=changed),self.assertRaises(Forbidden):self.publish(request_key=self.key+'-revoked')
            self.assertEqual(page()['run_id'],a['run_id'])

    def test_http_forgeries_monitoring_and_revoked_session(self):
        with jobs.offline(self.app):run=self.publish()
        u=client(self.app,911,1)
        self.assertEqual(u.get('/api/forecast-snapshots?branch_id=2').status_code,403)
        for path in ('/api/forecast-snapshots?actor_id=901','/api/forecast-snapshots?page_size=999','/api/forecast-snapshots?sku_id=1&sku_id=2'):
            self.assertIn(u.get(path).status_code,(400,403))
        with actor(self.app,901):
            db.session.execute(update(UserBranch).where(UserBranch.user_id==911,UserBranch.branch_id==1).values(active=False));db.session.commit()
        try:self.assertEqual(u.get('/api/forecast-snapshots').status_code,403)
        finally:
            with actor(self.app,901):
                db.session.execute(update(UserBranch).where(UserBranch.user_id==911,UserBranch.branch_id==1).values(active=True));db.session.commit()

    def test_retrieval_query_budget_no_observation_scan(self):
        with jobs.offline(self.app):self.publish()
        sql=[]
        with actor(self.app,911,1):
            def collect(conn,cursor,statement,parameters,context,many):sql.append(statement)
            event.listen(db.engine,'before_cursor_execute',collect)
            try:page({'page_size':1})
            finally:event.remove(db.engine,'before_cursor_execute',collect)
        self.assertLessEqual(len(sql),15)
        self.assertFalse(any('sales_observations' in s.lower() for s in sql))
        scoped=[s for s in sql if 'FROM forecast_snapshots' in s]
        self.assertTrue(scoped);self.assertTrue(all('branch_id IN' in s for s in scoped))

    def test_explicit_history_forecast_matches_only_same_origin(self):
        with jobs.offline(self.app):run=self.publish()
        u=client(self.app,911,1)
        current=u.get('/api/forecast-data?branch_id=1&sku_id=1&as_of=2001-03-31').get_json()
        old=u.get('/api/forecast-data?branch_id=1&sku_id=1&as_of=2001-03-30').get_json()
        self.assertEqual(current['forecast_run_id'],run['run_id']);self.assertEqual(current['forecast']['status'],'provisional')
        self.assertIsNone(old['forecast_run_id']);self.assertEqual(old['forecast']['status'],'withheld')

    def test_recovery_cannot_change_committed_run(self):
        with jobs.offline(self.app):
            a=self.publish()
            with self.assertRaises(MutationError):jobs.recover(a['run_id'],'not interrupted')

    def test_bounded_job_time_leaves_no_partial_results(self):
        with jobs.offline(self.app):
            a=self.publish()
            with patch('forecasting.jobs.time.monotonic',side_effect=[0,121]),self.assertRaises(MutationError):self.publish(request_key=self.key+'-overbudget')
            self.assertEqual(page()['run_id'],a['run_id'])

    def test_monitoring_known_metrics_scope_and_repeat(self):
        with jobs.offline(self.app):
            a=self.publish(as_of='2001-03-24');m=evaluate(a['run_id'],'2001-03-31');again=evaluate(a['run_id'],'2001-03-31')
            self.assertEqual(m['monitor_id'],again['monitor_id'])
            value=read(a['run_id']);self.assertEqual(value['status'],'evaluated')
            self.assertEqual({r['branch_id'] for r in value['metrics']},{1,2,3})
            self.assertTrue(all(r['mae']==0 for r in value['metrics']))
        with actor(self.app,911,1):
            self.assertEqual({r['branch_id'] for r in read(a['run_id'])['metrics']},{1})
            with self.assertRaises(Forbidden):read(a['run_id'],2)

    def test_monitoring_date_and_unavailable_observation_refusal(self):
        with jobs.offline(self.app):
            a=self.publish(as_of='2001-03-24')
            for day in ('2001-03-24','2001-04-01'):
                with self.assertRaises(MutationError):evaluate(a['run_id'],day)
            with patch('forecasting.monitoring.now',return_value='1999-01-01T00:00:00+00:00'),self.assertRaises(MutationError):evaluate(a['run_id'],'2001-03-31')

    def test_immutable_rows_and_zero_metric_denominators(self):
        with jobs.offline(self.app):
            a=self.publish()
            with self.assertRaises(IntegrityError):db.session.execute(S.delete().where(S.c.run_id==a['run_id']))
            db.session.rollback()
            with self.assertRaises(IntegrityError):db.session.execute(update(R).where(R.c.run_id==a['run_id']).values(model_version='tampered'))
            db.session.rollback()
        self.assertIsNone(summarize(0,0,0,0)['mae']);self.assertIsNone(summarize(7,1,0,1)['wape_percent'])
        self.assertEqual(summarize(7,7,14,-7)['signed_bias_units'],-1)


class IndependentTargetTests(unittest.TestCase):
    def test_competing_publications_one_consistent_batch(self):
        import threading
        with TemporaryDirectory() as temp:
            root,app=prepare(temp,'medium');barrier=threading.Barrier(2);original=jobs.snapshot;local=threading.local();outcomes=[]
            def overlap(history):
                if not getattr(local,'met',False):
                    local.met=True;barrier.wait(timeout=15)
                return original(history)
            def publish(index):
                try:
                    with jobs.offline(app):outcomes.append(jobs.publish(request_key='race-'+str(index),as_of='2001-03-31',skus=1))
                except Exception as exc:outcomes.append(type(exc).__name__)
            try:
                with patch('forecasting.jobs.snapshot',side_effect=overlap):
                    workers=[threading.Thread(target=publish,args=(i,)) for i in (1,2)]
                    for worker in workers:worker.start()
                    for worker in workers:worker.join(timeout=30)
                self.assertTrue(all(not worker.is_alive() for worker in workers))
                self.assertEqual(sum(isinstance(r,dict) for r in outcomes),1,outcomes)
                with jobs.offline(app):
                    value=page();self.assertEqual(len(value['items']),3)
                    self.assertEqual({r['run_id'] for r in value['items']},{value['run_id']})
            finally:
                with app.app_context():db.session.remove();db.engine.dispose()

    def test_small_ineligible_and_missing_states(self):
        with TemporaryDirectory() as temp:
            root,app=prepare(temp,'small')
            try:
                with jobs.offline(app):
                    self.assertEqual(page()['state'],'missing')
                    a=jobs.publish(request_key='small',as_of='2001-01-14',skus=6)
                    self.assertEqual(a['eligible_count'],0)
                    value=page();self.assertTrue(all(r['forecast']['predicted_demand'] is None for r in value['items']))
            finally:
                with app.app_context():db.session.remove();db.engine.dispose()

    def test_source_corrections_invalidate_and_cannot_rollback_old_source(self):
        from ingestion.pipeline import generate_fixture,load_manifest,ingest
        with TemporaryDirectory() as temp:
            root,app=prepare(temp,'medium')
            try:
                with jobs.offline(app):
                    a=jobs.publish(request_key='original',as_of='2001-03-24',skus=1)
                    evaluate(a['run_id'],'2001-03-31');version=page()['publication_version']
                path=generate_fixture(root,profile='medium',revision='synthetic-r2',seed=14)
                ingest(root,load_manifest(root,path))
                with jobs.offline(app):
                    self.assertEqual(page()['state'],'unavailable');self.assertEqual(page()['items'],[])
                    self.assertEqual(read(a['run_id'])['status'],'invalidated_source_revision')
                    with self.assertRaises(MutationError):jobs.rollback(a['run_id'],version,'old revision')
            finally:
                with app.app_context():db.session.remove();db.engine.dispose()
