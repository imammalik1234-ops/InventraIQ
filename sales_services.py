"""T08 priced sales/refunds. Caller owns commit; T07 owns every stock delta.

Approved policy: MYR, integer units, two decimal places, no tax/discounts,
HQ/branch-manager refunds, inspected returns only. No historical price inference.
Amounts are calculated with Decimal and persisted as exact integer sen on both
dialects; SQLite NUMERIC affinity must never round large amounts through float.
"""
from decimal import Decimal
from sqlalchemy import select
from database import db
from models import (PricedSale, SaleLine, SaleAllocation, SaleRefund, RefundLine,
                    InventoryMovement, InventoryOperation, CatalogAudit, Sale)
from models import InventoryReservation
from authz import resolve_scope, integer_id, legacy_predicate
from security import MutationError
from expiry import integer, zone, utcnow
import catalog_services as catalog
import inventory_ledger as ledger

CURRENCY='MYR'
MAX_MINOR=9000000000000000
POLICY=dict(version='T08-MYR-v1',currency=CURRENCY,decimal_places=2,tax='unsupported',
            discounts='unsupported',refund_roles=['hq_admin','manager'],restock='inspected_original_lot_only')


def amount(minor):return format(Decimal(minor)/Decimal(100),'.2f')


def _common(values,capability,allowed):
    scope=resolve_scope(capability,write=True,shared=True)
    if not isinstance(values,dict) or set(values)-set(allowed)-{'branch_id'}:
        raise MutationError('Invalid sale/refund fields; prices, currency, taxes and actor are server controlled.')
    if 'branch_id' in values and integer_id(values['branch_id'])!=scope.branches[0]:
        raise MutationError('Access denied.',403)
    data={k:v for k,v in values.items() if k!='branch_id'}
    for key,label,limit in [('request_key','Request key',120),('business_reference','Business reference',100),('reason','Reason',255)]:
        data[key]=catalog.text(data.get(key),label,limit)
    return scope,data


def _audit(scope,operation,facts):
    audit=CatalogAudit(actor_id=scope.actor_id,entity_type='sale_financial',entity_key=str(operation.operation_id),
        revision=1,reason=operation.evidence['parameters']['reason'],old_value=None,new_value=facts,
        occurred_at=utcnow().replace(tzinfo=None))
    db.session.add(audit);db.session.flush();return audit.audit_id


