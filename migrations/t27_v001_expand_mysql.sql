-- T27 additive schema. Review and obtain approval before applying to an existing DB.

CREATE TABLE operation_proposals (
	proposal_id VARCHAR(32) NOT NULL,
	tenant VARCHAR(128) NOT NULL,
	actor_id INTEGER NOT NULL,
	branch_id INTEGER NOT NULL,
	action VARCHAR(30) NOT NULL,
	request_key VARCHAR(80) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	envelope TEXT NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	seal VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL,
	expires_at DATETIME NOT NULL,
	state VARCHAR(16) NOT NULL,
	operation_id INTEGER,
	result JSON,
	PRIMARY KEY (proposal_id),
	CONSTRAINT uq_proposal_request UNIQUE (tenant, actor_id, request_key),
	CONSTRAINT ck_proposal_state CHECK (state IN ('pending','executed','cancelled')),
	CONSTRAINT ck_proposal_action CHECK (action IN ('propose_transfer','propose_adjustment')),
	FOREIGN KEY(branch_id) REFERENCES branches (branch_id),
	UNIQUE (operation_id),
	FOREIGN KEY(operation_id) REFERENCES inventory_operations (operation_id)
)ENGINE=InnoDB;

CREATE TABLE proposal_audit (
	audit_id INTEGER NOT NULL AUTO_INCREMENT,
	proposal_id VARCHAR(32) NOT NULL,
	actor_id INTEGER NOT NULL,
	event VARCHAR(16) NOT NULL,
	occurred_at DATETIME NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	operation_id INTEGER,
	PRIMARY KEY (audit_id),
	CONSTRAINT uq_proposal_audit_event UNIQUE (proposal_id, event),
	CONSTRAINT ck_proposal_event CHECK (event IN ('created','confirmed','cancelled')),
	FOREIGN KEY(proposal_id) REFERENCES operation_proposals (proposal_id),
	FOREIGN KEY(operation_id) REFERENCES inventory_operations (operation_id)
)ENGINE=InnoDB;

DELIMITER $$

CREATE TRIGGER t27_proposal_immutable BEFORE UPDATE ON operation_proposals FOR EACH ROW BEGIN IF (NOT (NEW.proposal_id <=> OLD.proposal_id)) OR (NOT (NEW.tenant <=> OLD.tenant)) OR (NOT (NEW.actor_id <=> OLD.actor_id)) OR (NOT (NEW.branch_id <=> OLD.branch_id)) OR (NOT (NEW.action <=> OLD.action)) OR (NOT (NEW.request_key <=> OLD.request_key)) OR (NOT (NEW.request_hash <=> OLD.request_hash)) OR (NOT (NEW.envelope <=> OLD.envelope)) OR (NOT (NEW.payload_hash <=> OLD.payload_hash)) OR (NOT (NEW.seal <=> OLD.seal)) OR (NOT (NEW.created_at <=> OLD.created_at)) OR (NOT (NEW.expires_at <=> OLD.expires_at)) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable proposal fact'; END IF; END$$

CREATE TRIGGER t27_proposal_transition BEFORE UPDATE ON operation_proposals FOR EACH ROW BEGIN IF OLD.state <> 'pending' OR NEW.state NOT IN ('executed','cancelled') OR (NEW.state = 'executed' AND (NEW.operation_id IS NULL OR NEW.result IS NULL)) OR (NEW.state = 'cancelled' AND (NEW.operation_id IS NOT NULL OR NEW.result IS NOT NULL)) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable proposal fact'; END IF; END$$

CREATE TRIGGER t27_proposal_delete BEFORE DELETE ON operation_proposals FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable proposal fact'; END$$

CREATE TRIGGER t27_audit_update BEFORE UPDATE ON proposal_audit FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable proposal fact'; END$$

CREATE TRIGGER t27_audit_delete BEFORE DELETE ON proposal_audit FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable proposal fact'; END$$

DELIMITER ;
