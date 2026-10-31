"""Read-only replenishment rules, separate from model predictions."""
from math import ceil
from ai_model import forecast_demand
from models import Product, Inventory, Sale, Branch, LegacyProductMapping
from sqlalchemy import select
from database import db
from authz import scoped_query, resolve_scope


def recommend(product, inventories, sales, today):
    forecast = forecast_demand(sales, today)
    stock = sum(i.quantity or 0 for i in inventories)
    expired = product.expiry_date is not None and product.expiry_date < today
    available = 0 if expired else stock
    row = dict(product_id=product.product_id, product_name=product.name,
               current_stock=stock, available_stock=available, forecast=forecast,
               projected_shortfall=None, recommended_reorder_qty=None,
               requires_human_review=True)
    if len(inventories) != 1 or any(i.quantity is None or i.quantity < 0 for i in inventories):
        row['explanation'] = 'Review missing, duplicate or invalid inventory records before recommending.'
    elif forecast['status'] != 'eligible':
        row['explanation'] = 'Recommendation withheld: ' + forecast['reason']
    else:
        demand = forecast['predicted_demand']
        row['projected_shortfall'] = max(0, ceil(demand - available))
        row['recommended_reorder_qty'] = max(0, ceil(demand * 1.2) - available)
        row['explanation'] = ('Business rule: ceil(7-day model demand × 1.20) minus available stock, minimum zero. '
                              'The 20% buffer is a policy assumption. '
                              + ('Expired stock is excluded. ' if expired else '')
                              + 'Review supplier lead time, expiry and open orders before purchasing.')
    if row['recommended_reorder_qty'] is None:
        row.update(risk_status='Review required', risk_priority=1)
    elif row['projected_shortfall'] > 0:
        row.update(risk_status='Projected shortfall', risk_priority=0)
    elif row['recommended_reorder_qty'] > 0:
        row.update(risk_status='Buffer replenishment', risk_priority=2)
    else:
        row.update(risk_status='Covered', risk_priority=3)
    return row


def inventory_intelligence(today=None):
    scope=resolve_scope("predictions.read")
    from forecasting.target import enabled
    if enabled():
        from forecasting.reader import page
        return page()['items']
    # Retained legacy DTOs never load Sale histories or invoke recommend/model fit.
    from forecasting.clock import business_date,iso_date
    from expiry import utcnow
    instant=utcnow()
    branch_dates={b.branch_id:business_date(instant,b.timezone) for b in
        db.session.scalars(select(Branch).where(Branch.branch_id.in_(scope.branches)))}
    product_branches=dict(db.session.execute(select(LegacyProductMapping.legacy_product_id,
        LegacyProductMapping.branch_id).where(LegacyProductMapping.branch_id.in_(scope.branches))).all())
    explicit=iso_date(today) if today is not None else None
    inventory = {}
    for item in scoped_query(Inventory).all():
        inventory.setdefault(item.product_id, []).append(item)
    rows = [withheld_legacy(p, inventory.get(p.product_id, []), [],
                      explicit or branch_dates[product_branches[p.product_id]])
            for p in scoped_query(Product).filter_by(archived=False).order_by(Product.product_id).all()]
    # Policy priority, not model confidence: shortfalls, data review, buffer, covered.
    return sorted(rows, key=lambda row: (row['risk_priority'],
                                        -(row['projected_shortfall'] or 0),
                                        -(row['recommended_reorder_qty'] or 0), row['product_id']))


def withheld_legacy(product,inventories,sales,today):
    """Compatibility stock facts only. Qualification/publication never inferred."""
    stock=sum(i.quantity or 0 for i in inventories)
    available=0 if product.expiry_date is not None and product.expiry_date<today else stock
    return dict(product_id=product.product_id,product_name=product.name,current_stock=stock,available_stock=available,
        forecast=dict(status='unavailable',reason='No compatible qualified snapshot; live fitting is disabled.',
            predicted_demand=None,horizon_days=7,mae=None,history_days=None,selling_days=None,method='snapshot_only'),
        projected_shortfall=None,recommended_reorder_qty=None,requires_human_review=True,
        explanation='Recommendation withheld. Snapshot publication/qualification is required; no model is fitted here.',
        risk_status='Review required',risk_priority=1)


def branch_recommendations(values=None):
    """Advisory-only T21 boundary; server authorization is enforced by the service."""
    from services.recommendations import report
    return report(values)
