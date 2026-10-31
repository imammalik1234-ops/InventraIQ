-- T04 v001 EXPAND ONLY. Manual review required; never run at startup.

-- Existing legacy tables are untouched. Backfill is separately reviewed.

CREATE TABLE branches (
	branch_id INTEGER NOT NULL,
	code VARCHAR(40) NOT NULL,
	display_name VARCHAR(100) NOT NULL,
	timezone VARCHAR(64) NOT NULL,
	active BOOLEAN NOT NULL,
	PRIMARY KEY (branch_id),
	CONSTRAINT ck_branch_code CHECK (length(code) > 0),
	UNIQUE (code)
);

CREATE TABLE categories (
	category_id INTEGER NOT NULL,
	code VARCHAR(80) NOT NULL,
	display_name VARCHAR(100) NOT NULL,
	PRIMARY KEY (category_id),
	UNIQUE (code)
);

CREATE TABLE schema_migrations (
	version VARCHAR(40) NOT NULL,
	checksum VARCHAR(64) NOT NULL,
	applied_at DATETIME NOT NULL,
	PRIMARY KEY (version)
);

CREATE TABLE category_expiry_policies (
	category_id INTEGER NOT NULL,
	require_expiry BOOLEAN NOT NULL,
	early_days INTEGER NOT NULL,
	high_days INTEGER NOT NULL,
	critical_days INTEGER NOT NULL,
	expected_shelf_life_days INTEGER,
	min_remaining_days_on_receipt INTEGER NOT NULL,
	min_remaining_days_on_transfer_receipt INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	effective_at DATETIME NOT NULL,
	PRIMARY KEY (category_id),
	CONSTRAINT ck_category_policy_thresholds CHECK (3650 >= early_days AND early_days > high_days AND high_days > critical_days AND critical_days >= 0),
	CONSTRAINT ck_category_policy_bounds CHECK (revision >= 1 AND min_remaining_days_on_receipt >= 0 AND min_remaining_days_on_transfer_receipt >= 0),
	CONSTRAINT ck_category_policy_shelf CHECK (expected_shelf_life_days IS NULL OR (expected_shelf_life_days > 0 AND expected_shelf_life_days <= 3650)),
	FOREIGN KEY(category_id) REFERENCES categories (category_id)
);

CREATE TABLE skus (
	sku_id INTEGER NOT NULL,
	code VARCHAR(80) NOT NULL,
	name VARCHAR(100) NOT NULL,
	category_id INTEGER NOT NULL,
	unit VARCHAR(20) NOT NULL,
	variant_attributes JSON,
	default_price NUMERIC(10, 2),
	active BOOLEAN NOT NULL,
	version INTEGER NOT NULL,
	PRIMARY KEY (sku_id),
	CONSTRAINT ck_sku_version CHECK (version >= 1),
	CONSTRAINT ck_sku_price CHECK (default_price IS NULL OR default_price >= 0),
	UNIQUE (code),
	FOREIGN KEY(category_id) REFERENCES categories (category_id)
);

CREATE TABLE user_branches (
	user_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	branch_role VARCHAR(20) NOT NULL,
	active BOOLEAN NOT NULL,
	PRIMARY KEY (user_id, branch_id),
	CONSTRAINT ck_assignment_role CHECK (branch_role IN ('manager', 'clerk')),
	FOREIGN KEY(user_id) REFERENCES users (user_id),
	FOREIGN KEY(branch_id) REFERENCES branches (branch_id)
);

CREATE TABLE user_hq_scopes (
	user_id INTEGER NOT NULL,
	hq_role VARCHAR(20) NOT NULL,
	active BOOLEAN NOT NULL,
	PRIMARY KEY (user_id),
	CONSTRAINT ck_hq_role CHECK (hq_role IN ('admin', 'analyst')),
	FOREIGN KEY(user_id) REFERENCES users (user_id)
);

CREATE TABLE branch_products (
	branch_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	enabled BOOLEAN NOT NULL,
	sale_price_override NUMERIC(10, 2),
	reorder_level_override INTEGER,
	version INTEGER NOT NULL,
	PRIMARY KEY (branch_id, sku_id),
	CONSTRAINT ck_branch_product_version CHECK (version >= 1),
	CONSTRAINT ck_branch_price CHECK (sale_price_override IS NULL OR sale_price_override >= 0),
	CONSTRAINT ck_branch_reorder CHECK (reorder_level_override IS NULL OR reorder_level_override >= 0),
	FOREIGN KEY(branch_id) REFERENCES branches (branch_id),
	FOREIGN KEY(sku_id) REFERENCES skus (sku_id)
);

