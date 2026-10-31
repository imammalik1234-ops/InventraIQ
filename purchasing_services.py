"""T12 HQ purchasing; T06 receiving and T07 own all stock effects.
Caller owns commit/rollback. No migration, existing-data mapping or commit here.
"""
from sqlalchemy import select,update,func
from werkzeug.exceptions import Forbidden,NotFound
from database import db
from models import (Supplier,PurchaseOrder,PurchaseOrderLine,PurchaseReceiptLink,PurchasingEvent,
    SKU,BranchProduct,StockLot,StockReceipt,InventoryOperation,InventoryMovement,InventoryDocument)
from authz import resolve_scope,integer_id
from security import MutationError
from expiry import integer,parse_date
from sales_services import amount,MAX_MINOR
import catalog_services as catalog
import inventory_ledger as ledger


def suppliers():
    from operational_services import _read_snapshot
    _read_snapshot()
    scope=resolve_scope('purchasing.read')
    return [dict(supplier_id=s.supplier_id,code=s.code,display_name=s.display_name,active=s.active,version=s.version)
        for s in db.session.scalars(select(Supplier).order_by(Supplier.code).limit(1000))]


def save_supplier(values,key=None):
    scope=resolve_scope('supplier.manage',consolidated=True,lock=True)
    allowed={'display_name','active','expected_version','reason'}|({'code'} if key is None else set())
    if not isinstance(values,dict) or set(values)!=allowed:raise MutationError('Supply exact supplier fields; codes are stable.')
    name=catalog.text(values['display_name'],'Supplier name',100);reason=catalog.text(values['reason'],'Reason',255)
    if type(values['active']) is not bool:raise MutationError('Supplier active must be boolean.')
    version=integer(values['expected_version'],'Expected version',0,ledger.MAX_QUANTITY)
    row=db.session.get(Supplier,integer_id(key),with_for_update=True,populate_existing=True) if key is not None else None
    if key is not None and row is None:raise NotFound('Resource not found.')
    if (row.version if row else 0)!=version:raise MutationError('Supplier revision conflict.',409)
    before=dict(code=row.code,display_name=row.display_name,active=row.active) if row else None
    if row is None:
        row=Supplier(code=catalog.code(values['code']),display_name=name,active=values['active'],version=1);db.session.add(row);db.session.flush()
    else:
        changed=db.session.execute(update(Supplier).where(Supplier.supplier_id==row.supplier_id,Supplier.version==version)
            .values(display_name=name,active=values['active'],version=version+1).execution_options(synchronize_session=False)).rowcount
        if changed!=1:raise MutationError('Supplier revision conflict.',409)
        db.session.refresh(row)
    result=dict(supplier_id=row.supplier_id,code=row.code,display_name=row.display_name,active=row.active,version=row.version)
    catalog._record(scope,'supplier',row.supplier_id,row.version,reason,before,result)
    ledger.guard(lambda:resolve_scope('supplier.manage',consolidated=True,lock=True))
    return result


def _common(values,fields):
    if not isinstance(values,dict) or set(values)!=set(fields)|{'request_key','business_reference','reason'}:
        raise MutationError('Invalid purchasing fields; authority, branch and balances are server controlled.')
    data=dict(values)
    for key,limit in [('request_key',120),('business_reference',100),('reason',255)]:data[key]=catalog.text(data[key],key,limit)
    return data


def _load(key,capability,write=False):
    scope=resolve_scope(capability,write=write,shared=write)
    query=select(PurchaseOrder).where(PurchaseOrder.order_id==integer_id(key),PurchaseOrder.branch_id.in_(scope.branches)).execution_options(populate_existing=True)
    row=db.session.scalar(query.with_for_update() if write else query)
    if row is None:raise NotFound('Resource not found.')
    return scope,row


def _lines(row):return db.session.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.order_id==row.order_id).order_by(PurchaseOrderLine.sku_id)).all()


