"""T06 receiving validation backed by the T07 atomic movement engine."""
import hashlib
import json
from sqlalchemy import select, update
from database import db
from models import (SKU, Branch, BranchProduct, BranchStock, StockLot, StockReceipt, CompanyExpirySettings, LegacyProductMapping, LotIdentity)
from authz import resolve_scope, integer_id, managed_sku_ids
from security import MutationError
from expiry import utcnow, integer, parse_date, canonical_expiry, classify, conversion_provenance, zone, UTC
import catalog_services as catalog
import inventory_ledger as ledger


def _receipt_result(row, replay=False):
    return dict(receipt_id=row.receipt_id,branch_id=row.branch_id,sku_id=row.sku_id,lot_id=row.lot_id,
                quantity=row.quantity,quarantined_quantity=row.quarantined_quantity,
                external_reference=row.external_reference,replayed=replay)


def receive(values, *, _po_operation_id=None):
    scope=resolve_scope('stock.receive',write=True,shared=True)
    allowed={'branch_id','mode','sku_id','lot_id','lot_code','supplier_lot_code','quantity','expiry_date',
             'request_key','external_reference','receipt_kind','reason','evidence','label_timezone','zone_reason'}
    if not isinstance(values,dict) or set(values)-allowed:raise MutationError('Invalid receiving fields. Catalog, price, policy and clock changes are not accepted.')
    if 'branch_id' in values and integer_id(values['branch_id']) != scope.branches[0]:raise MutationError('Access denied.',403)
    branch_id=scope.branches[0]
    data=dict(values)
    data.pop('branch_id',None)
    data['sku_id']=integer_id(data.get('sku_id'))
    data['quantity']=integer(data.get('quantity'),'Quantity',1,2147483647)
    data['external_reference']=catalog.text(data.get('external_reference'),'Delivery reference',100)
    if data.get('mode') not in ('new_lot','existing_lot'):raise MutationError('Explicitly choose a new lot or an existing lot.')
    data['receipt_kind']=data.get('receipt_kind','normal')
    if data['receipt_kind'] not in ('normal','quarantine'):raise MutationError('Invalid receipt kind.')
    data['reason']=catalog.text(data.get('reason'),'Receipt reason',255)
    data['evidence']=catalog.text(data.get('evidence'),'Observed label / delivery evidence',255)
    if data.get('expiry_date') not in (None,''):
        if hasattr(data['expiry_date'],'isoformat'):data['expiry_date']=data['expiry_date'].isoformat()
        data['expiry_date']=catalog.text(data['expiry_date'],'Observed expiry label',80)
    else:data['expiry_date']=None
    if data['mode']=='new_lot':
        if data.get('lot_id') is not None:raise MutationError('New lots cannot supply an existing lot ID.')
        data['lot_code']=catalog.code(data.get('lot_code'))
        data['supplier_lot_code']=catalog.text(data.get('supplier_lot_code'),'Actual supplier lot identifier',100)
    else:
        data['lot_id']=integer_id(data.get('lot_id'))
        if any(key in data for key in ('lot_code','supplier_lot_code','label_timezone','zone_reason')):
            raise MutationError('Existing lot facts cannot be changed by receiving.')
    if 'label_timezone' in data:
        resolve_scope('batch.correct',write=True)
        zone(data['label_timezone']);data['zone_reason']=catalog.text(data.get('zone_reason'),'Timezone approval evidence',255)
    if 'request_key' in data:data['request_key']=catalog.text(data['request_key'],'Request key',120)
    supplier_id=None
    if _po_operation_id is not None:
        from purchasing_services import receipt_authority
        supplier_id=receipt_authority(_po_operation_id,data)
    else:
        from models import PurchaseReceiptLink
        if db.session.scalar(select(PurchaseReceiptLink.link_id).join(StockReceipt,StockReceipt.receipt_id==PurchaseReceiptLink.receipt_id).where(StockReceipt.branch_id==branch_id,StockReceipt.external_reference==data['external_reference'])):
            raise MutationError('Use the original approved PO receiving request.',409)
        if data['mode']=='existing_lot':
            known=db.session.get(StockLot,data['lot_id'])
            if known and known.supplier_id is not None:raise MutationError('Known supplier lots require approved PO receiving.',409)
    replay,_=ledger.lookup(scope,'receipt',data,data.get('request_key') or f"receipt:{branch_id}:{data['external_reference']}")
    if replay:return replay
    digest=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    prior=db.session.scalar(select(StockReceipt).where(StockReceipt.branch_id==branch_id,
        StockReceipt.external_reference==data['external_reference']).with_for_update())
    if prior:
        if prior.payload_hash != digest:raise MutationError('Delivery reference already records different receipt facts.',409)
        from models import InventoryDocument, InventoryOperation
        document=db.session.scalar(select(InventoryDocument).where(InventoryDocument.receipt_id==prior.receipt_id))
        if document:
            original=db.session.get(InventoryOperation,document.operation_id)
            return dict(original.result,replayed=True)
        # A pre-ledger T06 receipt can be acknowledged, never applied again.
        return _receipt_result(prior,True)
    sku=db.session.get(SKU,data['sku_id'],populate_existing=True,with_for_update={'read':True})
    assortment=db.session.get(BranchProduct,(branch_id,data['sku_id']),populate_existing=True,with_for_update={'read':True})
    if sku is None or assortment is None:raise MutationError('Resource not found.',404)
    if not sku.active or not assortment.enabled:raise MutationError('This SKU is not active and approved for receiving.',409)
    if not ledger.legacy_ready(sku.sku_id):
        raise MutationError('Mapped legacy stock needs a reviewed opening-balance cutover before lot receiving. No stock was changed.',409)
    branch=db.session.get(Branch,branch_id,populate_existing=True,with_for_update={'read':True})
    policy=catalog._effective_policy(sku,lock=True,shared=True)
    if policy is None and data['receipt_kind']=='normal':
        raise MutationError('The SKU needs a valid headquarters expiry policy before ordinary receiving.',409)
    policy_snapshot=policy.snapshot() if policy else dict(source='unresolved',reason='Headquarters policy review required')
    now=utcnow()  # One trusted clock, captured after locks; never supplied by request.
    label_issue=None
    if data['mode']=='new_lot':
        lot_key=catalog.lot_signature(sku.sku_id,data['supplier_lot_code'],data['expiry_date'])
        if db.session.scalar(select(LotIdentity.lot_id).where(LotIdentity.signature==lot_key)) is not None:
            raise MutationError('This supplier batch/date already has a lot identity. Review your authorized lots or ask headquarters.',409)
        if db.session.scalar(select(StockLot.lot_id).where(StockLot.sku_id==sku.sku_id,StockLot.lot_code==data['lot_code'])) is not None:
            # Do not disclose another branch's lot or silently turn creation into receipt.
            raise MutationError('Lot identity cannot be created. Review your authorized lots or ask headquarters.',409)
        actual=tz=instant=None
        if data['expiry_date'] is not None:
            config=db.session.get(CompanyExpirySettings,1,populate_existing=True,with_for_update={'read':True})
            tz=data.get('label_timezone') or (config.label_timezone if config else None)
            try:
                if tz is None:raise MutationError('Headquarters must approve the company label timezone before dated receipts.')
                actual=parse_date(data['expiry_date']);instant=canonical_expiry(actual,tz)
            except MutationError as exc:
                if data['receipt_kind']=='normal':raise
                label_issue=str(exc);actual=tz=instant=None
        if data['expiry_date'] is None and policy and policy.require_expiry and data['receipt_kind']=='normal':
            raise MutationError('An actual expiry date is required for this SKU.')
        lot=StockLot(supplier_id=supplier_id,sku_id=sku.sku_id,lot_code=data['lot_code'],expiry_date=actual,expiry_timezone=tz,
            expires_at_utc=instant.replace(tzinfo=None) if instant else None,
            validation_state='quarantine' if data['receipt_kind']=='quarantine' else 'validated',
            provenance=dict(supplier_lot_code=data['supplier_lot_code'],observed_expiry_label=data['expiry_date'],
                evidence=data['evidence'],label_issue=label_issue,zone_reason=data.get('zone_reason'),**conversion_provenance()),
            receipt_policy_provenance=policy_snapshot)
        db.session.add(lot);db.session.flush()
        db.session.add(LotIdentity(lot_id=lot.lot_id,signature=lot_key))
    else:
        query=select(StockLot).join(BranchStock,BranchStock.lot_id==StockLot.lot_id).where(
            StockLot.lot_id==data['lot_id'],BranchStock.branch_id==branch_id).distinct().with_for_update(read=True)
        lot=db.session.scalar(query.execution_options(populate_existing=True))
        if lot is None and scope.role=='hq_admin':
            # HQ explicitly has cross-branch metadata authority; branch users do not.
            resolve_scope('batch.correct',consolidated=True)
            lot=db.session.get(StockLot,data['lot_id'],populate_existing=True,with_for_update={'read':True})
        if lot is None or lot.sku_id != sku.sku_id:raise MutationError('Resource not found.',404)
        if supplier_id is not None and lot.supplier_id!=supplier_id:
            raise MutationError('Lot supplier is unknown or does not match the approved PO; no legacy identity is inferred.',409)
        observed=lot.expiry_date.isoformat() if lot.expiry_date else None
        if data['expiry_date'] != observed:raise MutationError('Observed date conflicts with the existing lot; do not overwrite its label facts.',409)
    classification=classify(policy=policy,expiry_date=lot.expiry_date,expiry_timezone=lot.expiry_timezone,
        expires_at_utc=lot.expires_at_utc,validation_state=lot.validation_state,branch_timezone=branch.timezone,as_of=now)
    if data['receipt_kind']=='normal' and (classification['status'] in ('expired','review') or
        (classification['days_remaining'] is not None and classification['days_remaining']<policy.min_remaining_days_on_receipt)):
        raise MutationError('This lot cannot enter ordinary stock: review expiry, policy and minimum remaining life.',409)
    quarantined=data['quantity'] if data['receipt_kind']=='quarantine' else 0
    if data['mode']=='new_lot' and quarantined:lot.validation_state='quarantine'
    stock=db.session.get(BranchStock,(branch_id,lot.lot_id),populate_existing=True,with_for_update=True)
    if stock is None:
        stock=BranchStock(branch_id=branch_id,lot_id=lot.lot_id,sku_id=sku.sku_id,on_hand=0,
                          reserved=0,quarantined_quantity=0,version=1,first_received_at_utc=now.replace(tzinfo=None))
        db.session.add(stock);db.session.flush()
    ledger.ensure_opened(stock)
    operation,replay=ledger.begin(scope,'receipt',data,data.get('request_key') or
        f"receipt:{branch_id}:{data['external_reference']}",data['external_reference'],now)
    if replay:return replay
    # Revalidate after the balance lock: a queued request may cross expiry.
    commit_now=utcnow()
    state=classify(policy=policy,expiry_date=lot.expiry_date,expiry_timezone=lot.expiry_timezone,
        expires_at_utc=lot.expires_at_utc,validation_state=lot.validation_state,branch_timezone=branch.timezone,as_of=commit_now)
    if data['receipt_kind']=='normal' and (state['status'] in ('expired','review') or
        (state['days_remaining'] is not None and state['days_remaining']<policy.min_remaining_days_on_receipt)):
        raise MutationError('Lot is no longer eligible for ordinary receiving.',409)
    movement=ledger.apply(operation,stock,data['quantity'],quarantined=quarantined,
        evidence=dict(classification=state,policy=policy_snapshot,lot_version=lot.version))
    receipt=StockReceipt(branch_id=branch_id,lot_id=lot.lot_id,sku_id=sku.sku_id,actor_id=scope.actor_id,
        external_reference=data['external_reference'],payload_hash=digest,quantity=data['quantity'],
        quarantined_quantity=quarantined,received_at_utc=now.replace(tzinfo=None),policy_snapshot=policy_snapshot,
        evidence=dict(request=data,classification=classification,lot_version=lot.version,stock_version=stock.version,
                      label_provenance=lot.provenance,branch_timezone=branch.timezone,
                      sku_version=sku.version,assortment_version=assortment.version,
                      label_facts=dict(expiry_date=lot.expiry_date.isoformat() if lot.expiry_date else None,
                          expiry_timezone=lot.expiry_timezone,
                          expires_at_utc=lot.expires_at_utc.isoformat()+'Z' if lot.expires_at_utc else None)))
    db.session.add(receipt);db.session.flush()
    # Audit failure rolls back the receipt, lot and balance together.
    catalog._record(scope,'receipt',receipt.receipt_id,1,data['reason'],None,
                   dict(_receipt_result(receipt),policy=policy_snapshot,classification=classification))
    if data['receipt_kind']=='normal':
        def revalidate():
            final_now=utcnow()
            final=classify(policy=policy,expiry_date=lot.expiry_date,expiry_timezone=lot.expiry_timezone,
                expires_at_utc=lot.expires_at_utc,validation_state=lot.validation_state,branch_timezone=branch.timezone,as_of=final_now)
            if final['status'] in ('expired','review') or (final['days_remaining'] is not None and final['days_remaining']<policy.min_remaining_days_on_receipt):
                raise MutationError('Lot no longer satisfies receiving policy before commit.',409)
        ledger.guard(revalidate)
    if _po_operation_id is not None:
        from purchasing_services import verify_receipt_link
        ledger.guard(lambda:verify_receipt_link(_po_operation_id,receipt.receipt_id))
    return ledger.finish(scope,operation,_receipt_result(receipt),data['reason'],receipt_id=receipt.receipt_id,clock=utcnow)