CREATE TABLE sku_expiry_policy_overrides (
	sku_id INTEGER NOT NULL,
	require_expiry BOOLEAN NOT NULL,
	early_days INTEGER NOT NULL,
	high_days INTEGER NOT NULL,
	critical_days INTEGER NOT NULL,
	expected_shelf_life_days INTEGER,
	min_remaining_days_on_receipt INTEGER NOT NULL,
	min_remaining_days_on_transfer_receipt INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	effective_at DATETIME NOT NULL,
	PRIMARY KEY (sku_id),
	CONSTRAINT ck_sku_policy_thresholds CHECK (3650 >= early_days AND early_days > high_days AND high_days > critical_days AND critical_days >= 0),
	CONSTRAINT ck_sku_policy_bounds CHECK (revision >= 1 AND min_remaining_days_on_receipt >= 0 AND min_remaining_days_on_transfer_receipt >= 0),
	CONSTRAINT ck_sku_policy_shelf CHECK (expected_shelf_life_days IS NULL OR (expected_shelf_life_days > 0 AND expected_shelf_life_days <= 3650)),
	FOREIGN KEY(sku_id) REFERENCES skus (sku_id)
);

CREATE TABLE stock_lots (
	lot_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	lot_code VARCHAR(80) NOT NULL,
	expiry_date DATE,
	expiry_timezone VARCHAR(64),
	expires_at_utc DATETIME,
	provenance JSON NOT NULL,
	receipt_policy_provenance JSON,
	validation_state VARCHAR(20) NOT NULL,
	version INTEGER NOT NULL,
	PRIMARY KEY (lot_id),
	CONSTRAINT uq_sku_lot UNIQUE (sku_id, lot_code),
	CONSTRAINT uq_lot_sku UNIQUE (lot_id, sku_id),
	CONSTRAINT ck_lot_version CHECK (version >= 1),
	CONSTRAINT ck_lot_state CHECK (validation_state IN ('review', 'validated', 'quarantine')),
	CONSTRAINT ck_lot_expiry_tuple CHECK ((expiry_date IS NULL AND expiry_timezone IS NULL AND expires_at_utc IS NULL) OR (expiry_date IS NOT NULL AND expiry_timezone IS NOT NULL AND expires_at_utc IS NOT NULL)),
	FOREIGN KEY(sku_id) REFERENCES skus (sku_id)
);

CREATE INDEX ix_lot_sku_expiry ON stock_lots (sku_id, expires_at_utc);

CREATE TABLE branch_stock (
	branch_id INTEGER NOT NULL,
	lot_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	on_hand INTEGER NOT NULL,
	reserved INTEGER NOT NULL,
	quarantined_quantity INTEGER NOT NULL,
	first_received_at_utc DATETIME,
	version INTEGER NOT NULL,
	PRIMARY KEY (branch_id, lot_id),
	FOREIGN KEY(branch_id, sku_id) REFERENCES branch_products (branch_id, sku_id),
	FOREIGN KEY(lot_id, sku_id) REFERENCES stock_lots (lot_id, sku_id),
	CONSTRAINT ck_stock_quantities CHECK (on_hand >= 0 AND reserved >= 0 AND quarantined_quantity >= 0 AND reserved + quarantined_quantity <= on_hand),
	CONSTRAINT ck_stock_version CHECK (version >= 1)
);

CREATE TABLE legacy_product_mappings (
	legacy_product_id INTEGER NOT NULL,
	legacy_inventory_id INTEGER,
	branch_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	lot_id INTEGER NOT NULL,
	evidence JSON NOT NULL,
	PRIMARY KEY (legacy_product_id),
	FOREIGN KEY(lot_id, sku_id) REFERENCES stock_lots (lot_id, sku_id),
	FOREIGN KEY(branch_id, sku_id) REFERENCES branch_products (branch_id, sku_id),
	CONSTRAINT uq_legacy_lot UNIQUE (lot_id),
	FOREIGN KEY(legacy_product_id) REFERENCES products (product_id),
	UNIQUE (legacy_inventory_id),
	FOREIGN KEY(legacy_inventory_id) REFERENCES inventory (inventory_id)
);
