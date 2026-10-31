"""Offline retrospective realized-error measurement; scoped immutable metric reads."""
from collections import defaultdict
from datetime import timedelta
import json
from sqlalchemy import select
from authz import resolve_scope,integer_id
from database import db
from forecasting.jobs import authority,now,event
from forecasting.schema import ForecastRun as R,ForecastSnapshot as S,ForecastMonitor as M,ForecastMetric as V
from forecasting.data import source_contract
from forecasting.clock import iso_date
from forecasting.reader import payload,utc
from forecasting.artifacts import canonical,validate_run
from ingestion.schema import SalesObservation as O,ImportRun as I
from security import MutationError


def summarize(n,absolute,actual,bias):
    return dict(scored_days=n,absolute_error=absolute,actual_units=actual,bias_units=bias,
        mae=absolute/n if n else None,wape_percent=100*absolute/actual if actual else None,
        signed_bias_units=bias/n if n else None,signed_bias_percent=100*bias/actual if actual else None)


def evaluate(run_id,as_of):
    scope=authority();cutoff=iso_date(as_of);run_id=integer_id(run_id)
    run=db.session.execute(select(R).where(R.c.run_id==run_id,R.c.status=='published')).mappings().first()
    if not run:raise MutationError('Published run required.',404)
    source,manifest=source_contract();evaluated=now()
    if not run['as_of']<cutoff<=min(run['as_of']+timedelta(days=7),iso_date(manifest['coverage']['last_date'])):
        raise MutationError('Monitoring date must follow origin within the observed seven-day horizon.')
    imported=db.session.scalar(select(I.c.published_at_utc).where(I.c.run_id==source['run_id']))
    if imported is None or utc(imported)>utc(evaluated):raise MutationError('Observations were not available at monitoring time.',409)
    old=db.session.execute(select(M).where(M.c.run_id==run_id,M.c.observation_run_id==source['run_id'],M.c.as_of==cutoff)).mappings().first()
    if old:return {**dict(old),'as_of':old['as_of'].isoformat()}
    status='evaluated' if source['run_id']==run['source_run_id'] else 'source_changed'
    validate_run(run)
    groups=defaultdict(lambda:[0,0.,0.,0.]);coverage=defaultdict(lambda:[0,0])
    rows=db.session.execute(select(S).where(S.c.run_id==run_id,S.c.branch_id.in_(scope.branches))
        .order_by(S.c.branch_id,S.c.sku_id).limit(361)).mappings().all()
    if len(rows)>360:raise MutationError('Monitoring series bound exceeded.')
    if status=='evaluated':
        for row in rows:
            data=payload(row);key=(row['branch_id'],data['segment']);coverage[key][0]+=1
            if data['eligibility']!='eligible':continue
            coverage[key][1]+=1
            # No operational sales; only the exact frozen observation revision and known date window.
            observations=db.session.execute(select(O.c.business_date,O.c.units).where(O.c.run_id==source['run_id'],
                O.c.branch_id==row['branch_id'],O.c.sku_id==row['sku_id'],O.c.provenance=='synthetic_fixture',
                O.c.coverage=='observed',O.c.business_date>run['as_of'],O.c.business_date<=cutoff)
                .order_by(O.c.business_date).limit(8)).all()
            if len(observations)>7:raise MutationError('Conflicting observation identities.',409)
            for day,units in observations:
                prediction=data['predictions'][(day-run['as_of']).days-1];values=groups[key]
                values[0]+=1;values[1]+=abs(prediction-units);values[2]+=units;values[3]+=prediction-units
    try:
        monitor_id=db.session.execute(M.insert().values(run_id=run_id,observation_run_id=source['run_id'],as_of=cutoff,
            evaluated_at_utc=evaluated,status=status)).inserted_primary_key[0]
        for key,counts in coverage.items():
            metrics=summarize(*groups[key]);metrics.update(series=counts[0],eligible_series=counts[1],
                category='unavailable_source_category',provenance='synthetic_fixture',comparison='frozen_revision_retrospective')
            db.session.execute(V.insert().values(monitor_id=monitor_id,branch_id=key[0],segment=key[1],metrics_json=canonical(metrics)))
        event(run_id,scope.actor_id,'monitored',status);db.session.commit()
        return dict(monitor_id=monitor_id,run_id=run_id,observation_run_id=source['run_id'],status=status,
            as_of=cutoff.isoformat(),evaluated_at_utc=evaluated)
    except Exception:db.session.rollback();raise


def read(run_id,branch_id=None):
    scope=resolve_scope('predictions.read',branch_id=branch_id)
    from forecasting.target import enabled
    if not enabled():return dict(status='unavailable',metrics=[])
    from operational_services import _read_snapshot
    _read_snapshot()
    source,_=source_contract()
    # Existence check is scoped before exposing run metadata.
    permitted=db.session.scalar(select(S.c.snapshot_id).where(S.c.run_id==integer_id(run_id),S.c.branch_id.in_(scope.branches)).limit(1))
    if permitted is None:raise MutationError('Resource not found.',404)
    monitor=db.session.execute(select(M).where(M.c.run_id==run_id).order_by(M.c.monitor_id.desc()).limit(1)).mappings().first()
    if not monitor:return dict(status='missing',metrics=[])
    if monitor['observation_run_id']!=source['run_id']:return dict(status='invalidated_source_revision',metrics=[])
    metrics=[dict(branch_id=r.branch_id,segment=r.segment,**json.loads(r.metrics_json)) for r in db.session.execute(
        select(V).where(V.c.monitor_id==monitor['monitor_id'],V.c.branch_id.in_(scope.branches)).order_by(V.c.branch_id,V.c.segment).limit(361))]
    return dict(status=monitor['status'],run_id=run_id,monitor_id=monitor['monitor_id'],as_of=monitor['as_of'].isoformat(),
        evaluated_at=monitor['evaluated_at_utc'],metrics=metrics)


def health():
    """HQ offline publication health; failures have classification only, no raw logs."""
    authority()
    from forecasting.schema import ForecastPublication as P
    from ingestion.synthetic import SOURCE
    source,_=source_contract()
    pointer=db.session.execute(select(P).where(P.c.source_id==SOURCE)).mappings().one()
    runs=db.session.execute(select(R.c.run_id,R.c.status,R.c.as_of,R.c.generated_at_utc,R.c.published_at_utc,
        R.c.expected_count,R.c.eligible_count,R.c.error_type,R.c.source_run_id).order_by(R.c.run_id.desc()).limit(20)).mappings()
    return dict(active_run_id=pointer['active_run_id'],publication_version=pointer['version'],
        current_source_run_id=source['run_id'],history_limit=20,
        attempts=[{**dict(r),'as_of':r['as_of'].isoformat(),'source_current':r['source_run_id']==source['run_id']} for r in runs],
        qualification='provisional_synthetic',production_publication=False)
