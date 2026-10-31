"""T21 advisory-only scoped recommendations. No business writes or model fitting."""
from datetime import timedelta
from decimal import Decimal
from flask import current_app
from sqlalchemy import select,func
from database import db
from models import Branch,SKU,BranchProduct,Supplier
from authz import resolve_scope,integer_id,CAPABILITIES
from security import MutationError
from expiry import utcnow,zone
from forecasting.artifacts import digest
from forecasting.reader import page as forecast_page,payload
from forecasting.schema import ForecastSnapshot as S
from operational_services import _read_snapshot
from services import recommendation_facts as facts
from services import recommendation_policy as policies
from services.recommendation_math import calculate,number,floor,utc,day_end


def transfers(rows,scope,now):
    if not scope.consolidated:return dict(state='unavailable',reason_codes=['CONSOLIDATED_SCOPE_REQUIRED'],items=[])
    suggestions=[];donor_budget={};receiver_budget={};lot_budget={};withheld=[]
    for r in rows:
        if r.get('calculation') is not None:
            donor_budget[(r['branch_id'],r['sku_id'])]=r['calculation']['potential_excess']
            receiver_budget[(r['branch_id'],r['sku_id'])]=r['calculation']['raw_need']
    for receiver in rows:
        rkey=(receiver['branch_id'],receiver['sku_id']);need=receiver_budget.get(rkey,0)
        if not need:continue
        for donor in rows:
            dkey=(donor['branch_id'],donor['sku_id']);budget=donor_budget.get(dkey,0)
            if donor['branch_id']==receiver['branch_id'] or donor['sku_id']!=receiver['sku_id'] or not budget:continue
            route=policies.route(donor['branch_id'],receiver['branch_id'])
            if route is None:
                withheld.append(dict(source_branch_id=donor['branch_id'],destination_branch_id=receiver['branch_id'],sku_id=donor['sku_id'],reason='ROUTE_NOT_CONFIGURED'));continue
            arrival=now+timedelta(days=route['transit_days'])
            end=day_end(receiver['business_date']+timedelta(days=receiver['calculation']['horizon_days']),receiver['timezone'])
            if arrival>end:
                withheld.append(dict(source_branch_id=donor['branch_id'],destination_branch_id=receiver['branch_id'],sku_id=donor['sku_id'],reason='ARRIVAL_OUTSIDE_HORIZON'));continue
            expiry_policy=receiver['expiry_policy']
            if expiry_policy is None:continue
            allocations=[];remaining=min(budget,need)
            for lot in sorted(donor['calculation']['donor_remaining'],key=facts_order):
                if lot['source']!='on_hand':continue
                expiry=utc(lot['expires_at'])
                # Conservative: do not offer lots that cannot cover receiver's protection horizon.
                if expiry is not None:
                    if expiry<=end:continue
                    life=((expiry-timedelta(seconds=1)).astimezone(zone(receiver['timezone'])).date()-arrival.astimezone(zone(receiver['timezone'])).date()).days
                    if life<expiry_policy['min_remaining_days_on_transfer_receipt']:continue
                elif expiry_policy['require_expiry']:continue
                lkey=(donor['branch_id'],lot['key']);free=lot_budget.get(lkey,floor(number(lot['remaining'])))
                take=min(remaining,free)
                if take:
                    allocations.append(dict(lot_id=lot['key'],quantity=take));lot_budget[lkey]=free-take;remaining-=take
                if not remaining:break
            quantity=sum(a['quantity'] for a in allocations)
            if quantity:
                suggestions.append(dict(type='transfer',state='synthetic_provisional_review',suggested_quantity=quantity,
                    source_branch_id=donor['branch_id'],destination_branch_id=receiver['branch_id'],sku_id=donor['sku_id'],
                    donor_surplus_before=budget,receiver_need_before=need,allocations=allocations,
                    planned_arrival_assumption=arrival.isoformat(),route=route,
                    donor_decision_id=donor['decision_id'],receiver_decision_id=receiver['decision_id'],
                    donor_forecast=donor['forecast'],receiver_forecast=receiver['forecast'],
                    donor_policy=donor['policy'],receiver_policy=receiver['policy'],
                    donor_available_stock=donor['inputs']['available_stock'],receiver_available_stock=receiver['inputs']['available_stock'],
                    receiver_expected_inbound=receiver['inputs']['expected_eligible_units'],
                    receiver_shortage_before_arrival=any(number(d['unmet'])>0 and day_end(__import__('datetime').date.fromisoformat(d['date']),receiver['timezone'])<arrival for d in receiver['calculation']['timeline']),
                    reason_codes=['RECEIVER_NEED_AND_DONOR_SURPLUS','HUMAN_REVIEW_REQUIRED'],
                    formula='min(receiver_remaining_raw_need, donor_remaining_physical_surplus, eligible_lot_units)',
                    advisory_only=True,provenance='synthetic_provisional'))
                donor_budget[dkey]-=quantity;need-=quantity;receiver_budget[rkey]=need
            else:withheld.append(dict(source_branch_id=donor['branch_id'],destination_branch_id=receiver['branch_id'],sku_id=donor['sku_id'],reason='NO_ARRIVAL_AND_HORIZON_ELIGIBLE_LOTS'))
            if not need:break
    return dict(state='synthetic_provisional_review' if suggestions else 'unavailable',
        reason_codes=['NO_RESERVATION_OR_ACTION'] if suggestions else ['NO_SUPPORTED_NEED_SURPLUS_OR_ROUTE'],items=suggestions,withheld=withheld)


