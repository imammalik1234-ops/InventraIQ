-- T06 v001 EXPAND ONLY: review on a disposable restore before any approved use.

-- No legacy rows, SKU definitions, policies, settings or stock are backfilled automatically.

CREATE TABLE catalog_audit (
	audit_id INTEGER NOT NULL,
	actor_id INTEGER NOT NULL,
	entity_type VARCHAR(30) NOT NULL,
	entity_key VARCHAR(100) NOT NULL,
	revision INTEGER NOT NULL,
	reason VARCHAR(255) NOT NULL,
	old_value JSON,
	new_value JSON NOT NULL,
	occurred_at DATETIME NOT NULL,
	PRIMARY KEY (audit_id),
	CONSTRAINT uq_catalog_revision UNIQUE (entity_type, entity_key, revision),
	CONSTRAINT ck_catalog_audit_revision CHECK (revision >= 1)
);

CREATE TABLE company_expiry_settings (
	settings_id INTEGER NOT NULL,
	label_timezone VARCHAR(64) NOT NULL,
	version INTEGER NOT NULL,
	PRIMARY KEY (settings_id),
	CONSTRAINT ck_company_expiry_settings CHECK (settings_id = 1 AND version >= 1)
);

CREATE TABLE sku_identities (
	sku_id INTEGER NOT NULL,
	signature VARCHAR(64) NOT NULL,
	PRIMARY KEY (sku_id),
	FOREIGN KEY(sku_id) REFERENCES skus (sku_id),
	UNIQUE (signature)
);

CREATE TABLE lot_identities (
	lot_id INTEGER NOT NULL,
	signature VARCHAR(64) NOT NULL,
	PRIMARY KEY (lot_id),
	FOREIGN KEY(lot_id) REFERENCES stock_lots (lot_id),
	UNIQUE (signature)
);

CREATE TABLE stock_receipts (
	receipt_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	lot_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	actor_id INTEGER NOT NULL,
	external_reference VARCHAR(100) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	quantity INTEGER NOT NULL,
	quarantined_quantity INTEGER NOT NULL,
	received_at_utc DATETIME NOT NULL,
	policy_snapshot JSON NOT NULL,
	evidence JSON NOT NULL,
	PRIMARY KEY (receipt_id),
	CONSTRAINT uq_receipt_branch_reference UNIQUE (branch_id, external_reference),
	FOREIGN KEY(branch_id, lot_id) REFERENCES branch_stock (branch_id, lot_id),
	FOREIGN KEY(lot_id, sku_id) REFERENCES stock_lots (lot_id, sku_id),
	CONSTRAINT ck_receipt_quantity CHECK (quantity > 0 AND quarantined_quantity >= 0 AND quarantined_quantity <= quantity)
);
