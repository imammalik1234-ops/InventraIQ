"""Explicit offline, bounded synthetic publication. No model fitting or scheduling."""
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import math
import time
from flask import g,request
from sqlalchemy import select,update,func
from authz import resolve_scope
from database import db
from models import SKU,Branch
from security import MutationError
from forecasting.artifacts import trusted,canonical,digest,validate,validate_run
from forecasting.clock import iso_date
from forecasting.data import load_series,source_contract
from forecasting.schema import ForecastRun as R,ForecastPublication as P,ForecastSnapshot as S,ForecastEvent as E
from forecasting.target import require_target
from ingestion.synthetic import SOURCE


def now():return datetime.now(timezone.utc).isoformat(timespec='microseconds')


@contextmanager
def offline(app,uid=901):
    """Server-owned job context; HTTP input cannot set this capability."""
    from simulation.runtime import actor
    with actor(app,uid):
        g.forecast_offline=True
        yield


def authority():
    if not getattr(g,'forecast_offline',False):raise MutationError('Offline forecast job context required.',403)
    scope=resolve_scope('forecasts.publish',consolidated=True)
    require_target()
    return scope


def event(run_id,actor,event_name,reason):
    db.session.execute(E.insert().values(run_id=run_id,actor_id=actor,event=event_name,at_utc=now(),reason=reason))


def result(run):return dict(run_id=run['run_id'],status=run['status'],batch_hash=run['batch_hash'],
    expected_count=run['expected_count'],eligible_count=run['eligible_count'],qualification=run['qualification'])


def snapshot(history):
    from forecasting.candidates import baseline,CANDIDATES
    values=[r['units'] for r in history['training']];known=[v for v in values if v is not None]
    eligible=len(values)>=56 and len(known)>=28
    predicted,reason=baseline(next(c for c in CANDIDATES if c.name=='seasonal_mean2'),values,7) if eligible else (None,'insufficient_history')
    if predicted is not None and any(not math.isfinite(v) or v<0 for v in predicted):raise MutationError('Invalid forecast output.',409)
    return dict(schema='inventraiq-snapshot-v1',branch_id=history['contract']['branch_id'],sku_id=history['contract']['sku_id'],
        sku_code=history['contract']['sku_code'],timezone=history['contract']['timezone'],
        as_of=history['contract']['as_of'],horizon=7,predictions=predicted,predicted_demand=sum(predicted) if predicted else None,
        eligibility='eligible' if eligible and predicted is not None else 'unavailable',fallback_reason=reason,
        coverage=history['coverage'],history_identity=history['history_identity'],training_identity=history['training_identity'],
        segment='sparse_or_intermittent' if known and sum(v>0 for v in known)/len(known)<.2 else 'regular',
        category='unavailable_source_category',provenance='synthetic_fixture')


