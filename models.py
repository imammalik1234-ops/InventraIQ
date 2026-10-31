from database import db
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import event, inspect, update
from sqlalchemy.dialects.mysql import VARCHAR as MySQLVarchar

class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (db.CheckConstraint('session_version >= 1', name='ck_user_session_version_positive'),)

    user_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum("admin", "staff", name="user_roles"), default="staff")
    enabled = db.Column(db.Boolean, nullable=False, default=True, server_default='1')
    session_version = db.Column(db.Integer, nullable=False, default=1, server_default='1')

    @property
    def is_active(self):
        return bool(self.enabled)

    def revoke_sessions(self):
        db.session.execute(update(User).where(User.user_id == self.user_id).values(session_version=User.session_version + 1))
        db.session.refresh(self)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        from authentication import session_token
        return session_token(self)

    from database import db


@event.listens_for(User, 'before_update')
def invalidate_security_changes(mapper, connection, user):
    state = inspect(user)
    if any(state.attrs[name].history.has_changes() for name in ('password_hash', 'email', 'role', 'enabled')):
        if not state.attrs.session_version.history.has_changes():
            # Atomic DB expression prevents concurrent security changes losing a revocation.
            user.session_version = User.session_version + 1


class LoginThrottle(db.Model):
    __tablename__ = 'login_throttle'
    __table_args__ = (db.CheckConstraint('attempts >= 1', name='ck_login_attempts_positive'),
                      db.CheckConstraint('slot >= 0 AND slot < 262144', name='ck_login_slot_range'),
                      {'mysql_engine': 'InnoDB'})
    kind = db.Column(db.String(10), primary_key=True)
    slot = db.Column(db.Integer, primary_key=True)
    window_start = db.Column(db.BigInteger, nullable=False)
    attempts = db.Column(db.Integer, nullable=False)

class Staff(db.Model):
    __tablename__ = 'staff'

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.String(10))
    status = db.Column(db.String(20))


class Product(db.Model):
    __tablename__ = "products"
    __table_args__ = (db.UniqueConstraint('name', 'category', 'supplier', 'expiry_date', name='uq_product_batch'),
                      {'sqlite_autoincrement': True})

    product_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(80))
    supplier = db.Column(db.String(100))
    unit_price = db.Column(db.Numeric(10, 2))
    expiry_date = db.Column(db.Date)
    reorder_level = db.Column(db.Integer, default=10)
    version = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    archived = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    archived_at = db.Column(db.DateTime)


class Inventory(db.Model):
    __tablename__ = "inventory"
    __table_args__ = (
        db.UniqueConstraint('product_id', name='uq_inventory_product'),
        db.CheckConstraint('quantity >= 0', name='ck_inventory_nonnegative'),
    )

    inventory_id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.product_id"), nullable=False)
    quantity = db.Column(db.Integer, default=0, nullable=False)
    last_updated = db.Column(db.DateTime)


class Sale(db.Model):
    __tablename__ = "sales"
    __table_args__ = (db.CheckConstraint('quantity > 0', name='ck_sale_positive'),)

    sale_id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.product_id"))
    quantity = db.Column(db.Integer, nullable=False)
    sale_date = db.Column(db.Date)

class Alert(db.Model):
    __tablename__ = "alerts"

    alert_id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.product_id"))
    alert_type = db.Column(db.String(50))
    message = db.Column(db.String(255))
    status = db.Column(db.String(30), default="active")
    created_at = db.Column(db.DateTime)

class Prediction(db.Model):
    __tablename__ = "predictions"

    prediction_id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.product_id"))
    predicted_demand = db.Column(db.Integer)
    recommended_reorder_qty = db.Column(db.Integer)
    generated_on = db.Column(db.Date)


class InventoryAudit(db.Model):
    """Snapshots deliberately survive deletion of the product or actor account."""
    __tablename__ = 'inventory_audit'
    audit_id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, nullable=False)
    product_id = db.Column(db.Integer, nullable=False, index=True)
    action = db.Column(db.String(30), nullable=False)
    old_value = db.Column(db.JSON)
    new_value = db.Column(db.JSON)
    timestamp = db.Column(db.DateTime, nullable=False)

# T04 expansion: legacy Product remains the batch-shaped compatibility contract.
# These tables have no runtime routes/writers until T05/T06 cutover.
class Branch(db.Model):
    __tablename__ = 'branches'
    __table_args__ = (db.CheckConstraint("length(code) > 0", name='ck_branch_code'), {'mysql_engine': 'InnoDB'})
    branch_id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), nullable=False, unique=True)
    display_name = db.Column(db.String(100), nullable=False)
    timezone = db.Column(db.String(64), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class UserBranch(db.Model):
    __tablename__ = 'user_branches'
    __table_args__ = (db.CheckConstraint("branch_role IN ('manager', 'clerk')", name='ck_assignment_role'), {'mysql_engine': 'InnoDB'})
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.branch_id'), primary_key=True)
    branch_role = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class Category(db.Model):
    __tablename__ = 'categories'
    __table_args__ = ({'mysql_engine': 'InnoDB'},)
    category_id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)


class SKU(db.Model):
    """Stable sellable identity; deliberately has no expiry date or supplier."""
    __tablename__ = 'skus'
    __table_args__ = (db.CheckConstraint('version >= 1', name='ck_sku_version'),
                      db.CheckConstraint('default_price IS NULL OR default_price >= 0', name='ck_sku_price'), {'mysql_engine': 'InnoDB'})
    sku_id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.category_id'), nullable=False)
    unit = db.Column(db.String(20), nullable=False, default='unit')
    variant_attributes = db.Column(db.JSON)
    default_price = db.Column(db.Numeric(10, 2))
    active = db.Column(db.Boolean, nullable=False, default=True)
    version = db.Column(db.Integer, nullable=False, default=1)


