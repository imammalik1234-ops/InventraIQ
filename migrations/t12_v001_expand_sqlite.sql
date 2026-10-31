-- T12 v001 EXPAND ONLY. Owner approval and disposable restore rehearsal required.

-- Existing balances and history are untouched. No automatic transfer creation or reservations.

CREATE TABLE suppliers (
	supplier_id INTEGER NOT NULL,
	code VARCHAR(80) NOT NULL,
	display_name VARCHAR(100) NOT NULL,
	active BOOLEAN NOT NULL,
	version INTEGER NOT NULL,
	PRIMARY KEY (supplier_id),
	CONSTRAINT ck_supplier_version CHECK (version >= 1),
	UNIQUE (code)
);

CREATE TABLE purchase_orders (
	order_id INTEGER NOT NULL,
	supplier_id INTEGER NOT NULL,
	supplier_snapshot JSON NOT NULL,
	branch_id INTEGER NOT NULL,
	requester_id INTEGER NOT NULL,
	approver_id INTEGER,
	currency VARCHAR(3) NOT NULL,
	state VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	expected_date DATE,
	created_at_utc DATETIME NOT NULL,
	updated_at_utc DATETIME NOT NULL,
	creation_operation_id INTEGER NOT NULL,
	approval_operation_id INTEGER,
	PRIMARY KEY (order_id),
	CONSTRAINT ck_po_state CHECK (state IN ('Draft','Submitted','Approved','PartiallyReceived','Received','Rejected','Cancelled') AND version >= 1 AND currency = 'MYR'),
	CONSTRAINT ck_po_independent_approval CHECK (approver_id IS NULL OR approver_id <> requester_id),
	FOREIGN KEY(supplier_id) REFERENCES suppliers (supplier_id),
	FOREIGN KEY(branch_id) REFERENCES branches (branch_id),
	UNIQUE (creation_operation_id),
	FOREIGN KEY(creation_operation_id) REFERENCES inventory_operations (operation_id),
	UNIQUE (approval_operation_id),
	FOREIGN KEY(approval_operation_id) REFERENCES inventory_operations (operation_id)
);

CREATE INDEX ix_po_branch_state ON purchase_orders (branch_id, state, order_id);

CREATE TABLE purchase_order_lines (
	line_id INTEGER NOT NULL,
	order_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	ordered INTEGER NOT NULL,
	received INTEGER NOT NULL,
	cancelled INTEGER NOT NULL,
	unit_cost_minor BIGINT NOT NULL,
	PRIMARY KEY (line_id),
	CONSTRAINT uq_po_sku UNIQUE (order_id, sku_id),
	CONSTRAINT ck_po_line CHECK (ordered > 0 AND ordered <= 2147483647 AND received >= 0 AND cancelled >= 0 AND received + cancelled <= ordered AND unit_cost_minor >= 0 AND unit_cost_minor <= 9999999999),
	FOREIGN KEY(order_id) REFERENCES purchase_orders (order_id),
	FOREIGN KEY(sku_id) REFERENCES skus (sku_id)
);

CREATE TABLE purchasing_events (
	event_id INTEGER NOT NULL,
	order_id INTEGER,
	supplier_id INTEGER,
	operation_id INTEGER NOT NULL,
	actor_id INTEGER NOT NULL,
	occurred_at_utc DATETIME NOT NULL,
	reason VARCHAR(255) NOT NULL,
	facts JSON NOT NULL,
	PRIMARY KEY (event_id),
	FOREIGN KEY(order_id) REFERENCES purchase_orders (order_id),
	FOREIGN KEY(supplier_id) REFERENCES suppliers (supplier_id),
	UNIQUE (operation_id),
	FOREIGN KEY(operation_id) REFERENCES inventory_operations (operation_id)
);

CREATE TABLE purchase_receipt_links (
	link_id INTEGER NOT NULL,
	line_id INTEGER NOT NULL,
	receipt_id INTEGER NOT NULL,
	movement_id INTEGER NOT NULL,
	operation_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	quarantined INTEGER NOT NULL,
	PRIMARY KEY (link_id),
	CONSTRAINT ck_po_receipt CHECK (quantity > 0 AND quarantined >= 0 AND quarantined <= quantity),
	FOREIGN KEY(line_id) REFERENCES purchase_order_lines (line_id),
	UNIQUE (receipt_id),
	FOREIGN KEY(receipt_id) REFERENCES stock_receipts (receipt_id),
	UNIQUE (movement_id),
	FOREIGN KEY(movement_id) REFERENCES inventory_movements (movement_id),
	UNIQUE (operation_id),
	FOREIGN KEY(operation_id) REFERENCES inventory_operations (operation_id)
);

ALTER TABLE stock_lots ADD COLUMN supplier_id INTEGER REFERENCES suppliers(supplier_id);

CREATE TRIGGER t12_purchase_receipt_links_update BEFORE UPDATE ON purchase_receipt_links BEGIN SELECT RAISE(ABORT, 'Immutable purchasing fact'); END;

CREATE TRIGGER t12_purchase_receipt_links_delete BEFORE DELETE ON purchase_receipt_links BEGIN SELECT RAISE(ABORT, 'Immutable purchasing fact'); END;

CREATE TRIGGER t12_purchase_receipt_links_insert BEFORE INSERT ON purchase_receipt_links WHEN (SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable purchasing fact'); END;

CREATE TRIGGER t12_purchasing_events_update BEFORE UPDATE ON purchasing_events BEGIN SELECT RAISE(ABORT, 'Immutable purchasing fact'); END;

CREATE TRIGGER t12_purchasing_events_delete BEFORE DELETE ON purchasing_events BEGIN SELECT RAISE(ABORT, 'Immutable purchasing fact'); END;

CREATE TRIGGER t12_purchasing_events_insert BEFORE INSERT ON purchasing_events WHEN (SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable purchasing fact'); END;

CREATE TRIGGER t12_purchase_order_lines_update BEFORE UPDATE ON purchase_order_lines WHEN NEW.order_id <> OLD.order_id OR NEW.sku_id <> OLD.sku_id OR NEW.ordered <> OLD.ordered OR NEW.unit_cost_minor <> OLD.unit_cost_minor BEGIN SELECT RAISE(ABORT, 'Immutable purchasing fact'); END;

CREATE TRIGGER t12_purchase_order_lines_delete BEFORE DELETE ON purchase_order_lines BEGIN SELECT RAISE(ABORT, 'Immutable purchasing fact'); END;

CREATE TRIGGER t12_purchase_order_lines_insert BEFORE INSERT ON purchase_order_lines WHEN (SELECT o.status FROM inventory_operations o JOIN purchase_orders p ON p.creation_operation_id=o.operation_id WHERE p.order_id=NEW.order_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable purchasing fact'); END;
