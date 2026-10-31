"""T09 scoped read DTOs. No writes, global caches or inferred financial facts."""
from datetime import timedelta
from itertools import groupby
from flask import request,has_request_context,current_app
from sqlalchemy import select,func,or_
from database import db
from models import (Branch,BranchStock,StockLot,SKU,BranchProduct,CategoryExpiryPolicy,
 ProductExpiryPolicyOverride,LegacyProductMapping,Inventory,Product,Sale,PricedSale,
 SaleLine,SaleRefund,RefundLine,InventoryOperation,InventoryMovement,User,UserBranch)
from authz import resolve_scope,managed_sku_ids
from expiry import utcnow,parse_date,integer,Policy,validate_policy,classify,POLICY_FIELDS,zone
from security import MutationError
from sales_services import amount


def _read_snapshot():
    """Keep inventory, quantities and monetary aggregates in one read snapshot.

    Python sqlite3 legacy transaction mode does not BEGIN for SELECT. Flask owns
    rollback/removal at request teardown; this does not commit or change data.
    Do not silently promise coherent aggregates under MySQL READ COMMITTED.
    """
    connection=db.session.connection()
    if connection.dialect.name=='sqlite':
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN')
    elif connection.dialect.name=='mysql':
        if connection.get_isolation_level() not in ('REPEATABLE READ','SERIALIZABLE'):
            raise MutationError('Coherent reporting requires repeatable-read database isolation.',503)


def filters(values=None,default_end=None):
    values=request.args if values is None and has_request_context() else (values or {})
    allowed={'branch_id','start','end','page','page_size','search','status','sort','cursor','snapshot'}
    if set(values)-allowed:raise MutationError('Unsupported reporting filter.')
    if hasattr(values,'getlist') and any(len(values.getlist(k))!=1 for k in values):raise MutationError('Repeated reporting filters are not allowed.')
    now=utcnow();end=parse_date(values['end']) if values.get('end') else (default_end or now.date())
    start=parse_date(values['start']) if values.get('start') else end-timedelta(days=29)
    if start>end or (end-start).days>365:raise MutationError('Use an inclusive date range of at most 366 days.')
    search=values.get('search','')
    if not isinstance(search,str):raise MutationError('Search text is required.')
    search=search.strip()
    if len(search)>80 or any(ord(c)<32 for c in search):raise MutationError('Invalid search text.')
    status=values.get('status','all');sort=values.get('sort','asc')
    if status not in ('all','healthy','warning','expired','review','not_tracked','low_stock') or sort not in ('asc','desc'):
        raise MutationError('Invalid status or sort.')
    result=dict(start=start.isoformat(),end=end.isoformat(),page=integer(values.get('page',1),'Page',1,10000),
        page_size=integer(values.get('page_size',25),'Page size',1,100),search=search,status=status,sort=sort)
    if values.get('cursor'):
        if not isinstance(values['cursor'],str) or len(values['cursor'])>4096:raise MutationError('Invalid page token.')
        result['cursor']=values['cursor']
    if values.get('snapshot'):
        if not isinstance(values['snapshot'],str) or len(values['snapshot'])>4096:raise MutationError('Invalid snapshot token.')
        result['snapshot']=values['snapshot']
    return result


def _policy(sku,category,override):
    row=override or category
    if row is None:return None
    try:data=validate_policy({field:getattr(row,field) for field in POLICY_FIELDS})
    except MutationError:return None
    return Policy(source='sku' if override else 'category',key=sku.sku_id if override else sku.category_id,
        revision=row.revision,effective_at=row.effective_at.isoformat()+'Z',**data)