def create_sale(values, *, _reservation_id=None):
    scope,data=_common(values,'sales.create',{'lines','request_key','business_reference','reason'})
    if not isinstance(data.get('lines'),list) or not 1<=len(data['lines'])<=50:
        raise MutationError('Supply 1 to 50 product lines.')
    lines=[]
    for row in data['lines']:
        if not isinstance(row,dict) or set(row)!={'sku_id','quantity'}:
            raise MutationError('Each sale line accepts only SKU and quantity.')
        lines.append(dict(sku_id=integer_id(row['sku_id']),quantity=integer(row['quantity'],'Quantity',1,ledger.MAX_QUANTITY)))
    lines.sort(key=lambda row:row['sku_id'])
    if len({r['sku_id'] for r in lines})!=len(lines):raise MutationError('Duplicate SKU lines are not allowed.')
    data['lines']=lines
    if _reservation_id is not None:
        resolve_scope('stock.adjust',write=True,shared=True)
        data['reservation_id']=_reservation_id
    kind='priced_reserved_sale' if _reservation_id is not None else 'priced_sale'
    operation,replay=ledger.begin(scope,kind,data,data['request_key'],data['business_reference'],ledger.utcnow())
    if replay:return replay
    # Acquire ALL balances in global order before planning/deducting any line.
    locked={row['sku_id']:ledger._locked_stock(scope,row['sku_id']) for row in lines}
    now=ledger.utcnow();plans=[];total=0;reserved={}
    if _reservation_id is not None:
        original=db.session.scalar(select(InventoryOperation).where(InventoryOperation.operation_id==_reservation_id,
            InventoryOperation.branch_id==scope.branches[0],InventoryOperation.operation_type=='reservation',InventoryOperation.status=='committed'))
        if original is None or len(lines)!=1 or original.result.get('sku_id')!=lines[0]['sku_id']:
            raise MutationError('Resource not found.',404)
        rows=db.session.scalars(select(InventoryReservation).where(InventoryReservation.operation_id==_reservation_id)
            .order_by(InventoryReservation.lot_id).with_for_update().execution_options(populate_existing=True)).all()
        if sum(r.remaining for r in rows)!=lines[0]['quantity']:raise MutationError('Reservation is settled or quantity conflicts.',409)
        reserved={r.lot_id:r for r in rows if r.remaining}
    for row in lines:
        state=locked[row['sku_id']];sku,offer,*_=state
        price=offer.sale_price_override if offer.sale_price_override is not None else sku.default_price
        if price is None:raise MutationError('This SKU has no approved sale price.',409)
        unit_minor=int(catalog.price(price)*100)
        line_total=unit_minor*row['quantity'];total+=line_total
        if total>MAX_MINOR:raise MutationError('Sale total exceeds the supported exact amount range.')
        if _reservation_id is None:plan=ledger.fefo_plan(state,row['quantity'],now)
        else:
            sku,offer,stocks,lots,policy,branch=state;plan=[]
            for stock in stocks:
                if stock.lot_id not in reserved:continue
                valid,classification=ledger._eligibility(sku,offer,lots[stock.lot_id],policy,branch,now)
                if not valid:raise MutationError('Reserved lot expired or became unavailable; release and review.',409)
                plan.append((None,None,None,None,stock.lot_id,stock,classification))
            if len(plan)!=len(reserved):raise MutationError('Original reserved lot is unavailable.',409)
        plans.append((row,state,plan,unit_minor,line_total))
    branch=next(iter(locked.values()))[-1]
    operation.business_date=now.astimezone(zone(branch.timezone)).date()
    sale=PricedSale(operation_id=operation.operation_id,branch_id=scope.branches[0],cashier_id=scope.actor_id,
        occurred_at_utc=utcnow().replace(tzinfo=None),business_date=operation.business_date,currency=CURRENCY,
        total_minor=total,policy_snapshot=POLICY,audit_id=_audit(scope,operation,dict(kind=kind,currency=CURRENCY,total=amount(total))))
    db.session.add(sale);db.session.flush();result_lines=[]
    for row,state,plan,unit_minor,line_total in plans:
        sku,offer,stocks,lots,policy,branch=state
        line=SaleLine(sale_id=sale.sale_id,sku_id=sku.sku_id,quantity=row['quantity'],unit_price_minor=unit_minor,
            total_minor=line_total,sku_snapshot=catalog.sku_snapshot(sku),price_snapshot=dict(
                source='branch_override' if offer.sale_price_override is not None else 'hq_default',
                sku_version=sku.version,assortment_version=offer.version,currency=CURRENCY,unit_price=amount(unit_minor)))
        db.session.add(line);db.session.flush();remaining=row['quantity'];allocations=[]
        for _,_,_,_,lot_id,stock,classification in plan:
            take=reserved[lot_id].remaining if _reservation_id is not None else min(remaining,stock.on_hand-stock.reserved-stock.quarantined_quantity)
            balance=ledger.apply(operation,stock,-take,reserved=-take if _reservation_id is not None else 0,evidence=dict(sale_id=sale.sale_id,line_id=line.line_id,
                policy=policy.snapshot(),classification=classification,lot_version=lots[lot_id].version,as_of_utc=now.isoformat()))
            if _reservation_id is not None:reserved[lot_id].remaining=0
            db.session.flush()
            movement=db.session.scalar(select(InventoryMovement).where(InventoryMovement.operation_id==operation.operation_id,InventoryMovement.lot_id==lot_id))
            allocation=SaleAllocation(line_id=line.line_id,movement_id=movement.movement_id,lot_id=lot_id,quantity=take)
            db.session.add(allocation);db.session.flush()
            allocations.append(dict(allocation_id=allocation.allocation_id,lot_id=lot_id,quantity=take,**{'balance':balance}))
            remaining-=take
            if not remaining:break
        def validate(state=state,ids=[a['lot_id'] for a in allocations]):
            sku,offer,_,lots,policy,branch=state
            if any(not ledger._eligibility(sku,offer,lots[key],policy,branch,ledger.utcnow())[0] for key in ids):
                raise MutationError('Allocated lot expired or became unavailable before commit.',409)
        ledger.guard(validate)
        result_lines.append(dict(line_id=line.line_id,sku_id=sku.sku_id,quantity=row['quantity'],
            unit_price=amount(unit_minor),total=amount(line_total),allocations=allocations))
    result=dict(kind=kind,sale_id=sale.sale_id,branch_id=scope.branches[0],currency=CURRENCY,total=amount(total),lines=result_lines)
    if len(result_lines)==1:
        only=result_lines[0]
        result.update(sku_id=only['sku_id'],quantity=only['quantity'],allocations=[a['balance'] for a in only['allocations']])
    return ledger.finish(scope,operation,result,data['reason'])


