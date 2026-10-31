"""T10 proposal state machine. Caller owns commit/rollback on every exception.

T07 is the sole balance writer. Transfer holds are relational immutable allocations,
not generic InventoryReservations: generic release/fulfil APIs cannot consume them.
"""
from datetime import datetime
from sqlalchemy import select, update, or_
from werkzeug.exceptions import NotFound, Forbidden
from database import db
from models import (StockTransfer, StockTransferLine, StockTransferAllocation, StockTransferEvent,
                    Branch, BranchProduct, SKU, InventoryMovement, User, UserHQScope, UserBranch)
from authz import resolve_scope, integer_id
from security import MutationError
from expiry import UTC, zone, classify, integer
import catalog_services as catalog
import inventory_ledger as ledger


def _arrival(value):
    try:
        result=datetime.fromisoformat(value)
        if result.tzinfo is None or result.microsecond:raise ValueError()
        return result.astimezone(UTC)
    except (TypeError,ValueError,OverflowError):
        raise MutationError('Supply planned_arrival_utc as an ISO timestamp with timezone and second precision.') from None


def _common(values,allowed):
    if not isinstance(values,dict) or set(values)!=set(allowed)|{'request_key','business_reference','reason'}:
        raise MutationError('Invalid transfer fields; actor, authority, balances and state are server controlled.')
    data=dict(values)
    for field,limit in [('request_key',120),('business_reference',100),('reason',255)]:
        data[field]=catalog.text(data[field],field,limit)
    return data


def _resource(key,scope,lock=False,related=False):
    from models import TransferExecution
    predicate=StockTransfer.source_branch_id.in_(scope.branches)
    if related:predicate=or_(predicate, (StockTransfer.destination_branch_id.in_(scope.branches) & StockTransfer.transfer_id.in_(select(TransferExecution.transfer_id))))
    query=select(StockTransfer).where(StockTransfer.transfer_id==integer_id(key),predicate).execution_options(populate_existing=True)
    row=db.session.scalar(query.with_for_update() if lock else query)
    if row is None:raise NotFound('Resource not found.')
    return row


def _lines(row):
    return db.session.scalars(select(StockTransferLine).where(StockTransferLine.transfer_id==row.transfer_id)
        .order_by(StockTransferLine.sku_id)).all()


def _validate_catalog(source,destination,lines):
    branches={}
    for key in sorted((source,destination)):
        branch=db.session.get(Branch,key,populate_existing=True,with_for_update={'read':True})
        if branch is None or not branch.active:raise MutationError('Select distinct active source and destination branches.',409)
        branches[key]=branch
    if source==destination:raise MutationError('Source and destination must differ.')
    for line in lines:
        sku=db.session.get(SKU,line['sku_id'],populate_existing=True,with_for_update={'read':True})
        if sku is None:raise NotFound('Resource not found.')
        if not sku.active:raise MutationError('SKU is archived.',409)
        for key in sorted(branches):
            offer=db.session.get(BranchProduct,(key,sku.sku_id),populate_existing=True,with_for_update={'read':True})
            if offer is None or not offer.enabled:raise MutationError('SKU must be approved in both branches.',409)
    return branches[destination]


def _dto(row):
    allocations=db.session.scalars(select(StockTransferAllocation).where(StockTransferAllocation.transfer_id==row.transfer_id)
        .order_by(StockTransferAllocation.line_id,StockTransferAllocation.lot_id)).all()
    result=dict(transfer_id=row.transfer_id,state=row.state,version=row.version,source_branch_id=row.source_branch_id,
        destination_branch_id=row.destination_branch_id,requester_id=row.requester_id,approver_id=row.approver_id,
        planned_arrival_utc=row.planned_arrival_utc.isoformat()+'+00:00',
        created_at_utc=row.created_at_utc.isoformat()+'+00:00',updated_at_utc=row.updated_at_utc.isoformat()+'+00:00',
        creation_operation_id=row.creation_operation_id,approval_operation_id=row.approval_operation_id,
        cancellation_operation_id=row.cancellation_operation_id,
        lines=[dict(line_id=line.line_id,sku_id=line.sku_id,quantity=line.quantity) for line in _lines(row)],
        reservations=[dict(allocation_id=a.allocation_id,line_id=a.line_id,lot_id=a.lot_id,sku_id=a.sku_id,
            quantity=a.quantity,remaining=a.quantity if row.state=='Approved' else 0,movement_id=a.movement_id) for a in allocations])
    from transfer_execution import enrich
    return enrich(row,result)