class ExpiryPolicyFields:
    require_expiry = db.Column(db.Boolean, nullable=False)
    early_days = db.Column(db.Integer, nullable=False)
    high_days = db.Column(db.Integer, nullable=False)
    critical_days = db.Column(db.Integer, nullable=False)
    expected_shelf_life_days = db.Column(db.Integer)
    min_remaining_days_on_receipt = db.Column(db.Integer, nullable=False)
    min_remaining_days_on_transfer_receipt = db.Column(db.Integer, nullable=False)
    revision = db.Column(db.Integer, nullable=False)
    effective_at = db.Column(db.DateTime, nullable=False)


def policy_constraints(prefix):
    return (db.CheckConstraint('3650 >= early_days AND early_days > high_days AND high_days > critical_days AND critical_days >= 0', name=prefix+'_thresholds'),
            db.CheckConstraint('revision >= 1 AND min_remaining_days_on_receipt >= 0 AND min_remaining_days_on_transfer_receipt >= 0', name=prefix+'_bounds'),
            db.CheckConstraint('expected_shelf_life_days IS NULL OR (expected_shelf_life_days > 0 AND expected_shelf_life_days <= 3650)', name=prefix+'_shelf'), {'mysql_engine': 'InnoDB'})


class CategoryExpiryPolicy(ExpiryPolicyFields, db.Model):
    __tablename__ = 'category_expiry_policies'
    __table_args__ = policy_constraints('ck_category_policy')
    category_id = db.Column(db.Integer, db.ForeignKey('categories.category_id'), primary_key=True)


class ProductExpiryPolicyOverride(ExpiryPolicyFields, db.Model):
    __tablename__ = 'sku_expiry_policy_overrides'
    __table_args__ = policy_constraints('ck_sku_policy')
    sku_id = db.Column(db.Integer, db.ForeignKey('skus.sku_id'), primary_key=True)


class BranchProduct(db.Model):
    __tablename__ = 'branch_products'
    __table_args__ = (db.CheckConstraint('version >= 1', name='ck_branch_product_version'),
                      db.CheckConstraint('sale_price_override IS NULL OR sale_price_override >= 0', name='ck_branch_price'),
                      db.CheckConstraint('reorder_level_override IS NULL OR reorder_level_override >= 0', name='ck_branch_reorder'), {'mysql_engine': 'InnoDB'})
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.branch_id'), primary_key=True)
    sku_id = db.Column(db.Integer, db.ForeignKey('skus.sku_id'), primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    sale_price_override = db.Column(db.Numeric(10, 2))
    reorder_level_override = db.Column(db.Integer)
    version = db.Column(db.Integer, nullable=False, default=1)


class StockLot(db.Model):
    __tablename__ = 'stock_lots'
    __table_args__ = (db.UniqueConstraint('sku_id', 'lot_code', name='uq_sku_lot'),
                      db.UniqueConstraint('lot_id', 'sku_id', name='uq_lot_sku'),
                      db.CheckConstraint('version >= 1', name='ck_lot_version'),
                      db.CheckConstraint("validation_state IN ('review', 'validated', 'quarantine')", name='ck_lot_state'),
                      db.CheckConstraint('(expiry_date IS NULL AND expiry_timezone IS NULL AND expires_at_utc IS NULL) OR (expiry_date IS NOT NULL AND expiry_timezone IS NOT NULL AND expires_at_utc IS NOT NULL)', name='ck_lot_expiry_tuple'),
                      db.Index('ix_lot_sku_expiry', 'sku_id', 'expires_at_utc'), {'mysql_engine': 'InnoDB'})
    lot_id = db.Column(db.Integer, primary_key=True)
    sku_id = db.Column(db.Integer, db.ForeignKey('skus.sku_id'), nullable=False)
    lot_code = db.Column(db.String(80), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.supplier_id'))
    expiry_date = db.Column(db.Date)
    expiry_timezone = db.Column(db.String(64))
    expires_at_utc = db.Column(db.DateTime)
    provenance = db.Column(db.JSON, nullable=False)
    receipt_policy_provenance = db.Column(db.JSON)
    validation_state = db.Column(db.String(20), nullable=False, default='review')
    version = db.Column(db.Integer, nullable=False, default=1)


class BranchStock(db.Model):
    __tablename__ = 'branch_stock'
    __table_args__ = (db.ForeignKeyConstraint(['branch_id', 'sku_id'], ['branch_products.branch_id', 'branch_products.sku_id']),
                      db.ForeignKeyConstraint(['lot_id', 'sku_id'], ['stock_lots.lot_id', 'stock_lots.sku_id']),
                      db.CheckConstraint('on_hand >= 0 AND reserved >= 0 AND quarantined_quantity >= 0 AND reserved + quarantined_quantity <= on_hand', name='ck_stock_quantities'),
                      db.CheckConstraint('version >= 1', name='ck_stock_version'), {'mysql_engine': 'InnoDB'})
    branch_id = db.Column(db.Integer, primary_key=True)
    lot_id = db.Column(db.Integer, primary_key=True)
    sku_id = db.Column(db.Integer, nullable=False)
    on_hand = db.Column(db.Integer, nullable=False)
    reserved = db.Column(db.Integer, nullable=False, default=0)
    quarantined_quantity = db.Column(db.Integer, nullable=False, default=0)
    # Nullable for legacy stock whose receipt time was never captured.
    first_received_at_utc = db.Column(db.DateTime)
    version = db.Column(db.Integer, nullable=False, default=1)


class LegacyProductMapping(db.Model):
    __tablename__ = 'legacy_product_mappings'
    __table_args__ = (db.ForeignKeyConstraint(['lot_id', 'sku_id'], ['stock_lots.lot_id', 'stock_lots.sku_id']),
                      db.ForeignKeyConstraint(['branch_id', 'sku_id'], ['branch_products.branch_id', 'branch_products.sku_id']),
                      db.UniqueConstraint('lot_id', name='uq_legacy_lot'), {'mysql_engine': 'InnoDB'})
    legacy_product_id = db.Column(db.Integer, db.ForeignKey('products.product_id'), primary_key=True)
    legacy_inventory_id = db.Column(db.Integer, db.ForeignKey('inventory.inventory_id'), unique=True)
    branch_id = db.Column(db.Integer, nullable=False)
    sku_id = db.Column(db.Integer, nullable=False)
    lot_id = db.Column(db.Integer, nullable=False)
    evidence = db.Column(db.JSON, nullable=False)


class SchemaMigration(db.Model):
    __tablename__ = 'schema_migrations'
    __table_args__ = ({'mysql_engine': 'InnoDB'},)
    version = db.Column(db.String(40), primary_key=True)
    checksum = db.Column(db.String(64), nullable=False)
    applied_at = db.Column(db.DateTime, nullable=False)

class UserHQScope(db.Model):
    """Administrative scope, never an inventory branch; grants await T05."""
    __tablename__ = 'user_hq_scopes'
    __table_args__ = (db.CheckConstraint("hq_role IN ('admin', 'analyst')", name='ck_hq_role'), {'mysql_engine': 'InnoDB'})
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), primary_key=True)
    hq_role = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

