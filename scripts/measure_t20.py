"""Bounded single-reader snapshot measurements on NEW synthetic targets only."""
import argparse
import hashlib
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
import sys
from tempfile import TemporaryDirectory
import time
import tracemalloc
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def measure(profile,samples=5):
    if profile not in ('small','medium') or type(samples) is not int or not 2<=samples<=20:raise ValueError('Bounded synthetic settings required')
    from forecasting.target import prepare
    from forecasting.jobs import offline,publish
    from forecasting.data import source_contract
    from database import db
    from sqlalchemy import event
    from scripts.measure_t16 import client
    from benchmarks.t17 import rss,summary,disk_bytes
    with TemporaryDirectory(prefix='inventraiq-t20-measure-') as temp,ExitStack() as stack:
        root,app=prepare(temp,profile);start=time.perf_counter();queries=[]
        with app.app_context():engine=db.engine
        def sql(conn,cursor,stmt,params,context,many):queries.append(stmt)
        event.listen(engine,'before_cursor_execute',sql)
        fits=[0]
        def refuse(*args,**kwargs):fits[0]+=1;raise AssertionError('Fit forbidden during T20 publication/retrieval')
        for name in ('sklearn.linear_model.LinearRegression.fit','sklearn.linear_model.Ridge.fit',
            'sklearn.ensemble.HistGradientBoostingRegressor.fit'):stack.enter_context(patch(name,side_effect=refuse))
        result=dict(profile=profile,samples=samples,measurements=[],fits=0,database='new disposable SQLite',
            environment=dict(os=platform.platform(),python=platform.python_version(),logical_cpus=os.cpu_count(),
                cpu=platform.processor(),sqlite=__import__('sqlite3').sqlite_version,
                concurrency=1,threads='OMP/OpenBLAS=1',conditions='first request then warm repeated reads; uncontrolled host'),
            provenance='synthetic_fixture',production_capacity=False)
        try:
            with offline(app):
                source,manifest=source_contract();origin=manifest['coverage']['last_date']
                result['source_identity']={k:source[k] for k in ('source_revision','manifest_hash')}
            result['source_hashes']={p:hashlib.sha256((Path(__file__).resolve().parents[1]/p).read_bytes()).hexdigest() for p in
                ('forecasting/jobs.py','forecasting/reader.py','forecasting/monitoring.py','forecasting/artifacts.py','operational_routes.py','intelligence.py')}
            for skus in ((1,6) if profile=='small' else (1,12,120)):
                if time.perf_counter()-start>240:raise RuntimeError('Measurement wall limit')
                if rss()['peak']>1024*1048576 or disk_bytes(root)>128*1048576:raise RuntimeError('Measurement resource limit')
                queries.clear();began=time.perf_counter()
                with offline(app):run=publish(request_key='SYN-PERF-'+str(skus),as_of=origin,skus=skus)
                publication=dict(ms=(time.perf_counter()-began)*1000,queries=len(queries),rss=rss(),**run)
                u=client(app);paths=['/api/predictions','/ai-predictions','/admin-dashboard',
                    '/api/forecast-snapshots?page_size=100','/api/forecast-snapshots?page_size=100&page=4']
                endpoints=[]
                for path in paths:
                    rows=[]
                    for sample in range(samples+1):
                        queries.clear();t=time.perf_counter();response=u.get(path)
                        if response.status_code!=200:raise RuntimeError('Unexpected measured response')
                        if any('sales_observations' in q.lower() for q in queries):raise AssertionError('Request loaded observations')
                        rows.append(dict(ms=(time.perf_counter()-t)*1000,status=response.status_code,queries=len(queries),
                            bytes=len(response.data),rss=rss()['current']))
                    endpoints.append(dict(path=path,first=rows[0],warm=summary(rows[1:])))
                manager=client(app,912,2);value=manager.get('/api/forecast-snapshots?page_size=100').get_json()
                assert {r['branch_id'] for r in value['items']}<={2}
                tracemalloc.start();response=u.get('/api/forecast-snapshots?page_size=100');_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
                with app.app_context():
                    plan=[str(tuple(r)) for r in db.session.execute(__import__('sqlalchemy').text(
                        'EXPLAIN QUERY PLAN SELECT snapshot_id FROM forecast_snapshots WHERE run_id=:r AND branch_id IN (1,2,3) ORDER BY branch_id,sku_id LIMIT 100 OFFSET 200'),dict(r=run['run_id']))]
                result['measurements'].append(dict(series=run['expected_count'],publication=publication,endpoints=endpoints,
                    query_plan=plan,retrieval_traced_peak_bytes=peak,temporary_bytes=disk_bytes(root)))
            result['fits']=fits[0];result['worker_peak_rss']=rss();result['wall_seconds']=time.perf_counter()-start
            return result
        finally:
            event.remove(engine,'before_cursor_execute',sql)
            with app.app_context():db.session.remove();db.engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--profile',choices=('small','medium'),default='medium')
    parser.add_argument('--samples',type=int,default=5);parser.add_argument('--output',required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];output=Path(args.output).absolute()
    if not output.is_relative_to(root/'data/private/t20') or output.resolve()!=output or output.exists() or output.suffix!='.json':
        parser.error('Use a NEW JSON output under data/private/t20')
    if not 2<=args.samples<=20:parser.error('Use 2..20 samples')
    output.parent.mkdir(parents=True,exist_ok=True)
    os.environ.update(APP_ENV='testing',DATABASE_URL='sqlite:///:memory:',PYTHON_DOTENV_DISABLED='1',
        PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
    result=measure(args.profile,args.samples);output.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(profile=args.profile,fits=result['fits'],wall_seconds=result['wall_seconds'],output=str(output))))


if __name__=='__main__':main()
