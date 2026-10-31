"""T11 execution of T10 documents, using T07 for every branch balance effect.
Caller owns commit/rollback. No database migration or source configuration access.
"""
from sqlalchemy import select,update,func,or_
from werkzeug.exceptions import NotFound
from database import db
from models import (StockTransfer,StockTransferAllocation,StockTransferEvent,BranchStock,StockLot,Branch,
    SKU,BranchProduct,InventoryMovement,TransferExecution,TransferTransit,TransferTransitFact,
    TransferDiscrepancy,TransferResolution,TransferReturnLink)
from authz import resolve_scope,integer_id
from expiry import UTC,classify,zone,integer
from security import MutationError
import inventory_ledger as ledger
import catalog_services as catalog
import transfer_services as proposals


def execution(key):return db.session.get(TransferExecution,key,populate_existing=True)


def allocations(row):
    return db.session.scalars(select(StockTransferAllocation).where(StockTransferAllocation.transfer_id==row.transfer_id)
        .order_by(StockTransferAllocation.sku_id,StockTransferAllocation.lot_id)).all()


def _record(operation,a,before,delta,kind,movement=None,**evidence):
    db.session.add(TransferTransitFact(allocation_id=a.allocation_id,operation_id=operation.operation_id,
        movement_id=movement,before_quantity=before,delta=delta,after_quantity=before+delta,kind=kind,evidence=evidence))


def _movement(operation,lot):
    db.session.flush()
    return db.session.scalar(select(InventoryMovement.movement_id).where(InventoryMovement.operation_id==operation.operation_id,InventoryMovement.lot_id==lot))


def _bump(row,version):
    if row.version!=version:raise MutationError('Transfer version conflict.',409)
    if db.session.execute(update(StockTransfer).where(StockTransfer.transfer_id==row.transfer_id,StockTransfer.version==version)
        .values(version=version+1,updated_at_utc=ledger.utcnow().replace(tzinfo=None)).execution_options(synchronize_session=False)).rowcount!=1:
        raise MutationError('Transfer version conflict.',409)
    db.session.refresh(row)


def _state(row):
    active=execution(row.transfer_id)
    pending=db.session.scalar(select(func.sum(TransferTransit.remaining)).join(StockTransferAllocation)
        .where(StockTransferAllocation.transfer_id==row.transfer_id)) or 0
    open_issues=db.session.scalar(select(func.count()).select_from(TransferDiscrepancy).join(StockTransferAllocation)
        .outerjoin(TransferResolution).where(StockTransferAllocation.transfer_id==row.transfer_id,TransferResolution.discrepancy_id.is_(None)))
    received=db.session.scalar(select(func.sum(TransferTransit.received)).join(StockTransferAllocation)
        .where(StockTransferAllocation.transfer_id==row.transfer_id)) or 0
    active.state='Exception' if open_issues else ('Received' if not pending else 'PartiallyReceived' if received else 'Dispatched')
    db.session.flush()


def _finish(scope,row,operation,previous,reason,balances):
    db.session.flush();_state(row)
    return proposals._complete(scope,row,operation,previous,reason,balances)


def _load(key,capability,destination=False):
    scope=resolve_scope(capability,write=True,shared=True)
    column=StockTransfer.destination_branch_id if destination else StockTransfer.source_branch_id
    row=db.session.scalar(select(StockTransfer).where(StockTransfer.transfer_id==integer_id(key),column==scope.branches[0])
        .with_for_update().execution_options(populate_existing=True))
    if row is None:raise NotFound('Resource not found.')
    return scope,row


def _data(values,extra):
    data=proposals._common(values,{'expected_version'}|set(extra))
    data['expected_version']=integer(data['expected_version'],'Expected version',1,ledger.MAX_QUANTITY)
    return data


def _begin(scope,row,kind,data):
    data['transfer_id']=row.transfer_id
    replay,_=ledger.lookup(scope,kind,data,data['request_key'])
    if replay:return None,replay
    if row.version!=data['expected_version']:raise MutationError('Transfer version conflict.',409)
    return ledger.begin(scope,kind,data,data['request_key'],data['business_reference'],ledger.utcnow())