class AuthorizationAudit(db.Model):
    __tablename__ = 'authorization_audit'
    __table_args__ = ({'mysql_engine': 'InnoDB'},)
    audit_id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(40), nullable=False)
    entity_key = db.Column(db.String(100), nullable=False)
    old_value = db.Column(db.JSON)
    new_value = db.Column(db.JSON, nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False)

class SKUIdentity(db.Model):
    """Canonical sellable definition key; retained when a SKU is archived."""
    __tablename__ = 'sku_identities'
    __table_args__ = ({'mysql_engine': 'InnoDB'},)
    sku_id = db.Column(db.Integer, db.ForeignKey('skus.sku_id'), primary_key=True)
    signature = db.Column(db.String(64), nullable=False, unique=True)


class CatalogAudit(db.Model):
    __tablename__ = 'catalog_audit'
    __table_args__ = (db.UniqueConstraint('entity_type', 'entity_key', 'revision', name='uq_catalog_revision'),
                      db.CheckConstraint('revision >= 1', name='ck_catalog_audit_revision'),
                      {'mysql_engine': 'InnoDB'})
    audit_id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, nullable=False)
    entity_type = db.Column(db.String(30), nullable=False)
    entity_key = db.Column(db.String(100), nullable=False)
    revision = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    old_value = db.Column(db.JSON)
    new_value = db.Column(db.JSON, nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False)


class CompanyExpirySettings(db.Model):
    __tablename__ = 'company_expiry_settings'
    __table_args__ = (db.CheckConstraint('settings_id = 1 AND version >= 1', name='ck_company_expiry_settings'), {'mysql_engine': 'InnoDB'})
    settings_id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    label_timezone = db.Column(db.String(64), nullable=False)
    version = db.Column(db.Integer, nullable=False)


class StockReceipt(db.Model):
    """Receiving evidence only, not the T07 operation/movement ledger."""
    __tablename__ = 'stock_receipts'
    __table_args__ = (
        db.UniqueConstraint('branch_id', 'external_reference', name='uq_receipt_branch_reference'),
        db.ForeignKeyConstraint(['branch_id', 'lot_id'], ['branch_stock.branch_id', 'branch_stock.lot_id']),
        db.ForeignKeyConstraint(['lot_id', 'sku_id'], ['stock_lots.lot_id', 'stock_lots.sku_id']),
        db.CheckConstraint('quantity > 0 AND quarantined_quantity >= 0 AND quarantined_quantity <= quantity', name='ck_receipt_quantity'),
        {'mysql_engine': 'InnoDB'})
    receipt_id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, nullable=False)
    lot_id = db.Column(db.Integer, nullable=False)
    sku_id = db.Column(db.Integer, nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    # Exact delivery keys match SQLite's default binary comparison semantics.
    external_reference = db.Column(db.String(100).with_variant(
        MySQLVarchar(100, charset='utf8mb4', collation='utf8mb4_bin'), 'mysql'), nullable=False)
    payload_hash = db.Column(db.String(64), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    quarantined_quantity = db.Column(db.Integer, nullable=False)
    received_at_utc = db.Column(db.DateTime, nullable=False)
    policy_snapshot = db.Column(db.JSON, nullable=False)
    evidence = db.Column(db.JSON, nullable=False)

class LotIdentity(db.Model):
    """Prevent splitting the same observed supplier batch/date into aliases."""
    __tablename__ = 'lot_identities'
    __table_args__ = ({'mysql_engine': 'InnoDB'},)
    lot_id = db.Column(db.Integer, db.ForeignKey('stock_lots.lot_id'), primary_key=True)
    signature = db.Column(db.String(64), nullable=False, unique=True)


class InventoryOperation(db.Model):
    __tablename__ = 'inventory_operations'
    __table_args__ = (
        db.UniqueConstraint('actor_id', 'request_key', name='uq_inventory_operation_key'),
        db.UniqueConstraint('operation_id', 'branch_id', name='uq_operation_branch'),
        db.CheckConstraint("status IN ('staging','committed')", name='ck_operation_status'),
        {'mysql_engine': 'InnoDB'})
    operation_id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, nullable=False)  # historical identity survives account removal
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.branch_id'), nullable=False)
    request_key = db.Column(db.String(120).with_variant(MySQLVarchar(120, charset='utf8mb4', collation='utf8mb4_bin'), 'mysql'), nullable=False)
    payload_hash = db.Column(db.String(64), nullable=False)
    operation_type = db.Column(db.String(30), nullable=False)
    business_reference = db.Column(db.String(100), nullable=False)
    business_date = db.Column(db.Date, nullable=False)
    occurred_at_utc = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(15), nullable=False)
    result = db.Column(db.JSON, nullable=False)
    evidence = db.Column(db.JSON, nullable=False)


