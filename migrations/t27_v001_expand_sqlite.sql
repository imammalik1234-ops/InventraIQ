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
);

CREATE TABLE proposal_audit (
	audit_id INTEGER NOT NULL,
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
);

CREATE TRIGGER t27_proposal_immutable BEFORE UPDATE ON operation_proposals WHEN NEW.proposal_id IS NOT OLD.proposal_id OR NEW.tenant IS NOT OLD.tenant OR NEW.actor_id IS NOT OLD.actor_id OR NEW.branch_id IS NOT OLD.branch_id OR NEW.action IS NOT OLD.action OR NEW.request_key IS NOT OLD.request_key OR NEW.request_hash IS NOT OLD.request_hash OR NEW.envelope IS NOT OLD.envelope OR NEW.payload_hash IS NOT OLD.payload_hash OR NEW.seal IS NOT OLD.seal OR NEW.created_at IS NOT OLD.created_at OR NEW.expires_at IS NOT OLD.expires_at BEGIN SELECT RAISE(ABORT, 'Immutable proposal fact'); END;

CREATE TRIGGER t27_proposal_transition BEFORE UPDATE ON operation_proposals WHEN OLD.state <> 'pending' OR NEW.state NOT IN ('executed','cancelled') OR (NEW.state = 'executed' AND (NEW.operation_id IS NULL OR NEW.result IS NULL)) OR (NEW.state = 'cancelled' AND (NEW.operation_id IS NOT NULL OR NEW.result IS NOT NULL)) BEGIN SELECT RAISE(ABORT, 'Immutable proposal fact'); END;

CREATE TRIGGER t27_proposal_delete BEFORE DELETE ON operation_proposals BEGIN SELECT RAISE(ABORT, 'Immutable proposal fact'); END;

CREATE TRIGGER t27_audit_update BEFORE UPDATE ON proposal_audit BEGIN SELECT RAISE(ABORT, 'Immutable proposal fact'); END;

CREATE TRIGGER t27_audit_delete BEFORE DELETE ON proposal_audit BEGIN SELECT RAISE(ABORT, 'Immutable proposal fact'); END;