def _rows(data,fields):
    if not isinstance(data['lines'],list) or not 1<=len(data['lines'])<=100:raise MutationError('Supply 1 to 100 allocation lines.')
    result=[]
    for line in data['lines']:
        if not isinstance(line,dict) or set(line)!=set(fields):raise MutationError('Invalid allocation fields.')
        clean={k:integer(v,k,0 if k=='expected_revision' else 1,ledger.MAX_QUANTITY) if k in {'allocation_id','quantity','expected_revision'} else v for k,v in line.items()}
        result.append(clean)
    if len({r['allocation_id'] for r in result})!=len(result):raise MutationError('Duplicate allocation.')
    data['lines']=sorted(result,key=lambda r:r['allocation_id'])
    return {r['allocation_id']:r for r in result}


def dispatch(key,values):
    scope,row=_load(key,'transfer.dispatch');data=_data(values,{'lines'})
    requested=_rows(data,{'allocation_id','expected_revision'})
    operation,replay=_begin(scope,row,'transfer_dispatch',data)
    if replay:return replay
    if row.state!='Approved' or execution(row.transfer_id):raise MutationError('Only an undispatched approved transfer can dispatch.',409)
    items=allocations(row)
    if not items or set(requested)!={a.allocation_id for a in items}:raise MutationError('Dispatch requires every approved allocation.',409)
    dest=proposals._validate_catalog(row.source_branch_id,row.destination_branch_id,
        [dict(sku_id=l.sku_id,quantity=l.quantity) for l in proposals._lines(row)])
    locked={l.sku_id:ledger._locked_stock(scope,l.sku_id) for l in proposals._lines(row)}
    def validate():
        now=ledger.utcnow()
        for a in items:
            state=locked[a.sku_id];filtered=proposals._arrival_eligible(state,dest,row.planned_arrival_utc.replace(tzinfo=UTC),now)
            if a.lot_id not in {s.lot_id for s in filtered[2]} or not ledger._eligibility(state[0],state[1],state[3][a.lot_id],state[4],state[5],now)[0]:
                raise MutationError('Reserved lot or arrival is no longer eligible for dispatch.',409)
    validate();ledger.guard(validate);_bump(row,data['expected_version'])
    db.session.add(TransferExecution(transfer_id=row.transfer_id,state='Dispatched',dispatch_operation_id=operation.operation_id,
        dispatched_at_utc=ledger.utcnow().replace(tzinfo=None)))
    balances=[]
    for a in items:
        stock=next(s for s in locked[a.sku_id][2] if s.lot_id==a.lot_id)
        if stock.version!=requested[a.allocation_id]['expected_revision'] or stock.reserved<a.quantity:raise MutationError('Reserved stock revision conflict.',409)
        balances.append(ledger.apply(operation,stock,-a.quantity,-a.quantity,evidence=dict(transfer_id=row.transfer_id,
            allocation_id=a.allocation_id,approval_movement_id=a.movement_id)))
        movement=_movement(operation,a.lot_id)
        db.session.add(TransferTransit(allocation_id=a.allocation_id,dispatched=a.quantity,remaining=a.quantity,received=0,lost=0))
        _record(operation,a,0,a.quantity,'dispatch',movement)
    return _finish(scope,row,operation,'Approved',data['reason'],balances)


def _issue(a,qty,location,reason,operation):
    db.session.add(TransferDiscrepancy(allocation_id=a.allocation_id,quantity=qty,location=location,reason=reason,operation_id=operation.operation_id))


def _pending_issues(allocation):
    return db.session.scalar(select(func.coalesce(func.sum(TransferDiscrepancy.quantity),0)).outerjoin(TransferResolution)
        .where(TransferDiscrepancy.allocation_id==allocation,TransferDiscrepancy.location=='transit',TransferResolution.discrepancy_id.is_(None)))


