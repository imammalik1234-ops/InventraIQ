"""T07 native stock engine. Services authorize; caller owns commit/rollback.

Shared metadata locks protect decisions without serializing independent branches.
Exclusive balance locks are always acquired by (branch, SKU, lot), never FEFO order.
No retries or clocks supplied by clients. No prices or historical sales are invented.
"""
import hashlib
import json
from sqlalchemy import select, update, func
from sqlalchemy import event
from sqlalchemy.orm import Session
from database import db
from models import (InventoryOperation, InventoryMovement, InventoryDocument,
                    InventoryReservation, BranchStock, StockLot, SKU, Branch,
                    BranchProduct, LegacyProductMapping)
from authz import resolve_scope, integer_id
from security import MutationError
from expiry import utcnow, UTC, zone, classify, integer
import catalog_services as catalog

MAX_QUANTITY = 2147483647


@event.listens_for(Session, 'before_commit')
def _commit_checks(session):
    for check in session.info.get('inventory_commit_checks', ()):
        check()


@event.listens_for(Session, 'after_commit')
@event.listens_for(Session, 'after_rollback')
def _clear_checks(session):
    session.info.pop('inventory_commit_checks', None)


@event.listens_for(Session, 'after_commit')
@event.listens_for(Session, 'after_soft_rollback')
def _clear_pending_facts(session, *args):
    session.info.pop('inventory_pending_facts', None)


def guard(check):
    db.session.info.setdefault('inventory_commit_checks', []).append(check)


def legacy_ready(sku_id):
    for mapping in db.session.scalars(select(LegacyProductMapping).where(LegacyProductMapping.sku_id == sku_id)):
        if not db.session.scalar(select(InventoryMovement.movement_id).join(InventoryOperation).where(
            InventoryMovement.branch_id == mapping.branch_id, InventoryMovement.lot_id == mapping.lot_id,
            InventoryOperation.operation_type == 'opening', InventoryOperation.status == 'committed').limit(1)):
            return False
    return True


def lookup(scope, kind, data, key):
    key = catalog.text(key, 'Request key', 120)
    payload = dict(branch_id=scope.branches[0], operation_type=kind, parameters=data)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    prior = db.session.scalar(select(InventoryOperation).where(
        InventoryOperation.actor_id == scope.actor_id, InventoryOperation.request_key == key)
        .execution_options(populate_existing=True))
    if prior:
        if prior.payload_hash != digest:
            raise MutationError('Request key already records different parameters.', 409)
        if prior.status != 'committed':
            raise MutationError('Operation requires reconciliation.', 409)
        return dict(prior.result, replayed=True), digest
    return None, digest


def begin(scope, kind, data, key, reference, now):
    key = catalog.text(key, 'Request key', 120)
    reference = catalog.text(reference, 'Business reference', 100)
    replay, digest = lookup(scope, kind, data, key)
    if replay:
        return None, replay
    branch = db.session.get(Branch, scope.branches[0], populate_existing=True, with_for_update={'read': True})
    operation = InventoryOperation(actor_id=scope.actor_id, branch_id=branch.branch_id,
        request_key=key, payload_hash=digest, operation_type=kind, business_reference=reference,
        business_date=now.astimezone(zone(branch.timezone)).date(), occurred_at_utc=now.replace(tzinfo=None),
        status='staging', result={}, evidence=dict(parameters=data, branch_timezone=branch.timezone))
    db.session.add(operation)
    db.session.flush()  # unique actor/key claim; competing caller safely conflicts/retries
    return operation, None