def _lots(scope,now,sort,lot_id=None):
    opened=select(InventoryMovement.movement_id).join(InventoryOperation,InventoryOperation.operation_id==InventoryMovement.operation_id).where(
        InventoryMovement.branch_id==BranchStock.branch_id,InventoryMovement.lot_id==BranchStock.lot_id,InventoryOperation.status=='committed').exists()
    mapped=select(LegacyProductMapping.lot_id).where(LegacyProductMapping.branch_id==BranchStock.branch_id,LegacyProductMapping.lot_id==BranchStock.lot_id).exists()
    approved_opening=select(InventoryMovement.movement_id).join(InventoryOperation,InventoryOperation.operation_id==InventoryMovement.operation_id).where(
        InventoryMovement.branch_id==BranchStock.branch_id,InventoryMovement.lot_id==BranchStock.lot_id,InventoryOperation.status=='committed',InventoryOperation.operation_type=='opening').exists()
    query=(select(BranchStock,StockLot,SKU,Branch,BranchProduct,CategoryExpiryPolicy,ProductExpiryPolicyOverride,opened)
        .join(StockLot,StockLot.lot_id==BranchStock.lot_id).join(SKU,SKU.sku_id==BranchStock.sku_id)
        .join(Branch,Branch.branch_id==BranchStock.branch_id)
        .join(BranchProduct,(BranchProduct.branch_id==BranchStock.branch_id)&(BranchProduct.sku_id==BranchStock.sku_id))
        .outerjoin(CategoryExpiryPolicy,CategoryExpiryPolicy.category_id==SKU.category_id)
        .outerjoin(ProductExpiryPolicyOverride,ProductExpiryPolicyOverride.sku_id==SKU.sku_id)
        .where(BranchStock.branch_id.in_(scope.branches),SKU.sku_id.in_(managed_sku_ids()),or_(~mapped,approved_opening)))
    if lot_id is not None:query=query.where(BranchStock.lot_id==lot_id)
    order=[BranchStock.branch_id,BranchStock.sku_id,BranchStock.lot_id]
    query=query.order_by(*(c.desc() if sort=='desc' else c.asc() for c in order))
    for stock,lot,sku,branch,offer,category,override,ledger_opened in db.session.execute(query.execution_options(yield_per=200)):
        policy=_policy(sku,category,override)
        state=classify(policy=policy,expiry_date=lot.expiry_date,expiry_timezone=lot.expiry_timezone,
            expires_at_utc=lot.expires_at_utc,validation_state=lot.validation_state,branch_timezone=branch.timezone,as_of=now)
        if not ledger_opened and any((stock.on_hand,stock.reserved,stock.quarantined_quantity)):state=dict(state,status='review')
        if (not sku.active or not offer.enabled) and state['status']!='expired':state=dict(state,status='review')
        held=state['status'] in ('review','expired')
        yield dict(source='native_lot',branch_id=branch.branch_id,branch_code=branch.code,sku_id=sku.sku_id,
            sku_code=sku.code,name=sku.name,lot_id=lot.lot_id,lot_code=lot.lot_code,on_hand=stock.on_hand,
            reserved=stock.reserved,quarantined=stock.quarantined_quantity,
            available=0 if held else stock.on_hand-stock.reserved-stock.quarantined_quantity,
            unusable=stock.on_hand if held else stock.quarantined_quantity,
            status=state['status'],days_remaining=state['days_remaining'],expiry_date=lot.expiry_date.isoformat() if lot.expiry_date else None,
            expiry_timezone=lot.expiry_timezone,branch_timezone=branch.timezone,stock_revision=stock.version,
            can_reorder=bool(sku.active and offer.enabled),threshold=offer.reorder_level_override,availability_verified=bool(ledger_opened or not stock.on_hand),
            hold_reason='Opening reconciliation required' if not ledger_opened and stock.on_hand else 'SKU archived or not offered' if not sku.active or not offer.enabled else state['status'])
    legacy_opening=select(InventoryMovement.movement_id).join(InventoryOperation,InventoryOperation.operation_id==InventoryMovement.operation_id).where(
        InventoryMovement.branch_id==LegacyProductMapping.branch_id,InventoryMovement.lot_id==LegacyProductMapping.lot_id,InventoryOperation.status=='committed',InventoryOperation.operation_type=='opening').exists()
    legacy=(select(LegacyProductMapping,Inventory,Product,Branch,SKU)
        .join(Inventory,Inventory.product_id==LegacyProductMapping.legacy_product_id)
        .join(Product,Product.product_id==LegacyProductMapping.legacy_product_id)
        .join(Branch,Branch.branch_id==LegacyProductMapping.branch_id).join(SKU,SKU.sku_id==LegacyProductMapping.sku_id)
        .where(LegacyProductMapping.branch_id.in_(scope.branches),~legacy_opening))
    if lot_id is not None:legacy=legacy.where(LegacyProductMapping.lot_id==lot_id)
    legacy=legacy.order_by(*(c.desc() if sort=='desc' else c.asc() for c in (LegacyProductMapping.branch_id,LegacyProductMapping.sku_id,LegacyProductMapping.lot_id)))
    for link,inventory,product,branch,sku in db.session.execute(legacy.execution_options(yield_per=200)):
        yield dict(source='legacy_inventory',branch_id=branch.branch_id,branch_code=branch.code,sku_id=sku.sku_id,
            sku_code=sku.code,name=product.name,lot_id=link.lot_id,lot_code='Legacy source '+str(product.product_id),
            on_hand=inventory.quantity,reserved=0,quarantined=0,available=0,unusable=inventory.quantity,status='review',
            days_remaining=None,expiry_date=product.expiry_date.isoformat() if product.expiry_date else None,
            expiry_timezone=None,branch_timezone=branch.timezone,stock_revision=product.version,
            can_reorder=bool(sku.active and not product.archived),threshold=product.reorder_level,availability_verified=False,hold_reason='Legacy source awaiting approved opening')


