"""T18 bounded, authorized daily observed-unit histories. No fitting or imputation."""
from datetime import timedelta
import hashlib
import json
from flask import current_app
from sqlalchemy import select
from werkzeug.exceptions import NotFound
from authz import resolve_scope, integer_id
from database import db
from models import Branch, SKU
from security import MutationError
from ingestion.schema import SalesObservation as O, ImportDataset as D, ImportRun as R
from ingestion.synthetic import SOURCE, VERSION, encoded
from forecasting.clock import iso_date, clocks

MAX_DAYS=1096
VERSION_ID='inventraiq-forecast-data-v1'


def digest(value):return hashlib.sha256(encoded(value)).hexdigest()


def source_contract():
    """Only an attested synthetic app can read an active published T14 revision."""
    if getattr(current_app,'_t15_target',None) is None:
        raise MutationError('Forecast history requires a prepared synthetic demo target.',503)
    rows=db.session.execute(select(D.c.version,R.c.run_id,R.c.source_revision,R.c.manifest_hash,R.c.manifest_json)
        .join(R,D.c.active_run_id==R.c.run_id).where(D.c.source_id==SOURCE,R.c.source_id==SOURCE,
            R.c.status=='published').limit(2)).mappings().all()
    if not rows:raise MutationError('No published synthetic forecast history.',503)
    if len(rows)!=1:raise MutationError('Conflicting source publication.',409)
    row=dict(rows[0])
    try:
        if len(row['manifest_json'])>64000:raise ValueError()
        manifest=json.loads(row.pop('manifest_json'))
        if (manifest['schema_version']!=VERSION or manifest['source_id']!=SOURCE or
            manifest['provenance']!='synthetic_fixture' or manifest['source_revision']!=row['source_revision'] or
            digest(manifest)!=row['manifest_hash']):raise ValueError()
        start=iso_date(manifest['coverage']['first_date']);end=iso_date(manifest['coverage']['last_date'])
        if not 0<=(end-start).days<MAX_DAYS:raise ValueError()
    except (KeyError,TypeError,ValueError):raise MutationError('Invalid source revision contract.',409) from None
    return row,manifest