def single_sale_adapter(values):
    """Preserve the T07 single-SKU HTTP shape while recording new priced facts."""
    scope,data=_common(values,'sales.create',{'sku_id','quantity','request_key','business_reference','reason'})
    data['sku_id']=integer_id(data.get('sku_id'));data['quantity']=integer(data.get('quantity'),'Quantity',1,ledger.MAX_QUANTITY)
    old=db.session.scalar(select(InventoryOperation).where(InventoryOperation.actor_id==scope.actor_id,InventoryOperation.request_key==data['request_key']))
    if old and old.operation_type=='sale':return ledger.lookup(scope,'sale',data,data['request_key'])[0]
    sku_id=integer_id(data.pop('sku_id',None));quantity=data.pop('quantity',None)
    data['lines']=[dict(sku_id=sku_id,quantity=quantity)]
    return create_sale(data)


def reserved_sale_adapter(values):
    scope,data=_common(values,'stock.adjust',{'sku_id','quantity','reservation_id','request_key','business_reference','reason'})
    for key in ('sku_id','reservation_id'):data[key]=integer_id(data.get(key))
    data['quantity']=integer(data.get('quantity'),'Quantity',1,ledger.MAX_QUANTITY)
    old=db.session.scalar(select(InventoryOperation).where(InventoryOperation.actor_id==scope.actor_id,InventoryOperation.request_key==data['request_key']))
    if old and old.operation_type=='reserved_sale':return ledger.lookup(scope,'reserved_sale',data,data['request_key'])[0]
    reservation_id=data.pop('reservation_id');sku_id=data.pop('sku_id');quantity=data.pop('quantity')
    return create_sale(dict(data,lines=[dict(sku_id=sku_id,quantity=quantity)]),_reservation_id=reservation_id)


def _sale(sale_id,scope,lock=False):
    query=select(PricedSale).where(PricedSale.sale_id==integer_id(sale_id),PricedSale.branch_id.in_(scope.branches)).execution_options(populate_existing=True)
    sale=db.session.scalar(query.with_for_update() if lock else query)
    if sale is None:raise MutationError('Resource not found.',404)
    return sale


def _allocations(sale_id):
    return db.session.execute(select(SaleAllocation,SaleLine).join(SaleLine,SaleLine.line_id==SaleAllocation.line_id)
        .where(SaleLine.sale_id==sale_id).order_by(SaleLine.sku_id,SaleAllocation.lot_id)).all()


def _refunded(sale_id,lock=False):
    query=select(RefundLine).join(SaleRefund,SaleRefund.refund_id==RefundLine.refund_id).where(SaleRefund.sale_id==sale_id)
    # Current locking read avoids stale REPEATABLE READ aggregates after waiting
    # for a competing refund's sale-header lock in MySQL.
    rows=db.session.scalars(query.with_for_update() if lock else query).all();result={}
    for row in rows:result[row.allocation_id]=result.get(row.allocation_id,0)+row.quantity
    return result


