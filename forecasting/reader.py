"""Bounded, current-permission snapshot reads. No training-history or fit imports."""
from datetime import datetime,timezone,timedelta
import json
import math
from flask import current_app
from sqlalchemy import select,func
from authz import resolve_scope,integer_id
from database import db
from security import MutationError
from forecasting.artifacts import validate,validate_run,digest
from forecasting.schema import ForecastRun as R,ForecastPublication as P,ForecastSnapshot as S
from forecasting.target import enabled
from forecasting.data import source_contract


def utc(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)


def payload(row):
    try:
        if len(row['payload_json'])>16384:raise ValueError()
        value=json.loads(row['payload_json']);pred=value['predictions']
        if (digest(value)!=row['payload_hash'] or value['schema']!='inventraiq-snapshot-v1' or
            value['branch_id']!=row['branch_id'] or value['sku_id']!=row['sku_id'] or value['horizon']!=7):raise ValueError()
        if pred is not None and (len(pred)!=7 or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in pred)
            or not math.isclose(sum(pred),value['predicted_demand'],abs_tol=1e-8)):raise ValueError()
        return value
    except (ValueError,TypeError,KeyError):raise MutationError('Forecast snapshot integrity check failed.',409) from None


def page(values=None):
    values=values or {}
    allowed={'branch_id','sku_id','page','page_size','run_id'}
    if set(values)-allowed or (hasattr(values,'getlist') and any(len(values.getlist(k))!=1 for k in values)):
        raise MutationError('Unsupported or repeated snapshot filters.')
    scope=resolve_scope('predictions.read',branch_id=values.get('branch_id'))
    def bounded(name,default,maximum):
        raw=values.get(name,default)
        if isinstance(raw,bool) or not str(raw).isascii() or not str(raw).isdigit() or not 1<=int(raw)<=maximum:
            raise MutationError('Invalid snapshot '+name+'.')
        return int(raw)
    number=bounded('page',1,10000);size=bounded('page_size',25,100)
    sku=integer_id(values['sku_id']) if 'sku_id' in values else None
    pin=integer_id(values['run_id']) if 'run_id' in values else None
    output=dict(state='missing',reason='No published synthetic snapshot batch.',qualification='unqualified',freshness='unavailable',
        run_id=None,publication_version=None,as_of=None,generated_at=None,coverage=None,items=[],
        pagination=dict(page=number,page_size=size,total=0,pages=0),
        provenance='synthetic_fixture',production_publication=False)
    if not enabled():
        output.update(state='unqualified',reason='Production/real-data forecasting publication remains gated.')
        return output
    from operational_services import _read_snapshot
    _read_snapshot()
    run=db.session.execute(select(R,P.c.version.label('publication_version')).join(P,P.c.active_run_id==R.c.run_id)
        .where(P.c.source_id=='inventraiq-generated-history-v1',R.c.status=='published')).mappings().first()
    if not run:return output
    if pin is not None and pin!=run['run_id']:raise MutationError('Forecast batch changed; restart with the current version.',409)
    source,manifest=source_contract()
    output.update(run_id=run['run_id'],publication_version=run['publication_version'],as_of=run['as_of'].isoformat(),
        generated_at=run['generated_at_utc'],model_id=run['model_id'],model_version=run['model_version'],
        source_revision=run['source_revision'],manifest_hash=run['manifest_hash'],artifact_hash=run['artifact_hash'],
        horizon_days=7,qualification=run['qualification'],state='provisional',freshness='fresh',
        reason='Provisional synthetic demonstration; T19 locked-test qualification is incomplete.')
    if source['run_id']!=run['source_run_id'] or source['manifest_hash']!=run['manifest_hash']:
        output.update(state='unavailable',freshness='invalidated',reason='Source revision changed; regenerate a compatible batch.')
        return output
    try:
        validate(run['artifact_json'],run['artifact_hash'])
        validate_run(run)
    except MutationError:
        output.update(state='unavailable',freshness='invalidated',reason='Forecast artifact incompatible or damaged.')
        return output
    wall=datetime.now(timezone.utc)
    # Live generation age and historical-source age are separate clocks. Clients cannot set either.
    if (utc(run['generated_at_utc'])>wall+timedelta(minutes=5) or
        wall-utc(run['generated_at_utc'])>timedelta(hours=24) or
        (datetime.fromisoformat(manifest['coverage']['last_date']).date()-run['as_of']).days>1):
        output.update(state='stale',freshness='stale',reason='Snapshot exceeds 24-hour generation age or one-day historical-source lag.')
    predicates=[S.c.run_id==run['run_id'],S.c.branch_id.in_(scope.branches)]
    if sku is not None:predicates.append(S.c.sku_id==sku)
    total=db.session.scalar(select(func.count()).select_from(S).where(*predicates))
    output['pagination'].update(total=total,pages=(total+size-1)//size)
    rows=db.session.execute(select(S).where(*predicates).order_by(S.c.branch_id,S.c.sku_id)
        .offset((number-1)*size).limit(size)).mappings()
    observed=days=eligible=0
    try:
        for row in rows:
            data=payload(row);coverage=data['coverage'];days+=coverage['days'];observed+=coverage['observed']
            available=output['state']=='provisional' and data['eligibility']=='eligible'
            eligible+=available
            forecast=dict(status='provisional' if available else 'stale' if output['state']=='stale' else 'unavailable',
                reason=output['reason'] if available or output['state']=='stale' else data['fallback_reason'],
                predicted_demand=data['predicted_demand'] if available else None,horizon_days=7,mae=None,
                history_days=coverage['days'],selling_days=None,method=run['model_id'])
            output['items'].append(dict(branch_id=row['branch_id'],sku_id=row['sku_id'],sku_code=data['sku_code'],
                run_id=run['run_id'],snapshot_id=row['snapshot_id'],as_of=output['as_of'],generated_at=output['generated_at'],
                model_id=run['model_id'],model_version=run['model_version'],qualification=run['qualification'],
                freshness=output['freshness'],provenance='synthetic_fixture',coverage=coverage,
                eligibility=data['eligibility'],fallback_reason=data['fallback_reason'],
                predictions=data['predictions'] if available else None,forecast=forecast))
    except MutationError:
        output.update(state='unavailable',freshness='invalidated',reason='Snapshot integrity validation failed.',items=[])
        return output
    output['coverage']=dict(page_series=len(output['items']),page_eligible_series=eligible,
        observed_days=observed,calendar_days=days,scope_series=total)
    return output