def report(values=None,*,capability='reports.read',sections=('inventory','alerts','sales')):
    _read_snapshot()
    incoming=request.args if values is None and has_request_context() else (values or {})
    scope=resolve_scope(capability,branch_id=incoming.get('branch_id'))
    now=utcnow()
    branch_rows=db.session.scalars(select(Branch).where(Branch.branch_id.in_(scope.branches)).order_by(Branch.branch_id).execution_options(populate_existing=True)).all()
    dates=[]
    for branch in branch_rows:
        try:dates.append(now.astimezone(zone(branch.timezone)).date())
        except MutationError:pass
    query=filters(incoming,default_end=max(dates) if dates else now.date())
    from query_paging import report_snapshot
    now,snapshot=report_snapshot(scope,query,now)
    branches={b.branch_id:dict(branch_id=b.branch_id,code=b.code,name=b.display_name,
        outgoing_transit=0,incoming_transit=0,authorized_transfer_loss=0,on_hand=0,available=0,reserved=0,quarantined=0,unusable=0,legacy_unverified=0,lot_count=0,
        expired_lots=0,warning_lots=0,review_lots=0,low_stock_skus=0,sold_units=0,refunded_units=0,
        known_sold_units=0,unknown_revenue_units=0,gross_minor=0,refund_minor=0)
        for b in branch_rows}
    from scoped_inventory_queries import lists_and_totals
    inventory, conditions = lists_and_totals(scope, now, query, branches,sections)
    offset=(query['page']-1)*query['page_size']
    start,end=query['start'],query['end']
    sold=(select(PricedSale.branch_id,func.sum(SaleLine.quantity)).join(SaleLine,SaleLine.sale_id==PricedSale.sale_id)
        .where(PricedSale.branch_id.in_(scope.branches),PricedSale.business_date.between(start,end)).group_by(PricedSale.branch_id))
    for branch,units in db.session.execute(sold):branches[branch]['known_sold_units']=int(units or 0)
    for branch,total in db.session.execute(select(PricedSale.branch_id,func.sum(PricedSale.total_minor)).where(
        PricedSale.branch_id.in_(scope.branches),PricedSale.business_date.between(start,end)).group_by(PricedSale.branch_id)):
        branches[branch]['gross_minor']=int(total or 0)
    for branch,total in db.session.execute(select(SaleRefund.branch_id,func.sum(SaleRefund.total_minor)).where(
        SaleRefund.branch_id.in_(scope.branches),SaleRefund.business_date.between(start,end)).group_by(SaleRefund.branch_id)):
        branches[branch]['refund_minor']=int(total or 0)
    for branch,units in db.session.execute(select(SaleRefund.branch_id,func.sum(RefundLine.quantity)).join(RefundLine,RefundLine.refund_id==SaleRefund.refund_id).where(
        SaleRefund.branch_id.in_(scope.branches),SaleRefund.business_date.between(start,end)).group_by(SaleRefund.branch_id)):
        branches[branch]['refunded_units']=int(units or 0)
    unknown=(select(LegacyProductMapping.branch_id,func.sum(Sale.quantity)).join(Sale,Sale.product_id==LegacyProductMapping.legacy_product_id)
        .where(LegacyProductMapping.branch_id.in_(scope.branches),Sale.sale_date.between(start,end)).group_by(LegacyProductMapping.branch_id))
    for branch,units in db.session.execute(unknown):branches[branch]['unknown_revenue_units']+=int(units or 0)
    unknown_native=(select(InventoryOperation.branch_id,-func.sum(InventoryMovement.on_hand_delta))
        .join(InventoryMovement,InventoryMovement.operation_id==InventoryOperation.operation_id).where(
            InventoryOperation.branch_id.in_(scope.branches),InventoryOperation.status=='committed',
            InventoryOperation.operation_type.in_(('sale','reserved_sale')),InventoryOperation.business_date.between(start,end)).group_by(InventoryOperation.branch_id))
    for branch,units in db.session.execute(unknown_native):branches[branch]['unknown_revenue_units']+=int(units or 0)
    from models import StockTransfer,StockTransferAllocation,TransferTransit
    for source,dest,remaining,lost in db.session.execute(select(StockTransfer.source_branch_id,StockTransfer.destination_branch_id,
        func.sum(TransferTransit.remaining),func.sum(TransferTransit.lost)).join(StockTransferAllocation,StockTransferAllocation.transfer_id==StockTransfer.transfer_id)
        .join(TransferTransit,TransferTransit.allocation_id==StockTransferAllocation.allocation_id).where(or_(
            StockTransfer.source_branch_id.in_(scope.branches),StockTransfer.destination_branch_id.in_(scope.branches)))
        .group_by(StockTransfer.source_branch_id,StockTransfer.destination_branch_id)):
        if source in branches:
            branches[source]['outgoing_transit']+=int(remaining or 0);branches[source]['authorized_transfer_loss']+=int(lost or 0)
        if dest in branches:branches[dest]['incoming_transit']+=int(remaining or 0)
    numeric=('outgoing_transit','incoming_transit','authorized_transfer_loss','on_hand','available','reserved','quarantined','unusable','legacy_unverified','lot_count','expired_lots','warning_lots','review_lots','low_stock_skus','sold_units','refunded_units','known_sold_units','unknown_revenue_units','gross_minor','refund_minor')
    def monetary(row):
        row['company_owned_physical']=row['on_hand']+row['outgoing_transit']
        row['sold_units']=row['known_sold_units']+row['unknown_revenue_units'];row['net_units']=row['sold_units']-row['refunded_units']
        row.update(recorded_gross=amount(row['gross_minor']),recorded_refunds=amount(row['refund_minor']),recorded_net=amount(row['gross_minor']-row['refund_minor']))
        return row
    for row in branches.values():monetary(row)
    total=monetary({field:sum(row[field] for row in branches.values()) for field in numeric})
    if scope.role in ('hq_admin','hq_analyst','manager'):
        from models import PurchaseOrder,PurchaseOrderLine
        inbound=dict(db.session.execute(select(PurchaseOrder.branch_id,func.sum(PurchaseOrderLine.ordered-PurchaseOrderLine.received-PurchaseOrderLine.cancelled))
            .join(PurchaseOrderLine,PurchaseOrderLine.order_id==PurchaseOrder.order_id).where(PurchaseOrder.branch_id.in_(scope.branches),
                PurchaseOrder.state.in_(('Approved','PartiallyReceived'))).group_by(PurchaseOrder.branch_id)).all())
        for branch,row in branches.items():row['approved_po_inbound']=int(inbound.get(branch) or 0)
        total['approved_po_inbound']=sum(row['approved_po_inbound'] for row in branches.values())
    sales_query=select(PricedSale.sale_id,PricedSale.branch_id,PricedSale.business_date,PricedSale.total_minor).where(PricedSale.branch_id.in_(scope.branches),PricedSale.business_date.between(start,end))
    from query_paging import page
    sale_page=page(sales_query,PricedSale.sale_id,scope,query,'report-sales') if 'sales' in sections else dict(total=0,items=[],next_cursor=None)
    sale_count=sale_page['total'];sale_rows=sale_page['items']
    activity=db.session.execute(select(InventoryOperation.operation_id,InventoryOperation.branch_id,InventoryOperation.operation_type,
        InventoryOperation.business_reference,InventoryOperation.business_date).where(InventoryOperation.branch_id.in_(scope.branches),
            InventoryOperation.status=='committed',InventoryOperation.business_date.between(start,end)).order_by(InventoryOperation.operation_id.desc()).limit(10)).all()
    result=dict(scope=dict(branch_ids=list(scope.branches),consolidated=scope.consolidated,role=scope.role),filters=query,
        as_of_utc=now.isoformat(),currency='MYR',unit='integer units',total=total,branches=list(branches.values()),
        pagination=dict(snapshot=snapshot,consistency='SQL offset inventory/alerts with signed optimistic revision; reuse snapshot on navigation. Changed facts/policies require restart (409). Sales also offer high-watermark ID cursors. Tokens expire in 15 minutes.'),
        inventory=inventory,alerts=conditions,
        sales=dict(items=[dict(sale_id=r['sale_id'],branch_id=r['branch_id'],date=r['business_date'].isoformat(),total=amount(r['total_minor'])) for r in sale_rows],total=sale_count,next_cursor=sale_page['next_cursor']),
        activity=[dict(operation_id=r[0],branch_id=r[1],kind=r[2],reference=r[3],date=r[4].isoformat()) for r in activity],
        provenance=dict(inventory='Live native lots plus unmigrated Inventory source; no T04 snapshot duplication',
            transfers='Transit attributed once to source in company_owned_physical; incoming is informational. Authorized losses are separate, never silent.',
            financial='Immutable T08 sale/refund headers; refunds use their own business dates',unknown='Legacy Sale and pre-T08 quantity movements; no inferred revenue',
            coverage='Inventory is current as_of; finance uses inclusive branch business-date labels. Default end date is the latest current business date among included branch timezones; invalid zones fall back to UTC only when none are valid.',
            filters='Search/status affect lot and alert lists only; summaries cover entire authorized scope.',
            consistency='One database read snapshot per request; MySQL requires REPEATABLE READ or SERIALIZABLE.',
            pagination='Maximum 100 rows per list. Reuse pagination.snapshot on offset navigation; changed facts require restart. Sales next_cursor provides immutable-ID keyset continuation. No retained transaction between requests.'))
    if getattr(current_app,'_t15_target',None) is not None:
        result['provenance']['simulation']='Synthetic demo SYN-T15-V1: generated operational facts; all MYR amounts are simulated, not real-world revenue. Historical observations are separate.'
    return result


def staff_page(values=None):
    incoming=request.args if values is None and has_request_context() else (values or {})
    scope=resolve_scope('inventory.read',branch_id=incoming.get('branch_id'));query=filters(incoming);offset=(query['page']-1)*query['page_size']
    stmt=select(UserBranch,User).join(User,User.user_id==UserBranch.user_id).where(UserBranch.branch_id.in_(scope.branches))
    if scope.role=='clerk':stmt=stmt.where(User.user_id==scope.actor_id)
    total=db.session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows=db.session.execute(stmt.order_by(UserBranch.branch_id,User.user_id).offset(offset).limit(query['page_size'])).all()
    return dict(total=total,items=[dict(branch_id=a.branch_id,user_id=u.user_id,name=u.name or 'Unnamed member',role=a.branch_role,
        assignment_active=a.active,user_enabled=u.enabled) for a,u in rows])


def lot_detail(lot_id):
    from authz import integer_id
    scope=resolve_scope('inventory.read')
    row=next(_lots(scope,utcnow(),'asc',integer_id(lot_id)),None)
    if row is None:raise MutationError('Resource not found.',404)
    return row