def _catalog(row,lines):
    supplier=db.session.get(Supplier,row.supplier_id,with_for_update={'read':True},populate_existing=True)
    if not supplier or not supplier.active:raise MutationError('Supplier is archived.',409)
    for line in lines:
        sku=db.session.get(SKU,line.sku_id,with_for_update={'read':True},populate_existing=True)
        offer=db.session.get(BranchProduct,(row.branch_id,line.sku_id),with_for_update={'read':True},populate_existing=True)
        if not sku or not sku.active or not offer or not offer.enabled:raise MutationError('SKU is not active/approved at destination.',409)


def _dto(row):
    lines=_lines(row);total=sum(l.ordered*l.unit_cost_minor for l in lines)
    return dict(order_id=row.order_id,branch_id=row.branch_id,supplier_id=row.supplier_id,supplier_snapshot=row.supplier_snapshot,
        requester_id=row.requester_id,approver_id=row.approver_id,currency=row.currency,state=row.state,version=row.version,
        expected_date=row.expected_date.isoformat() if row.expected_date else None,created_at_utc=row.created_at_utc.isoformat()+'+00:00',
        updated_at_utc=row.updated_at_utc.isoformat()+'+00:00',creation_operation_id=row.creation_operation_id,approval_operation_id=row.approval_operation_id,
        ordered_cost=amount(total),received_cost=amount(sum(l.received*l.unit_cost_minor for l in lines)),
        cancelled_cost=amount(sum(l.cancelled*l.unit_cost_minor for l in lines)),
        outstanding_cost=amount(sum((l.ordered-l.received-l.cancelled)*l.unit_cost_minor for l in lines)),
        lines=[dict(line_id=l.line_id,sku_id=l.sku_id,ordered=l.ordered,received=l.received,cancelled=l.cancelled,
            outstanding=l.ordered-l.received-l.cancelled,approved_inbound=(l.ordered-l.received-l.cancelled) if row.state in ('Approved','PartiallyReceived') else 0,
            unit_cost=amount(l.unit_cost_minor)) for l in lines])


def _bump(row,version):
    if row.version!=version:raise MutationError('Purchase order revision conflict.',409)
    if db.session.execute(update(PurchaseOrder).where(PurchaseOrder.order_id==row.order_id,PurchaseOrder.version==version)
        .values(version=version+1,updated_at_utc=ledger.utcnow().replace(tzinfo=None)).execution_options(synchronize_session=False)).rowcount!=1:
        raise MutationError('Purchase order revision conflict.',409)
    db.session.refresh(row)


def _finish(scope,row,op,reason,**extra):
    db.session.flush();result=dict(_dto(row),**extra)
    db.session.add(PurchasingEvent(order_id=row.order_id,supplier_id=row.supplier_id,operation_id=op.operation_id,
        actor_id=scope.actor_id,occurred_at_utc=ledger.utcnow().replace(tzinfo=None),reason=reason,facts=result))
    return ledger.finish(scope,op,result,reason)