def receiving_choices():
    scope=resolve_scope('stock.receive',write=True,shared=True)
    rows=db.session.execute(select(SKU,BranchProduct).join(BranchProduct,SKU.sku_id==BranchProduct.sku_id)
        .where(BranchProduct.branch_id==scope.branches[0],BranchProduct.enabled.is_(True),SKU.active.is_(True)).order_by(SKU.code)).all()
    choices=[]
    for sku,assortment in rows:
        policy=catalog._effective_policy(sku)
        legacy=not ledger.legacy_ready(sku.sku_id)
        choices.append(dict(catalog.sku_snapshot(sku),policy=policy.snapshot() if policy else None,
            legacy_cutover_required=legacy,
            effective_price=str(assortment.sale_price_override if assortment.sale_price_override is not None else sku.default_price)))
    return choices


def sales_choices(values):
    """Bounded SKU choices without loading or classifying every holding lot."""
    from sqlalchemy import func
    scope=resolve_scope('inventory.read')
    page=integer(values.get('choice_page',1),'Choice page',1,10000)
    search=values.get('choice_search','')
    if not isinstance(search,str):raise MutationError('Invalid SKU search.')
    search=search.strip()
    if len(search)>80 or any(ord(c)<32 for c in search):raise MutationError('Invalid SKU search.')
    if scope.consolidated:return dict(page=page,search=search,has_next=False,items=[])
    stocked=select(BranchStock.lot_id).where(BranchStock.branch_id==BranchProduct.branch_id,
        BranchStock.sku_id==SKU.sku_id).exists()
    stmt=select(SKU.sku_id,SKU.code,SKU.active,func.coalesce(BranchProduct.sale_price_override,SKU.default_price).label('price')).join(
        BranchProduct,BranchProduct.sku_id==SKU.sku_id).where(BranchProduct.branch_id.in_(scope.branches),stocked)
    if search:stmt=stmt.where(func.lower(SKU.code+' '+SKU.name).contains(search.lower(),autoescape=True))
    rows=db.session.execute(stmt.order_by(SKU.code,SKU.sku_id).offset((page-1)*100).limit(101)).all()
    return dict(page=page,search=search,has_next=len(rows)>100,items=[dict(sku_id=r.sku_id,sku_code=r.code,
        sku_active=r.active,effective_price=str(r.price) if r.price is not None else 'Unknown') for r in rows[:100]])