class InventoryMovement(db.Model):
    __tablename__ = 'inventory_movements'
    __table_args__ = (
        db.ForeignKeyConstraint(['operation_id', 'branch_id'], ['inventory_operations.operation_id', 'inventory_operations.branch_id']),
        db.ForeignKeyConstraint(['branch_id', 'lot_id'], ['branch_stock.branch_id', 'branch_stock.lot_id']),
        db.ForeignKeyConstraint(['lot_id', 'sku_id'], ['stock_lots.lot_id', 'stock_lots.sku_id']),
        db.UniqueConstraint('operation_id', 'lot_id', name='uq_movement_operation_lot'),
        db.CheckConstraint('after_on_hand = before_on_hand + on_hand_delta AND after_reserved = before_reserved + reserved_delta AND after_quarantined = before_quarantined + quarantined_delta', name='ck_movement_arithmetic'),
        db.CheckConstraint('before_on_hand >= before_reserved + before_quarantined AND before_reserved >= 0 AND before_quarantined >= 0 AND after_on_hand >= after_reserved + after_quarantined AND after_reserved >= 0 AND after_quarantined >= 0', name='ck_movement_balances'),
        db.CheckConstraint('after_version = before_version + 1', name='ck_movement_version'),
        db.Index('ix_movement_branch_lot', 'branch_id', 'lot_id', 'movement_id'),
        {'mysql_engine': 'InnoDB'})
    movement_id = db.Column(db.Integer, primary_key=True)
    operation_id = db.Column(db.Integer, nullable=False)
    branch_id = db.Column(db.Integer, nullable=False)
    sku_id = db.Column(db.Integer, nullable=False)
    lot_id = db.Column(db.Integer, nullable=False)
    on_hand_delta = db.Column(db.Integer, nullable=False)
    reserved_delta = db.Column(db.Integer, nullable=False)
    quarantined_delta = db.Column(db.Integer, nullable=False)
    before_on_hand = db.Column(db.Integer, nullable=False)
    after_on_hand = db.Column(db.Integer, nullable=False)
    before_reserved = db.Column(db.Integer, nullable=False)
    after_reserved = db.Column(db.Integer, nullable=False)
    before_quarantined = db.Column(db.Integer, nullable=False)
    after_quarantined = db.Column(db.Integer, nullable=False)
    before_version = db.Column(db.Integer, nullable=False)
    after_version = db.Column(db.Integer, nullable=False)
    evidence = db.Column(db.JSON, nullable=False)


class InventoryReservation(db.Model):
    __tablename__ = 'inventory_reservations'
    __table_args__ = (
        db.ForeignKeyConstraint(['operation_id', 'branch_id'], ['inventory_operations.operation_id', 'inventory_operations.branch_id']),
        db.ForeignKeyConstraint(['branch_id', 'lot_id'], ['branch_stock.branch_id', 'branch_stock.lot_id']),
        db.CheckConstraint('quantity > 0 AND remaining >= 0 AND remaining <= quantity', name='ck_reservation_quantity'),
        {'mysql_engine': 'InnoDB'})
    operation_id = db.Column(db.Integer, primary_key=True)
    lot_id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    remaining = db.Column(db.Integer, nullable=False)


class InventoryDocument(db.Model):
    """Quantity-only business evidence. Priced sale documents remain T08."""
    __tablename__ = 'inventory_documents'
    __table_args__ = ({'mysql_engine': 'InnoDB'},)
    operation_id = db.Column(db.Integer, db.ForeignKey('inventory_operations.operation_id'), primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('stock_receipts.receipt_id'), unique=True)
    kind = db.Column(db.String(30), nullable=False)
    facts = db.Column(db.JSON, nullable=False)


# Installed by create_all ONLY on explicitly selected disposable fixtures, and by
# reviewed T07 SQL on a real cutover. No startup schema modification.
from sqlalchemy import event, DDL


def ledger_trigger_sql(dialect):
    statements = []
    for table in ('inventory_operations', 'inventory_movements', 'inventory_documents'):
        for action in ('UPDATE', 'DELETE'):
            condition = "OLD.status = 'committed'" if table == 'inventory_operations' and action == 'UPDATE' else None
            name = 'immutable_' + table + '_' + action.lower()
            if dialect == 'sqlite':
                statements.append(f"CREATE TRIGGER {name} BEFORE {action} ON {table} " +
                    (f"WHEN {condition} " if condition else '') +
                    "BEGIN SELECT RAISE(ABORT, 'Immutable inventory fact'); END;")
            else:
                body = "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable inventory fact';"
                if condition: body = f'IF {condition} THEN {body} END IF;'
                statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} FOR EACH ROW BEGIN {body} END;')
    for table in ('inventory_movements', 'inventory_documents'):
        condition = "(SELECT status FROM inventory_operations WHERE operation_id = NEW.operation_id) <> 'staging'"
        name = 'sealed_' + table
        if dialect == 'sqlite':
            statements.append(f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT, 'Sealed inventory operation'); END;")
        else:
            statements.append(f"CREATE TRIGGER {name} BEFORE INSERT ON {table} FOR EACH ROW BEGIN IF {condition} THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Sealed inventory operation'; END IF; END;")
    return statements


for _dialect in ('sqlite', 'mysql'):
    for _statement in ledger_trigger_sql(_dialect):
        event.listen(db.metadata.tables[_statement.split(' ON ')[1].split()[0]], 'after_create', DDL(_statement).execute_if(dialect=_dialect))