def _complete(scope,row,operation,previous,reason,balances):
    result=_dto(row)
    db.session.add(StockTransferEvent(transfer_id=row.transfer_id,version=row.version,from_state=previous,to_state=result['state'],
        actor_id=scope.actor_id,reason=reason,occurred_at_utc=ledger.utcnow().replace(tzinfo=None),operation_id=operation.operation_id))
    result['balances']=balances
    return ledger.finish(scope,operation,result,reason)


def create(values):
    scope=resolve_scope('transfer.propose',write=True,shared=True)
    data=_common(values,{'destination_branch_id','planned_arrival_utc','lines'})
    data['destination_branch_id']=integer_id(data['destination_branch_id'])
    arrival=_arrival(data['planned_arrival_utc']);data['planned_arrival_utc']=arrival.isoformat()
    lines=data['lines']
    if not isinstance(lines,list) or not 1<=len(lines)<=50:raise MutationError('Supply 1 to 50 SKU lines.')
    normalized=[]
    for line in lines:
        if not isinstance(line,dict) or set(line)!={'sku_id','quantity'}:raise MutationError('Each line accepts SKU and quantity only.')
        normalized.append(dict(sku_id=integer_id(line['sku_id']),quantity=integer(line['quantity'],'Quantity',1,ledger.MAX_QUANTITY)))
    data['lines']=sorted(normalized,key=lambda x:x['sku_id'])
    if len({x['sku_id'] for x in normalized})!=len(normalized):raise MutationError('Duplicate SKU lines.')
    replay,_=ledger.lookup(scope,'transfer_create',data,data['request_key'])
    if replay:return replay
    if arrival<=ledger.utcnow():raise MutationError('Planned arrival must be in the future.',409)
    _validate_catalog(scope.branches[0],data['destination_branch_id'],data['lines'])
    operation,_=ledger.begin(scope,'transfer_create',data,data['request_key'],data['business_reference'],ledger.utcnow())
    now=ledger.utcnow().replace(tzinfo=None)
    row=StockTransfer(source_branch_id=scope.branches[0],destination_branch_id=data['destination_branch_id'],
        requester_id=scope.actor_id,state='Draft',version=1,planned_arrival_utc=arrival.replace(tzinfo=None),
        created_at_utc=now,updated_at_utc=now,creation_operation_id=operation.operation_id)
    db.session.add(row);db.session.flush()
    db.session.add_all([StockTransferLine(transfer_id=row.transfer_id,**line) for line in data['lines']]);db.session.flush()
    return _complete(scope,row,operation,None,data['reason'],[])


def _arrival_eligible(locked,destination,arrival,now):
    sku,offer,stocks,lots,policy,branch=locked
    if arrival<=now:raise MutationError('Planned arrival has passed; cancel and propose a reviewed arrival.',409)
    eligible=[]
    for stock in stocks:
        lot=lots[stock.lot_id]
        projected=classify(policy=policy,expiry_date=lot.expiry_date,expiry_timezone=lot.expiry_timezone,
            expires_at_utc=lot.expires_at_utc,validation_state=lot.validation_state,
            branch_timezone=destination.timezone,as_of=arrival)
        if projected['status'] not in ('expired','review') and (projected['days_remaining'] is None or
                projected['days_remaining']>=policy.min_remaining_days_on_transfer_receipt):eligible.append(stock)
    return sku,offer,eligible,lots,policy,branch


def _requester_active(row):
    requester=db.session.get(User,row.requester_id,populate_existing=True,with_for_update={'read':True})
    hq=db.session.get(UserHQScope,row.requester_id,populate_existing=True,with_for_update={'read':True})
    membership=db.session.get(UserBranch,(row.requester_id,row.source_branch_id),populate_existing=True,with_for_update={'read':True})
    if not requester or not requester.enabled or not ((hq and hq.active and hq.hq_role=='admin') or
            (membership and membership.active and membership.branch_role=='manager' and not (hq and hq.active))):
        raise MutationError('Requester no longer has source proposal authority; cancel and review.',409)