def holdings():
    scope=resolve_scope('inventory.read')
    now=utcnow()
    rows=db.session.execute(select(BranchStock,StockLot,SKU,Branch,BranchProduct)
        .join(StockLot,StockLot.lot_id==BranchStock.lot_id).join(SKU,SKU.sku_id==BranchStock.sku_id)
        .join(Branch,Branch.branch_id==BranchStock.branch_id)
        .join(BranchProduct,(BranchProduct.branch_id==BranchStock.branch_id)&(BranchProduct.sku_id==BranchStock.sku_id))
        .where(BranchStock.branch_id.in_(scope.branches),SKU.sku_id.in_(managed_sku_ids()))
        .order_by(BranchStock.branch_id,SKU.code,StockLot.lot_id)).all()
    result=[];policies={}
    for stock,lot,sku,branch,assortment in rows:
        if sku.sku_id not in policies:policies[sku.sku_id]=catalog._effective_policy(sku)
        policy=policies[sku.sku_id]
        state=classify(policy=policy,expiry_date=lot.expiry_date,expiry_timezone=lot.expiry_timezone,
            expires_at_utc=lot.expires_at_utc,validation_state=lot.validation_state,branch_timezone=branch.timezone,as_of=now)
        held=state['status'] in ('expired','review') or not sku.active or not assortment.enabled
        eligible=0 if held else stock.on_hand-stock.reserved-stock.quarantined_quantity
        instant=lot.expires_at_utc.replace(tzinfo=UTC) if lot.expires_at_utc else None
        local_expiry=None
        if instant:
            try:local_expiry=instant.astimezone(zone(branch.timezone)).isoformat()
            except MutationError:pass  # The classifier already returned review; retain physical evidence.
        result.append(dict(branch_id=stock.branch_id,branch_code=branch.code,timezone=branch.timezone,
            sku_id=sku.sku_id,sku_code=sku.code,sku_name=sku.name,sku_active=sku.active,lot_id=lot.lot_id,lot_code=lot.lot_code,
            expiry_date=lot.expiry_date.isoformat() if lot.expiry_date else None,expiry_timezone=lot.expiry_timezone,
            observed_expiry_label=lot.provenance.get('observed_expiry_label'),
            expires_at_utc=instant.isoformat() if instant else None,
            local_expiry=local_expiry,
            on_hand=stock.on_hand,reserved=stock.reserved,quarantined_quantity=stock.quarantined_quantity,
            eligible_quantity=eligible,unusable_quantity=stock.on_hand if held else stock.quarantined_quantity,
            stock_version=stock.version,lot_version=lot.version,validation_state=lot.validation_state,
            first_received_at_utc=stock.first_received_at_utc.isoformat()+'Z' if stock.first_received_at_utc else None,
            effective_price=str(assortment.sale_price_override if assortment.sale_price_override is not None else sku.default_price),
            policy=policy.snapshot() if policy else None,as_of_utc=now.isoformat(),**state))
    return result


def receipts():
    scope=resolve_scope('inventory.read')
    return [dict(_receipt_result(r),received_at_utc=r.received_at_utc.isoformat()+'Z')
            for r in db.session.scalars(select(StockReceipt).where(StockReceipt.branch_id.in_(scope.branches))
            .order_by(StockReceipt.receipt_id.desc()).limit(100))]
