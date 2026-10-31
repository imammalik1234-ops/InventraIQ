"""Permission-scoped bounded facts. No writes, stock locks, fitting or caching."""
from collections import defaultdict
from datetime import timedelta
from sqlalchemy import select,or_
from database import db
from models import (BranchStock,StockLot,SKU,Branch,BranchProduct,CategoryExpiryPolicy,ProductExpiryPolicyOverride,
    InventoryMovement,InventoryOperation,LegacyProductMapping,PurchaseOrder,PurchaseOrderLine,
    StockTransfer,StockTransferAllocation,TransferExecution,TransferTransit,TransferDiscrepancy,TransferResolution)
from operational_services import _policy
from expiry import classify,UTC
from services.recommendation_math import day_end
from security import MutationError


def iso(value):return value.replace(tzinfo=UTC).isoformat() if value is not None else None


def bounded(query,limit):
    rows=db.session.execute(query.limit(limit+1)).all()
    if len(rows)>limit:raise MutationError('Recommendation fact bound exceeded; narrow SKU selection.',503)
    return rows


def lots(branches,skus,now):
    opened=select(InventoryMovement.movement_id).join(InventoryOperation).where(
        InventoryMovement.branch_id==BranchStock.branch_id,InventoryMovement.lot_id==BranchStock.lot_id,
        InventoryOperation.status=='committed').exists()
    mapping=select(LegacyProductMapping.lot_id).where(LegacyProductMapping.branch_id==BranchStock.branch_id,
        LegacyProductMapping.lot_id==BranchStock.lot_id).exists()
    opening=select(InventoryMovement.movement_id).join(InventoryOperation).where(
        InventoryMovement.branch_id==BranchStock.branch_id,InventoryMovement.lot_id==BranchStock.lot_id,
        InventoryOperation.status=='committed',InventoryOperation.operation_type=='opening').exists()
    query=(select(BranchStock,StockLot,SKU,Branch,BranchProduct,CategoryExpiryPolicy,ProductExpiryPolicyOverride,opened,mapping,opening)
        .join(StockLot,StockLot.lot_id==BranchStock.lot_id).join(SKU,SKU.sku_id==BranchStock.sku_id)
        .join(Branch,Branch.branch_id==BranchStock.branch_id)
        .join(BranchProduct,(BranchProduct.branch_id==BranchStock.branch_id)&(BranchProduct.sku_id==BranchStock.sku_id))
        .outerjoin(CategoryExpiryPolicy,CategoryExpiryPolicy.category_id==SKU.category_id)
        .outerjoin(ProductExpiryPolicyOverride,ProductExpiryPolicyOverride.sku_id==SKU.sku_id)
        .where(BranchStock.branch_id.in_(branches),BranchStock.sku_id.in_(skus))
        .order_by(BranchStock.branch_id,BranchStock.sku_id,BranchStock.lot_id))
    result=defaultdict(list)
    for stock,lot,sku,branch,offer,category,override,has_opened,mapped,has_opening in bounded(query,2000):
        policy=_policy(sku,category,override)
        state=classify(policy=policy,expiry_date=lot.expiry_date,expiry_timezone=lot.expiry_timezone,
            expires_at_utc=lot.expires_at_utc,validation_state=lot.validation_state,branch_timezone=branch.timezone,as_of=now)
        reasons=[]
        if not has_opened or (mapped and not has_opening):reasons.append('OPENING_NOT_RECONCILED')
        if stock.first_received_at_utc and stock.first_received_at_utc.replace(tzinfo=UTC)>now:reasons.append('FUTURE_RECEIPT')
        if state['status']=='review':reasons.append('LOT_REQUIRES_REVIEW')
        eligible=not reasons and state['status'] not in ('expired','review') and sku.active and offer.enabled
        result[(stock.branch_id,stock.sku_id)].append(dict(key=lot.lot_id,lot_id=lot.lot_id,
            stock_revision=stock.version,lot_revision=lot.version,on_hand=stock.on_hand,reserved=stock.reserved,
            quarantined=stock.quarantined_quantity,available=stock.on_hand-stock.reserved-stock.quarantined_quantity if eligible else 0,
            eligibility=state['status'],verified=not reasons,reason_codes=reasons,
            expires_at=iso(lot.expires_at_utc),expiry_date=lot.expiry_date.isoformat() if lot.expiry_date else None,
            expiry_timezone=lot.expiry_timezone,first_received=iso(stock.first_received_at_utc),
            expiry_policy=policy.snapshot() if policy else None,source='on_hand'))
    return result


