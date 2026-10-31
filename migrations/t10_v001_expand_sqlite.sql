-- T10 v001 EXPAND ONLY. Owner approval and disposable restore rehearsal required.

-- Existing balances and history are untouched. No automatic transfer creation or reservations.

CREATE TABLE stock_transfers (
	transfer_id INTEGER NOT NULL,
	source_branch_id INTEGER NOT NULL,
	destination_branch_id INTEGER NOT NULL,
	requester_id INTEGER NOT NULL,
	approver_id INTEGER,
	state VARCHAR(20) NOT NULL,
	version INTEGER NOT NULL,
	planned_arrival_utc DATETIME NOT NULL,
	created_at_utc DATETIME NOT NULL,
	updated_at_utc DATETIME NOT NULL,
	creation_operation_id INTEGER NOT NULL,
	approval_operation_id INTEGER,
	cancellation_operation_id INTEGER,
	PRIMARY KEY (transfer_id),
	CONSTRAINT ck_transfer_state CHECK (source_branch_id <> destination_branch_id AND version >= 1 AND state IN ('Draft','Submitted','Approved','Rejected','Cancelled')),
	CONSTRAINT ck_transfer_independent_approval CHECK (approver_id IS NULL OR approver_id <> requester_id),
	CONSTRAINT uq_transfer_source UNIQUE (transfer_id, source_branch_id),
	FOREIGN KEY(source_branch_id) REFERENCES branches (branch_id),
	FOREIGN KEY(destination_branch_id) REFERENCES branches (branch_id),
	UNIQUE (creation_operation_id),
	FOREIGN KEY(creation_operation_id) REFERENCES inventory_operations (operation_id),
	UNIQUE (approval_operation_id),
	FOREIGN KEY(approval_operation_id) REFERENCES inventory_operations (operation_id),
	UNIQUE (cancellation_operation_id),
	FOREIGN KEY(cancellation_operation_id) REFERENCES inventory_operations (operation_id)
);

CREATE INDEX ix_transfer_destination_state ON stock_transfers (destination_branch_id, state, updated_at_utc, transfer_id);

CREATE INDEX ix_transfer_source_state ON stock_transfers (source_branch_id, state, updated_at_utc, transfer_id);

CREATE TABLE stock_transfer_events (
	event_id INTEGER NOT NULL,
	transfer_id INTEGER NOT NULL,
	version INTEGER NOT NULL,
	from_state VARCHAR(20),
	to_state VARCHAR(20) NOT NULL,
	actor_id INTEGER NOT NULL,
	reason VARCHAR(255) NOT NULL,
	occurred_at_utc DATETIME NOT NULL,
	operation_id INTEGER NOT NULL,
	PRIMARY KEY (event_id),
	CONSTRAINT uq_transfer_event_version UNIQUE (transfer_id, version),
	FOREIGN KEY(transfer_id) REFERENCES stock_transfers (transfer_id),
	UNIQUE (operation_id),
	FOREIGN KEY(operation_id) REFERENCES inventory_operations (operation_id)
);

CREATE TABLE stock_transfer_lines (
	line_id INTEGER NOT NULL,
	transfer_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	PRIMARY KEY (line_id),
	CONSTRAINT uq_transfer_line_sku UNIQUE (transfer_id, sku_id),
	CONSTRAINT uq_transfer_line_identity UNIQUE (line_id, transfer_id, sku_id),
	CONSTRAINT ck_transfer_line_quantity CHECK (quantity > 0 AND quantity <= 2147483647),
	FOREIGN KEY(transfer_id) REFERENCES stock_transfers (transfer_id),
	FOREIGN KEY(sku_id) REFERENCES skus (sku_id)
);

CREATE TABLE stock_transfer_allocations (
	allocation_id INTEGER NOT NULL,
	line_id INTEGER NOT NULL,
	transfer_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	lot_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	movement_id INTEGER NOT NULL,
	PRIMARY KEY (allocation_id),
	FOREIGN KEY(line_id, transfer_id, sku_id) REFERENCES stock_transfer_lines (line_id, transfer_id, sku_id),
	FOREIGN KEY(transfer_id, branch_id) REFERENCES stock_transfers (transfer_id, source_branch_id),
	FOREIGN KEY(branch_id, lot_id) REFERENCES branch_stock (branch_id, lot_id),
	FOREIGN KEY(lot_id, sku_id) REFERENCES stock_lots (lot_id, sku_id),
	CONSTRAINT uq_transfer_allocation_lot UNIQUE (line_id, lot_id),
	CONSTRAINT ck_transfer_allocation_quantity CHECK (quantity > 0),
	UNIQUE (movement_id),
	FOREIGN KEY(movement_id) REFERENCES inventory_movements (movement_id)
);

CREATE TRIGGER t10_stock_transfer_lines_update BEFORE UPDATE ON stock_transfer_lines BEGIN SELECT RAISE(ABORT, 'Immutable transfer fact'); END;

CREATE TRIGGER t10_stock_transfer_lines_delete BEFORE DELETE ON stock_transfer_lines BEGIN SELECT RAISE(ABORT, 'Immutable transfer fact'); END;

CREATE TRIGGER t10_stock_transfer_lines_insert BEFORE INSERT ON stock_transfer_lines WHEN (SELECT o.status FROM inventory_operations o JOIN stock_transfers t ON t.creation_operation_id=o.operation_id WHERE t.transfer_id=NEW.transfer_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable transfer fact'); END;

CREATE TRIGGER t10_stock_transfer_allocations_update BEFORE UPDATE ON stock_transfer_allocations BEGIN SELECT RAISE(ABORT, 'Immutable transfer fact'); END;

CREATE TRIGGER t10_stock_transfer_allocations_delete BEFORE DELETE ON stock_transfer_allocations BEGIN SELECT RAISE(ABORT, 'Immutable transfer fact'); END;

CREATE TRIGGER t10_stock_transfer_allocations_insert BEFORE INSERT ON stock_transfer_allocations WHEN (SELECT o.status FROM inventory_operations o JOIN inventory_movements m ON m.operation_id=o.operation_id WHERE m.movement_id=NEW.movement_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable transfer fact'); END;

CREATE TRIGGER t10_stock_transfer_events_update BEFORE UPDATE ON stock_transfer_events BEGIN SELECT RAISE(ABORT, 'Immutable transfer fact'); END;

CREATE TRIGGER t10_stock_transfer_events_delete BEFORE DELETE ON stock_transfer_events BEGIN SELECT RAISE(ABORT, 'Immutable transfer fact'); END;

CREATE TRIGGER t10_stock_transfer_events_insert BEFORE INSERT ON stock_transfer_events WHEN (SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable transfer fact'); END;