def facts_order(lot):
    from services.recommendation_math import order
    return order(lot)


def report(values=None):
    values=values or {}
    if set(values)-{'branch_id','sku_id','page','page_size'} or (hasattr(values,'getlist') and any(len(values.getlist(k))!=1 for k in values)):
        raise MutationError('Unsupported or repeated recommendation filters.')
    scope=resolve_scope('predictions.read',branch_id=values.get('branch_id'))
    _read_snapshot();now=utcnow();caps=CAPABILITIES[scope.role]
    def bound(key,default,maximum):
        v=values.get(key,default)
        if isinstance(v,bool) or not str(v).isascii() or not str(v).isdigit() or not 1<=int(v)<=maximum:raise MutationError('Invalid recommendation '+key+'.')
        return int(v)
    size=bound('page_size',5,10);number_page=bound('page',1,10000)
    selected=select(BranchProduct.sku_id).where(BranchProduct.branch_id.in_(scope.branches)).distinct()
    predicate=SKU.sku_id.in_(selected)
    if values.get('sku_id') is not None:predicate=predicate & (SKU.sku_id==integer_id(values['sku_id']))
    total=db.session.scalar(select(func.count()).select_from(SKU).where(predicate))
    skus=db.session.scalars(select(SKU).where(predicate).order_by(SKU.sku_id).offset((number_page-1)*size).limit(size)).all()
    ids=[s.sku_id for s in skus];sku_map={s.sku_id:s for s in skus}
    branches={b.branch_id:b for b in db.session.scalars(select(Branch).where(Branch.branch_id.in_(scope.branches)).order_by(Branch.branch_id))}
    offers=db.session.scalars(select(BranchProduct).where(BranchProduct.branch_id.in_(scope.branches),BranchProduct.sku_id.in_(ids))
        .order_by(BranchProduct.branch_id,BranchProduct.sku_id)).all()
    holdings=facts.lots(scope.branches,ids,now)
    can_po='purchasing.read' in caps;can_transfer='transfer.read' in caps
    incoming=facts.inbound(scope.branches,ids,now,include_purchasing=can_po,include_transfer=can_transfer,consolidated=scope.consolidated)
    batch=forecast_page({'page_size':1,**({'branch_id':values['branch_id']} if values.get('branch_id') else {})})
    snapshots={}
    if batch['state']=='provisional':
        for stored in db.session.execute(select(S).where(S.c.run_id==batch['run_id'],S.c.branch_id.in_(scope.branches),S.c.sku_id.in_(ids))
            .order_by(S.c.branch_id,S.c.sku_id).limit(31)).mappings():
            try:snapshots[(stored['branch_id'],stored['sku_id'])]=(stored['snapshot_id'],payload(stored))
            except MutationError:snapshots[(stored['branch_id'],stored['sku_id'])]=(None,None)
    rows=[]
    for offer in offers:
        branch=branches[offer.branch_id];sku=sku_map[offer.sku_id];key=(offer.branch_id,offer.sku_id)
        day=now.astimezone(zone(branch.timezone)).date();lots=holdings.get(key,[]);reasons=[]
        policy,issue=policies.policy(*key)
        # Supplier policy includes purchasing routing identity; clerks have no purchasing read grant.
        if not can_po:policy=None;issue='POLICY_VISIBILITY_RESTRICTED'
        if issue:reasons.append(issue)
        snapshot_id,forecast=snapshots.get(key,(None,None))
        if forecast is None or forecast['eligibility']!='eligible' or forecast['predictions'] is None:reasons.append('FORECAST_'+('STALE' if batch['state']=='stale' else 'UNAVAILABLE'))
        if forecast is not None and (forecast['provenance']!='synthetic_fixture' or forecast['as_of']!=batch['as_of']):reasons.append('FORECAST_PROVENANCE_OR_ORIGIN_INVALID')
        if batch['run_id'] is not None and batch['qualification']!='provisional_synthetic':reasons.append('FORECAST_QUALIFICATION_UNSUPPORTED')
        if batch['as_of'] is not None and batch['as_of']!=day.isoformat():reasons.append('FORECAST_OPERATIONAL_DATE_MISMATCH')
        from services.recommendation_demo import environment,SCENARIO
        if not environment(current_app) or batch.get('manifest_hash')!=SCENARIO['manifest_hash']:reasons.append('SYNTHETIC_REVIEW_ENVIRONMENT_REQUIRED')
        if not sku.active or not offer.enabled:reasons.append('SKU_NOT_ACTIVE_OR_OFFERED')
        if not lots:reasons.append('STOCK_NOT_RECONCILED')
        if any(not l['verified'] for l in lots):reasons.append('LOT_OR_OPENING_REVIEW_REQUIRED')
        inbound=incoming[key]
        if not inbound['complete']:reasons.append('INBOUND_VISIBILITY_INCOMPLETE')
        supplier=db.session.get(Supplier,policy['supplier_id']) if policy else None
        if policy and (supplier is None or not supplier.active):reasons.append('SUPPLIER_NOT_ACTIVE')
        expected=[]
        if policy:
            expected,inbound_issues=facts.expected_inputs(inbound,day,policy['lead_days']+policy['review_days'],branch.timezone,now)
            reasons.extend(inbound_issues)
        expiry_policy=next((l['expiry_policy'] for l in lots if l['expiry_policy'] is not None),None)
        if expiry_policy is None:reasons.append('EXPIRY_POLICY_UNAVAILABLE')
        reasons=sorted(set(reasons));calculation=None
        if not reasons:
            try:calculation=calculate([l for l in lots if l['available']>0],forecast['predictions'],day.isoformat(),branch.timezone,policy,expected)
            except MutationError:reasons.append('CALCULATION_INPUT_INVALID_OR_OUT_OF_BOUNDS')
        input_data=dict(lots=lots,available_stock=sum(l['available'] for l in lots) if lots else None,
            physical_stock=sum(l['on_hand'] for l in lots) if lots else None,reserved=sum(l['reserved'] for l in lots) if lots else None,
            quarantined=sum(l['quarantined'] for l in lots) if lots else None,
            purchase_orders=inbound['purchase_orders'] if can_po else None,transfers=inbound['transfers'] if can_transfer else None,
            inbound_complete=inbound['complete'],expected_eligible_inbound=expected if inbound['complete'] else None,
            expected_eligible_units=sum(e['available'] for e in expected) if inbound['complete'] and not any(r.startswith(('PO_','TRANSFER_')) for r in reasons) else None,
            daily_forecast=forecast['predictions'] if forecast and forecast['eligibility']=='eligible' else None,
            supplier=dict(supplier_id=supplier.supplier_id,version=supplier.version) if supplier else None)
        row=dict(branch_id=branch.branch_id,branch_code=branch.code,sku_id=sku.sku_id,sku_code=sku.code,
            business_date=day,timezone=branch.timezone,offer_revision=offer.version,sku_revision=sku.version,
            forecast=dict(run_id=batch['run_id'],snapshot_id=snapshot_id,model_id=batch.get('model_id'),
                model_version=batch.get('model_version'),qualification=batch['qualification'],freshness=batch['freshness'],as_of=batch['as_of'],
                coverage=forecast['coverage'] if forecast else None,fallback_reason=forecast['fallback_reason'] if forecast else None,
                training_identity=forecast['training_identity'] if forecast else None),
            inputs=input_data,policy=policy,expiry_policy=expiry_policy,calculation=calculation,
            reason_codes=reasons,advisory_only=True,production_numeric_enabled=False,
            assumptions=['Forecast is additional unreserved demand; reservations are excluded from free supply.',
                'Expected arrivals are conditional and usable from the next business day, not guaranteed physical stock.',
                'Daily FEFO projection evaluates expiry conservatively at business-day end.',
                'Legacy 20% buffer is historical only; it is not this policy.',
                'Provisional synthetic forecasts are not production-qualified.'])
        status='synthetic_provisional_review' if calculation is not None else 'forecast_unavailable' if any(r.startswith('FORECAST_') for r in reasons) else 'needs_configuration'
        row['recommendations']=[dict(type='replenishment',state=status,suggested_quantity=calculation['purchase_quantity'] if calculation else None),
            dict(type='potential_excess',state=status,suggested_quantity=calculation['potential_excess'] if calculation else None),
            dict(type='expiry_risk',state='informational',suggested_quantity=None,
                observed_expired_units=sum(l['on_hand'] for l in lots if l['eligibility']=='expired') if lots else None,
                projected_unused_expiry=calculation['projected_expiry'] if calculation else None)]
        # Dates are semantic identities; wall time is deliberately outside the decision hash.
        row['business_date']=day.isoformat();row['decision_id']=digest(row);row['business_date']=day
        rows.append(row)
    transfer_report=transfers(rows,scope,now)
    for row in rows:row['business_date']=row['business_date'].isoformat()
    return dict(version=policies.VERSION,approval_reference=policies.APPROVAL,production_numeric_enabled=False,
        provenance='synthetic_provisional_advisory',evaluated_at_utc=now.isoformat(),scope=dict(branches=list(scope.branches),consolidated=scope.consolidated),
        pagination=dict(page=number_page,page_size=size,total_skus=total,pages=(total+size-1)//size),items=rows,
        transfer_recommendations=transfer_report,actions_enabled=False,
        decision_set_id=digest(dict(items=[r['decision_id'] for r in rows],transfers=transfer_report)))
