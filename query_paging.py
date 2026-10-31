"""Bounded keyset pages over immutable IDs; tokens never confer authority."""
from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadData
from sqlalchemy import select, func
from database import db
from security import MutationError


def serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='inventraiq-t16-query-v1')


def decode(token, context):
    try:
        if not isinstance(token, str) or len(token) > 4096: raise ValueError()
        value = serializer().loads(token, max_age=900)
        if value['context'] != context: raise ValueError()
        return value
    except (BadData, ValueError, KeyError, TypeError):
        raise MutationError('Page token is invalid, expired or belongs to another query. Restart from page one.', 409) from None


def context(scope, query, kind, revision=None):
    return dict(actor=scope.actor_id, branches=list(scope.branches), role=scope.role, kind=kind,
        filters={k:v for k,v in query.items() if k not in ('page', 'cursor', 'snapshot')}, revision=revision)


def page(stmt, key, scope, query, kind, revision=None, *, flat=False):
    binding = context(scope, query, kind, revision)
    token = query.get('cursor')
    value = decode(token, binding) if token else None
    # Use the selected subquery's key rather than introducing a second FROM.
    # Plain observation facts already expose their immutable key. Avoid cloning
    # every selected schema column into a subquery on every streamed page.
    # Grouped/derived readers still need the subquery's key and retain that path.
    source = None if flat else stmt.subquery()
    col = key if flat else source.c[key.key]
    base = stmt if flat else select(source)
    high_query = (stmt.with_only_columns(func.max(col), maintain_column_froms=True)
                  if flat else select(func.max(col)))
    high = value['high'] if value else db.session.scalar(high_query)
    bounded = base.where(col <= high) if high is not None else base.where(False)
    count_query = (bounded.with_only_columns(func.count(), maintain_column_froms=True)
                   if flat else select(func.count()).select_from(bounded.subquery()))
    total = value['total'] if value else db.session.scalar(count_query)
    if value:
        bounded = bounded.where(col < value['last'] if query['sort'] == 'desc' else col > value['last'])
    order = col.desc() if query['sort'] == 'desc' else col.asc()
    if not value: bounded = bounded.offset((query['page']-1)*query['page_size'])
    rows = db.session.execute(bounded.order_by(order).limit(query['page_size']+1)).mappings().all()
    more = len(rows) > query['page_size']
    rows = rows[:query['page_size']]
    next_token = serializer().dumps(dict(context=binding, high=high, last=rows[-1][key.key], total=total)) if more else None
    return dict(items=[dict(r) for r in rows], total=total, next_cursor=next_token,
        consistency='Immutable-ID high-watermark keyset; new inserts excluded. Tokens bind actor, current scope, filters and source revision; expire after 15 minutes.')


def report_snapshot(scope, query, now):
    """Optimistic read revision for offset navigation, not a retained DB snapshot.

    Native writes increment balance/lot versions and produce operation IDs;
    catalog/policy writes append audits. Changes require restarting navigation.
    """
    from models import BranchStock, StockLot, InventoryOperation, CatalogAudit, LegacyProductMapping, Inventory, Product, Branch
    from datetime import datetime
    stock = select(func.count(), func.coalesce(func.sum(BranchStock.version), 0)).where(BranchStock.branch_id.in_(scope.branches)).subquery()
    operation = select(func.max(InventoryOperation.operation_id)).where(InventoryOperation.branch_id.in_(scope.branches)).scalar_subquery()
    audit = select(func.max(CatalogAudit.audit_id)).scalar_subquery()
    lot = select(func.sum(StockLot.version)).join(BranchStock, BranchStock.lot_id == StockLot.lot_id).where(BranchStock.branch_id.in_(scope.branches)).scalar_subquery()
    legacy = select(func.sum(Inventory.quantity)).select_from(LegacyProductMapping).join(
        Inventory, Inventory.product_id == LegacyProductMapping.legacy_product_id).join(Product, Product.product_id == Inventory.product_id).where(
        LegacyProductMapping.branch_id.in_(scope.branches))
    # Keep quantities and revisions separate: a one-unit legacy sale decreases
    # quantity while increasing version, which must not cancel in the stamp.
    # MySQL returns DECIMAL for integer SUMs; revisions remain exact integers.
    stamp = [None if value is None else int(value) for value in db.session.execute(
        select(*stock.c, operation, audit, lot, legacy.scalar_subquery(),
        legacy.with_only_columns(func.sum(Product.version)).scalar_subquery())).one()]
    stamp += [list(r) for r in db.session.execute(select(Branch.branch_id, Branch.timezone).where(Branch.branch_id.in_(scope.branches)).order_by(Branch.branch_id))]
    # Do not disclose audit sequence values through readable signed token JSON.
    # Catalog writes conservatively invalidate navigation across scopes; this is
    # a query revision, never a cached broader result.
    import hashlib, hmac, json
    revision=hmac.new(current_app.secret_key.encode(),json.dumps(stamp,separators=(',',':')).encode(),hashlib.sha256).hexdigest()
    binding = context(scope, query, 'report-snapshot', revision)
    if query.get('snapshot'):
        value = decode(query['snapshot'], binding)
        return datetime.fromisoformat(value['as_of']), query['snapshot']
    return now, serializer().dumps(dict(context=binding, as_of=now.isoformat()))
