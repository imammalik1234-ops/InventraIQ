-- T11 v001 EXPAND ONLY. Owner approval and disposable restore rehearsal required.

-- Existing balances and history are untouched. No automatic transfer creation or reservations.

CREATE TABLE transfer_executions (
	transfer_id INTEGER NOT NULL,
	state VARCHAR(20) NOT NULL,
	dispatch_operation_id INTEGER NOT NULL,
	dispatched_at_utc DATETIME NOT NULL,
	PRIMARY KEY (transfer_id),
	CONSTRAINT ck_transfer_execution_state CHECK (state IN ('Dispatched','PartiallyReceived','Exception','Received')),
	FOREIGN KEY(transfer_id) REFERENCES stock_transfers (transfer_id),
	UNIQUE (dispatch_operation_id),
	FOREIGN KEY(dispatch_operation_id) REFERENCES inventory_operations (operation_id)
);

CREATE TABLE transfer_discrepancies (
	discrepancy_id INTEGER NOT NULL,
	allocation_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	location VARCHAR(20) NOT NULL,
	reason VARCHAR(255) NOT NULL,
	operation_id INTEGER NOT NULL,
	PRIMARY KEY (discrepancy_id),
	CONSTRAINT ck_transfer_discrepancy CHECK (quantity > 0 AND location IN ('transit','quarantine')),
	FOREIGN KEY(allocation_id) REFERENCES stock_transfer_allocations (allocation_id),
	FOREIGN KEY(operation_id) REFERENCES inventory_operations (operation_id)
);

CREATE TABLE transfer_return_links (
	link_id INTEGER NOT NULL,
	child_transfer_id INTEGER NOT NULL,
	allocation_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	operation_id INTEGER NOT NULL,
	PRIMARY KEY (link_id),
	CONSTRAINT ck_transfer_return_quantity CHECK (quantity > 0),
	CONSTRAINT uq_return_allocation UNIQUE (child_transfer_id, allocation_id),
	FOREIGN KEY(child_transfer_id) REFERENCES stock_transfers (transfer_id),
	FOREIGN KEY(allocation_id) REFERENCES stock_transfer_allocations (allocation_id),
	FOREIGN KEY(operation_id) REFERENCES inventory_operations (operation_id)
);

CREATE TABLE transfer_transit (
	allocation_id INTEGER NOT NULL,
	dispatched INTEGER NOT NULL,
	remaining INTEGER NOT NULL,
	received INTEGER NOT NULL,
	lost INTEGER NOT NULL,
	PRIMARY KEY (allocation_id),
	CONSTRAINT ck_transfer_transit_conservation CHECK (dispatched > 0 AND remaining >= 0 AND received >= 0 AND lost >= 0 AND dispatched = remaining + received + lost),
	FOREIGN KEY(allocation_id) REFERENCES stock_transfer_allocations (allocation_id)
);

CREATE TABLE transfer_transit_facts (
	fact_id INTEGER NOT NULL,
	allocation_id INTEGER NOT NULL,
	operation_id INTEGER NOT NULL,
	movement_id INTEGER,
	before_quantity INTEGER NOT NULL,
	delta INTEGER NOT NULL,
	after_quantity INTEGER NOT NULL,
	kind VARCHAR(30) NOT NULL,
	evidence JSON NOT NULL,
	PRIMARY KEY (fact_id),
	CONSTRAINT ck_transit_fact_arithmetic CHECK (after_quantity = before_quantity + delta AND before_quantity >= 0 AND after_quantity >= 0),
	CONSTRAINT uq_transit_operation_allocation UNIQUE (operation_id, allocation_id),
	FOREIGN KEY(allocation_id) REFERENCES stock_transfer_allocations (allocation_id),
	FOREIGN KEY(operation_id) REFERENCES inventory_operations (operation_id),
	UNIQUE (movement_id),
	FOREIGN KEY(movement_id) REFERENCES inventory_movements (movement_id)
);

CREATE TABLE transfer_resolutions (
	discrepancy_id INTEGER NOT NULL,
	decision VARCHAR(30) NOT NULL,
	reason VARCHAR(255) NOT NULL,
	operation_id INTEGER NOT NULL,
	PRIMARY KEY (discrepancy_id),
	CONSTRAINT ck_transfer_resolution CHECK (decision IN ('found','loss','quarantine_acknowledged')),
	FOREIGN KEY(discrepancy_id) REFERENCES transfer_discrepancies (discrepancy_id),
	UNIQUE (operation_id),
	FOREIGN KEY(operation_id) REFERENCES inventory_operations (operation_id)
);

CREATE TRIGGER t11_transfer_transit_facts_update BEFORE UPDATE ON transfer_transit_facts BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_transit_facts_delete BEFORE DELETE ON transfer_transit_facts BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_transit_facts_insert BEFORE INSERT ON transfer_transit_facts WHEN (SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_discrepancies_update BEFORE UPDATE ON transfer_discrepancies BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_discrepancies_delete BEFORE DELETE ON transfer_discrepancies BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_discrepancies_insert BEFORE INSERT ON transfer_discrepancies WHEN (SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_resolutions_update BEFORE UPDATE ON transfer_resolutions BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_resolutions_delete BEFORE DELETE ON transfer_resolutions BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_resolutions_insert BEFORE INSERT ON transfer_resolutions WHEN (SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_return_links_update BEFORE UPDATE ON transfer_return_links BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_return_links_delete BEFORE DELETE ON transfer_return_links BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;

CREATE TRIGGER t11_transfer_return_links_insert BEFORE INSERT ON transfer_return_links WHEN (SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable transit fact'); END;