def refund_sale(sale_id,values,*,reversal=False):
    scope,data=_common(values,'sales.refund',{'lines','mode','request_key','business_reference','reason'})
    sale_id=integer_id(sale_id);data['sale_id']=sale_id;data['mode']=data.get('mode','partial')
    if data['mode'] not in ('partial','full'):raise MutationError('Invalid refund mode.')
    if data['mode']=='full':
        if 'lines' in data:raise MutationError('Full financial refund has no automatic physical restock. Use explicit lines for inspected returns.')
    else:
        if not isinstance(data.get('lines'),list) or not 1<=len(data['lines'])<=100:
            raise MutationError('Supply 1 to 100 original lot allocations.')
        normalized=[]
        for row in data['lines']:
            if not isinstance(row,dict) or set(row)-{'allocation_id','quantity','disposition','saleable_verified','inspection_evidence'}:
                raise MutationError('Invalid refund line fields.')
            item=dict(allocation_id=integer_id(row.get('allocation_id')),quantity=integer(row.get('quantity'),'Quantity',1,ledger.MAX_QUANTITY),
                disposition=row.get('disposition'),saleable_verified=row.get('saleable_verified',False),
                inspection_evidence=row.get('inspection_evidence',''))
            if not isinstance(item['inspection_evidence'],str):raise MutationError('Return inspection evidence must be text.')
            if item['disposition'] not in ('no_restock','saleable','quarantine') or type(item['saleable_verified']) is not bool:
                raise MutationError('Choose an explicit return disposition and valid verification.')
            if item['disposition']=='saleable' and not item['saleable_verified']:
                raise MutationError('Saleable restock requires explicit inspection verification.')
            if item['disposition']!='no_restock':item['inspection_evidence']=catalog.text(item['inspection_evidence'],'Return inspection evidence',255)
            elif item['inspection_evidence']:item['inspection_evidence']=catalog.text(item['inspection_evidence'],'Return evidence',255)
            normalized.append(item)
        normalized.sort(key=lambda row:row['allocation_id'])
        if len({r['allocation_id'] for r in normalized})!=len(normalized):raise MutationError('Duplicate refund allocations are not allowed.')
        data['lines']=normalized
    operation,replay=ledger.begin(scope,'reversal' if reversal else 'refund',data,data['request_key'],data['business_reference'],ledger.utcnow())
    if replay:return replay
    sale=_sale(sale_id,scope,True);original={a.allocation_id:(a,line) for a,line in _allocations(sale_id)}
    refunded=_refunded(sale_id,True)
    remaining={key:a.quantity-refunded.get(key,0) for key,(a,_) in original.items()}
    requested=data.get('lines') if data['mode']=='partial' else [dict(allocation_id=key,quantity=qty,disposition='no_restock',saleable_verified=False,inspection_evidence='') for key,qty in remaining.items() if qty]
    if not requested:raise MutationError('Sale is already fully refunded.',409)
    for row in requested:
        if row['allocation_id'] not in original:raise MutationError('Resource not found.',404)
        if row['quantity']>remaining[row['allocation_id']]:raise MutationError('Refund exceeds the remaining original quantity.',409)
    if reversal and sum(r['quantity'] for r in requested)!=sum(remaining.values()):
        raise MutationError('Reversal must compensate the entire remaining sale.',409)
    physical_skus=sorted({original[r['allocation_id']][1].sku_id for r in requested if r['disposition']!='no_restock'})
    locked={sku:ledger._locked_stock(scope,sku) for sku in physical_skus}
    now=ledger.utcnow()
    from models import Branch
    branch=db.session.get(Branch,scope.branches[0]);operation.business_date=now.astimezone(zone(branch.timezone)).date()
    total=sum(original[r['allocation_id']][1].unit_price_minor*r['quantity'] for r in requested)
    refund=SaleRefund(sale_id=sale.sale_id,branch_id=sale.branch_id,operation_id=operation.operation_id,
        actor_id=scope.actor_id,occurred_at_utc=utcnow().replace(tzinfo=None),business_date=operation.business_date,
        kind=operation.operation_type,currency=sale.currency,total_minor=total,reason=data['reason'],
        audit_id=_audit(scope,operation,dict(kind=operation.operation_type,original_sale_id=sale.sale_id,
            original_operation_id=sale.operation_id,currency=sale.currency,total=amount(total))))
    db.session.add(refund);db.session.flush();result_lines=[]
    for row in sorted(requested,key=lambda row:(original[row['allocation_id']][1].sku_id,original[row['allocation_id']][0].lot_id)):
        allocation,line=original[row['allocation_id']];effective=row['disposition'];movement_id=None
        if effective!='no_restock':
            sku,offer,stocks,lots,policy,branch=locked[line.sku_id]
            stock=next((s for s in stocks if s.lot_id==allocation.lot_id),None)
            if stock is None:raise MutationError('Original lot is unavailable for return.',409)
            valid,state=ledger._eligibility(sku,offer,lots[stock.lot_id],policy,branch,now)
            if effective=='saleable' and not valid:effective='quarantine'
            ledger.apply(operation,stock,row['quantity'],quarantined=row['quantity'] if effective=='quarantine' else 0,
                evidence=dict(refund_id=refund.refund_id,original_sale_id=sale.sale_id,original_allocation_id=allocation.allocation_id,
                    original_movement_id=allocation.movement_id,requested_disposition=row['disposition'],disposition=effective,
                    inspection_evidence=row['inspection_evidence'],classification=state))
            db.session.flush();movement_id=db.session.scalar(select(InventoryMovement.movement_id).where(
                InventoryMovement.operation_id==operation.operation_id,InventoryMovement.lot_id==allocation.lot_id))
            if effective=='saleable':
                def validate(sku=sku,offer=offer,lot=lots[stock.lot_id],policy=policy,branch=branch):
                    if not ledger._eligibility(sku,offer,lot,policy,branch,ledger.utcnow())[0]:
                        raise MutationError('Returned lot expired before commit; retry with quarantine or no restock.',409)
                ledger.guard(validate)
        value=line.unit_price_minor*row['quantity']
        db.session.add(RefundLine(refund_id=refund.refund_id,allocation_id=allocation.allocation_id,quantity=row['quantity'],
            total_minor=value,disposition=effective,inspection_evidence=row['inspection_evidence'],movement_id=movement_id))
        result_lines.append(dict(allocation_id=allocation.allocation_id,quantity=row['quantity'],total=amount(value),
            requested_disposition=row['disposition'],disposition=effective,movement_id=movement_id))
    return ledger.finish(scope,operation,dict(kind=operation.operation_type,refund_id=refund.refund_id,sale_id=sale.sale_id,
        original_operation_id=sale.operation_id,branch_id=sale.branch_id,currency=sale.currency,total=amount(total),
        remaining_refundable_quantity=sum(remaining.values())-sum(r['quantity'] for r in requested),lines=result_lines),data['reason'])