def opening(values):
    """Offline operator service, deliberately no HTTP endpoint or automatic caller.

    Caller supplies an approved count, revision and exact source receipt list.
    This records the existing balance, NEVER reapplies historical receipt deltas.
    A legacy opening additionally requires frozen source quantity agreement.
    """
    scope = resolve_scope('catalog.manage', write=True, shared=True)
    allowed = {'request_key','business_reference','reason','lot_id','expected_revision',
               'on_hand','reserved','quarantined_quantity','source_receipt_ids','approval_evidence'}
    if not isinstance(values, dict) or set(values) != allowed:
        raise MutationError('A complete reviewed opening manifest is required.')
    data = dict(values)
    for field in ('lot_id','expected_revision'):
        data[field] = integer_id(data[field])
    for field in ('on_hand','reserved','quarantined_quantity'):
        data[field] = integer(data[field], field, 0, MAX_QUANTITY)
    data['approval_evidence'] = catalog.text(data['approval_evidence'], 'Approval evidence', 255)
    data['reason'] = catalog.text(data['reason'], 'Reason', 255)
    if not isinstance(data['source_receipt_ids'], list) or any(type(v) is not int or v < 1 for v in data['source_receipt_ids']):
        raise MutationError('Exact source receipt IDs are required.')
    data['source_receipt_ids'] = sorted(set(data['source_receipt_ids']))
    operation, replay = begin(scope, 'opening', data, data['request_key'], data['business_reference'], utcnow())
    if replay: return replay
    stock = db.session.get(BranchStock, (scope.branches[0], data['lot_id']), populate_existing=True, with_for_update=True)
    if stock is None: raise MutationError('Resource not found.', 404)
    if stock.version != data['expected_revision'] or (stock.on_hand,stock.reserved,stock.quarantined_quantity) != (
        data['on_hand'],data['reserved'],data['quarantined_quantity']):
        raise MutationError('Opening count or revision conflicts.', 409)
    if db.session.scalar(select(InventoryMovement.movement_id).where(
        InventoryMovement.branch_id == stock.branch_id, InventoryMovement.lot_id == stock.lot_id).limit(1)):
        raise MutationError('This lot already has ledger history.', 409)
    from models import StockReceipt, Inventory, Product, SKUIdentity
    receipts = db.session.scalars(select(StockReceipt).where(StockReceipt.branch_id == stock.branch_id,
        StockReceipt.lot_id == stock.lot_id).order_by(StockReceipt.receipt_id).with_for_update()).all()
    if [r.receipt_id for r in receipts] != data['source_receipt_ids']:
        raise MutationError('Opening source receipt evidence conflicts.', 409)
    if stock.reserved:
        raise MutationError('Unknown reservations need owner-level allocation reconciliation before opening.', 409)
    mapping = db.session.scalar(select(LegacyProductMapping).where(
        LegacyProductMapping.branch_id == stock.branch_id, LegacyProductMapping.lot_id == stock.lot_id))
    if mapping:
        source = db.session.get(Inventory, mapping.legacy_inventory_id, with_for_update=True) if mapping.legacy_inventory_id else None
        if source is None or source.quantity != stock.on_hand or receipts:
            raise MutationError('Legacy source and opening balance need explicit reconciliation; source is never overwritten.', 409)
        sku = db.session.get(SKU, stock.sku_id, with_for_update=True)
        catalog._adopt_identity(sku)  # permanently disables old compatibility writers
        data['legacy_source'] = dict(product_id=mapping.legacy_product_id, inventory_id=source.inventory_id,
                                     mapping_evidence=mapping.evidence, quantity=source.quantity)
    elif receipts and (sum(r.quantity for r in receipts),sum(r.quarantined_quantity for r in receipts)) != (stock.on_hand,stock.quarantined_quantity):
        raise MutationError('T06 receipt totals do not reconcile to this balance.', 409)
    db.session.add(InventoryMovement(operation_id=operation.operation_id, branch_id=stock.branch_id,
        lot_id=stock.lot_id, sku_id=stock.sku_id, on_hand_delta=stock.on_hand, reserved_delta=stock.reserved,
        quarantined_delta=stock.quarantined_quantity, before_on_hand=0, after_on_hand=stock.on_hand,
        before_reserved=0, after_reserved=stock.reserved, before_quarantined=0,
        after_quarantined=stock.quarantined_quantity, before_version=stock.version-1, after_version=stock.version,
        evidence=data))
    operation.evidence = data
    return finish(scope, operation, dict(kind='opening', branch_id=stock.branch_id, lot_id=stock.lot_id,
        on_hand=stock.on_hand, source_receipt_ids=data['source_receipt_ids']), data['reason'])