def transition(key,action,values):
    if action in ('dispatch','receive','discrepancy','resolve','return'):
        import transfer_execution as execution
        return {'dispatch':execution.dispatch,'receive':execution.receive,'discrepancy':execution.discrepancy,
            'resolve':execution.resolve,'return':execution.propose_return}[action](key,values)
    if action not in ('submit','approve','reject','cancel'):raise MutationError('Unsupported transfer transition.')
    capability='transfer.approve' if action in ('approve','reject') else 'transfer.propose'
    scope=resolve_scope(capability,write=True,shared=True)
    data=_common(values,{'expected_version'})
    data['expected_version']=integer(data['expected_version'],'Expected version',1,ledger.MAX_QUANTITY)
    data['transfer_id']=integer_id(key)
    row=_resource(key,scope,True)
    # Approved cancellation is HQ-only, including retries. No source manager can
    # silently release a reviewed HQ commitment through generic reservation APIs.
    if action=='cancel' and row.approval_operation_id is not None:
        scope=resolve_scope('transfer.approve',write=True,shared=True)
        capability='transfer.approve'
    if action=='approve' and scope.actor_id==row.requester_id:raise Forbidden('Independent approval is required.')
    kind='transfer_cancel_approved' if action=='cancel' and row.approval_operation_id is not None else 'transfer_'+action
    replay,_=ledger.lookup(scope,kind,data,data['request_key'])
    if replay:return replay
    from transfer_execution import execution
    if execution(row.transfer_id):raise MutationError('Dispatched transfers cannot use approval/cancellation shortcuts.',409)
    allowed={'submit':{'Draft'},'approve':{'Submitted'},'reject':{'Submitted'},'cancel':{'Draft','Submitted','Approved'}}
    if row.state not in allowed[action]:raise MutationError('Transfer state does not permit this transition.',409)
    if row.version!=data['expected_version']:raise MutationError('Transfer version conflict.',409)
    previous=row.state
    lines=_lines(row);line_data=[dict(sku_id=l.sku_id,quantity=l.quantity) for l in lines]
    locked={};plans=[]
    if action in ('submit','approve'):
        destination=_validate_catalog(row.source_branch_id,row.destination_branch_id,line_data)
        if row.planned_arrival_utc.replace(tzinfo=UTC)<=ledger.utcnow():raise MutationError('Planned arrival has passed.',409)
    if action=='approve':
        _requester_active(row)
        locked={line.sku_id:ledger._locked_stock(scope,line.sku_id) for line in lines}
        now=ledger.utcnow()
        for line in lines:
            filtered=_arrival_eligible(locked[line.sku_id],destination,row.planned_arrival_utc.replace(tzinfo=UTC),now)
            from transfer_execution import return_limits
            limits=return_limits(row.transfer_id)
            if limits:
                filtered=(*filtered[:2],[s for s in filtered[2] if s.lot_id in limits],*filtered[3:])
                if sum(min(limits[s.lot_id],s.on_hand-s.reserved-s.quarantined_quantity) for s in filtered[2])<line.quantity:
                    raise MutationError('Linked return lot no longer available.',409)
            plans.append((line,ledger.fefo_plan(filtered,line.quantity,now)))
    if action=='cancel' and previous=='Approved':
        # Cancellation must remain possible after archive, expiry or destination
        # deactivation. Lock only original source balances in canonical order.
        allocations=db.session.scalars(select(StockTransferAllocation).where(StockTransferAllocation.transfer_id==row.transfer_id)
            .order_by(StockTransferAllocation.sku_id,StockTransferAllocation.lot_id)).all()
        from models import BranchStock
        for allocation in allocations:
            stock=db.session.get(BranchStock,(row.source_branch_id,allocation.lot_id),populate_existing=True,with_for_update=True)
            if stock is None or stock.reserved<allocation.quantity:raise MutationError('Reservation needs reconciliation.',409)
            locked[allocation.lot_id]=stock
    operation,replay=ledger.begin(scope,kind,data,data['request_key'],data['business_reference'],ledger.utcnow())
    if replay:return replay
    target={'submit':'Submitted','approve':'Approved','reject':'Rejected','cancel':'Cancelled'}[action]
    updates=dict(state=target,version=row.version+1,updated_at_utc=ledger.utcnow().replace(tzinfo=None))
    if action=='approve':updates.update(approver_id=scope.actor_id,approval_operation_id=operation.operation_id)
    if action=='cancel':updates['cancellation_operation_id']=operation.operation_id
    changed=db.session.execute(update(StockTransfer).where(StockTransfer.transfer_id==row.transfer_id,
        StockTransfer.state==previous,StockTransfer.version==data['expected_version']).values(**updates)
        .execution_options(synchronize_session=False)).rowcount
    if changed!=1:raise MutationError('Transfer version conflict.',409)
    db.session.refresh(row);balances=[];selected=[]
    if action=='approve':
        now=ledger.utcnow();operation.business_date=now.astimezone(zone(locked[lines[0].sku_id][-1].timezone)).date()
        for line,plan in plans:
            remaining=line.quantity
            for *_,stock,state in plan:
                from transfer_execution import return_limits
                limits=return_limits(row.transfer_id)
                take=min(remaining,stock.on_hand-stock.reserved-stock.quarantined_quantity,limits.get(stock.lot_id,remaining) if limits else remaining)
                balances.append(ledger.apply(operation,stock,reserved=take,evidence=dict(transfer_id=row.transfer_id,
                    transfer_line_id=line.line_id,classification=state,policy=locked[line.sku_id][-2].snapshot(),
                    destination_branch_id=row.destination_branch_id,planned_arrival_utc=row.planned_arrival_utc.isoformat()+'+00:00')))
                db.session.flush()
                movement=db.session.scalar(select(InventoryMovement).where(InventoryMovement.operation_id==operation.operation_id,InventoryMovement.lot_id==stock.lot_id))
                db.session.add(StockTransferAllocation(line_id=line.line_id,transfer_id=row.transfer_id,sku_id=line.sku_id,
                    branch_id=row.source_branch_id,lot_id=stock.lot_id,quantity=take,movement_id=movement.movement_id))
                selected.append((line.sku_id,stock.lot_id));remaining-=take
                if not remaining:break
        def validate():
            _requester_active(row)
            _validate_catalog(row.source_branch_id,row.destination_branch_id,line_data)
            now=ledger.utcnow()
            for sku_id,lot_id in selected:
                state=locked[sku_id];filtered=_arrival_eligible(state,destination,row.planned_arrival_utc.replace(tzinfo=UTC),now)
                if lot_id not in {s.lot_id for s in filtered[2]} or not ledger._eligibility(state[0],state[1],state[3][lot_id],state[4],state[5],now)[0]:
                    raise MutationError('Transfer lot is no longer eligible before commit.',409)
        ledger.guard(validate)
    elif action=='cancel' and previous=='Approved':
        for allocation in allocations:
            balances.append(ledger.apply(operation,locked[allocation.lot_id],reserved=-allocation.quantity,
                evidence=dict(transfer_id=row.transfer_id,allocation_id=allocation.allocation_id,
                    compensates_movement_id=allocation.movement_id,transfer_line_id=allocation.line_id)))
    db.session.flush()
    return _complete(scope,row,operation,previous,data['reason'],balances)