def sale_detail(sale_id):
    scope=resolve_scope('sales.read');sale=_sale(sale_id,scope);refunded=_refunded(sale.sale_id)
    lines={}
    for allocation,line in _allocations(sale.sale_id):
        entry=lines.setdefault(line.line_id,dict(line_id=line.line_id,sku_id=line.sku_id,sku_snapshot=line.sku_snapshot,
            quantity=line.quantity,unit_price=amount(line.unit_price_minor),total=amount(line.total_minor),allocations=[]))
        entry['allocations'].append(dict(allocation_id=allocation.allocation_id,lot_id=allocation.lot_id,quantity=allocation.quantity,
            refundable_quantity=allocation.quantity-refunded.get(allocation.allocation_id,0)))
    refunds=db.session.scalars(select(SaleRefund).where(SaleRefund.sale_id==sale.sale_id).order_by(SaleRefund.refund_id)).all()
    refunded_minor=sum(r.total_minor for r in refunds)
    return dict(sale_id=sale.sale_id,branch_id=sale.branch_id,cashier_id=sale.cashier_id,operation_id=sale.operation_id,
        audit_id=sale.audit_id,currency=sale.currency,total=amount(sale.total_minor),refunded_total=amount(refunded_minor),
        net_total=amount(sale.total_minor-refunded_minor),business_date=sale.business_date.isoformat(),
        occurred_at_utc=sale.occurred_at_utc.isoformat()+'Z',lines=list(lines.values()),
        refunds=[dict(refund_id=r.refund_id,kind=r.kind,total=amount(r.total_minor),operation_id=r.operation_id,reason=r.reason) for r in refunds])


