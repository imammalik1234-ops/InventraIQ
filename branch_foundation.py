"""T04 offline rehearsal helpers. Never called by app startup or routes.
Caller owns transaction; only use on an explicitly approved disposable copy.
"""
from sqlalchemy import select
from models import Branch, Product, Inventory, SKU, StockLot, BranchProduct, BranchStock, LegacyProductMapping

DEMO_BRANCHES = (
    ('DEMO-01', 'Central', 'Asia/Singapore'),
    ('DEMO-02', 'Eastside', 'Asia/Singapore'),
    ('DEMO-03', 'Westside', 'Asia/Singapore'),
)


def seed_demo_branches(session):
    """Idempotent, non-overwriting seed; no users, credentials or HQ branch."""
    for code, name, timezone in DEMO_BRANCHES:
        existing = session.scalar(select(Branch).where(Branch.code == code))
        if existing:
            if (existing.display_name, existing.timezone, existing.active) != (name, timezone, True):
                raise ValueError('Demo branch conflicts with existing definition')
        else:
            session.add(Branch(code=code, display_name=name, timezone=timezone, active=True))
    session.flush()
    return [session.scalar(select(Branch).where(Branch.code == row[0])).branch_id for row in DEMO_BRANCHES]


def backfill_legacy(session, *, legacy_branch_id, approved_mapping, approval_reference):
    """Explicit mapping {legacy_id: {sku_id, expiry_timezone, expires_at_utc}}.
    Canonical timestamps must be reviewed externally; no guessed dates/policies.
    All dated legacy rows remain review-held. No commit; rollback on any failure.
    One preserved lot per old Product, including zero/missing Inventory rows.
    """
    if not approval_reference or legacy_branch_id is None or not session.get(Branch, legacy_branch_id):
        raise ValueError('Explicit approved legacy branch and evidence required')
    products = session.scalars(select(Product).order_by(Product.product_id)).all()
    if set(approved_mapping) != {p.product_id for p in products}:
        raise ValueError('Mapping must cover every legacy product exactly once')
    # Validate the entire manifest before staging any writes.
    for p in products:
        entry = approved_mapping[p.product_id]
        if not session.get(SKU, entry['sku_id']):
            raise ValueError('Unknown SKU')
        if p.expiry_date is not None:
            if not entry.get('expiry_timezone') or not entry.get('expires_at_utc'):
                raise ValueError('Dated legacy lot requires reviewed timezone and canonical instant')
        elif entry.get('expiry_timezone') or entry.get('expires_at_utc'):
            raise ValueError('Cannot invent expiry interpretation for an undated lot')
        if session.get(LegacyProductMapping, p.product_id):
            raise ValueError('Already mapped; reconcile before replay')
    total = 0
    for p in products:
        entry = approved_mapping[p.product_id]
        sku_id = entry['sku_id']
        inv = session.scalar(select(Inventory).where(Inventory.product_id == p.product_id))
        quantity = inv.quantity if inv else 0
        if quantity < 0:
            raise ValueError('Invalid source quantity')
        assortment = session.get(BranchProduct, (legacy_branch_id, sku_id))
        if assortment is None:
            session.add(BranchProduct(branch_id=legacy_branch_id, sku_id=sku_id, enabled=False))
            session.flush()
        evidence = dict(approval_reference=approval_reference, legacy_product_id=p.product_id,
                        legacy_inventory_id=inv.inventory_id if inv else None,
                        original_expiry=p.expiry_date.isoformat() if p.expiry_date else None,
                        original_category=p.category, original_supplier=p.supplier,
                        source_quantity=quantity, source_version=p.version,
                        original_current_price=str(p.unit_price) if p.unit_price is not None else None,
                        historical_sale_price='unknown')
        lot = StockLot(sku_id=sku_id, lot_code='LEGACY-P-'+str(p.product_id),
                       expiry_date=p.expiry_date, expiry_timezone=entry.get('expiry_timezone'),
                       expires_at_utc=entry.get('expires_at_utc'), provenance=evidence,
                       validation_state='review')
        session.add(lot); session.flush()
        session.add(BranchStock(branch_id=legacy_branch_id, lot_id=lot.lot_id, sku_id=sku_id,
                                on_hand=quantity, reserved=0, quarantined_quantity=quantity))
        session.add(LegacyProductMapping(legacy_product_id=p.product_id,
                    legacy_inventory_id=inv.inventory_id if inv else None,
                    branch_id=legacy_branch_id, sku_id=sku_id, lot_id=lot.lot_id, evidence=evidence))
        total += quantity
    session.flush()
    return dict(products=len(products), quantity=total, legacy_branch_id=legacy_branch_id)
