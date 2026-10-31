"""T16 live SQL projections. No materialized facts, permission cache or writes.

Expiry cutoffs are calculated with T06's pinned timezone rules, then compared in
SQL against canonical UTC label boundaries. No database timezone plugin is needed.
"""
from datetime import timedelta, datetime, date
import json
from importlib.resources import files
from sqlalchemy import select, func, case, literal, union_all, and_, or_, cast, String
from database import db
from models import (Branch, BranchStock, StockLot, SKU, BranchProduct, CategoryExpiryPolicy,
    ProductExpiryPolicyOverride, InventoryMovement, InventoryOperation, LegacyProductMapping,
    Inventory, Product)
from authz import managed_sku_ids
from expiry import POLICY_FIELDS, canonical_expiry, zone
from security import MutationError


def projection(scope, now):
    """All predicates, classification and grouping remain on authorized SQL rows."""
    def movement(branch, lot, opening=False):
        stmt = select(InventoryMovement.movement_id).join(InventoryOperation,
            InventoryOperation.operation_id == InventoryMovement.operation_id).where(
                InventoryMovement.branch_id == branch, InventoryMovement.lot_id == lot,
                InventoryOperation.status == 'committed')
        if opening: stmt = stmt.where(InventoryOperation.operation_type == 'opening')
        return stmt.exists()

    opened = movement(BranchStock.branch_id, BranchStock.lot_id)
    mapped = select(LegacyProductMapping.lot_id).where(
        LegacyProductMapping.branch_id == BranchStock.branch_id,
        LegacyProductMapping.lot_id == BranchStock.lot_id).exists()
    opening = movement(BranchStock.branch_id, BranchStock.lot_id, True)
    effective = {field: case((ProductExpiryPolicyOverride.sku_id.is_not(None),
        getattr(ProductExpiryPolicyOverride, field)), else_=getattr(CategoryExpiryPolicy, field))
        for field in POLICY_FIELDS}
    base = (select(BranchStock.branch_id).join(SKU, SKU.sku_id == BranchStock.sku_id)
        .outerjoin(CategoryExpiryPolicy, CategoryExpiryPolicy.category_id == SKU.category_id)
        .outerjoin(ProductExpiryPolicyOverride, ProductExpiryPolicyOverride.sku_id == SKU.sku_id)
        .where(BranchStock.branch_id.in_(scope.branches), SKU.sku_id.in_(managed_sku_ids())))
    # Only distinct effective thresholds, never whole lot/ORM collections.
    thresholds = db.session.execute(base.with_only_columns(
        effective['critical_days'], effective['high_days'], effective['early_days']).distinct()).all()
    days = sorted({int(d) for row in thresholds for d in row if d is not None and 0 <= d <= 3650})
    branch_rows = db.session.execute(select(Branch.branch_id, Branch.timezone).where(Branch.branch_id.in_(scope.branches))).all()
    bounds = {}; valid_branches = []
    for branch, name in branch_rows:
        try:
            today = now.astimezone(zone(name)).date()
            bounds[branch] = {d: canonical_expiry(today + timedelta(days=d), name).replace(tzinfo=None) for d in days}
            valid_branches.append(branch)
        except (MutationError, OverflowError): pass
    def cutoff(field):
        if not any(bounds.values()): return literal(None)
        return case(*[(BranchStock.branch_id == branch,
            case(*[(effective[field] == d, instant) for d, instant in values.items()], else_=None))
            for branch, values in bounds.items() if values], else_=None)

    valid_policy = and_(effective['require_expiry'].is_not(None),
        effective['critical_days'] < effective['high_days'], effective['high_days'] < effective['early_days'])
    for field in POLICY_FIELDS[1:]:
        value = effective[field]
        valid_policy = and_(valid_policy, or_(value.is_(None), value.between(1, 3650)) if field == 'expected_shelf_life_days'
                           else value.between(0, 3650))
    zones = files('tzdata').joinpath('zones').read_text(encoding='utf-8').splitlines()
    dated_valid = and_(StockLot.expiry_date.is_not(None), StockLot.expiry_timezone.in_(zones),
        StockLot.expires_at_utc.is_not(None), BranchStock.branch_id.in_(valid_branches))
    undated_valid = and_(StockLot.expiry_date.is_(None), StockLot.expiry_timezone.is_(None),
        StockLot.expires_at_utc.is_(None), effective['require_expiry'].is_(False))
    state = case((or_(valid_policy.is_not(True), StockLot.validation_state != 'validated'), 'review'),
        (undated_valid, 'not_tracked'), (dated_valid.is_not(True), 'review'),
        (StockLot.expires_at_utc <= now.replace(tzinfo=None), 'expired'),
        (StockLot.expires_at_utc <= cutoff('critical_days'), 'critical'),
        (StockLot.expires_at_utc <= cutoff('high_days'), 'high'),
        (StockLot.expires_at_utc <= cutoff('early_days'), 'early'), else_='healthy')
    status = case((and_(~opened, or_(BranchStock.on_hand != 0, BranchStock.reserved != 0,
        BranchStock.quarantined_quantity != 0)), 'review'),
        (and_(or_(SKU.active.is_(False), BranchProduct.enabled.is_(False)), state != 'expired'), 'review'), else_=state)
    available = case((status.in_(('review', 'expired')), 0), else_=BranchStock.on_hand - BranchStock.reserved - BranchStock.quarantined_quantity)
    unusable = case((status.in_(('review', 'expired')), BranchStock.on_hand), else_=BranchStock.quarantined_quantity)
    native = (select(literal(0).label('source_order'), literal('native_lot').label('source'),
        BranchStock.branch_id, Branch.code.label('branch_code'), SKU.sku_id, SKU.code.label('sku_code'), SKU.name,
        StockLot.lot_id, StockLot.lot_code, BranchStock.on_hand, BranchStock.reserved,
        BranchStock.quarantined_quantity.label('quarantined'), available.label('available'), unusable.label('unusable'),
        status.label('status'), StockLot.expiry_date, StockLot.expiry_timezone, StockLot.expires_at_utc,
        Branch.timezone.label('branch_timezone'), BranchStock.version.label('stock_revision'),
        and_(SKU.active.is_(True), BranchProduct.enabled.is_(True)).label('can_reorder'),
        BranchProduct.reorder_level_override.label('threshold'),
        or_(opened, BranchStock.on_hand == 0).label('availability_verified'),
        case((and_(~opened, BranchStock.on_hand != 0), 'Opening reconciliation required'),
             (or_(SKU.active.is_(False), BranchProduct.enabled.is_(False)), 'SKU archived or not offered'), else_=status).label('hold_reason'),
        and_(valid_policy,StockLot.validation_state=='validated',dated_valid).label('_days_valid'))
        .select_from(BranchStock).join(StockLot, StockLot.lot_id == BranchStock.lot_id)
        .join(SKU, SKU.sku_id == BranchStock.sku_id).join(Branch, Branch.branch_id == BranchStock.branch_id)
        .join(BranchProduct, and_(BranchProduct.branch_id == BranchStock.branch_id, BranchProduct.sku_id == BranchStock.sku_id))
        .outerjoin(CategoryExpiryPolicy, CategoryExpiryPolicy.category_id == SKU.category_id)
        .outerjoin(ProductExpiryPolicyOverride, ProductExpiryPolicyOverride.sku_id == SKU.sku_id)
        .where(BranchStock.branch_id.in_(scope.branches), SKU.sku_id.in_(managed_sku_ids()), or_(~mapped, opening)))
    # Factor the expensive classification once; page/aggregate SQL projects
    # simple columns instead of repeating every policy/zone CASE per balance.
    raw = native.with_only_columns(*[literal(0).label(c.name) if c.name in
        ('available', 'unusable', 'hold_reason') else c for c in native.selected_columns]).cte('native_classification')
    held = raw.c.status.in_(('review', 'expired'))
    native = select(*[
        case((held, 0), else_=raw.c.on_hand-raw.c.reserved-raw.c.quarantined).label('available') if c.name == 'available' else
        case((held, raw.c.on_hand), else_=raw.c.quarantined).label('unusable') if c.name == 'unusable' else
        case((and_(raw.c.availability_verified.is_(False), raw.c.on_hand != 0), 'Opening reconciliation required'),
            (raw.c.can_reorder.is_(False), 'SKU archived or not offered'), else_=raw.c.status).label('hold_reason') if c.name == 'hold_reason' else c
        for c in raw.c])
    legacy = (select(literal(1), literal('legacy_inventory'), LegacyProductMapping.branch_id, Branch.code,
        SKU.sku_id, SKU.code, Product.name, LegacyProductMapping.lot_id,
        literal('Legacy source ') + cast(Product.product_id, String), Inventory.quantity, literal(0), literal(0), literal(0),
        Inventory.quantity, literal('review'), Product.expiry_date, literal(None), literal(None), Branch.timezone,
        Product.version, and_(SKU.active.is_(True), Product.archived.is_(False)), Product.reorder_level, literal(False),
        literal('Legacy source awaiting approved opening'), literal(False))
        .select_from(LegacyProductMapping).join(Inventory, Inventory.product_id == LegacyProductMapping.legacy_product_id)
        .join(Product, Product.product_id == LegacyProductMapping.legacy_product_id)
        .join(SKU, SKU.sku_id == LegacyProductMapping.sku_id).join(Branch, Branch.branch_id == LegacyProductMapping.branch_id)
        .where(LegacyProductMapping.branch_id.in_(scope.branches),
            ~movement(LegacyProductMapping.branch_id, LegacyProductMapping.lot_id, True)))
    return union_all(native, legacy).cte('authorized_lots')


