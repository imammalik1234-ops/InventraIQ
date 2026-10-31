"""Authorized T14 observation reads. Never POS revenue, inventory or model input fitting."""
from flask import current_app, request
from sqlalchemy import select, func, case
from database import db
from authz import resolve_scope
from operational_services import filters, _read_snapshot
from security import MutationError
from ingestion.schema import SalesObservation as O, ImportDataset as D, ImportRun as R
from ingestion.synthetic import SOURCE
from query_paging import page


def read(values=None, *, capability='reports.read', detail=True, aggregate=True, grouping=False):
    values = request.args if values is None else values
    scope = resolve_scope(capability, branch_id=values.get('branch_id'))
    # T14 execution/observations remain synthetic-only and disposable-demo gated.
    if getattr(current_app, '_t15_target', None) is None:
        raise MutationError('Historical observations require a prepared synthetic demo target.', 503)
    group=None
    if grouping:
        if hasattr(values,'getlist') and any(len(values.getlist(k))!=1 for k in values):raise MutationError('Repeated reporting filters are not allowed.')
        group=values.get('group_by','day')
        if group not in ('branch','sku','day'):raise MutationError('Invalid historical grouping.')
        values={k:v for k,v in values.items() if k!='group_by'}
    query = filters(values)
    if group:query['group_by']=group
    if query['search'] or query['status'] != 'all' or query.get('snapshot'):
        raise MutationError('Observation queries accept date, scope, order and page filters only.')
    _read_snapshot()
    source = O.join(D, (D.c.source_id == O.c.source_id) & (D.c.active_run_id == O.c.run_id)).join(R, R.c.run_id == O.c.run_id)
    predicate = (O.c.branch_id.in_(scope.branches) & O.c.business_date.between(query['start'], query['end']) &
        (R.c.status == 'published') & (O.c.source_id == SOURCE) & (O.c.provenance == 'synthetic_fixture'))
    revision = [list(row) for row in db.session.execute(select(D.c.source_id, D.c.version, D.c.active_run_id)
        .where(D.c.source_id == SOURCE).order_by(D.c.source_id))]
    result = dict(scope=dict(branch_ids=list(scope.branches), consolidated=scope.consolidated), filters=query,
        provenance='synthetic_fixture', measure_type='daily_units', source_revisions=revision,
        coverage='Units sum observed cells only; missing cells remain unknown. A zero sum does not imply complete observed coverage.',
        monetary='Optional source USD price metadata is not recorded MYR POS revenue; no inferred prices.')
    if detail:
        stmt = select(O.c.observation_id, O.c.source_id, O.c.source_revision, O.c.source_store_id, O.c.source_item_id,
            O.c.branch_id, O.c.sku_id, O.c.business_date, O.c.units, O.c.coverage, O.c.measure_type,
            O.c.provenance, O.c.price_minor, O.c.price_currency).select_from(source).where(predicate)
        if group:
            columns={'branch':[O.c.branch_id], 'sku':[O.c.branch_id,O.c.sku_id], 'day':[O.c.branch_id,O.c.business_date]}[group]
            stmt=select(func.min(O.c.observation_id).label('observation_id'),*columns,
                func.sum(O.c.units).label('units'),func.count().label('cells'),
                func.sum(case((O.c.coverage=='missing',1),else_=0)).label('missing'),
                func.sum(case((O.c.units==0,1),else_=0)).label('observed_zero')).select_from(source).where(predicate).group_by(*columns)
        result.update(page(stmt, stmt.selected_columns.observation_id, scope, query, 'observation-'+(group or 'facts'), revision, flat=not group))
        for row in result['items']:
            if row.get('business_date') is not None:row['business_date'] = row['business_date'].isoformat()
    # Recomputed live from the active publication pointer: corrections switch
    # versions atomically in T14; superseded revisions are never counted twice.
    if not aggregate: return result
    # T14 uniqueness already gives one daily branch/SKU/measure fact per active
    # revision. Avoid grouping every cell then summing the groups a second time.
    aggregate_query = select(func.coalesce(func.sum(O.c.units),0).label('units'), func.count().label('cells'),
        func.coalesce(func.sum(case((O.c.coverage == 'missing', 1), else_=0)),0).label('missing'),
        func.coalesce(func.sum(case((O.c.units == 0, 1), else_=0)),0).label('observed_zero')).select_from(source).where(predicate)
    totals = db.session.execute(aggregate_query).mappings().one()
    result['totals'] = dict(totals)
    return result
