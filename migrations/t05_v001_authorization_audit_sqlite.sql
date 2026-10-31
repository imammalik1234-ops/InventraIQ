-- T05 v001 additive authorization lifecycle evidence; manual review only.
CREATE TABLE authorization_audit (
	audit_id INTEGER NOT NULL,
	actor_id INTEGER NOT NULL,
	action VARCHAR(40) NOT NULL,
	entity_key VARCHAR(100) NOT NULL,
	old_value JSON,
	new_value JSON NOT NULL,
	occurred_at DATETIME NOT NULL,
	PRIMARY KEY (audit_id)
);