def create(values):
    scope=resolve_scope('purchasing.manage',write=True,shared=True)
    data=_common(values,{'supplier_id','expected_date','lines'})
    data['supplier_id']=integer_id(data['supplier_id']);data['expected_date']=parse_date(data['expected_date']).isoformat() if data['expected_date'] else None
    if not isinstance(data['lines'],list) or not 1<=len(data['lines'])<=50:raise MutationError('Supply 1 to 50 distinct SKU lines.')
    lines=[]
    for line in data['lines']:
        if not isinstance(line,dict) or set(line)!={'sku_id','quantity','unit_cost'}:raise MutationError('Line accepts SKU, quantity and agreed unit_cost only.')
        qty=integer(line['quantity'],'Quantity',1,ledger.MAX_QUANTITY);price=catalog.price(line['unit_cost'])
        if price is None:raise MutationError('Agreed unit cost is required; unknown history must stay unknown.')
        lines.append(dict(sku_id=integer_id(line['sku_id']),quantity=qty,unit_cost_minor=int(price*100)))
    data['lines']=sorted(lines,key=lambda l:l['sku_id'])
    if len({l['sku_id'] for l in lines})!=len(lines):raise MutationError('Duplicate SKU lines.')
    if sum(l['quantity']*l['unit_cost_minor'] for l in lines)>MAX_MINOR:raise MutationError('Order cost exceeds supported exact range.')
    replay,_=ledger.lookup(scope,'po_create',data,data['request_key'])
    if replay:return replay
    supplier=db.session.get(Supplier,data['supplier_id'],with_for_update={'read':True},populate_existing=True)
    if not supplier:raise NotFound('Resource not found.')
    now=ledger.utcnow().replace(tzinfo=None)
    op,_=ledger.begin(scope,'po_create',data,data['request_key'],data['business_reference'],ledger.utcnow())
    row=PurchaseOrder(supplier_id=supplier.supplier_id,supplier_snapshot=dict(code=supplier.code,display_name=supplier.display_name),
        branch_id=scope.branches[0],requester_id=scope.actor_id,currency='MYR',state='Draft',version=1,
        expected_date=parse_date(data['expected_date']) if data['expected_date'] else None,created_at_utc=now,updated_at_utc=now,creation_operation_id=op.operation_id)
    db.session.add(row);db.session.flush()
    db.session.add_all([PurchaseOrderLine(order_id=row.order_id,sku_id=l['sku_id'],ordered=l['quantity'],unit_cost_minor=l['unit_cost_minor'],received=0,cancelled=0) for l in data['lines']])
    db.session.flush();_catalog(row,_lines(row))
    ledger.guard(lambda:_catalog(row,_lines(row)))
    return _finish(scope,row,op,data['reason'])


def transition(key,action,values):
    if action=='receive':return receive(key,values)
    if action not in ('submit','approve','reject','cancel'):raise MutationError('Unsupported purchasing transition.')
    scope,row=_load(key,'purchasing.manage',True)
    data=_common(values,{'expected_version'});data['expected_version']=integer(data['expected_version'],'Expected version',1,ledger.MAX_QUANTITY);data['order_id']=row.order_id
    if action=='approve' and row.requester_id==scope.actor_id:raise Forbidden('Independent HQ approval is required.')
    replay,_=ledger.lookup(scope,'po_'+action,data,data['request_key'])
    if replay:return replay
    allowed={'submit':{'Draft'},'approve':{'Submitted'},'reject':{'Submitted'},'cancel':{'Draft','Submitted','Approved','PartiallyReceived'}}
    if row.state not in allowed[action]:raise MutationError('Order state does not permit this transition.',409)
    _bump(row,data['expected_version']);lines=_lines(row)
    if action in ('submit','approve'):
        _catalog(row,lines);ledger.guard(lambda:_catalog(row,lines))
    op,_=ledger.begin(scope,'po_'+action,data,data['request_key'],data['business_reference'],ledger.utcnow())
    if action=='cancel':
        for line in lines:line.cancelled=line.ordered-line.received
    row.state={'submit':'Submitted','approve':'Approved','reject':'Rejected','cancel':'Cancelled'}[action]
    if action=='approve':row.approver_id=scope.actor_id;row.approval_operation_id=op.operation_id
    return _finish(scope,row,op,data['reason'])


def receipt_authority(operation_id,data):
    scope=resolve_scope('purchasing.receive',write=True,shared=True)
    op=db.session.get(InventoryOperation,operation_id,populate_existing=True)
    if not op or op.status!='staging' or op.operation_type!='po_receive' or op.actor_id!=scope.actor_id or op.branch_id!=scope.branches[0]:raise Forbidden('Access denied.')
    params=op.evidence['parameters']
    scope,row=_load(params['order_id'],'purchasing.receive',True)
    line=db.session.get(PurchaseOrderLine,params['line_id'],populate_existing=True)
    if not line or line.order_id!=row.order_id or row.state not in ('Approved','PartiallyReceived') or not row.approval_operation_id:
        raise MutationError('Approved purchase order is required.',409)
    if data['sku_id']!=line.sku_id or data['quantity']!=params['delivery']['quantity'] or data['quantity']>line.ordered-line.received-line.cancelled:
        raise MutationError('Delivery exceeds authorized PO line.',409)
    return row.supplier_id