class PricedSale(db.Model):
    """Native sales header; legacy Sale remains untouched, with unknown revenue."""
    __tablename__ = 'priced_sales'
    __table_args__ = (
        db.ForeignKeyConstraint(['operation_id','branch_id'], ['inventory_operations.operation_id','inventory_operations.branch_id']),
        db.UniqueConstraint('sale_id','branch_id', name='uq_priced_sale_branch'),
        db.CheckConstraint("currency = 'MYR' AND total_minor >= 0 AND total_minor <= 9000000000000000", name='ck_priced_sale_money'),
        db.Index('ix_priced_sale_branch_date','branch_id','business_date','sale_id'), {'mysql_engine':'InnoDB'})
    sale_id = db.Column(db.Integer, primary_key=True)
    operation_id = db.Column(db.Integer, nullable=False, unique=True)
    branch_id = db.Column(db.Integer, nullable=False)
    cashier_id = db.Column(db.Integer, nullable=False)
    occurred_at_utc = db.Column(db.DateTime, nullable=False)
    business_date = db.Column(db.Date, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    total_minor = db.Column(db.BigInteger, nullable=False)
    policy_snapshot = db.Column(db.JSON, nullable=False)
    audit_id = db.Column(db.Integer, db.ForeignKey('catalog_audit.audit_id'), nullable=False, unique=True)

    @property
    def total(self):
        from decimal import Decimal
        return Decimal(self.total_minor)/100


class SaleLine(db.Model):
    __tablename__ = 'sale_lines'
    __table_args__ = (
        db.UniqueConstraint('sale_id','sku_id', name='uq_sale_line_sku'),
        db.CheckConstraint('quantity > 0 AND unit_price_minor >= 0 AND unit_price_minor <= 9999999999 AND total_minor = quantity * unit_price_minor AND total_minor <= 9000000000000000', name='ck_sale_line_money'),
        {'mysql_engine':'InnoDB'})
    line_id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('priced_sales.sale_id'), nullable=False)
    sku_id = db.Column(db.Integer, db.ForeignKey('skus.sku_id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price_minor = db.Column(db.BigInteger, nullable=False)
    total_minor = db.Column(db.BigInteger, nullable=False)
    sku_snapshot = db.Column(db.JSON, nullable=False)
    price_snapshot = db.Column(db.JSON, nullable=False)

    @property
    def unit_price(self):
        from decimal import Decimal
        return Decimal(self.unit_price_minor)/100

    @property
    def total(self):
        from decimal import Decimal
        return Decimal(self.total_minor)/100


class SaleAllocation(db.Model):
    __tablename__ = 'sale_allocations'
    __table_args__ = (db.CheckConstraint('quantity > 0',name='ck_sale_allocation_quantity'), {'mysql_engine':'InnoDB'})
    allocation_id = db.Column(db.Integer, primary_key=True)
    line_id = db.Column(db.Integer, db.ForeignKey('sale_lines.line_id'), nullable=False)
    movement_id = db.Column(db.Integer, db.ForeignKey('inventory_movements.movement_id'), nullable=False, unique=True)
    lot_id = db.Column(db.Integer, db.ForeignKey('stock_lots.lot_id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)


class SaleRefund(db.Model):
    __tablename__ = 'sale_refunds'
    __table_args__ = (
        db.ForeignKeyConstraint(['sale_id','branch_id'], ['priced_sales.sale_id','priced_sales.branch_id']),
        db.ForeignKeyConstraint(['operation_id','branch_id'], ['inventory_operations.operation_id','inventory_operations.branch_id']),
        db.CheckConstraint("kind IN ('refund','reversal') AND currency = 'MYR' AND total_minor >= 0 AND total_minor <= 9000000000000000", name='ck_refund_money'),
        {'mysql_engine':'InnoDB'})
    refund_id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, nullable=False)
    branch_id = db.Column(db.Integer, nullable=False)
    operation_id = db.Column(db.Integer, nullable=False, unique=True)
    actor_id = db.Column(db.Integer, nullable=False)
    occurred_at_utc = db.Column(db.DateTime, nullable=False)
    business_date = db.Column(db.Date, nullable=False)
    kind = db.Column(db.String(12), nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    total_minor = db.Column(db.BigInteger, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    audit_id = db.Column(db.Integer, db.ForeignKey('catalog_audit.audit_id'), nullable=False, unique=True)


class RefundLine(db.Model):
    __tablename__ = 'refund_lines'
    __table_args__ = (
        db.UniqueConstraint('refund_id','allocation_id', name='uq_refund_allocation'),
        db.CheckConstraint("quantity > 0 AND total_minor >= 0 AND total_minor <= 9000000000000000 AND disposition IN ('no_restock','saleable','quarantine')", name='ck_refund_line'),
        {'mysql_engine':'InnoDB'})
    refund_line_id = db.Column(db.Integer, primary_key=True)
    refund_id = db.Column(db.Integer, db.ForeignKey('sale_refunds.refund_id'), nullable=False)
    allocation_id = db.Column(db.Integer, db.ForeignKey('sale_allocations.allocation_id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    total_minor = db.Column(db.BigInteger, nullable=False)
    disposition = db.Column(db.String(15), nullable=False)
    inspection_evidence = db.Column(db.String(255), nullable=False)
    movement_id = db.Column(db.Integer, db.ForeignKey('inventory_movements.movement_id'), unique=True)


def sales_trigger_sql(dialect):
    parents = {
        'priced_sales': '(SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id)',
        'sale_lines': '(SELECT o.status FROM inventory_operations o JOIN priced_sales s ON s.operation_id=o.operation_id WHERE s.sale_id=NEW.sale_id)',
        'sale_allocations': '(SELECT o.status FROM inventory_operations o JOIN priced_sales s ON s.operation_id=o.operation_id JOIN sale_lines l ON l.sale_id=s.sale_id WHERE l.line_id=NEW.line_id)',
        'sale_refunds': '(SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id)',
        'refund_lines': '(SELECT o.status FROM inventory_operations o JOIN sale_refunds r ON r.operation_id=o.operation_id WHERE r.refund_id=NEW.refund_id)'}
    statements=[]
    for table, parent in parents.items():
        for action in ('UPDATE','DELETE','INSERT'):
            name='t08_'+table+'_'+action.lower()
            condition=f"{parent} <> 'staging'" if action=='INSERT' else None
            if dialect=='sqlite':
                statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} '+
                    (f'WHEN {condition} ' if condition else '')+"BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;")
            else:
                body="SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable sale fact';"
                if condition:body=f'IF {condition} THEN {body} END IF;'
                statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} FOR EACH ROW BEGIN {body} END;')
    for action in ('UPDATE','DELETE'):
        condition='EXISTS (SELECT 1 FROM priced_sales WHERE audit_id=OLD.audit_id) OR EXISTS (SELECT 1 FROM sale_refunds WHERE audit_id=OLD.audit_id)'
        name='t08_financial_audit_'+action.lower()
        if dialect=='sqlite':
            statements.append(f"CREATE TRIGGER {name} BEFORE {action} ON catalog_audit WHEN {condition} BEGIN SELECT RAISE(ABORT, 'Immutable financial audit'); END;")
        else:
            statements.append(f"CREATE TRIGGER {name} BEFORE {action} ON catalog_audit FOR EACH ROW BEGIN IF {condition} THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable financial audit'; END IF; END;")
    return statements


for _dialect in ('sqlite','mysql'):
    for _statement in sales_trigger_sql(_dialect):
        _target=_statement.split(' ON ')[1].split()[0]
        event.listen(db.metadata.tables['sale_refunds' if _target=='catalog_audit' else _target], 'after_create', DDL(_statement).execute_if(dialect=_dialect))


class StockTransfer(db.Model):
    __tablename__ = 'stock_transfers'
    __table_args__ = (
        db.CheckConstraint("source_branch_id <> destination_branch_id AND version >= 1 AND state IN ('Draft','Submitted','Approved','Rejected','Cancelled')", name='ck_transfer_state'),
        db.CheckConstraint("approver_id IS NULL OR approver_id <> requester_id", name='ck_transfer_independent_approval'),
        db.UniqueConstraint('transfer_id','source_branch_id',name='uq_transfer_source'),
        db.Index('ix_transfer_source_state','source_branch_id','state','updated_at_utc','transfer_id'),
        db.Index('ix_transfer_destination_state','destination_branch_id','state','updated_at_utc','transfer_id'),
        {'mysql_engine':'InnoDB'})
    transfer_id = db.Column(db.Integer,primary_key=True)
    source_branch_id = db.Column(db.Integer,db.ForeignKey('branches.branch_id'),nullable=False)
    destination_branch_id = db.Column(db.Integer,db.ForeignKey('branches.branch_id'),nullable=False)
    requester_id = db.Column(db.Integer,nullable=False)
    approver_id = db.Column(db.Integer)
    state = db.Column(db.String(20),nullable=False)
    version = db.Column(db.Integer,nullable=False)
    planned_arrival_utc = db.Column(db.DateTime,nullable=False)
    created_at_utc = db.Column(db.DateTime,nullable=False)
    updated_at_utc = db.Column(db.DateTime,nullable=False)
    creation_operation_id = db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),nullable=False,unique=True)
    approval_operation_id = db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),unique=True)
    cancellation_operation_id = db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),unique=True)


