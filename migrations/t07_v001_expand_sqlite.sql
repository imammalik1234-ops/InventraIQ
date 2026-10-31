-- T07 v001 EXPAND ONLY. Manual approval and disposable restore validation required.

-- No existing rows are changed. Opening reconciliation is a separate reviewed transaction.

CREATE TABLE inventory_operations (
	operation_id INTEGER NOT NULL,
	actor_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	request_key VARCHAR(120) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	operation_type VARCHAR(30) NOT NULL,
	business_reference VARCHAR(100) NOT NULL,
	business_date DATE NOT NULL,
	occurred_at_utc DATETIME NOT NULL,
	status VARCHAR(15) NOT NULL,
	result JSON NOT NULL,
	evidence JSON NOT NULL,
	PRIMARY KEY (operation_id),
	CONSTRAINT uq_inventory_operation_key UNIQUE (actor_id, request_key),
	CONSTRAINT uq_operation_branch UNIQUE (operation_id, branch_id),
	CONSTRAINT ck_operation_status CHECK (status IN ('staging','committed')),
	FOREIGN KEY(branch_id) REFERENCES branches (branch_id)
);

CREATE TABLE inventory_movements (
	movement_id INTEGER NOT NULL,
	operation_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	lot_id INTEGER NOT NULL,
	on_hand_delta INTEGER NOT NULL,
	reserved_delta INTEGER NOT NULL,
	quarantined_delta INTEGER NOT NULL,
	before_on_hand INTEGER NOT NULL,
	after_on_hand INTEGER NOT NULL,
	before_reserved INTEGER NOT NULL,
	after_reserved INTEGER NOT NULL,
	before_quarantined INTEGER NOT NULL,
	after_quarantined INTEGER NOT NULL,
	before_version INTEGER NOT NULL,
	after_version INTEGER NOT NULL,
	evidence JSON NOT NULL,
	PRIMARY KEY (movement_id),
	FOREIGN KEY(operation_id, branch_id) REFERENCES inventory_operations (operation_id, branch_id),
	FOREIGN KEY(branch_id, lot_id) REFERENCES branch_stock (branch_id, lot_id),
	FOREIGN KEY(lot_id, sku_id) REFERENCES stock_lots (lot_id, sku_id),
	CONSTRAINT uq_movement_operation_lot UNIQUE (operation_id, lot_id),
	CONSTRAINT ck_movement_arithmetic CHECK (after_on_hand = before_on_hand + on_hand_delta AND after_reserved = before_reserved + reserved_delta AND after_quarantined = before_quarantined + quarantined_delta),
	CONSTRAINT ck_movement_balances CHECK (before_on_hand >= before_reserved + before_quarantined AND before_reserved >= 0 AND before_quarantined >= 0 AND after_on_hand >= after_reserved + after_quarantined AND after_reserved >= 0 AND after_quarantined >= 0),
	CONSTRAINT ck_movement_version CHECK (after_version = before_version + 1)
);

CREATE INDEX ix_movement_branch_lot ON inventory_movements (branch_id, lot_id, movement_id);

CREATE TABLE inventory_reservations (
	operation_id INTEGER NOT NULL,
	lot_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	remaining INTEGER NOT NULL,
	PRIMARY KEY (operation_id, lot_id),
	FOREIGN KEY(operation_id, branch_id) REFERENCES inventory_operations (operation_id, branch_id),
	FOREIGN KEY(branch_id, lot_id) REFERENCES branch_stock (branch_id, lot_id),
	CONSTRAINT ck_reservation_quantity CHECK (quantity > 0 AND remaining >= 0 AND remaining <= quantity)
);

CREATE TABLE inventory_documents (
	operation_id INTEGER NOT NULL,
	receipt_id INTEGER,
	kind VARCHAR(30) NOT NULL,
	facts JSON NOT NULL,
	PRIMARY KEY (operation_id),
	FOREIGN KEY(operation_id) REFERENCES inventory_operations (operation_id),
	UNIQUE (receipt_id),
	FOREIGN KEY(receipt_id) REFERENCES stock_receipts (receipt_id)
);

CREATE TRIGGER immutable_inventory_operations_update BEFORE UPDATE ON inventory_operations WHEN OLD.status = 'committed' BEGIN SELECT RAISE(ABORT, 'Immutable inventory fact'); END;

CREATE TRIGGER immutable_inventory_operations_delete BEFORE DELETE ON inventory_operations BEGIN SELECT RAISE(ABORT, 'Immutable inventory fact'); END;

CREATE TRIGGER immutable_inventory_movements_update BEFORE UPDATE ON inventory_movements BEGIN SELECT RAISE(ABORT, 'Immutable inventory fact'); END;

CREATE TRIGGER immutable_inventory_movements_delete BEFORE DELETE ON inventory_movements BEGIN SELECT RAISE(ABORT, 'Immutable inventory fact'); END;

CREATE TRIGGER immutable_inventory_documents_update BEFORE UPDATE ON inventory_documents BEGIN SELECT RAISE(ABORT, 'Immutable inventory fact'); END;

CREATE TRIGGER immutable_inventory_documents_delete BEFORE DELETE ON inventory_documents BEGIN SELECT RAISE(ABORT, 'Immutable inventory fact'); END;

CREATE TRIGGER sealed_inventory_movements BEFORE INSERT ON inventory_movements WHEN (SELECT status FROM inventory_operations WHERE operation_id = NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Sealed inventory operation'); END;

CREATE TRIGGER sealed_inventory_documents BEFORE INSERT ON inventory_documents WHEN (SELECT status FROM inventory_operations WHERE operation_id = NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Sealed inventory operation'); END;