def verify_receipt_link(operation_id,receipt_id):
    link=db.session.scalar(select(PurchaseReceiptLink).where(PurchaseReceiptLink.operation_id==operation_id,PurchaseReceiptLink.receipt_id==receipt_id))
    if not link:raise MutationError('PO receipt linkage did not complete; stock was not committed.',409)
    line=db.session.get(PurchaseOrderLine,link.line_id,populate_existing=True)
    received=db.session.scalar(select(func.sum(PurchaseReceiptLink.quantity)).where(PurchaseReceiptLink.line_id==line.line_id)) or 0
    if received!=line.received:raise MutationError('PO receipt reconciliation failed.',409)


def receive(key,values):
    scope,row=_load(key,'purchasing.receive',True)
    data=_common(values,{'expected_version','line_id','delivery'})
    data['expected_version']=integer(data['expected_version'],'Expected version',1,ledger.MAX_QUANTITY);data['line_id']=integer_id(data['line_id']);data['order_id']=row.order_id
    if not isinstance(data['delivery'],dict):raise MutationError('Delivery object is required.')
    delivery=dict(data['delivery'])
    if set(delivery)&{'actor_id','branch_id','supplier_id','purchase_order_id','request_key','sku_id','reason'}:raise MutationError('Delivery authority and SKU are server controlled.')
    delivery['quantity']=integer(delivery.get('quantity'),'Quantity',1,ledger.MAX_QUANTITY)
    data['delivery']=delivery
    replay,_=ledger.lookup(scope,'po_receive',data,data['request_key'])
    if replay:return replay
    if row.state not in ('Approved','PartiallyReceived'):raise MutationError('Only approved outstanding orders can receive.',409)
    line=db.session.get(PurchaseOrderLine,data['line_id'],populate_existing=True,with_for_update=True)
    if not line or line.order_id!=row.order_id:raise NotFound('Resource not found.')
    if delivery['quantity']>line.ordered-line.received-line.cancelled:raise MutationError('Receipt exceeds approved outstanding quantity.',409)
    reference=catalog.text(delivery.get('external_reference'),'Delivery reference',100)
    if db.session.scalar(select(StockReceipt.receipt_id).where(StockReceipt.branch_id==row.branch_id,StockReceipt.external_reference==reference)):
        raise MutationError('Delivery reference already used; retry the original PO request key.',409)
    _bump(row,data['expected_version'])
    op,_=ledger.begin(scope,'po_receive',data,data['request_key'],data['business_reference'],ledger.utcnow())
    import receiving_services
    native=receiving_services.receive(dict(delivery,sku_id=line.sku_id,reason=data['reason'],request_key='po-native:'+str(op.operation_id)),_po_operation_id=op.operation_id)
    if native['replayed']:raise MutationError('A prior receipt cannot be attached as a new purchase receipt.',409)
    receipt=db.session.get(StockReceipt,native['receipt_id'])
    native_document=db.session.scalar(select(InventoryDocument).where(InventoryDocument.receipt_id==receipt.receipt_id))
    movement=db.session.scalar(select(InventoryMovement).where(InventoryMovement.operation_id==native_document.operation_id,InventoryMovement.lot_id==receipt.lot_id))
    db.session.add(PurchaseReceiptLink(line_id=line.line_id,receipt_id=receipt.receipt_id,movement_id=movement.movement_id,
        operation_id=op.operation_id,quantity=receipt.quantity,quarantined=receipt.quarantined_quantity))
    line.received+=receipt.quantity;db.session.flush()
    row.state='Received' if all(l.received+l.cancelled==l.ordered for l in _lines(row)) else 'PartiallyReceived'
    return _finish(scope,row,op,data['reason'],receipt=dict(receipt_id=receipt.receipt_id,lot_id=receipt.lot_id,
        native_operation_id=native_document.operation_id,movement_id=movement.movement_id,quantity=receipt.quantity,quarantined=receipt.quarantined_quantity))


