"""Explicit authorization grants/mappings for pre-T05 disposable test fixtures only."""
from database import db
from models import (User, Product, Inventory, Branch, UserBranch, UserHQScope, Category,
                    SKU, StockLot, BranchProduct, LegacyProductMapping)


def grant_fixture_scopes():
    from flask import current_app
    current_app.config['LEGACY_STOCK_REHEARSAL'] = True
    db.session.flush()
    if db.session.get(Branch, 1) is None:
        db.session.add(Branch(branch_id=1, code='FIXTURE-01', display_name='Fixture branch', timezone='UTC'))
        db.session.add(Category(category_id=1, code='FIXTURE', display_name='Fixture category'))
        db.session.flush()
    for user in User.query.all():
        hq = db.session.get(UserHQScope, user.user_id)
        if user.role == 'admin':
            if hq is None:
                db.session.add(UserHQScope(user_id=user.user_id, hq_role='admin', active=True))
            else: hq.active = True
        elif hq: hq.active = False
        if db.session.get(UserBranch, (user.user_id, 1)) is None:
            db.session.add(UserBranch(user_id=user.user_id, branch_id=1, branch_role='manager', active=True))
    db.session.flush()
    for product in Product.query.all():
        if db.session.get(LegacyProductMapping, product.product_id): continue
        sku=SKU(code='FIXTURE-'+str(product.product_id), name=product.name, category_id=1)
        db.session.add(sku); db.session.flush()
        lot=StockLot(sku_id=sku.sku_id, lot_code='FIXTURE', provenance={'fixture': True})
        db.session.add(lot)
        db.session.add(BranchProduct(branch_id=1, sku_id=sku.sku_id))
        db.session.flush()
        inv=Inventory.query.filter_by(product_id=product.product_id).first()
        db.session.add(LegacyProductMapping(legacy_product_id=product.product_id,
            legacy_inventory_id=None, branch_id=1,
            sku_id=sku.sku_id, lot_id=lot.lot_id, evidence={'fixture': True}))
    db.session.flush()
