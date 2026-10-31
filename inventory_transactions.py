"""All application inventory writes. Call within security.transaction; never commit here."""
from datetime import date, datetime, timezone
from flask import current_app
from decimal import Decimal, InvalidOperation
import re
from database import db
from models import Product, Inventory, Sale, InventoryAudit
from security import MutationError
from authz import resolve_scope, legacy_resource, identity

MAX_QUANTITY = 2147483647


def whole_number(value, label='Quantity', minimum=0):
    # JSON numbers must be ints; forms supply digit strings. Never truncate floats/bools.
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r'[0-9]{1,10}', value):
        number = int(value)
    else:
        raise MutationError(f'{label} must be a whole number')
    if not minimum <= number <= MAX_QUANTITY:
        raise MutationError(f'{label} must be between {minimum} and {MAX_QUANTITY}')
    return number


def product_values(data):
    if not isinstance(data, dict):
        raise MutationError('A product object is required')
    cleaned = {}
    for field, limit in [('name', 100), ('category', 80), ('supplier', 100)]:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise MutationError(f'{field} must contain 1 to {limit} characters')
        cleaned[field] = value.strip()
    for field in ('quantity', 'reorder_level'):
        cleaned[field] = whole_number(data.get(field), field)
    try:
        if isinstance(data.get('unit_price'), bool):
            raise InvalidOperation()
        price = Decimal(str(data.get('unit_price')))
        if not price.is_finite() or not Decimal('0') < price <= Decimal('99999999.99') or price != price.quantize(Decimal('0.01')):
            raise InvalidOperation()
    except (InvalidOperation, ValueError):
        raise MutationError('Price must be positive, finite, at most 99999999.99 and use at most two decimal places')
    cleaned['unit_price'] = price
    expiry = data.get('expiry_date')
    if type(expiry) is date:
        cleaned['expiry_date'] = expiry
    else:
        try:
            cleaned['expiry_date'] = datetime.strptime(expiry, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            raise MutationError('A valid expiry date is required (YYYY-MM-DD)')
    return cleaned


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def actor_id():
    return identity().user_id


def _claim_product(product_id, expected_version=None, allow_archived=False):
    legacy_resource(product_id, 'sales.create', write=True)
    if not (current_app.testing and current_app.config.get('LEGACY_STOCK_REHEARSAL', False)):
        raise MutationError('Legacy stock writes require reviewed ledger cutover. Use native SKU operations after reconciliation.',409)
    # All writers acquire product before inventory. InnoDB holds this lock to commit.
    product = Product.query.filter_by(product_id=product_id).populate_existing().with_for_update().one_or_none()
    if product is None:
        raise MutationError('Product not found', 404)
    if product.archived and not allow_archived:
        raise MutationError('Archived products cannot be used for new operations.', 409)
    version = product.version
    if expected_version is not None and expected_version != version:
        raise MutationError('Product changed since it was displayed. Reload and review before retrying.', 409)
    changed = Product.query.filter_by(product_id=product_id, version=version).update(
        {Product.version: Product.version + 1}, synchronize_session=False)
    if changed != 1:
        raise MutationError('Concurrent inventory change. Reload and retry.', 409)
    db.session.refresh(product)
    return product


def _inventory_row(product_id, allow_missing=False):
    legacy_resource(product_id, 'sales.create', write=True)
    rows = Inventory.query.filter_by(product_id=product_id).populate_existing().with_for_update().all()
    if len(rows) > 1 or (not rows and not allow_missing):
        raise MutationError('Missing or duplicate inventory record. Correct data before changing stock.', 409)
    if rows and (rows[0].quantity is None or rows[0].quantity < 0):
        raise MutationError('Invalid existing stock. Correct data before changing stock.', 409)
    return rows[0] if rows else None


def snapshot(product, inventory):
    return dict(name=product.name, category=product.category, supplier=product.supplier,
                unit_price=str(product.unit_price) if product.unit_price is not None else None,
                expiry_date=product.expiry_date.isoformat() if product.expiry_date else None,
                reorder_level=product.reorder_level, quantity=inventory.quantity if inventory else None,
                archived=product.archived,
                archived_at=product.archived_at.isoformat() if product.archived_at else None)


def _audit(product, action, old, new, reason=None):
    legacy_resource(product.product_id, 'sales.create', write=True)
    if reason is not None:
        new = dict(new, reason=reason.strip())
    db.session.add(InventoryAudit(actor_id=actor_id(), product_id=product.product_id,
                                 action=action, old_value=old, new_value=new, timestamp=utcnow()))


def create_product(values):
    resolve_scope('catalog.manage', write=True)
    # Creating a legacy batch would bypass the stable SKU/lot receiving contract.
    raise MutationError('Use the shared SKU catalog and branch receiving pages to create definitions and lots.', 409)


def receive_stock(product_id, quantity):
    legacy_resource(product_id, 'stock.receive', write=True)
    quantity = whole_number(quantity)
    product = _claim_product(product_id)
    inventory = _inventory_row(product_id)
    old = snapshot(product, inventory)
    if inventory.quantity + quantity > MAX_QUANTITY:
        raise MutationError('Stock exceeds the supported quantity range')
    inventory.quantity += quantity
    inventory.last_updated = utcnow()
    _audit(product, 'receive', old, snapshot(product, inventory))
    return product, inventory


def edit_product(product_id, values, expected_version):
    legacy_resource(product_id, 'catalog.manage', write=True)
    resolve_scope('batch.correct', write=True)
    values = product_values(values)
    version = whole_number(expected_version, 'Revision', minimum=1)
    product = _claim_product(product_id, version)
    inventory = _inventory_row(product_id, allow_missing=True)
    old = snapshot(product, inventory)
    for key, value in values.items():
        if key != 'quantity':
            setattr(product, key, value)
    if inventory is None:
        inventory = Inventory(product_id=product_id)
        db.session.add(inventory)
    inventory.quantity = values['quantity']
    inventory.last_updated = utcnow()
    _audit(product, 'edit', old, snapshot(product, inventory))
    return product, inventory


def deduct_stock(product_id, quantity, sale=False, expected_version=None, reason=None):
    legacy_resource(product_id, 'sales.create' if sale else 'stock.adjust', write=True)
    if not sale:
        from flask import request
        if reason is None:
            payload = request.get_json(silent=True)
            reason = payload.get('reason') if isinstance(payload, dict) else request.form.get('reason')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 255:
            raise MutationError('A stock adjustment reason is required.')
    quantity = whole_number(quantity, minimum=1)
    if not sale:
        expected_version = whole_number(expected_version, 'Revision', minimum=1)
    product = _claim_product(product_id, expected_version)
    inventory = _inventory_row(product_id)
    if sale and product.expiry_date and product.expiry_date < date.today():
        raise MutationError('Cannot sell expired stock')
    old = snapshot(product, inventory)
    changed = Inventory.query.filter(Inventory.inventory_id == inventory.inventory_id,
                                     Inventory.quantity >= quantity).update(
        {Inventory.quantity: Inventory.quantity - quantity, Inventory.last_updated: utcnow()},
        synchronize_session=False)
    if changed != 1:
        raise MutationError('Not enough stock')
    db.session.refresh(inventory)
    if sale:
        db.session.add(Sale(product_id=product_id, quantity=quantity, sale_date=date.today()))
    _audit(product, 'sale' if sale else 'remove', old, snapshot(product, inventory), reason=reason)
    return product, inventory


def delete_product(product_id, expected_version, mode='archive'):
    legacy_resource(product_id, 'catalog.manage', write=True)
    if not isinstance(mode, str) or mode not in {'archive', 'permanent'}:
        raise MutationError('Removal mode must be archive or permanent')
    version = whole_number(expected_version, 'Revision', minimum=1)
    product = _claim_product(product_id, version, allow_archived=True)
    inventory = _inventory_row(product_id, allow_missing=True)
    if inventory is not None and inventory.quantity != 0:
        raise MutationError('Stock must be zero before archiving or permanent deletion. Review and reconcile stock first.', 409)
    if mode == 'permanent':
        # T04 mappings are reconciliation evidence, even if old stock is zero.
        raise MutationError('Mapped legacy definitions must be archived to retain migration evidence.', 409)
    if product.archived:
        raise MutationError('Product is already archived.', 409)
    old = snapshot(product, inventory)
    product.archived = True
    product.archived_at = utcnow()
    _audit(product, 'archive', old, snapshot(product, inventory))
    return 'archived'