def detail(key):
    from operational_services import _read_snapshot
    _read_snapshot()
    _,row=_load(key,'purchasing.read');result=_dto(row)
    links=db.session.execute(select(PurchaseReceiptLink,StockReceipt).join(StockReceipt).join(PurchaseOrderLine,PurchaseOrderLine.line_id==PurchaseReceiptLink.line_id)
        .where(PurchaseOrderLine.order_id==row.order_id).order_by(PurchaseReceiptLink.link_id.desc()).limit(101)).all()
    result['receipts_truncated']=len(links)>100
    result['receipts']=[dict(line_id=l.line_id,receipt_id=l.receipt_id,operation_id=l.operation_id,movement_id=l.movement_id,
        quantity=l.quantity,quarantined=l.quarantined,lot_id=r.lot_id,external_reference=r.external_reference) for l,r in links[:100]]
    result['events']=[dict(operation_id=e.operation_id,reason=e.reason,actor_id=e.actor_id,date=e.occurred_at_utc.isoformat()+'+00:00',state=e.facts['state'])
        for e in db.session.scalars(select(PurchasingEvent).where(PurchasingEvent.order_id==row.order_id).order_by(PurchasingEvent.event_id.desc()).limit(100))]
    return result


def listing(page=1):
    from operational_services import _read_snapshot
    _read_snapshot()
    scope=resolve_scope('purchasing.read');page=integer(page,'Page',1,10000)
    rows=db.session.scalars(select(PurchaseOrder).where(PurchaseOrder.branch_id.in_(scope.branches)).order_by(PurchaseOrder.order_id.desc())
        .offset((page-1)*25).limit(26)).all()
    return dict(page=page,has_more=len(rows)>25,page_size=25,orders=[_dto(r) for r in rows[:25]])


def reconcile():
    from operational_services import _read_snapshot
    _read_snapshot()
    scope=resolve_scope('purchasing.read');result=[]
    rows=db.session.execute(select(PurchaseOrderLine,PurchaseOrder).join(PurchaseOrder)
        .where(PurchaseOrder.branch_id.in_(scope.branches)).order_by(PurchaseOrderLine.line_id)).all()
    for line,row in rows:
        links=db.session.scalars(select(PurchaseReceiptLink).where(PurchaseReceiptLink.line_id==line.line_id)).all();valid=True
        latest=db.session.scalar(select(PurchasingEvent).where(PurchasingEvent.order_id==row.order_id).order_by(PurchasingEvent.event_id.desc()).limit(1))
        snapshot=next((entry for entry in latest.facts['lines'] if entry['line_id']==line.line_id),None) if latest else None
        valid=bool(latest and snapshot and latest.facts['state']==row.state and latest.facts['version']==row.version and
            snapshot['cancelled']==line.cancelled and snapshot['received']==line.received and snapshot['ordered']==line.ordered and
            snapshot['unit_cost']==amount(line.unit_cost_minor))
        for link in links:
            receipt=db.session.get(StockReceipt,link.receipt_id);movement=db.session.get(InventoryMovement,link.movement_id)
            lot=db.session.get(StockLot,receipt.lot_id);op=db.session.get(InventoryOperation,link.operation_id)
            valid=valid and receipt.sku_id==line.sku_id and receipt.branch_id==row.branch_id and lot.supplier_id==row.supplier_id and receipt.quantity==link.quantity and receipt.quarantined_quantity==link.quarantined and movement.branch_id==row.branch_id and movement.lot_id==receipt.lot_id and movement.sku_id==line.sku_id and movement.on_hand_delta==link.quantity and movement.quarantined_delta==link.quarantined and op.status=='committed' and db.session.get(InventoryOperation,movement.operation_id).status=='committed'
        valid=valid and sum(l.quantity for l in links)==line.received and line.received+line.cancelled<=line.ordered
        result.append(dict(order_id=row.order_id,line_id=line.line_id,ordered=line.ordered,received=line.received,cancelled=line.cancelled,
            outstanding=line.ordered-line.received-line.cancelled,received_cost=amount(line.received*line.unit_cost_minor),balanced=bool(valid)))
    return result