def detail(key):
    scope=resolve_scope('transfer.read')
    row=_resource(key,scope,related=True)
    result=_dto(row)
    result['events']=[dict(version=e.version,from_state=e.from_state,to_state=e.to_state,actor_id=e.actor_id,
        reason=e.reason,occurred_at_utc=e.occurred_at_utc.isoformat()+'+00:00',operation_id=e.operation_id)
        for e in db.session.scalars(select(StockTransferEvent).where(StockTransferEvent.transfer_id==row.transfer_id).order_by(StockTransferEvent.version))]
    if not scope.consolidated and row.source_branch_id not in scope.branches:
        result.pop('creation_operation_id',None);result['events']=[e for e in result['events'] if e['to_state'] in ('Dispatched','PartiallyReceived','Exception','Received')]
    return result


def listing(page=1):
    scope=resolve_scope('transfer.read');page=integer(page,'Page',1,10000)
    from models import TransferExecution
    rows=db.session.scalars(select(StockTransfer).where(or_(StockTransfer.source_branch_id.in_(scope.branches),
        StockTransfer.destination_branch_id.in_(scope.branches) & StockTransfer.transfer_id.in_(select(TransferExecution.transfer_id))))
        .order_by(StockTransfer.transfer_id.desc()).offset((page-1)*25).limit(26)).all()
    return dict(page=page,page_size=25,has_more=len(rows)>25,transfers=[dict(transfer_id=r.transfer_id,state=(_dto(r)['state']),
        version=r.version,source_branch_id=r.source_branch_id,destination_branch_id=r.destination_branch_id) for r in rows[:25]])


def choices():
    scope=resolve_scope('transfer.propose')
    if scope.consolidated:return dict(branches=[],skus=[])
    return dict(branches=[dict(branch_id=b.branch_id,code=b.code) for b in db.session.scalars(select(Branch)
        .where(Branch.active.is_(True),Branch.branch_id!=scope.branches[0]).order_by(Branch.branch_id))],
        skus=[dict(sku_id=s.sku_id,code=s.code,name=s.name) for s in db.session.scalars(select(SKU).join(BranchProduct)
        .where(BranchProduct.branch_id==scope.branches[0],BranchProduct.enabled.is_(True),SKU.active.is_(True)).order_by(SKU.code))])
