-- T15 v002: synthetic NEW disposable target only. No existing-target migration authorized.

CREATE TABLE simulation_runs (
	run_id INTEGER NOT NULL,
	manifest_hash VARCHAR(64) NOT NULL,
	manifest_json TEXT NOT NULL,
	code_hash VARCHAR(64) NOT NULL,
	status VARCHAR(20) NOT NULL,
	completed_steps INTEGER NOT NULL,
	history_hash VARCHAR(64) NOT NULL,
	PRIMARY KEY (run_id),
	CONSTRAINT ck_simulation_run CHECK (run_id=1 AND completed_steps>=0 AND status IN ('running','complete')),
	UNIQUE (manifest_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE simulation_steps (
	run_id INTEGER NOT NULL,
	step_number INTEGER NOT NULL,
	step_key VARCHAR(100) NOT NULL,
	actor_id INTEGER NOT NULL,
	branch_id INTEGER,
	at_utc VARCHAR(32) NOT NULL,
	action VARCHAR(80) NOT NULL,
	payload_json TEXT NOT NULL,
	result_json TEXT NOT NULL,
	PRIMARY KEY (run_id, step_number),
	CONSTRAINT ck_simulation_step CHECK (step_number>=0),
	FOREIGN KEY(run_id) REFERENCES simulation_runs (run_id),
	UNIQUE (step_key),
	FOREIGN KEY(actor_id) REFERENCES users (user_id),
	FOREIGN KEY(branch_id) REFERENCES branches (branch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

DELIMITER $$

CREATE TRIGGER t15_step_update BEFORE UPDATE ON simulation_steps FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable simulation step'; END$$

CREATE TRIGGER t15_step_delete BEFORE DELETE ON simulation_steps FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable simulation step'; END$$

DELIMITER ;