def receive(key,values):
    scope,row=_load(key,'transfer.receive',True);data=_data(values,{'lines'})
    requested=_rows(data,{'allocation_id','quantity','expected_revision','disposition'})
    if any(r['disposition'] not in ('saleable','quarantine') for r in requested.values()):raise MutationError('Invalid receipt disposition.')
    operation,replay=_begin(scope,row,'transfer_receive',data)
    if replay:return replay
    active=execution(row.transfer_id)
    if not active or active.state=='Received':raise MutationError('Transfer is not awaiting receipt.',409)
    previous=active.state;items=[a for a in allocations(row) if a.allocation_id in requested]
    if len(items)!=len(requested):raise NotFound('Resource not found.')
    _bump(row,data['expected_version']);balances=[];checks=[]
    # Header lock/CAS serializes absent destination balance creation for this transfer.
    # Other transfers use the existing composite balance uniqueness + guarded update.
    branch=db.session.get(Branch,row.destination_branch_id,with_for_update={'read':True},populate_existing=True)
    for a in items:
        line=requested[a.allocation_id];transit=db.session.get(TransferTransit,a.allocation_id,with_for_update=True,populate_existing=True)
        qty=line['quantity']
        if qty>transit.remaining-_pending_issues(a.allocation_id):raise MutationError('Receipt exceeds undisputed remaining shipment; resolve discrepancy first.',409)
        sku=db.session.get(SKU,a.sku_id,with_for_update={'read':True},populate_existing=True)
        offer=db.session.get(BranchProduct,(row.destination_branch_id,a.sku_id),with_for_update={'read':True},populate_existing=True)
        lot=db.session.get(StockLot,a.lot_id,with_for_update={'read':True},populate_existing=True)
        policy=catalog._effective_policy(sku,lock=True,shared=True)
        def eligible():
            state=classify(policy=policy,expiry_date=lot.expiry_date,expiry_timezone=lot.expiry_timezone,expires_at_utc=lot.expires_at_utc,
                validation_state=lot.validation_state,branch_timezone=branch.timezone,as_of=ledger.utcnow())
            return bool(sku.active and offer and offer.enabled and state['status'] not in ('expired','review') and
                (state['days_remaining'] is None or state['days_remaining']>=policy.min_remaining_days_on_transfer_receipt))
        safe=eligible();q=qty if line['disposition']=='quarantine' or not safe else 0
        stock=db.session.get(BranchStock,(row.destination_branch_id,a.lot_id),with_for_update=True,populate_existing=True)
        if (stock.version if stock else 0)!=line['expected_revision']:raise MutationError('Destination stock revision conflict.',409)
        if stock is None:
            stock=BranchStock(branch_id=row.destination_branch_id,sku_id=a.sku_id,lot_id=a.lot_id,on_hand=0,reserved=0,quarantined_quantity=0,version=1,
                first_received_at_utc=ledger.utcnow().replace(tzinfo=None));db.session.add(stock);db.session.flush()
        ledger.ensure_opened(stock)
        balances.append(ledger.apply(operation,stock,qty,quarantined=q,evidence=dict(transfer_id=row.transfer_id,allocation_id=a.allocation_id,
            original_lot_id=a.lot_id,requested_disposition=line['disposition'],quarantined_quantity=q)))
        before=transit.remaining;transit.remaining-=qty;transit.received+=qty
        _record(operation,a,before,-qty,'receipt',_movement(operation,a.lot_id),quarantined_quantity=q,accepted_quantity=qty-q)
        if q:_issue(a,q,'quarantine','Physical arrival requires quarantine review: '+data['reason'][:170],operation)
        if not q:
            # Capture loop variables in a separate factory to avoid late binding.
            checks.append((sku,offer,lot,policy,branch))
    def revalidate():
        for sku,offer,lot,policy,branch in checks:
            state=classify(policy=policy,expiry_date=lot.expiry_date,expiry_timezone=lot.expiry_timezone,expires_at_utc=lot.expires_at_utc,
                validation_state=lot.validation_state,branch_timezone=branch.timezone,as_of=ledger.utcnow())
            if not sku.active or not offer.enabled or state['status'] in ('expired','review') or (state['days_remaining'] is not None and state['days_remaining']<policy.min_remaining_days_on_transfer_receipt):
                raise MutationError('Arrival eligibility changed before commit; retry for quarantine review.',409)
    ledger.guard(revalidate)
    return _finish(scope,row,operation,previous,data['reason'],balances)