class StockTransferLine(db.Model):
    __tablename__ = 'stock_transfer_lines'
    __table_args__ = (
        db.UniqueConstraint('transfer_id','sku_id',name='uq_transfer_line_sku'),
        db.UniqueConstraint('line_id','transfer_id','sku_id',name='uq_transfer_line_identity'),
        db.CheckConstraint('quantity > 0 AND quantity <= 2147483647',name='ck_transfer_line_quantity'),
        {'mysql_engine':'InnoDB'})
    line_id = db.Column(db.Integer,primary_key=True)
    transfer_id = db.Column(db.Integer,db.ForeignKey('stock_transfers.transfer_id'),nullable=False)
    sku_id = db.Column(db.Integer,db.ForeignKey('skus.sku_id'),nullable=False)
    quantity = db.Column(db.Integer,nullable=False)


class StockTransferAllocation(db.Model):
    """Immutable approval allocation; cancellation references its original movement."""
    __tablename__ = 'stock_transfer_allocations'
    __table_args__ = (
        db.ForeignKeyConstraint(['line_id','transfer_id','sku_id'],['stock_transfer_lines.line_id','stock_transfer_lines.transfer_id','stock_transfer_lines.sku_id']),
        db.ForeignKeyConstraint(['transfer_id','branch_id'],['stock_transfers.transfer_id','stock_transfers.source_branch_id']),
        db.ForeignKeyConstraint(['branch_id','lot_id'],['branch_stock.branch_id','branch_stock.lot_id']),
        db.ForeignKeyConstraint(['lot_id','sku_id'],['stock_lots.lot_id','stock_lots.sku_id']),
        db.UniqueConstraint('line_id','lot_id',name='uq_transfer_allocation_lot'),
        db.CheckConstraint('quantity > 0',name='ck_transfer_allocation_quantity'),
        {'mysql_engine':'InnoDB'})
    allocation_id = db.Column(db.Integer,primary_key=True)
    line_id = db.Column(db.Integer,nullable=False)
    transfer_id = db.Column(db.Integer,nullable=False)
    sku_id = db.Column(db.Integer,nullable=False)
    branch_id = db.Column(db.Integer,nullable=False)
    lot_id = db.Column(db.Integer,nullable=False)
    quantity = db.Column(db.Integer,nullable=False)
    movement_id = db.Column(db.Integer,db.ForeignKey('inventory_movements.movement_id'),nullable=False,unique=True)


class StockTransferEvent(db.Model):
    __tablename__ = 'stock_transfer_events'
    __table_args__ = (db.UniqueConstraint('transfer_id','version',name='uq_transfer_event_version'),{'mysql_engine':'InnoDB'})
    event_id = db.Column(db.Integer,primary_key=True)
    transfer_id = db.Column(db.Integer,db.ForeignKey('stock_transfers.transfer_id'),nullable=False)
    version = db.Column(db.Integer,nullable=False)
    from_state = db.Column(db.String(20))
    to_state = db.Column(db.String(20),nullable=False)
    actor_id = db.Column(db.Integer,nullable=False)
    reason = db.Column(db.String(255),nullable=False)
    occurred_at_utc = db.Column(db.DateTime,nullable=False)
    operation_id = db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),nullable=False,unique=True)


def transfer_trigger_sql(dialect):
    parents = {
        'stock_transfer_lines': '(SELECT o.status FROM inventory_operations o JOIN stock_transfers t ON t.creation_operation_id=o.operation_id WHERE t.transfer_id=NEW.transfer_id)',
        'stock_transfer_allocations': '(SELECT o.status FROM inventory_operations o JOIN inventory_movements m ON m.operation_id=o.operation_id WHERE m.movement_id=NEW.movement_id)',
        'stock_transfer_events': '(SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id)'}
    statements=[]
    for table,parent in parents.items():
        for action in ('UPDATE','DELETE','INSERT'):
            condition=f"{parent} <> 'staging'" if action=='INSERT' else None
            name='t10_'+table+'_'+action.lower()
            if dialect=='sqlite':
                statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} '+
                    (f'WHEN {condition} ' if condition else '')+"BEGIN SELECT RAISE(ABORT, 'Immutable transfer fact'); END;")
            else:
                body="SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable transfer fact';"
                if condition:body=f'IF {condition} THEN {body} END IF;'
                statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} FOR EACH ROW BEGIN {body} END;')
    return statements