def dto(row, now):
    result = dict(row)
    result.pop('source_order', None)
    instant = result.pop('expires_at_utc', None)
    days_valid=result.pop('_days_valid',False)
    result['days_remaining'] = None
    if isinstance(instant, str): instant = datetime.fromisoformat(instant)
    if isinstance(result['expiry_date'], str): result['expiry_date'] = date.fromisoformat(result['expiry_date'])
    if instant and result['expiry_timezone'] and days_valid:
        from expiry import UTC, TICK
        try:
            tz = zone(result['branch_timezone'])
            result['days_remaining'] = ((instant.replace(tzinfo=UTC)-TICK).astimezone(tz).date()-now.astimezone(tz).date()).days
        except MutationError: pass
    if result['expiry_date']: result['expiry_date'] = result['expiry_date'].isoformat()
    for key in ('can_reorder', 'availability_verified'): result[key] = bool(result[key])
    return result


def matches(rows, query):
    clause = literal(True)
    if query['search']:
        # Escaped substring semantics: user % and _ are literal characters.
        text = rows.c.sku_code + literal(' ') + rows.c.name + literal(' ') + rows.c.lot_code
        clause = and_(clause, func.lower(text).contains(query['search'].lower(), autoescape=True))
    if query['status'] != 'all':
        clause = and_(clause, rows.c.status.in_(('early', 'high', 'critical')) if query['status'] == 'warning' else rows.c.status == query['status'])
    return clause