def ensure_opened(stock):
    """Unrecorded balances (including T04 copies/T06 receipts) require sign-off."""
    if any((stock.on_hand, stock.reserved, stock.quarantined_quantity)) and not db.session.scalar(
        select(InventoryMovement.movement_id).join(InventoryOperation).where(
            InventoryMovement.branch_id == stock.branch_id, InventoryMovement.lot_id == stock.lot_id,
            InventoryOperation.status == 'committed').limit(1)):
        raise MutationError('This balance requires a reviewed opening reconciliation before ledger use.', 409)


def apply(operation, stock, on_hand=0, reserved=0, quarantined=0, *, evidence=None, opening=False):
    if not opening:
        ensure_opened(stock)
    before = (stock.on_hand, stock.reserved, stock.quarantined_quantity, stock.version)
    after = (before[0]+on_hand, before[1]+reserved, before[2]+quarantined, before[3]+1)
    if min(after[:3]) < 0 or after[1]+after[2] > after[0] or after[0] > MAX_QUANTITY:
        raise MutationError('Insufficient available stock or invalid lot balance.', 409)
    changed = db.session.execute(update(BranchStock).where(
        BranchStock.branch_id == operation.branch_id, BranchStock.lot_id == stock.lot_id,
        BranchStock.version == before[3], BranchStock.on_hand == before[0],
        BranchStock.reserved == before[1], BranchStock.quarantined_quantity == before[2])
        .values(on_hand=after[0], reserved=after[1], quarantined_quantity=after[2], version=after[3])
        .execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        raise MutationError('Stock changed; retry with the same request key after reviewing revisions.', 409)
    movement = InventoryMovement(operation_id=operation.operation_id, branch_id=operation.branch_id,
        sku_id=stock.sku_id, lot_id=stock.lot_id, on_hand_delta=on_hand, reserved_delta=reserved,
        quarantined_delta=quarantined, before_on_hand=before[0], after_on_hand=after[0],
        before_reserved=before[1], after_reserved=after[1], before_quarantined=before[2], after_quarantined=after[2],
        before_version=before[3], after_version=after[3], evidence=evidence or {})
    db.session.add(movement)
    # Keep inserted instances alive until transaction completion. A subsequent
    # allocation lookup must reuse that instance so SQLAlchemy can expunge it on
    # rollback rather than retaining a reloaded, expired identity for a reused ID.
    db.session.info.setdefault('inventory_pending_facts', []).append(movement)
    db.session.refresh(stock)
    return dict(lot_id=stock.lot_id, quantity_delta=on_hand, reserved_delta=reserved,
                quarantined_delta=quarantined, on_hand=after[0], reserved=after[1],
                quarantined_quantity=after[2], stock_version=after[3])


def finish(scope, operation, result, reason, *, receipt_id=None, clock=None):
    result = dict(result, operation_id=operation.operation_id, replayed=False)
    db.session.add(InventoryDocument(operation_id=operation.operation_id, receipt_id=receipt_id,
        kind=operation.operation_type, facts=result))
    catalog._record(scope, 'inventory_operation', operation.operation_id, 1, reason, None, result)
    # Flush all lines and business/audit evidence BEFORE sealing the operation.
    db.session.flush()
    operation.result = result
    operation.status = 'committed'
    db.session.flush()
    capability = {'receipt':'stock.receive', 'sale':'sales.create', 'opening':'catalog.manage', 'priced_sale':'sales.create', 'priced_reserved_sale':'stock.adjust', 'refund':'sales.refund', 'reversal':'sales.refund', 'transfer_dispatch':'transfer.dispatch', 'transfer_receive':'transfer.receive', 'transfer_discrepancy':'transfer.receive', 'transfer_resolve':'transfer.resolve', 'transfer_return':'transfer.propose', 'supplier_save':'supplier.manage', 'po_create':'purchasing.manage', 'po_submit':'purchasing.manage', 'po_approve':'purchasing.manage', 'po_reject':'purchasing.manage', 'po_cancel':'purchasing.manage', 'po_receive':'purchasing.receive', 'transfer_create':'transfer.propose', 'transfer_submit':'transfer.propose', 'transfer_cancel':'transfer.propose', 'transfer_approve':'transfer.approve', 'transfer_reject':'transfer.approve', 'transfer_cancel_approved':'transfer.approve'}.get(operation.operation_type, 'stock.adjust')
    clock = clock or utcnow
    def reauthorize():
        current = resolve_scope(capability, branch_id=scope.branches[0], write=True, shared=True)
        if current.actor_id != scope.actor_id:
            raise MutationError('Access denied.', 403)
        branch = db.session.get(Branch, scope.branches[0])
        if clock().astimezone(zone(branch.timezone)).date() != operation.business_date:
            raise MutationError('Branch business date changed; retry the same request key.', 409)
    guard(reauthorize)
    return result


def _payload(values, extra):
    allowed = {'branch_id', 'sku_id', 'quantity', 'request_key', 'business_reference', 'reason'} | set(extra)
    if not isinstance(values, dict) or set(values)-allowed:
        raise MutationError('Invalid inventory operation fields.')
    scope = resolve_scope('stock.adjust' if extra else 'sales.create', write=True, shared=True)
    if 'branch_id' in values and integer_id(values['branch_id']) != scope.branches[0]:
        raise MutationError('Access denied.', 403)
    data = dict(values); data.pop('branch_id', None)
    data['sku_id'] = integer_id(data.get('sku_id'))
    data['quantity'] = integer(data.get('quantity'), 'Quantity', 1, MAX_QUANTITY)
    data['reason'] = catalog.text(data.get('reason'), 'Reason', 255)
    data['request_key'] = catalog.text(data.get('request_key'), 'Request key', 120)
    data['business_reference'] = catalog.text(data.get('business_reference'), 'Business reference', 100)
    return scope, data


def _locked_stock(scope, sku_id):
    sku = db.session.get(SKU, sku_id, populate_existing=True, with_for_update={'read': True})
    assortment = db.session.get(BranchProduct, (scope.branches[0], sku_id), populate_existing=True, with_for_update={'read': True})
    if sku is None or assortment is None:
        raise MutationError('Resource not found.', 404)
    # Legacy snapshots are not operational balances, even when currently zero.
    if not legacy_ready(sku_id):
        raise MutationError('Mapped legacy stock requires approved opening reconciliation.', 409)
    stocks = db.session.scalars(select(BranchStock).where(BranchStock.branch_id == scope.branches[0],
        BranchStock.sku_id == sku_id).order_by(BranchStock.branch_id, BranchStock.sku_id, BranchStock.lot_id)
        .with_for_update().execution_options(populate_existing=True)).all()
    for stock in stocks:
        ensure_opened(stock)
    # Metadata has shared locks only; another branch can operate on the same SKU/lot.
    lots = {s.lot_id: db.session.get(StockLot, s.lot_id, populate_existing=True,
                                  with_for_update={'read': True}) for s in stocks}
    policy = catalog._effective_policy(sku, lock=True, shared=True)
    branch = db.session.get(Branch, scope.branches[0], populate_existing=True, with_for_update={'read': True})
    return sku, assortment, stocks, lots, policy, branch


def _eligibility(sku, assortment, lot, policy, branch, now):
    state = classify(policy=policy, expiry_date=lot.expiry_date, expiry_timezone=lot.expiry_timezone,
        expires_at_utc=lot.expires_at_utc, validation_state=lot.validation_state,
        branch_timezone=branch.timezone, as_of=now)
    return sku.active and assortment.enabled and state['status'] not in ('expired', 'review'), state


def fefo_plan(locked, quantity, now):
    sku, assortment, stocks, lots, policy, branch = locked
    eligible = []
    for stock in stocks:
        lot = lots[stock.lot_id]
        valid, state = _eligibility(sku, assortment, lot, policy, branch, now)
        available = stock.on_hand-stock.reserved-stock.quarantined_quantity
        if valid and available > 0:
            # Null receipt timestamps on approved legacy openings use ID tie-break,
            # never a fabricated historical receipt instant.
            eligible.append((lot.expires_at_utc is None, lot.expires_at_utc,
                             stock.first_received_at_utc is None, stock.first_received_at_utc,
                             lot.lot_id, stock, state))
    eligible.sort(key=lambda row: row[:5])
    if sum(row[5].on_hand-row[5].reserved-row[5].quarantined_quantity for row in eligible) < quantity:
        raise MutationError('Insufficient eligible stock.', 409)
    return eligible

def allocate(values, *, reserve=False):
    scope, data = _payload(values, {'reservation'} if reserve else set())
    if reserve and 'reservation' in data:
        raise MutationError('Reservation identity is generated by the server.')
    now = utcnow()
    operation, replay = begin(scope, 'reservation' if reserve else 'sale', data,
        data['request_key'], data['business_reference'], now)
    if replay: return replay
    sku, assortment, stocks, lots, policy, branch = _locked_stock(scope, data['sku_id'])
    now = utcnow()  # Recheck time AFTER every allocation lock, not at request arrival.
    operation.business_date = now.astimezone(zone(branch.timezone)).date()
    eligible = fefo_plan((sku,assortment,stocks,lots,policy,branch),data['quantity'],now)
    remaining = data['quantity']; lines = []
    for _, _, _, _, lot_id, stock, state in eligible:
        take = min(remaining, stock.on_hand-stock.reserved-stock.quarantined_quantity)
        lines.append(apply(operation, stock, 0 if reserve else -take, take if reserve else 0,
            evidence=dict(classification=state, policy=policy.snapshot(), lot_version=lots[lot_id].version,
                          as_of_utc=now.isoformat())))
        if reserve:
            db.session.add(InventoryReservation(operation_id=operation.operation_id, branch_id=scope.branches[0],
                lot_id=lot_id, quantity=take, remaining=take))
        remaining -= take
        if not remaining: break
    def revalidate():
        instant = utcnow()
        for line in lines:
            if not _eligibility(sku, assortment, lots[line['lot_id']], policy, branch, instant)[0]:
                raise MutationError('Lot expired or became unavailable before commit.', 409)
        if instant.astimezone(zone(branch.timezone)).date() != operation.business_date:
            raise MutationError('Branch business date changed; retry the same request key.', 409)
    guard(revalidate)
    return finish(scope, operation, dict(kind=operation.operation_type, sku_id=sku.sku_id,
        branch_id=scope.branches[0], quantity=data['quantity'], allocations=lines), data['reason'])


def adjust(values):
    scope, data = _payload(values, {'lot_id', 'expected_revision', 'direction', 'kind'})
    data['lot_id'] = integer_id(data.get('lot_id'))
    data['expected_revision'] = integer(data.get('expected_revision'), 'Expected revision', 1, MAX_QUANTITY)
    data['kind'] = data.get('kind', 'adjustment')
    data['direction'] = data.get('direction', 'decrease')
    if data['kind'] not in ('adjustment', 'write_off') or data['direction'] not in ('increase', 'decrease'):
        raise MutationError('Invalid adjustment kind or direction.')
    if data['kind'] == 'write_off' and data['direction'] != 'decrease':
        raise MutationError('Write-off must reduce physical stock.')
    operation, replay = begin(scope, data['kind'], data, data['request_key'], data['business_reference'], utcnow())
    if replay: return replay
    sku, assortment, stocks, lots, policy, branch = _locked_stock(scope, data['sku_id'])
    stock = next((s for s in stocks if s.lot_id == data['lot_id']), None)
    if stock is None: raise MutationError('Resource not found.', 404)
    if stock.version != data['expected_revision']: raise MutationError('Stock revision conflict.', 409)
    now = utcnow()
    operation.business_date = now.astimezone(zone(branch.timezone)).date()
    valid, state = _eligibility(sku, assortment, lots[stock.lot_id], policy, branch, now)
    positive = data['direction'] == 'increase'
    if positive and not valid:
        raise MutationError('Invalid lot for positive adjustment; use an evidenced quarantine receipt.', 409)
    # Disposal consumes quarantined physical units first, never reservations.
    quarantine_delta = -min(stock.quarantined_quantity, data['quantity']) if not positive else 0
    line = apply(operation, stock, data['quantity'] if positive else -data['quantity'],
        quarantined=quarantine_delta, evidence=dict(classification=state, lot_version=lots[stock.lot_id].version))
    return finish(scope, operation, dict(kind=data['kind'], branch_id=scope.branches[0], sku_id=sku.sku_id,
        allocations=[line]), data['reason'])


def settle_reservation(values, *, sell=False):
    scope, data = _payload(values, {'reservation_id'})
    reservation_id = integer_id(data.get('reservation_id')); data['reservation_id'] = reservation_id
    # Whole-reservation settlement only; quantity must match the remaining total.
    operation, replay = begin(scope, 'reserved_sale' if sell else 'release', data,
        data['request_key'], data['business_reference'], utcnow())
    if replay: return replay
    original = db.session.scalar(select(InventoryOperation).where(InventoryOperation.operation_id == reservation_id,
        InventoryOperation.branch_id == scope.branches[0], InventoryOperation.operation_type == 'reservation',
        InventoryOperation.status == 'committed'))
    if original is None or original.result.get('sku_id') != data['sku_id']:
        raise MutationError('Resource not found.', 404)
    sku, assortment, stocks, lots, policy, branch = _locked_stock(scope, data['sku_id'])
    reservations = db.session.scalars(select(InventoryReservation).where(
        InventoryReservation.operation_id == reservation_id, InventoryReservation.branch_id == scope.branches[0])
        .order_by(InventoryReservation.lot_id).with_for_update().execution_options(populate_existing=True)).all()
    if sum(r.remaining for r in reservations) != data['quantity']:
        raise MutationError('Reservation is settled or quantity conflicts.', 409)
    stock_by_id = {s.lot_id:s for s in stocks}; now = utcnow(); lines = []
    operation.business_date = now.astimezone(zone(branch.timezone)).date()
    for reservation in reservations:
        if not reservation.remaining: continue
        stock = stock_by_id[reservation.lot_id]
        valid, state = _eligibility(sku, assortment, lots[stock.lot_id], policy, branch, now)
        if sell and not valid:
            raise MutationError('Reserved lot is expired or unavailable; release and review allocation.', 409)
        quantity = reservation.remaining
        lines.append(apply(operation, stock, -quantity if sell else 0, -quantity,
            evidence=dict(reservation_id=reservation_id, classification=state)))
        reservation.remaining = 0
    if sell:
        def revalidate():
            for line in lines:
                if not _eligibility(sku, assortment, lots[line['lot_id']], policy, branch, utcnow())[0]:
                    raise MutationError('Reserved lot expired before commit.', 409)
        guard(revalidate)
    return finish(scope, operation, dict(kind=operation.operation_type, branch_id=scope.branches[0],
        sku_id=sku.sku_id, quantity=data['quantity'], reservation_id=reservation_id, allocations=lines), data['reason'])


def reconcile():
    """Read-only, permission-scoped reconciliation including zero/uncutover rows."""
    scope = resolve_scope('inventory.read')
    result = []
    for stock in db.session.scalars(select(BranchStock).where(BranchStock.branch_id.in_(scope.branches))
        .order_by(BranchStock.branch_id, BranchStock.sku_id, BranchStock.lot_id)):
        lines = db.session.scalars(select(InventoryMovement).join(InventoryOperation).where(
            InventoryMovement.branch_id == stock.branch_id, InventoryMovement.lot_id == stock.lot_id,
            InventoryOperation.status == 'committed').order_by(InventoryMovement.movement_id)).all()
        expected = tuple(sum(getattr(line, field) for line in lines) for field in ('on_hand_delta','reserved_delta','quarantined_delta'))
        actual = (stock.on_hand, stock.reserved, stock.quarantined_quantity)
        chain = (0,0,0); coherent = True; version = None
        for line in lines:
            before = (line.before_on_hand,line.before_reserved,line.before_quarantined)
            coherent = coherent and before == chain and (version is None or line.before_version == version)
            chain = (line.after_on_hand,line.after_reserved,line.after_quarantined); version = line.after_version
        result.append(dict(branch_id=stock.branch_id, lot_id=stock.lot_id, sku_id=stock.sku_id,
            expected=list(expected), actual=list(actual), movement_count=len(lines),
            balanced=bool(lines) and expected == actual and coherent and version == stock.version))
    return result