def discrepancy(key,values):
    scope,row=_load(key,'transfer.receive',True);data=_data(values,{'allocation_id','quantity'})
    data['allocation_id']=integer_id(data['allocation_id']);data['quantity']=integer(data['quantity'],'Quantity',1,ledger.MAX_QUANTITY)
    operation,replay=_begin(scope,row,'transfer_discrepancy',data)
    if replay:return replay
    active=execution(row.transfer_id)
    if not active or active.state=='Received':raise MutationError('Shipment is not open.',409)
    a=next((a for a in allocations(row) if a.allocation_id==data['allocation_id']),None)
    if not a:raise NotFound('Resource not found.')
    transit=db.session.get(TransferTransit,a.allocation_id,with_for_update=True,populate_existing=True)
    if data['quantity']>transit.remaining-_pending_issues(a.allocation_id):raise MutationError('Disputed quantity exceeds remaining transit.',409)
    previous=active.state;_bump(row,data['expected_version']);_issue(a,data['quantity'],'transit',data['reason'],operation)
    return _finish(scope,row,operation,previous,data['reason'],[])


def resolve(key,values):
    scope,row=_load(key,'transfer.resolve',True);data=_data(values,{'discrepancy_id','decision'})
    data['discrepancy_id']=integer_id(data['discrepancy_id'])
    if data['decision'] not in ('found','loss','quarantine_acknowledged'):raise MutationError('Invalid resolution decision.')
    operation,replay=_begin(scope,row,'transfer_resolve',data)
    if replay:return replay
    active=execution(row.transfer_id)
    if not active:raise MutationError('Shipment not dispatched.',409)
    issue=db.session.get(TransferDiscrepancy,data['discrepancy_id'])
    a=next((a for a in allocations(row) if issue and a.allocation_id==issue.allocation_id),None)
    if not a:raise NotFound('Resource not found.')
    if db.session.get(TransferResolution,issue.discrepancy_id):raise MutationError('Discrepancy already resolved.',409)
    if (issue.location=='quarantine') != (data['decision']=='quarantine_acknowledged'):
        raise MutationError('Resolution does not match discrepancy location.',409)
    previous=active.state;_bump(row,data['expected_version'])
    if data['decision']=='loss':
        transit=db.session.get(TransferTransit,a.allocation_id,with_for_update=True,populate_existing=True)
        if issue.quantity>transit.remaining:raise MutationError('Transit reconciliation conflict.',409)
        before=transit.remaining;transit.remaining-=issue.quantity;transit.lost+=issue.quantity
        _record(operation,a,before,-issue.quantity,'authorized_loss',discrepancy_id=issue.discrepancy_id,reason=data['reason'])
    db.session.add(TransferResolution(discrepancy_id=issue.discrepancy_id,decision=data['decision'],reason=data['reason'],operation_id=operation.operation_id))
    return _finish(scope,row,operation,previous,data['reason'],[])


