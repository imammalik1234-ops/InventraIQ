-- T08 v001 EXPAND ONLY. Owner approval and disposable restore rehearsal required.

-- Legacy sales and T07 operations are untouched. No historical prices are backfilled.

CREATE TABLE priced_sales (
	sale_id INTEGER NOT NULL,
	operation_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	cashier_id INTEGER NOT NULL,
	occurred_at_utc DATETIME NOT NULL,
	business_date DATE NOT NULL,
	currency VARCHAR(3) NOT NULL,
	total_minor BIGINT NOT NULL,
	policy_snapshot JSON NOT NULL,
	audit_id INTEGER NOT NULL,
	PRIMARY KEY (sale_id),
	FOREIGN KEY(operation_id, branch_id) REFERENCES inventory_operations (operation_id, branch_id),
	CONSTRAINT uq_priced_sale_branch UNIQUE (sale_id, branch_id),
	CONSTRAINT ck_priced_sale_money CHECK (currency = 'MYR' AND total_minor >= 0 AND total_minor <= 9000000000000000),
	UNIQUE (operation_id),
	UNIQUE (audit_id),
	FOREIGN KEY(audit_id) REFERENCES catalog_audit (audit_id)
);

CREATE INDEX ix_priced_sale_branch_date ON priced_sales (branch_id, business_date, sale_id);

CREATE TABLE sale_lines (
	line_id INTEGER NOT NULL,
	sale_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	unit_price_minor BIGINT NOT NULL,
	total_minor BIGINT NOT NULL,
	sku_snapshot JSON NOT NULL,
	price_snapshot JSON NOT NULL,
	PRIMARY KEY (line_id),
	CONSTRAINT uq_sale_line_sku UNIQUE (sale_id, sku_id),
	CONSTRAINT ck_sale_line_money CHECK (quantity > 0 AND unit_price_minor >= 0 AND unit_price_minor <= 9999999999 AND total_minor = quantity * unit_price_minor AND total_minor <= 9000000000000000),
	FOREIGN KEY(sale_id) REFERENCES priced_sales (sale_id),
	FOREIGN KEY(sku_id) REFERENCES skus (sku_id)
);

CREATE TABLE sale_refunds (
	refund_id INTEGER NOT NULL,
	sale_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	operation_id INTEGER NOT NULL,
	actor_id INTEGER NOT NULL,
	occurred_at_utc DATETIME NOT NULL,
	business_date DATE NOT NULL,
	kind VARCHAR(12) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	total_minor BIGINT NOT NULL,
	reason VARCHAR(255) NOT NULL,
	audit_id INTEGER NOT NULL,
	PRIMARY KEY (refund_id),
	FOREIGN KEY(sale_id, branch_id) REFERENCES priced_sales (sale_id, branch_id),
	FOREIGN KEY(operation_id, branch_id) REFERENCES inventory_operations (operation_id, branch_id),
	CONSTRAINT ck_refund_money CHECK (kind IN ('refund','reversal') AND currency = 'MYR' AND total_minor >= 0 AND total_minor <= 9000000000000000),
	UNIQUE (operation_id),
	UNIQUE (audit_id),
	FOREIGN KEY(audit_id) REFERENCES catalog_audit (audit_id)
);

CREATE TABLE sale_allocations (
	allocation_id INTEGER NOT NULL,
	line_id INTEGER NOT NULL,
	movement_id INTEGER NOT NULL,
	lot_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	PRIMARY KEY (allocation_id),
	CONSTRAINT ck_sale_allocation_quantity CHECK (quantity > 0),
	FOREIGN KEY(line_id) REFERENCES sale_lines (line_id),
	UNIQUE (movement_id),
	FOREIGN KEY(movement_id) REFERENCES inventory_movements (movement_id),
	FOREIGN KEY(lot_id) REFERENCES stock_lots (lot_id)
);

CREATE TABLE refund_lines (
	refund_line_id INTEGER NOT NULL,
	refund_id INTEGER NOT NULL,
	allocation_id INTEGER NOT NULL,
	quantity INTEGER NOT NULL,
	total_minor BIGINT NOT NULL,
	disposition VARCHAR(15) NOT NULL,
	inspection_evidence VARCHAR(255) NOT NULL,
	movement_id INTEGER,
	PRIMARY KEY (refund_line_id),
	CONSTRAINT uq_refund_allocation UNIQUE (refund_id, allocation_id),
	CONSTRAINT ck_refund_line CHECK (quantity > 0 AND total_minor >= 0 AND total_minor <= 9000000000000000 AND disposition IN ('no_restock','saleable','quarantine')),
	FOREIGN KEY(refund_id) REFERENCES sale_refunds (refund_id),
	FOREIGN KEY(allocation_id) REFERENCES sale_allocations (allocation_id),
	UNIQUE (movement_id),
	FOREIGN KEY(movement_id) REFERENCES inventory_movements (movement_id)
);

CREATE TRIGGER t08_priced_sales_update BEFORE UPDATE ON priced_sales BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_priced_sales_delete BEFORE DELETE ON priced_sales BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_priced_sales_insert BEFORE INSERT ON priced_sales WHEN (SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_sale_lines_update BEFORE UPDATE ON sale_lines BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_sale_lines_delete BEFORE DELETE ON sale_lines BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_sale_lines_insert BEFORE INSERT ON sale_lines WHEN (SELECT o.status FROM inventory_operations o JOIN priced_sales s ON s.operation_id=o.operation_id WHERE s.sale_id=NEW.sale_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_sale_allocations_update BEFORE UPDATE ON sale_allocations BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_sale_allocations_delete BEFORE DELETE ON sale_allocations BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_sale_allocations_insert BEFORE INSERT ON sale_allocations WHEN (SELECT o.status FROM inventory_operations o JOIN priced_sales s ON s.operation_id=o.operation_id JOIN sale_lines l ON l.sale_id=s.sale_id WHERE l.line_id=NEW.line_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_sale_refunds_update BEFORE UPDATE ON sale_refunds BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_sale_refunds_delete BEFORE DELETE ON sale_refunds BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_sale_refunds_insert BEFORE INSERT ON sale_refunds WHEN (SELECT status FROM inventory_operations WHERE operation_id=NEW.operation_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_refund_lines_update BEFORE UPDATE ON refund_lines BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_refund_lines_delete BEFORE DELETE ON refund_lines BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_refund_lines_insert BEFORE INSERT ON refund_lines WHEN (SELECT o.status FROM inventory_operations o JOIN sale_refunds r ON r.operation_id=o.operation_id WHERE r.refund_id=NEW.refund_id) <> 'staging' BEGIN SELECT RAISE(ABORT, 'Immutable sale fact'); END;

CREATE TRIGGER t08_financial_audit_update BEFORE UPDATE ON catalog_audit WHEN EXISTS (SELECT 1 FROM priced_sales WHERE audit_id=OLD.audit_id) OR EXISTS (SELECT 1 FROM sale_refunds WHERE audit_id=OLD.audit_id) BEGIN SELECT RAISE(ABORT, 'Immutable financial audit'); END;

CREATE TRIGGER t08_financial_audit_delete BEFORE DELETE ON catalog_audit WHEN EXISTS (SELECT 1 FROM priced_sales WHERE audit_id=OLD.audit_id) OR EXISTS (SELECT 1 FROM sale_refunds WHERE audit_id=OLD.audit_id) BEGIN SELECT RAISE(ABORT, 'Immutable financial audit'); END;