def publish(*,request_key,as_of,skus=12):
    """New run -> generate bounded rows -> one atomic pointer CAS. Same key is a retry."""
    scope=authority()
    if not isinstance(request_key,str) or not 1<=len(request_key)<=100 or not request_key.isascii() or not request_key.replace('-','').replace('_','').isalnum():
        raise MutationError('Use a bounded ASCII job key.')
    if type(skus) is not int or not 1<=skus<=120:raise MutationError('Use 1..120 manifest SKUs.')
    origin=iso_date(as_of);source,manifest=source_contract();artifact=trusted()
    if not iso_date(manifest['coverage']['first_date'])<=origin<=iso_date(manifest['coverage']['last_date']):
        raise MutationError('Origin must lie in the approved historical source window.')
    codes=[item['sku_code'] for item in manifest['skus'][:skus]]
    sku_ids=list(db.session.scalars(select(SKU.sku_id).where(SKU.code.in_(codes)).order_by(SKU.sku_id)))
    if len(sku_ids)!=len(codes):raise MutationError('Source SKU mapping incomplete.',409)
    branches=list(db.session.scalars(select(Branch.branch_id).where(Branch.branch_id.in_(scope.branches),
        Branch.code.in_([b['branch_code'] for b in manifest['branches']])).order_by(Branch.branch_id)))
    if not branches:raise MutationError('No active authorized source branches.',409)
    config=dict(as_of=origin.isoformat(),skus=sku_ids,branches=branches,artifact_hash=digest(artifact),
        manifest_hash=source['manifest_hash'],source_revision=source['source_revision'],source_run_id=source['run_id'])
    payload_hash=digest(config)
    old=db.session.execute(select(R).where(R.c.request_key==request_key)).mappings().first()
    if old:
        if old['payload_hash']!=payload_hash:raise MutationError('Job key conflicts with prior configuration.',409)
        if old['status']=='published':return result(old)
        raise MutationError('Job exists; inspect or recover it and use a new key.',409)
    if db.session.scalar(select(func.count()).select_from(R))>=365:
        raise MutationError('Synthetic forecast journal capacity reached; reviewed archival is required.',409)
    base=db.session.execute(select(P).where(P.c.source_id==SOURCE)).mappings().one()
    row=dict(request_key=request_key,payload_hash=payload_hash,actor_id=scope.actor_id,source_run_id=source['run_id'],
        source_id=SOURCE,source_revision=source['source_revision'],manifest_hash=source['manifest_hash'],as_of=origin,horizon=7,
        model_id=artifact['model_id'],model_version=artifact['model_version'],qualification='provisional_synthetic',
        artifact_json=canonical(artifact),artifact_hash=digest(artifact),configuration_json=canonical(config),
        base_version=base['version'],status='running',expected_count=len(branches)*len(sku_ids),generated_at_utc=now())
    try:
        run_id=db.session.execute(R.insert().values(**row)).inserted_primary_key[0]
        event(run_id,scope.actor_id,'started','Explicit synthetic offline publication')
        db.session.commit()
    except Exception:
        db.session.rollback();raise
    started=time.monotonic()
    try:
        rows=[]
        for branch in branches:
            for sku in sku_ids:
                if time.monotonic()-started>120:raise MutationError('Forecast generation time limit reached.',409)
                history=load_series(branch_id=branch,sku_id=sku,as_of=origin,source_revision=source['source_revision'],manifest_hash=source['manifest_hash'])
                value=snapshot(history)
                rows.append(dict(run_id=run_id,branch_id=branch,sku_id=sku,payload_json=canonical(value),payload_hash=digest(value)))
        # Release the read snapshot and recheck authority/source immediately before writes.
        db.session.rollback();scope=authority();current,_=source_contract()
        validate(row['artifact_json'],row['artifact_hash'])
        if current['run_id']!=source['run_id'] or any(b not in scope.branches for b in branches):
            raise MutationError('Source or authorization changed during generation.',409)
        if len(rows)!=row['expected_count']:raise MutationError('Incomplete forecast batch.',409)
        db.session.execute(S.insert(),rows)
        eligible=sum(json.loads(r['payload_json'])['eligibility']=='eligible' for r in rows)
        batch_hash=digest([(r['branch_id'],r['sku_id'],r['payload_hash']) for r in rows])
        changed=db.session.execute(update(R).where(R.c.run_id==run_id,R.c.status=='running').values(
            status='published',eligible_count=eligible,batch_hash=batch_hash,published_at_utc=now())).rowcount
        if changed!=1:raise MutationError('Run was interrupted or recovered.',409)
        changed=db.session.execute(update(P).where(P.c.source_id==SOURCE,P.c.version==base['version']).values(
            active_run_id=run_id,version=base['version']+1)).rowcount
        if changed!=1:raise MutationError('Another batch published first.',409)
        event(run_id,scope.actor_id,'published','Atomic synthetic batch publication')
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        # Do not disclose SQL, paths, history rows or credentials in audit failures.
        try:
            db.session.execute(update(R).where(R.c.run_id==run_id,R.c.status=='running').values(status='failed',error_type=type(exc).__name__))
            event(run_id,scope.actor_id,'failed',type(exc).__name__);db.session.commit()
        except Exception:db.session.rollback()
        raise
    return result(db.session.execute(select(R).where(R.c.run_id==run_id)).mappings().one())


def recover(run_id,reason):
    scope=authority()
    from authz import integer_id
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=240:raise MutationError('Recovery reason required.')
    changed=db.session.execute(update(R).where(R.c.run_id==integer_id(run_id),R.c.status=='running').values(status='failed',error_type='Interrupted')).rowcount
    if changed!=1:raise MutationError('Only an interrupted running job can be recovered.',409)
    event(run_id,scope.actor_id,'recovered',reason.strip());db.session.commit()


def rollback(run_id,expected_version,reason):
    scope=authority()
    from authz import integer_id
    if type(expected_version) is not int or expected_version<1:raise MutationError('Expected publication version required.')
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=240:raise MutationError('Rollback reason required.')
    old=db.session.execute(select(R).where(R.c.run_id==integer_id(run_id),R.c.status=='published')).mappings().first()
    source,_=source_contract()
    if not old or old['source_run_id']!=source['run_id']:raise MutationError('Rollback requires a compatible current-source batch.',409)
    validate_run(old)
    changed=db.session.execute(update(P).where(P.c.source_id==SOURCE,P.c.version==expected_version).values(
        active_run_id=run_id,version=expected_version+1)).rowcount
    if changed!=1:raise MutationError('Publication version conflict.',409)
    event(run_id,scope.actor_id,'rollback',reason.strip());db.session.commit()
    return result(old)