def sales_list(values=None):
    from flask import request, has_request_context
    from sqlalchemy import literal, union_all
    from operational_services import filters, _read_snapshot
    from query_paging import page
    incoming = request.args if values is None and has_request_context() else (values or {})
    if hasattr(incoming,'getlist') and any(len(incoming.getlist(k))!=1 for k in incoming):
        raise MutationError('Repeated sales filters are not allowed.')
    # Detail/choice navigation belongs to the form adapter, not the history query.
    incoming = {k:v for k,v in incoming.items() if k not in ('sale_id','choice_page','choice_search')}
    scope=resolve_scope('sales.read',branch_id=incoming.get('branch_id'))
    query=filters(incoming);_read_snapshot()
    dated=bool(incoming.get('start') or incoming.get('end'))
    stmt=select(PricedSale.sale_id,PricedSale.branch_id,PricedSale.business_date,PricedSale.total_minor).where(
        PricedSale.branch_id.in_(scope.branches))
    if dated:stmt=stmt.where(PricedSale.business_date.between(query['start'],query['end']))
    result=page(stmt,PricedSale.sale_id,scope,dict(query,date_filter_applied=dated),'priced-sales')
    legacy=select(literal('legacy').label('source'),Sale.sale_id.label('id'),Sale.quantity).where(
        legacy_predicate(Sale,'sales.read'))
    if dated:legacy=legacy.where(Sale.sale_date.between(query['start'],query['end']))
    unpriced=select(literal('t07_quantity'),InventoryOperation.operation_id,
        InventoryOperation.result['quantity'].as_integer()).where(InventoryOperation.branch_id.in_(scope.branches),
        InventoryOperation.operation_type.in_(('sale','reserved_sale')),InventoryOperation.status=='committed')
    if dated:unpriced=unpriced.where(InventoryOperation.business_date.between(query['start'],query['end']))
    unknown=union_all(legacy,unpriced).subquery()
    rows=db.session.execute(select(unknown).order_by(unknown.c.source,unknown.c.id).offset((query['page']-1)*query['page_size']).limit(query['page_size'])).all()
    return dict(currency=CURRENCY,filters=query,date_filter_applied=dated,total=result['total'],next_cursor=result['next_cursor'],
        sales=[dict(sale_id=r['sale_id'],branch_id=r['branch_id'],total=amount(r['total_minor']),currency=CURRENCY,
            business_date=r['business_date'].isoformat()) for r in result['items']],
        historical_unknown=[dict(source=r.source,**({'sale_id':r.id} if r.source=='legacy' else {'operation_id':r.id}),
            quantity=r.quantity,total=None,currency=None) for r in rows])


def reconcile_sales():
    """Read-only operator reconciliation; always restricted to current scope."""
    scope=resolve_scope('reports.read');results=[]
    for sale in db.session.scalars(select(PricedSale).where(PricedSale.branch_id.in_(scope.branches)).order_by(PricedSale.sale_id)):
        lines=db.session.scalars(select(SaleLine).where(SaleLine.sale_id==sale.sale_id)).all()
        allocations=_allocations(sale.sale_id);refunded=_refunded(sale.sale_id)
        valid=sale.total_minor==sum(line.total_minor for line in lines)
        valid=valid and all(line.total_minor==line.unit_price_minor*line.quantity for line in lines)
        valid=valid and all(line.quantity==sum(a.quantity for a,l in allocations if l.line_id==line.line_id) for line in lines)
        original={a.allocation_id:(a,l) for a,l in allocations}
        for allocation,line in allocations:
            movement=db.session.get(InventoryMovement,allocation.movement_id)
            valid=valid and bool(movement and movement.operation_id==sale.operation_id and movement.branch_id==sale.branch_id
                and movement.sku_id==line.sku_id and movement.lot_id==allocation.lot_id and movement.on_hand_delta==-allocation.quantity)
            valid=valid and refunded.get(allocation.allocation_id,0)<=allocation.quantity
        refund_total=0
        for refund in db.session.scalars(select(SaleRefund).where(SaleRefund.sale_id==sale.sale_id)):
            rows=db.session.scalars(select(RefundLine).where(RefundLine.refund_id==refund.refund_id)).all()
            valid=valid and refund.total_minor==sum(row.total_minor for row in rows) and refund.currency==sale.currency
            for row in rows:
                source=original.get(row.allocation_id)
                valid=valid and bool(source and row.total_minor==row.quantity*source[1].unit_price_minor)
                if row.disposition=='no_restock':valid=valid and row.movement_id is None
                else:
                    movement=db.session.get(InventoryMovement,row.movement_id)
                    valid=valid and bool(source and movement and movement.operation_id==refund.operation_id and
                        movement.branch_id==sale.branch_id and movement.lot_id==source[0].lot_id and
                        movement.on_hand_delta==row.quantity and movement.quarantined_delta==(row.quantity if row.disposition=='quarantine' else 0))
            refund_total+=refund.total_minor
        valid=valid and refund_total<=sale.total_minor
        results.append(dict(sale_id=sale.sale_id,branch_id=sale.branch_id,currency=sale.currency,
            original_total=amount(sale.total_minor),refunded_total=amount(refund_total),balanced=bool(valid)))
    return results
