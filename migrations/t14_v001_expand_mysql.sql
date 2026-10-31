-- T14 v001 ADDITIVE ONLY. Synthetic disposable targets only; never auto-apply.

CREATE TABLE historical_demo_target (
	singleton INTEGER NOT NULL AUTO_INCREMENT,
	token_hash VARCHAR(64) NOT NULL,
	purpose VARCHAR(40) NOT NULL,
	PRIMARY KEY (singleton),
	CHECK (singleton = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE import_runs (
	run_id INTEGER NOT NULL AUTO_INCREMENT,
	source_id VARCHAR(80) NOT NULL,
	source_revision VARCHAR(80) NOT NULL,
	manifest_hash VARCHAR(64) NOT NULL,
	manifest_json TEXT NOT NULL,
	checksums_json TEXT NOT NULL,
	configuration_json TEXT NOT NULL,
	base_run_id INTEGER,
	status VARCHAR(20) NOT NULL,
	checkpoint_line INTEGER NOT NULL,
	checkpoint_offset INTEGER NOT NULL,
	counters_json TEXT NOT NULL,
	reconciliation_json TEXT,
	created_at_utc VARCHAR(32) NOT NULL,
	updated_at_utc VARCHAR(32) NOT NULL,
	published_at_utc VARCHAR(32),
	PRIMARY KEY (run_id),
	CONSTRAINT uq_import_source_revision UNIQUE (source_id, source_revision),
	CONSTRAINT uq_import_run_identity UNIQUE (run_id, source_id, source_revision),
	CONSTRAINT ck_import_status CHECK (status IN ('staging','reconciled','published','superseded')),
	CONSTRAINT ck_import_checkpoint CHECK (checkpoint_line >= 0 AND checkpoint_offset >= 0),
	UNIQUE (manifest_hash),
	FOREIGN KEY(base_run_id) REFERENCES import_runs (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE historical_datasets (
	source_id VARCHAR(80) NOT NULL,
	active_run_id INTEGER,
	version INTEGER NOT NULL,
	PRIMARY KEY (source_id),
	FOREIGN KEY(active_run_id) REFERENCES import_runs (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE import_quarantine (
	run_id INTEGER NOT NULL,
	line_number INTEGER NOT NULL,
	reason VARCHAR(40) NOT NULL,
	record_hash VARCHAR(64) NOT NULL,
	numeric_units INTEGER NOT NULL,
	PRIMARY KEY (run_id, line_number),
	FOREIGN KEY(run_id) REFERENCES import_runs (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE sales_observations (
	observation_id INTEGER NOT NULL AUTO_INCREMENT,
	run_id INTEGER NOT NULL,
	source_id VARCHAR(80) NOT NULL,
	source_revision VARCHAR(80) NOT NULL,
	source_store_id VARCHAR(40) NOT NULL,
	source_item_id VARCHAR(40) NOT NULL,
	branch_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	business_date DATE NOT NULL,
	units INTEGER,
	coverage VARCHAR(12) NOT NULL,
	measure_type VARCHAR(30) NOT NULL,
	provenance VARCHAR(30) NOT NULL,
	price_minor INTEGER,
	price_currency VARCHAR(3),
	PRIMARY KEY (observation_id),
	CONSTRAINT uq_observation_fact UNIQUE (source_id, source_revision, branch_id, sku_id, business_date, measure_type),
	FOREIGN KEY(run_id, source_id, source_revision) REFERENCES import_runs (run_id, source_id, source_revision),
	CONSTRAINT ck_observation_coverage CHECK ((coverage='observed' AND units IS NOT NULL AND units >= 0) OR (coverage='missing' AND units IS NULL)),
	CONSTRAINT ck_observation_origin CHECK (provenance='synthetic_fixture' AND measure_type='daily_units'),
	CONSTRAINT ck_observation_price CHECK ((price_minor IS NULL AND price_currency IS NULL) OR (price_minor IS NOT NULL AND price_currency IS NOT NULL AND price_minor >= 0 AND price_currency='USD')),
	FOREIGN KEY(run_id) REFERENCES import_runs (run_id),
	FOREIGN KEY(branch_id) REFERENCES branches (branch_id),
	FOREIGN KEY(sku_id) REFERENCES skus (sku_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE INDEX ix_observation_run_branch_date ON sales_observations (run_id, branch_id, business_date);

CREATE VIEW published_sales_observations AS
SELECT o.* FROM sales_observations o JOIN historical_datasets d
ON d.source_id=o.source_id AND d.active_run_id=o.run_id
JOIN import_runs r ON r.run_id=o.run_id WHERE r.status='published';

DELIMITER $$

CREATE TRIGGER t14_sales_observations_update BEFORE UPDATE ON sales_observations FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable historical fact; staging inserts only'; END$$

CREATE TRIGGER t14_sales_observations_delete BEFORE DELETE ON sales_observations FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable historical fact; staging inserts only'; END$$

CREATE TRIGGER t14_sales_observations_insert BEFORE INSERT ON sales_observations FOR EACH ROW BEGIN IF COALESCE((SELECT status FROM import_runs WHERE run_id=NEW.run_id),'absent') <> 'staging' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable historical fact; staging inserts only'; END IF; END$$

CREATE TRIGGER t14_import_quarantine_update BEFORE UPDATE ON import_quarantine FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable historical fact; staging inserts only'; END$$

CREATE TRIGGER t14_import_quarantine_delete BEFORE DELETE ON import_quarantine FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable historical fact; staging inserts only'; END$$

CREATE TRIGGER t14_import_quarantine_insert BEFORE INSERT ON import_quarantine FOR EACH ROW BEGIN IF COALESCE((SELECT status FROM import_runs WHERE run_id=NEW.run_id),'absent') <> 'staging' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable historical fact; staging inserts only'; END IF; END$$

DELIMITER ;