def lists_and_totals(scope, now, query, branches, sections=('inventory', 'alerts')):
    rows = projection(scope, now)
    fields = ('on_hand', 'available', 'reserved', 'quarantined', 'unusable')
    metrics = [func.sum(rows.c[f]).label(f) for f in fields]
    metrics += [func.count().label('lot_count'),
        func.sum(case((rows.c.source == 'legacy_inventory', rows.c.on_hand), else_=0)).label('legacy_unverified')]
    for key, statuses in [('expired_lots', ('expired',)), ('review_lots', ('review',)), ('warning_lots', ('early', 'high', 'critical'))]:
        metrics.append(func.sum(case((and_(rows.c.on_hand > 0, rows.c.status.in_(statuses)), 1), else_=0)).label(key))
    metric_query = select(rows.c.branch_id, *metrics).group_by(rows.c.branch_id)
    grouped = select(rows.c.source_order, rows.c.branch_id, rows.c.sku_id,
        func.max(rows.c.lot_id).label('lot_id'), func.sum(rows.c.on_hand).label('physical'),
        func.sum(rows.c.available).label('available')).group_by(rows.c.source_order, rows.c.branch_id, rows.c.sku_id).cte('sku_balances')
    joined = rows.join(grouped, and_(rows.c.source_order == grouped.c.source_order,
        rows.c.branch_id == grouped.c.branch_id, rows.c.sku_id == grouped.c.sku_id, rows.c.lot_id == grouped.c.lot_id))
    low = select(*[grouped.c.physical.label(c.name) if c.name == 'on_hand' else grouped.c.available.label(c.name) if c.name == 'available'
        else literal('low_stock').label(c.name) if c.name == 'status'
        else literal('All lots of SKU').label(c.name) if c.name == 'lot_code' else c for c in rows.c]).select_from(joined).where(
            rows.c.can_reorder.is_(True), rows.c.threshold.is_not(None),
            case((rows.c.source == 'legacy_inventory', grouped.c.physical), else_=grouped.c.available) <= rows.c.threshold)
    low_rows = low.cte('low_stock')
    low_query = select(low_rows.c.branch_id, func.count(func.distinct(low_rows.c.sku_id)).label('count')).group_by(low_rows.c.branch_id)
    risks = select(rows).where(rows.c.on_hand > 0, rows.c.status.in_(('expired', 'review', 'early', 'high', 'critical')))
    alerts = union_all(risks, select(low_rows)).cte('authorized_alerts')
    offset = (query['page']-1)*query['page_size']
    def page(source):
        stmt = select(source).where(matches(source, query))
        total = select(func.count()).select_from(stmt.subquery()).scalar_subquery()
        order = [source.c.branch_id, source.c.sku_id, source.c.lot_id, source.c.status]
        order = [c.desc() if query['sort'] == 'desc' else c.asc() for c in order]
        return stmt.order_by(source.c.source_order, *order).offset(offset).limit(query['page_size']), total
    # One SQL statement shares the scoped CTE and returns only page-sized arrays
    # plus per-branch aggregate rows. JSON is transport, never stored business data.
    def packed(stmt):
        source = stmt.subquery()
        obj = func.json_object(*[value for c in source.c for value in (literal(c.name), c)])
        aggregate = func.json_group_array(obj) if db.engine.dialect.name == 'sqlite' else func.json_arrayagg(obj)
        return select(aggregate).select_from(source).scalar_subquery()
    inventory_stmt, inventory_count = page(rows)
    alert_stmt, alert_count = page(alerts)
    columns = [packed(metric_query).label('metrics'), packed(low_query).label('low')]
    if 'inventory' in sections: columns += [packed(inventory_stmt).label('inventory'), inventory_count.label('inventory_count')]
    if 'alerts' in sections: columns += [packed(alert_stmt).label('alerts'), alert_count.label('alert_count')]
    data = db.session.execute(select(*columns)).mappings().one()
    def unpack(name):
        value = data.get(name)
        return json.loads(value) if isinstance(value, str) else (value or [])
    for item in unpack('metrics'):
        branches[item['branch_id']].update({k: int(v or 0) for k, v in item.items() if k != 'branch_id'})
    for item in unpack('low'): branches[item['branch_id']]['low_stock_skus'] = item['count']
    inventory = dict(total=data.get('inventory_count', 0), items=[dto(r, now) for r in unpack('inventory')])
    conditions = dict(total=data.get('alert_count', 0), items=[dto(r, now) for r in unpack('alerts')])
    # JSON aggregate element order is engine-dependent. Sort only the already
    # SQL-bounded page; membership was selected by deterministic SQL ordering.
    for page_data in (inventory, conditions):
        page_data['items'].sort(key=lambda r:(r['branch_id'],r['sku_id'],r['lot_id'],r['status']),reverse=query['sort']=='desc')
        page_data['items'].sort(key=lambda r:0 if r['source']=='native_lot' else 1)
    for item in conditions['items']:
        item['action'] = 'Review source opening' if item['source'] == 'legacy_inventory' else 'Review lot inventory'
    return inventory, conditions