for _dialect in ('sqlite','mysql'):
    for _statement in transfer_trigger_sql(_dialect):
        event.listen(db.metadata.tables[_statement.split(' ON ')[1].split()[0]], 'after_create', DDL(_statement).execute_if(dialect=_dialect))


# T11 additive execution state: the T10 approval schema/history is not rewritten.
class TransferExecution(db.Model):
    __tablename__='transfer_executions'
    __table_args__=(db.CheckConstraint("state IN ('Dispatched','PartiallyReceived','Exception','Received')",name='ck_transfer_execution_state'),{'mysql_engine':'InnoDB'})
    transfer_id=db.Column(db.Integer,db.ForeignKey('stock_transfers.transfer_id'),primary_key=True)
    state=db.Column(db.String(20),nullable=False)
    dispatch_operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),nullable=False,unique=True)
    dispatched_at_utc=db.Column(db.DateTime,nullable=False)


class TransferTransit(db.Model):
    __tablename__='transfer_transit'
    __table_args__=(db.CheckConstraint('dispatched > 0 AND remaining >= 0 AND received >= 0 AND lost >= 0 AND dispatched = remaining + received + lost',name='ck_transfer_transit_conservation'),{'mysql_engine':'InnoDB'})
    allocation_id=db.Column(db.Integer,db.ForeignKey('stock_transfer_allocations.allocation_id'),primary_key=True)
    dispatched=db.Column(db.Integer,nullable=False)
    remaining=db.Column(db.Integer,nullable=False)
    received=db.Column(db.Integer,nullable=False)
    lost=db.Column(db.Integer,nullable=False)


class TransferTransitFact(db.Model):
    __tablename__='transfer_transit_facts'
    __table_args__=(db.CheckConstraint('after_quantity = before_quantity + delta AND before_quantity >= 0 AND after_quantity >= 0',name='ck_transit_fact_arithmetic'),db.UniqueConstraint('operation_id','allocation_id',name='uq_transit_operation_allocation'),{'mysql_engine':'InnoDB'})
    fact_id=db.Column(db.Integer,primary_key=True)
    allocation_id=db.Column(db.Integer,db.ForeignKey('stock_transfer_allocations.allocation_id'),nullable=False)
    operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),nullable=False)
    movement_id=db.Column(db.Integer,db.ForeignKey('inventory_movements.movement_id'),unique=True)
    before_quantity=db.Column(db.Integer,nullable=False)
    delta=db.Column(db.Integer,nullable=False)
    after_quantity=db.Column(db.Integer,nullable=False)
    kind=db.Column(db.String(30),nullable=False)
    evidence=db.Column(db.JSON,nullable=False)


class TransferDiscrepancy(db.Model):
    __tablename__='transfer_discrepancies'
    __table_args__=(db.CheckConstraint("quantity > 0 AND location IN ('transit','quarantine')",name='ck_transfer_discrepancy'),{'mysql_engine':'InnoDB'})
    discrepancy_id=db.Column(db.Integer,primary_key=True)
    allocation_id=db.Column(db.Integer,db.ForeignKey('stock_transfer_allocations.allocation_id'),nullable=False)
    quantity=db.Column(db.Integer,nullable=False)
    location=db.Column(db.String(20),nullable=False)
    reason=db.Column(db.String(255),nullable=False)
    operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),nullable=False)


class TransferResolution(db.Model):
    __tablename__='transfer_resolutions'
    __table_args__=(db.CheckConstraint("decision IN ('found','loss','quarantine_acknowledged')",name='ck_transfer_resolution'),{'mysql_engine':'InnoDB'})
    discrepancy_id=db.Column(db.Integer,db.ForeignKey('transfer_discrepancies.discrepancy_id'),primary_key=True)
    decision=db.Column(db.String(30),nullable=False)
    reason=db.Column(db.String(255),nullable=False)
    operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),nullable=False,unique=True)


class TransferReturnLink(db.Model):
    __tablename__='transfer_return_links'
    __table_args__=(db.CheckConstraint('quantity > 0',name='ck_transfer_return_quantity'),db.UniqueConstraint('child_transfer_id','allocation_id',name='uq_return_allocation'),{'mysql_engine':'InnoDB'})
    link_id=db.Column(db.Integer,primary_key=True)
    child_transfer_id=db.Column(db.Integer,db.ForeignKey('stock_transfers.transfer_id'),nullable=False)
    allocation_id=db.Column(db.Integer,db.ForeignKey('stock_transfer_allocations.allocation_id'),nullable=False)
    quantity=db.Column(db.Integer,nullable=False)
    operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),nullable=False)


def transit_trigger_sql(dialect):
    statements=[]
    for table in ('transfer_transit_facts','transfer_discrepancies','transfer_resolutions','transfer_return_links'):
        for action in ('UPDATE','DELETE','INSERT'):
            condition="(SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id) <> 'staging'" if action=='INSERT' else None
            name='t11_'+table+'_'+action.lower()
            if dialect=='sqlite':
                statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} '+(f'WHEN {condition} ' if condition else '')+"BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;")
            else:
                body="SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable transit fact';"
                if condition:body=f'IF {condition} THEN {body} END IF;'
                statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} FOR EACH ROW BEGIN {body} END;')
    return statements


for _dialect in ('sqlite','mysql'):
    for _statement in transit_trigger_sql(_dialect):
        event.listen(db.metadata.tables[_statement.split(' ON ')[1].split()[0]],'after_create',DDL(_statement).execute_if(dialect=_dialect))