def enrich(row,result):
    active=execution(row.transfer_id)
    if active:
        result['state']=active.state;result['dispatch_operation_id']=active.dispatch_operation_id
        result['dispatched_at_utc']=active.dispatched_at_utc.isoformat()+'+00:00'
        for r in result['reservations']:r['remaining']=0
    result['transit']=[]
    for a in allocations(row):
        t=db.session.get(TransferTransit,a.allocation_id)
        if t:result['transit'].append(dict(allocation_id=a.allocation_id,lot_id=a.lot_id,sku_id=a.sku_id,dispatched=t.dispatched,
            remaining=t.remaining,received=t.received,lost=t.lost))
    result['discrepancies']=[dict(discrepancy_id=d.discrepancy_id,allocation_id=d.allocation_id,quantity=d.quantity,location=d.location,
        reason=d.reason,operation_id=d.operation_id,resolution=(db.session.get(TransferResolution,d.discrepancy_id).decision if db.session.get(TransferResolution,d.discrepancy_id) else None))
        for d in db.session.scalars(select(TransferDiscrepancy).join(StockTransferAllocation)
        .where(StockTransferAllocation.transfer_id==row.transfer_id).order_by(TransferDiscrepancy.discrepancy_id))]
    scope=resolve_scope('transfer.read')
    result['effects']=[]
    facts=db.session.scalars(select(TransferTransitFact).join(StockTransferAllocation)
        .where(StockTransferAllocation.transfer_id==row.transfer_id).order_by(TransferTransitFact.fact_id.desc()).limit(51)).all()
    result['effects_truncated']=len(facts)>50
    for fact in facts[:50]:
        item=dict(operation_id=fact.operation_id,allocation_id=fact.allocation_id,kind=fact.kind,
            transit_before=fact.before_quantity,transit_delta=fact.delta,transit_after=fact.after_quantity)
        if fact.movement_id:
            movement=db.session.get(InventoryMovement,fact.movement_id)
            if movement.branch_id in scope.branches:item['branch_effect']=dict(branch_id=movement.branch_id,
                lot_id=movement.lot_id,movement_id=movement.movement_id,before_on_hand=movement.before_on_hand,
                after_on_hand=movement.after_on_hand,before_reserved=movement.before_reserved,after_reserved=movement.after_reserved,
                before_quarantined=movement.before_quarantined,after_quarantined=movement.after_quarantined)
        result['effects'].append(item)
    result['return_parent_allocations']=[dict(allocation_id=l.allocation_id,quantity=l.quantity) for l in
        db.session.scalars(select(TransferReturnLink).where(TransferReturnLink.child_transfer_id==row.transfer_id))]
    return result


def return_limits(key):
    links=db.session.scalars(select(TransferReturnLink).where(TransferReturnLink.child_transfer_id==key)).all()
    limits={}
    for link in links:
        a=db.session.get(StockTransferAllocation,link.allocation_id);limits[a.lot_id]=limits.get(a.lot_id,0)+link.quantity
    return limits