def prepare_history(contract, facts, *, exposure=None):
    """Pure adapter for validated source rows. Never queries, selects SKUs or fits.

    Exposure is server-reviewed evidence, not inferred from first/last sales.
    HTTP callers cannot supply it. Only information known by training_end applies.
    """
    from expiry import zone
    zone(contract['timezone'])
    start=iso_date(contract['start']);as_of=iso_date(contract['as_of'])
    train_end=iso_date(contract['training_end'])
    source_start=iso_date(contract['source_start']);source_end=iso_date(contract['source_end'])
    if not start<=train_end<=as_of or not 1<=(as_of-start).days+1<=MAX_DAYS:
        raise MutationError('Invalid or oversized chronological window.')
    if contract.get('provenance')!='synthetic_fixture' or contract.get('source_id')!=SOURCE:
        raise MutationError('Unapproved forecasting source.')
    launch=end=None;window_reference=known_on=None
    if exposure is not None:
        if not isinstance(exposure,dict) or set(exposure)!={'start','end','known_on','reference'}:
            raise MutationError('Invalid exposure evidence.')
        known=iso_date(exposure['known_on']);first=iso_date(exposure['start'])
        last=iso_date(exposure['end']) if exposure['end'] is not None else None
        reference=exposure['reference']
        if last is not None and last<first:raise MutationError('Invalid exposure window.')
        if not isinstance(reference,str) or not 1<=len(reference)<=160:raise MutationError('Exposure reference required.')
        if known<=train_end:launch,end,window_reference,known_on=first,last,reference,known
    by_day={}
    for index,row in enumerate(facts):
        if index>=MAX_DAYS:raise MutationError('Series row bound exceeded.')
        day=iso_date(row['business_date'])
        if day>as_of or day<start:continue
        if day in by_day:raise MutationError('Duplicate or conflicting daily observation.',409)
        if (row.get('branch_id')!=contract['branch_id'] or row.get('sku_id')!=contract['sku_id'] or
            row.get('source_revision')!=contract['source_revision'] or row.get('provenance')!='synthetic_fixture'):
            raise MutationError('Conflicting series identity.',409)
        units=row['units'];coverage=row['coverage']
        if not ((coverage=='observed' and type(units) is int and 0<=units<=2147483647) or
                (coverage=='missing' and units is None)):
            raise MutationError('Invalid coverage or unit value.',409)
        if not source_start<=day<=source_end:raise MutationError('Observation outside source coverage.',409)
        by_day[day]=row
    days=[]
    for i in range((as_of-start).days+1):
        day=start+timedelta(days=i);row=by_day.get(day)
        if launch is not None and day<launch:state='not_yet_launched'
        elif end is not None and day>end:state='outside_exposure'
        elif not source_start<=day<=source_end:state='outside_source_coverage'
        elif row is None:state='missing_record'
        else:state=row['coverage']
        observed=row is not None and row['coverage']=='observed'
        if state in ('not_yet_launched','outside_exposure') and observed and row['units']!=0:
            raise MutationError('Observed units conflict with exposure evidence.',409)
        usable=state=='observed'
        days.append(dict(date=day.isoformat(),units=row['units'] if observed else None,coverage=state,
            record_present=row is not None,observed=observed,recorded_zero=observed and row['units']==0,
            observed_zero=usable and row['units']==0,
            exposure=None if launch is None else launch<=day and (end is None or day<=end),
            stock_available=None,training_eligible=usable and day<=train_end,
            evaluation_eligible=usable and day>train_end))
    # Separate labels: training values never include evaluation units.
    training=[dict(date=d['date'],units=d['units'] if d['training_eligible'] else None,
        eligible=d['training_eligible'],coverage=d['coverage']) for d in days if d['date']<=train_end.isoformat()]
    evaluation=[dict(date=d['date'],units=d['units'] if d['evaluation_eligible'] else None,
        eligible=d['evaluation_eligible'],coverage=d['coverage']) for d in days if d['date']>train_end.isoformat()]
    identity=dict(contract,exposure=dict(start=launch.isoformat() if launch else None,
        end=end.isoformat() if end else None,reference=window_reference,
        known_on=known_on.isoformat() if known_on else None))
    return dict(contract=identity,input_identity=digest(identity),history_identity=digest(dict(contract=identity,days=days)),
        training_identity=digest(dict(source=contract['source_id'],revision=contract['source_revision'],
            manifest_hash=contract['manifest_hash'],branch_id=contract['branch_id'],sku_id=contract['sku_id'],
            training_end=train_end.isoformat(),exposure=identity['exposure'],training=training)),
        days=days,training=training,evaluation=evaluation,
        coverage=dict(days=len(days),observed=sum(d['observed'] for d in days),
            observed_zero=sum(d['observed_zero'] for d in days),
            eligible_training=sum(d['training_eligible'] for d in days),
            explicit_missing=sum(d['coverage']=='missing' for d in days),
            absent=sum(d['coverage']=='missing_record' for d in days)),
        limitations=['Observed sales units, not latent demand; stockout censoring unknown.',
            'No price, promotion, current catalog attributes or future-derived features.',
            'Frozen-revision retrospective evaluation, not a claim of historical publication-time availability.',
            'Exposure unknown unless source-bound server evidence was known by training cutoff.'])