class Supplier(db.Model):
    __tablename__='suppliers'
    __table_args__=(db.CheckConstraint('version >= 1',name='ck_supplier_version'),{'mysql_engine':'InnoDB'})
    supplier_id=db.Column(db.Integer,primary_key=True)
    code=db.Column(db.String(80),nullable=False,unique=True)
    display_name=db.Column(db.String(100),nullable=False)
    active=db.Column(db.Boolean,nullable=False)
    version=db.Column(db.Integer,nullable=False)


class PurchaseOrder(db.Model):
    __tablename__='purchase_orders'
    __table_args__=(db.CheckConstraint("state IN ('Draft','Submitted','Approved','PartiallyReceived','Received','Rejected','Cancelled') AND version >= 1 AND currency = 'MYR'",name='ck_po_state'),
        db.CheckConstraint('approver_id IS NULL OR approver_id <> requester_id',name='ck_po_independent_approval'),
        db.Index('ix_po_branch_state','branch_id','state','order_id'),{'mysql_engine':'InnoDB'})
    order_id=db.Column(db.Integer,primary_key=True)
    supplier_id=db.Column(db.Integer,db.ForeignKey('suppliers.supplier_id'),nullable=False)
    supplier_snapshot=db.Column(db.JSON,nullable=False)
    branch_id=db.Column(db.Integer,db.ForeignKey('branches.branch_id'),nullable=False)
    requester_id=db.Column(db.Integer,nullable=False)
    approver_id=db.Column(db.Integer)
    currency=db.Column(db.String(3),nullable=False)
    state=db.Column(db.String(24),nullable=False)
    version=db.Column(db.Integer,nullable=False)
    expected_date=db.Column(db.Date)
    created_at_utc=db.Column(db.DateTime,nullable=False)
    updated_at_utc=db.Column(db.DateTime,nullable=False)
    creation_operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),nullable=False,unique=True)
    approval_operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),unique=True)


class PurchaseOrderLine(db.Model):
    __tablename__='purchase_order_lines'
    __table_args__=(db.UniqueConstraint('order_id','sku_id',name='uq_po_sku'),
        db.CheckConstraint('ordered > 0 AND ordered <= 2147483647 AND received >= 0 AND cancelled >= 0 AND received + cancelled <= ordered AND unit_cost_minor >= 0 AND unit_cost_minor <= 9999999999',name='ck_po_line'),{'mysql_engine':'InnoDB'})
    line_id=db.Column(db.Integer,primary_key=True)
    order_id=db.Column(db.Integer,db.ForeignKey('purchase_orders.order_id'),nullable=False)
    sku_id=db.Column(db.Integer,db.ForeignKey('skus.sku_id'),nullable=False)
    ordered=db.Column(db.Integer,nullable=False)
    received=db.Column(db.Integer,nullable=False)
    cancelled=db.Column(db.Integer,nullable=False)
    unit_cost_minor=db.Column(db.BigInteger,nullable=False)


class PurchaseReceiptLink(db.Model):
    __tablename__='purchase_receipt_links'
    __table_args__=(db.CheckConstraint('quantity > 0 AND quarantined >= 0 AND quarantined <= quantity',name='ck_po_receipt'),{'mysql_engine':'InnoDB'})
    link_id=db.Column(db.Integer,primary_key=True)
    line_id=db.Column(db.Integer,db.ForeignKey('purchase_order_lines.line_id'),nullable=False)
    receipt_id=db.Column(db.Integer,db.ForeignKey('stock_receipts.receipt_id'),nullable=False,unique=True)
    movement_id=db.Column(db.Integer,db.ForeignKey('inventory_movements.movement_id'),nullable=False,unique=True)
    operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),nullable=False,unique=True)
    quantity=db.Column(db.Integer,nullable=False)
    quarantined=db.Column(db.Integer,nullable=False)


class PurchasingEvent(db.Model):
    __tablename__='purchasing_events'
    __table_args__=({'mysql_engine':'InnoDB'},)
    event_id=db.Column(db.Integer,primary_key=True)
    order_id=db.Column(db.Integer,db.ForeignKey('purchase_orders.order_id'))
    supplier_id=db.Column(db.Integer,db.ForeignKey('suppliers.supplier_id'))
    operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),nullable=False,unique=True)
    actor_id=db.Column(db.Integer,nullable=False)
    occurred_at_utc=db.Column(db.DateTime,nullable=False)
    reason=db.Column(db.String(255),nullable=False)
    facts=db.Column(db.JSON,nullable=False)


def purchasing_trigger_sql(dialect):
    statements=[]
    for table,parent in {
        'purchase_receipt_links':'(SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id)',
        'purchasing_events':'(SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id)',
        'purchase_order_lines':'(SELECT o.status FROM inventory_operations o JOIN purchase_orders p ON p.creation_operation_id=o.operation_id WHERE p.order_id=NEW.order_id)'}.items():
        for action in ('UPDATE','DELETE','INSERT'):
            condition=(parent+" <> 'staging'") if action=='INSERT' else None
            if table=='purchase_order_lines' and action=='UPDATE':condition='NEW.order_id <> OLD.order_id OR NEW.sku_id <> OLD.sku_id OR NEW.ordered <> OLD.ordered OR NEW.unit_cost_minor <> OLD.unit_cost_minor'
            name='t12_'+table+'_'+action.lower()
            if dialect=='sqlite':statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} '+(f'WHEN {condition} ' if condition else '')+"BEGIN SELECT RAISE(ABORT, 'Immutable purchasing fact'); END;")
            else:
                body="SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable purchasing fact';"
                if condition:body=f'IF {condition} THEN {body} END IF;'
                statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} FOR EACH ROW BEGIN {body} END;')
    return statements


for _dialect in ('sqlite','mysql'):
    for _statement in purchasing_trigger_sql(_dialect):
        event.listen(db.metadata.tables[_statement.split(' ON ')[1].split()[0]],'after_create',DDL(_statement).execute_if(dialect=_dialect))

# T27 definitions join metadata for NEW targets only; existing DBs need migration.
from assistant.proposal_models import OperationProposal, ProposalAudit
