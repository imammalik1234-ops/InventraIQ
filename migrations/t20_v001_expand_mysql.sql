-- T20 v001 ADDITIVE ONLY; never auto-apply to existing databases.

CREATE TABLE forecast_demo_target (
	singleton INTEGER NOT NULL AUTO_INCREMENT,
	purpose VARCHAR(50) NOT NULL,
	PRIMARY KEY (singleton),
	CHECK (singleton=1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE forecast_runs (
	run_id INTEGER NOT NULL AUTO_INCREMENT,
	request_key VARCHAR(100) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	actor_id INTEGER NOT NULL,
	source_run_id INTEGER NOT NULL,
	source_id VARCHAR(80) NOT NULL,
	source_revision VARCHAR(80) NOT NULL,
	manifest_hash VARCHAR(64) NOT NULL,
	as_of DATE NOT NULL,
	horizon INTEGER NOT NULL,
	model_id VARCHAR(60) NOT NULL,
	model_version VARCHAR(30) NOT NULL,
	qualification VARCHAR(40) NOT NULL,
	artifact_json TEXT NOT NULL,
	artifact_hash VARCHAR(64) NOT NULL,
	configuration_json TEXT NOT NULL,
	base_version INTEGER NOT NULL,
	status VARCHAR(16) NOT NULL,
	expected_count INTEGER NOT NULL,
	eligible_count INTEGER,
	batch_hash VARCHAR(64),
	generated_at_utc VARCHAR(32) NOT NULL,
	published_at_utc VARCHAR(32),
	error_type VARCHAR(50),
	PRIMARY KEY (run_id),
	CONSTRAINT ck_forecast_run_status CHECK (status IN ('running','published','failed')),
	CONSTRAINT ck_forecast_run_bounds CHECK (horizon=7 AND expected_count BETWEEN 1 AND 360 AND base_version>=0),
	CONSTRAINT ck_forecast_qualification CHECK (qualification='provisional_synthetic'),
	UNIQUE (request_key),
	FOREIGN KEY(actor_id) REFERENCES users (user_id),
	FOREIGN KEY(source_run_id) REFERENCES import_runs (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE INDEX ix_forecast_run_created ON forecast_runs (generated_at_utc, run_id);

CREATE TABLE forecast_events (
	event_id INTEGER NOT NULL AUTO_INCREMENT,
	run_id INTEGER NOT NULL,
	actor_id INTEGER NOT NULL,
	event VARCHAR(40) NOT NULL,
	at_utc VARCHAR(32) NOT NULL,
	reason VARCHAR(240) NOT NULL,
	PRIMARY KEY (event_id),
	FOREIGN KEY(run_id) REFERENCES forecast_runs (run_id),
	FOREIGN KEY(actor_id) REFERENCES users (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE forecast_monitors (
	monitor_id INTEGER NOT NULL AUTO_INCREMENT,
	run_id INTEGER NOT NULL,
	observation_run_id INTEGER NOT NULL,
	as_of DATE NOT NULL,
	evaluated_at_utc VARCHAR(32) NOT NULL,
	status VARCHAR(40) NOT NULL,
	PRIMARY KEY (monitor_id),
	CONSTRAINT uq_forecast_monitor UNIQUE (run_id, observation_run_id, as_of),
	FOREIGN KEY(run_id) REFERENCES forecast_runs (run_id),
	FOREIGN KEY(observation_run_id) REFERENCES import_runs (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE forecast_publications (
	source_id VARCHAR(80) NOT NULL,
	active_run_id INTEGER,
	version INTEGER NOT NULL,
	PRIMARY KEY (source_id),
	CHECK (version>=0),
	FOREIGN KEY(active_run_id) REFERENCES forecast_runs (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE forecast_snapshots (
	snapshot_id INTEGER NOT NULL AUTO_INCREMENT,
	run_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	sku_id INTEGER NOT NULL,
	payload_json TEXT NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	PRIMARY KEY (snapshot_id),
	CONSTRAINT uq_forecast_series UNIQUE (run_id, branch_id, sku_id),
	FOREIGN KEY(run_id) REFERENCES forecast_runs (run_id),
	FOREIGN KEY(branch_id) REFERENCES branches (branch_id),
	FOREIGN KEY(sku_id) REFERENCES skus (sku_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE INDEX ix_forecast_snapshot_scope ON forecast_snapshots (run_id, branch_id, sku_id);

CREATE TABLE forecast_metrics (
	monitor_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	segment VARCHAR(40) NOT NULL,
	metrics_json TEXT NOT NULL,
	PRIMARY KEY (monitor_id, branch_id, segment),
	FOREIGN KEY(monitor_id) REFERENCES forecast_monitors (monitor_id),
	FOREIGN KEY(branch_id) REFERENCES branches (branch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

DELIMITER $$

CREATE TRIGGER t20_forecast_snapshots_update BEFORE UPDATE ON forecast_snapshots FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable forecast fact'; END$$

CREATE TRIGGER t20_forecast_snapshots_delete BEFORE DELETE ON forecast_snapshots FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable forecast fact'; END$$

CREATE TRIGGER t20_forecast_events_update BEFORE UPDATE ON forecast_events FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable forecast fact'; END$$

CREATE TRIGGER t20_forecast_events_delete BEFORE DELETE ON forecast_events FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable forecast fact'; END$$

CREATE TRIGGER t20_forecast_metrics_update BEFORE UPDATE ON forecast_metrics FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable forecast fact'; END$$

CREATE TRIGGER t20_forecast_metrics_delete BEFORE DELETE ON forecast_metrics FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable forecast fact'; END$$

CREATE TRIGGER t20_forecast_monitors_update BEFORE UPDATE ON forecast_monitors FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable forecast fact'; END$$

CREATE TRIGGER t20_forecast_monitors_delete BEFORE DELETE ON forecast_monitors FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable forecast fact'; END$$

CREATE TRIGGER t20_snapshot_insert BEFORE INSERT ON forecast_snapshots FOR EACH ROW BEGIN IF COALESCE((SELECT status FROM forecast_runs WHERE run_id=NEW.run_id),'missing') <> 'running' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Run is not staging'; END IF; END$$

CREATE TRIGGER t20_run_update BEFORE UPDATE ON forecast_runs FOR EACH ROW BEGIN IF OLD.status='published' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable published run'; END IF; END$$

CREATE TRIGGER t20_run_delete BEFORE DELETE ON forecast_runs FOR EACH ROW BEGIN IF OLD.status='published' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable published run'; END IF; END$$

DELIMITER ;
