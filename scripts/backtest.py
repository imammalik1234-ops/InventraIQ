"""T19 bounded offline evaluation. Only NEW approved synthetic disposable targets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
ROOT=Path(__file__).resolve().parents[1]


def config(profile='medium',skus=12,seconds=300,memory_mb=1024):
    if profile not in ('small','medium'):raise ValueError('Approved synthetic profiles only')
    for key,value,bounds in [('skus',skus,(1,12)),('seconds',seconds,(30,900)),('memory_mb',memory_mb,(256,2048))]:
        if type(value) is not int or not bounds[0]<=value<=bounds[1]:raise ValueError('Invalid bounded '+key)
    return dict(profile=profile,skus=skus,seconds=seconds,memory_mb=memory_mb)


def output_path(value):
    path=Path(value).absolute();private=ROOT/'data'/'private'/'t19'
    if not path.is_relative_to(private) or path.resolve()!=path or path.suffix!='.json' or path.exists():
        raise ValueError('Use a NEW JSON file inside data/private/t19')
    path.parent.mkdir(parents=True,exist_ok=True)
    return path


def collect(parent,settings):
    from unittest.mock import patch
    from simulation import runtime
    from simulation.runner import run
    from ingestion.target import ACK
    from ingestion.synthetic import PROFILES
    from forecasting.data import load_series
    from forecasting.evaluation import evaluate
    from forecasting.features import VERSION,NAMES
    from database import db
    original=runtime.generate_fixture
    with patch.object(runtime,'generate_fixture',side_effect=lambda root,**kw:original(root,profile=settings['profile'])):
        root=runtime.initialize(parent,acknowledgment=ACK)
    app=runtime.create_app(root)
    try:
        run(app)
        histories=[]
        from datetime import date,timedelta
        end=(date(2001,1,1)+timedelta(days=PROFILES[settings['profile']][1]-1)).isoformat()
        with runtime.actor(app,901):
            for branch in (1,2,3):
                for sku in range(1,min(settings['skus'],PROFILES[settings['profile']][0])+1):
                    histories.append(load_series(branch_id=branch,sku_id=sku,as_of=end))
        result=evaluate(histories)
        from importlib.metadata import version
        result.update(dataset=dict(profile=settings['profile'],source='inventraiq-generated-history-v1',revision='synthetic-r1',
            manifest_hash=histories[0]['contract']['manifest_hash'],provenance='synthetic_fixture',
            selection='First requested SKU ordinals from frozen generator manifest; all three branches; no outcome ranking',
            category='Unavailable in historical source; never inferred from current catalog',
            histories=[dict(contract=h['contract'],history_identity=h['history_identity'],coverage=h['coverage']) for h in histories]),
            feature_schema=dict(version=VERSION,names=NAMES,lags=[1,7,14,28],seed=42,
                preprocessing='Training-fold median imputation + missing indicators + scaling; targets never imputed',
                recursion='Future lags use predictions, never evaluation labels'),
            estimator_configuration=dict(version='bounded-candidates-v1',minimum_ml_targets=20,
                trend='OLS day index on observed targets only',linear='OLS shifted calendar/lag features',
                ridge_alphas=[1,10],hist_gradient_boosting=dict(max_iter=40,max_leaf_nodes=7,min_samples_leaf=8,
                    learning_rate=.08,l2_regularization=1,early_stopping=False,random_state=42),
                legacy='Original seven-day total spread uniformly across seven daily predictions; complete calendars only',
                output='Reject nonfinite, clip finite ML outputs at zero; explicit trailing-mean fallbacks'),
            environment=dict(python=platform.python_version(),os=platform.platform(),cpu=platform.processor(),logical_cpus=os.cpu_count(),
                libraries={name:version(name) for name in ('numpy','scikit-learn','SQLAlchemy','Flask','tzdata')},
                threads='OPENBLAS/OMP=1; one evaluation worker; no OS CPU quota',database='new disposable SQLite only'),
            source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                ('forecasting/data.py','forecasting/features.py','forecasting/candidates.py','forecasting/evaluation.py','scripts/backtest.py','ai_model.py')})
        return result
    finally:
        with app.app_context():db.session.remove();db.engine.dispose()


def worker(path):
    path=Path(path)
    if path.name!='control.json' or path.is_symlink() or path.parent.resolve()!=path.parent.absolute() or not path.parent.name.startswith('inventraiq-t19-'):
        raise ValueError('Unverified worker control')
    if not path.is_file() or path.stat().st_size>2048:raise ValueError('Invalid worker control')
    value=json.loads(path.read_text());settings=config(**value['config'])
    if set(value)!={'purpose','config'} or value['purpose']!='new-synthetic-t19-only':raise ValueError('Invalid worker purpose')
    (path.parent/'worker.pid').write_text(str(os.getpid()),encoding='ascii')
    try:result=collect(path.parent,settings)
    except Exception as exc:
        result=dict(status='FAILED',error_type=type(exc).__name__)
        (path.parent/'result.json').write_text(json.dumps(result));raise SystemExit(1)
    (path.parent/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')


def guarded(settings):
    from benchmarks.t17 import rss,disk_bytes
    settings=config(**settings)
    with TemporaryDirectory(prefix='inventraiq-t19-') as temp:
        root=Path(temp);control=root/'control.json'
        control.write_text(json.dumps(dict(purpose='new-synthetic-t19-only',config=settings)))
        env=dict(os.environ,APP_ENV='testing',DATABASE_URL='sqlite:///:memory:',PYTHON_DOTENV_DISABLED='1',
            PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
        start=time.perf_counter();peak=0;disk_peak=0;stop=None;actual=None
        with (root/'worker.log').open('wb') as log:
            p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker',str(control)],env=env,stdout=log,stderr=log)
            try:
                while p.poll() is None:
                    memory=rss(p.pid)
                    if (root/'worker.pid').is_file():
                        actual=int((root/'worker.pid').read_text());child=rss(actual)
                        if actual!=p.pid:memory['peak']+=child['peak']
                        memory['available']=memory['available'] and child['available']
                    peak=max(peak,memory['peak']);disk_peak=max(disk_peak,disk_bytes(root))
                    if time.perf_counter()-start>settings['seconds']:stop='wall_time_limit'
                    elif peak>settings['memory_mb']*1048576:stop='rss_limit'
                    elif disk_peak>128*1048576:stop='disk_limit'
                    elif not memory['available']:stop='rss_unavailable'
                    if stop:break
                    time.sleep(.2)
            finally:
                if p.poll() is None:
                    if os.name=='nt' and actual and actual!=p.pid:
                        try:os.kill(actual,signal.SIGTERM)
                        except ProcessLookupError:pass
                    p.kill();p.wait(timeout=15)
        result=dict(status='INCOMPLETE' if p.returncode or stop else 'COMPLETED',config=settings,
            wall_seconds=time.perf_counter()-start,peak_rss_bytes=peak,temporary_bytes=disk_peak,resource_stop=stop,
            exit_code=p.returncode,sampling_ms=200)
        if (root/'result.json').is_file():result['evaluation']=json.loads((root/'result.json').read_text(encoding='utf-8'))
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--worker',help=argparse.SUPPRESS)
    parser.add_argument('--profile',default='medium');parser.add_argument('--skus',type=int,default=12)
    parser.add_argument('--seconds',type=int,default=300);parser.add_argument('--memory-mb',type=int,default=1024)
    parser.add_argument('--output');args=parser.parse_args()
    if args.worker:return worker(args.worker)
    try:
        settings=config(args.profile,args.skus,args.seconds,args.memory_mb);output=output_path(args.output or '')
    except ValueError as exc:parser.error(str(exc))
    result=guarded(settings);output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=result['status'],wall_seconds=result['wall_seconds'],peak_rss_bytes=result['peak_rss_bytes'],
        decision=result.get('evaluation',{}).get('selection'),output=str(output)),allow_nan=False))
    if result['status']!='COMPLETED':raise SystemExit(1)


if __name__=='__main__':main()