def inbound(branches,skus,now,*,include_purchasing,include_transfer,consolidated):
    result=defaultdict(lambda:dict(purchase_orders=[],transfers=[],complete=include_purchasing and include_transfer and consolidated))
    if include_purchasing:
        query=(select(PurchaseOrder,PurchaseOrderLine,SKU,CategoryExpiryPolicy,ProductExpiryPolicyOverride)
            .join(PurchaseOrderLine,PurchaseOrderLine.order_id==PurchaseOrder.order_id).join(SKU,SKU.sku_id==PurchaseOrderLine.sku_id)
            .outerjoin(CategoryExpiryPolicy,CategoryExpiryPolicy.category_id==SKU.category_id)
            .outerjoin(ProductExpiryPolicyOverride,ProductExpiryPolicyOverride.sku_id==SKU.sku_id)
            .where(PurchaseOrder.branch_id.in_(branches),PurchaseOrderLine.sku_id.in_(skus),
                PurchaseOrder.state.in_(('Approved','PartiallyReceived'))).order_by(PurchaseOrder.order_id,PurchaseOrderLine.line_id))
        for po,line,sku,category,override in bounded(query,500):
            remaining=line.ordered-line.received-line.cancelled
            if remaining<=0:continue
            policy=_policy(sku,category,override)
            result[(po.branch_id,line.sku_id)]['purchase_orders'].append(dict(reference=po.order_id,line_id=line.line_id,
                document_version=po.version,quantity=remaining,received=line.received,cancelled=line.cancelled,
                expected_date=po.expected_date.isoformat() if po.expected_date else None,
                perishable=policy.require_expiry if policy else None))
    if include_transfer:
        disputed=select(TransferDiscrepancy.discrepancy_id).outerjoin(TransferResolution,
            TransferResolution.discrepancy_id==TransferDiscrepancy.discrepancy_id).where(
            TransferDiscrepancy.allocation_id==StockTransferAllocation.allocation_id,
            TransferDiscrepancy.location=='transit',TransferResolution.discrepancy_id.is_(None)).exists()
        query=(select(StockTransfer,StockTransferAllocation,TransferExecution,TransferTransit,StockLot,SKU,Branch,
                CategoryExpiryPolicy,ProductExpiryPolicyOverride,disputed)
            .join(StockTransferAllocation,StockTransferAllocation.transfer_id==StockTransfer.transfer_id)
            .outerjoin(TransferExecution,TransferExecution.transfer_id==StockTransfer.transfer_id)
            .outerjoin(TransferTransit,TransferTransit.allocation_id==StockTransferAllocation.allocation_id)
            .join(StockLot,StockLot.lot_id==StockTransferAllocation.lot_id).join(SKU,SKU.sku_id==StockTransferAllocation.sku_id)
            .join(Branch,Branch.branch_id==StockTransfer.destination_branch_id)
            .outerjoin(CategoryExpiryPolicy,CategoryExpiryPolicy.category_id==SKU.category_id)
            .outerjoin(ProductExpiryPolicyOverride,ProductExpiryPolicyOverride.sku_id==SKU.sku_id)
            .where(StockTransfer.destination_branch_id.in_(branches),StockTransferAllocation.sku_id.in_(skus),StockTransfer.state=='Approved'))
        # Branch staff cannot see another source's undispatched proposal/reservation.
        if not consolidated:query=query.where(TransferExecution.transfer_id.is_not(None))
        for transfer,allocation,execution,transit,lot,sku,branch,category,override,issue in bounded(query.order_by(
            StockTransfer.transfer_id,StockTransferAllocation.allocation_id),500):
            remaining=transit.remaining if execution is not None and transit is not None else allocation.quantity if execution is None else None
            if remaining==0:continue
            policy=_policy(sku,category,override);arrival=transfer.planned_arrival_utc.replace(tzinfo=UTC)
            state=classify(policy=policy,expiry_date=lot.expiry_date,expiry_timezone=lot.expiry_timezone,
                expires_at_utc=lot.expires_at_utc,validation_state=lot.validation_state,branch_timezone=branch.timezone,as_of=arrival)
            valid=not issue and state['status'] not in ('expired','review') and policy is not None
            if valid and state['days_remaining'] is not None:
                valid=state['days_remaining']>=policy.min_remaining_days_on_transfer_receipt
            result[(transfer.destination_branch_id,allocation.sku_id)]['transfers'].append(dict(
                reference=transfer.transfer_id,allocation_id=allocation.allocation_id,document_version=transfer.version,
                quantity=remaining,kind='transit_remaining' if execution else 'approved_reservation',
                planned_arrival=arrival.isoformat(),lot_id=lot.lot_id,expires_at=iso(lot.expires_at_utc),
                eligible_at_arrival=valid,disputed=bool(issue)))
    return result


def expected_inputs(facts,origin,horizon,tz,now):
    """Date-only/within-day arrivals become usable next business day, never earlier."""
    from datetime import date
    from expiry import zone
    last=origin+timedelta(days=horizon);rows=[];reasons=[]
    for po in facts['purchase_orders']:
        if po['expected_date'] is None:reasons.append('PO_ARRIVAL_UNKNOWN');continue
        due=date.fromisoformat(po['expected_date'])
        if due>last:continue
        if due<origin:reasons.append('PO_OVERDUE');continue
        if po['perishable'] is not False:reasons.append('PO_EXPIRY_ELIGIBILITY_UNKNOWN');continue
        ready=due+timedelta(days=1)
        if ready<=last:rows.append(dict(key='PO-'+str(po['line_id']),available=po['quantity'],ready_date=ready.isoformat(),
            expires_at=None,first_received=None,source='expected_po',reference=po['reference']))
    for transfer in facts['transfers']:
        arrival=__import__('datetime').datetime.fromisoformat(transfer['planned_arrival'])
        if arrival>day_end(last,tz):continue
        if arrival<now:reasons.append('TRANSFER_ARRIVAL_OVERDUE');continue
        if transfer['quantity'] is None:reasons.append('TRANSFER_QUANTITY_UNKNOWN');continue
        if not transfer['eligible_at_arrival']:reasons.append('TRANSFER_ARRIVAL_INELIGIBLE');continue
        ready=arrival.astimezone(zone(tz)).date()+timedelta(days=1)
        if ready<=last:rows.append(dict(key='TRANSFER-'+str(transfer['allocation_id']),available=transfer['quantity'],
            ready_date=ready.isoformat(),expires_at=transfer['expires_at'],first_received=None,
            source='expected_transfer',reference=transfer['reference']))
    return rows,sorted(set(reasons))