def load_series(*,branch_id,sku_id,as_of,start=None,training_end=None,source_revision=None,manifest_hash=None):
    # Authority precedes any source/fact retrieval. Consolidation never merges series.
    scope=resolve_scope('predictions.read',branch_id=branch_id)
    if len(scope.branches)!=1:raise MutationError('Choose one authorized branch.')
    sku_id=integer_id(sku_id)
    from operational_services import _read_snapshot
    _read_snapshot()
    branch=db.session.scalar(select(Branch).where(Branch.branch_id==scope.branches[0]))
    row,manifest=source_contract()
    if ((source_revision is not None and source_revision!=row['source_revision']) or
        (manifest_hash is not None and manifest_hash!=row['manifest_hash'])):
        raise MutationError('Source revision changed; choose an explicit current revision.',409)
    stores=[b['store_id'] for b in manifest['branches'] if b['branch_code']==branch.code]
    sku=db.session.scalar(select(SKU).where(SKU.sku_id==sku_id))
    items=[s['item_id'] for s in manifest['skus'] if sku is not None and s['sku_code']==sku.code]
    if len(stores)!=1 or len(items)!=1:raise NotFound('Resource not found.')
    as_of=iso_date(as_of);start=iso_date(manifest['coverage']['first_date'] if start is None else start)
    train_end=iso_date(as_of if training_end is None else training_end)
    if not start<=train_end<=as_of or not 1<=(as_of-start).days+1<=MAX_DAYS:
        raise MutationError('Invalid or oversized chronological window.')
    # Read one daily identity, with an extra row to fail closed on corrupt duplicates.
    facts=db.session.execute(select(O.c.business_date,O.c.units,O.c.coverage,O.c.branch_id,O.c.sku_id,
        O.c.source_revision,O.c.provenance).where(O.c.run_id==row['run_id'],O.c.source_id==SOURCE,
        O.c.source_revision==row['source_revision'],O.c.branch_id==branch.branch_id,O.c.sku_id==sku_id,
        O.c.source_store_id==stores[0],O.c.source_item_id==items[0],O.c.business_date.between(start,as_of))
        .order_by(O.c.business_date,O.c.observation_id).limit(MAX_DAYS+1)).mappings().all()
    if len(facts)>MAX_DAYS:raise MutationError('Series row bound exceeded.',409)
    contract=dict(version=VERSION_ID,branch_id=branch.branch_id,branch_code=branch.code,sku_id=sku_id,sku_code=sku.code,
        timezone=branch.timezone,source_id=SOURCE,source_revision=row['source_revision'],manifest_hash=row['manifest_hash'],
        provenance='synthetic_fixture',measure_type='observed_daily_units',clock='historical_dataset',
        as_of=as_of.isoformat(),start=start.isoformat(),training_end=train_end.isoformat(),
        forecast_origin=train_end.isoformat(),evaluation_cutoff=as_of.isoformat(),
        source_start=manifest['coverage']['first_date'],source_end=manifest['coverage']['last_date'],
        selection='Pinned T14 synthetic manifest; no outcome-based selection or normalization',
        availability='Frozen source revision; business-date cutoff only, stock availability unknown')
    exposure=current_app.config.get('FORECAST_EXPOSURES',{}).get((row['manifest_hash'],branch.branch_id,sku_id))
    return prepare_history(contract,facts,exposure=exposure)


def view_context(branch_id=None,as_of=None):
    scope=resolve_scope('predictions.read',branch_id=branch_id)
    from operational_services import _read_snapshot
    _read_snapshot()  # Clock metadata and an optional page preview share one publication.
    branches=db.session.scalars(select(Branch).where(Branch.branch_id.in_(scope.branches)).order_by(Branch.branch_id)).all()
    value=clocks(branches)
    value.update(historical_as_of=None,source_revision=None,provenance=None)
    if getattr(current_app,'_t15_target',None) is not None:
        row,manifest=source_contract()
        selected=iso_date(manifest['coverage']['last_date'] if as_of is None else as_of)
        first=iso_date(manifest['coverage']['first_date'])
        if not 0<=(selected-first).days<MAX_DAYS:raise MutationError('Invalid or oversized historical date.')
        value.update(historical_as_of=selected.isoformat(),source_coverage_end=manifest['coverage']['last_date'],
            source_revision=row['source_revision'],provenance='synthetic_fixture')
    elif as_of is not None:raise MutationError('Historical clock requires a prepared synthetic source.',503)
    return value