def propose_return(key,values):
    scope,row=_load(key,'transfer.propose',True);data=_data(values,{'lines','planned_arrival_utc'})
    requested=_rows(data,{'allocation_id','quantity'})
    arrival=proposals._arrival(data['planned_arrival_utc']);data['planned_arrival_utc']=arrival.isoformat()
    # One operation creates parent linkage and reverse Draft. Reuse T10 create
    # with the original operation kept staging until links/parent event exist.
    replay,_=ledger.lookup(scope,'transfer_return',dict(data,transfer_id=row.transfer_id),data['request_key'])
    if replay:return replay
    if not execution(row.transfer_id):raise MutationError('No dispatched return source.',409)
    items=[a for a in allocations(row) if a.allocation_id in requested]
    if len(items)!=len(requested):raise NotFound('Resource not found.')
    quantities={}
    for a in items:
        received=db.session.get(TransferTransit,a.allocation_id)
        claimed=db.session.scalar(select(func.coalesce(func.sum(TransferReturnLink.quantity),0)).join(StockTransfer,
            StockTransfer.transfer_id==TransferReturnLink.child_transfer_id).where(TransferReturnLink.allocation_id==a.allocation_id,
            StockTransfer.state.not_in(('Cancelled','Rejected'))))
        if requested[a.allocation_id]['quantity']>received.received-claimed:raise MutationError('Return exceeds received quantity less linked returns.',409)
        stock=db.session.get(BranchStock,(row.destination_branch_id,a.lot_id),with_for_update=True,populate_existing=True)
        state=ledger._locked_stock(scope,a.sku_id)
        if not stock or stock.on_hand-stock.reserved-stock.quarantined_quantity<requested[a.allocation_id]['quantity'] or not ledger._eligibility(state[0],state[1],state[3][a.lot_id],state[4],state[5],ledger.utcnow())[0]:
            raise MutationError('Only verified eligible received stock can enter an ordinary return transfer. Quarantined returns require separate approved handling.',409)
        quantities[a.sku_id]=quantities.get(a.sku_id,0)+requested[a.allocation_id]['quantity']
    previous=execution(row.transfer_id).state;_bump(row,data['expected_version'])
    operation,_=ledger.begin(scope,'transfer_return',dict(data,transfer_id=row.transfer_id),data['request_key'],data['business_reference'],ledger.utcnow())
    proposals._validate_catalog(row.destination_branch_id,row.source_branch_id,[dict(sku_id=k,quantity=v) for k,v in quantities.items()])
    if arrival<=ledger.utcnow():raise MutationError('Return arrival must be future.',409)
    now=ledger.utcnow().replace(tzinfo=None)
    child=StockTransfer(source_branch_id=row.destination_branch_id,destination_branch_id=row.source_branch_id,requester_id=scope.actor_id,
        state='Draft',version=1,planned_arrival_utc=arrival.replace(tzinfo=None),created_at_utc=now,updated_at_utc=now,creation_operation_id=operation.operation_id)
    db.session.add(child);db.session.flush()
    from models import StockTransferLine
    db.session.add_all([StockTransferLine(transfer_id=child.transfer_id,sku_id=k,quantity=v) for k,v in sorted(quantities.items())])
    db.session.add_all([TransferReturnLink(child_transfer_id=child.transfer_id,allocation_id=a.allocation_id,
        quantity=requested[a.allocation_id]['quantity'],operation_id=operation.operation_id) for a in items]);db.session.flush()
    db.session.add(StockTransferEvent(transfer_id=child.transfer_id,version=1,from_state=None,to_state='Draft',actor_id=scope.actor_id,
        reason=data['reason'],occurred_at_utc=now,operation_id=operation.operation_id))
    result=proposals._dto(child);result['parent_transfer_id']=row.transfer_id
    # Parent version records concurrency/review evidence in the same ledger document.
    result['parent_version']=row.version
    return ledger.finish(scope,operation,result,data['reason'])


def reconcile():
    scope=resolve_scope('transfer.read')
    rows=db.session.scalars(select(StockTransfer).where(or_(StockTransfer.source_branch_id.in_(scope.branches),
        StockTransfer.destination_branch_id.in_(scope.branches))).order_by(StockTransfer.transfer_id)).all()
    result=[]
    for row in rows:
        for a in allocations(row):
            t=db.session.get(TransferTransit,a.allocation_id)
            if not t:continue
            facts=db.session.scalars(select(TransferTransitFact).where(TransferTransitFact.allocation_id==a.allocation_id).order_by(TransferTransitFact.fact_id)).all()
            balance=0;valid=True
            for f in facts:
                valid=valid and f.before_quantity==balance;balance=f.after_quantity
                if f.movement_id:
                    m=db.session.get(InventoryMovement,f.movement_id)
                    valid=valid and m.branch_id==(row.source_branch_id if f.kind=='dispatch' else row.destination_branch_id) and m.lot_id==a.lot_id and m.sku_id==a.sku_id and m.operation_id==f.operation_id and m.on_hand_delta==-f.delta
            valid=valid and bool(facts) and sum(f.delta for f in facts if f.kind=='dispatch')==t.dispatched and -sum(f.delta for f in facts if f.kind=='receipt')==t.received and -sum(f.delta for f in facts if f.kind=='authorized_loss')==t.lost and balance==t.remaining and t.dispatched==t.received+t.remaining+t.lost and t.dispatched==a.quantity
            result.append(dict(transfer_id=row.transfer_id,allocation_id=a.allocation_id,dispatched=t.dispatched,received=t.received,
                in_transit=t.remaining,authorized_loss=t.lost,balanced=bool(valid)))
    return result
