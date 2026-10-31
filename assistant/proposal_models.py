"""T27 durable execution gate. Imported into metadata; never auto-migrates."""
from sqlalchemy import DDL, event
from database import db


class OperationProposal(db.Model):
    __tablename__='operation_proposals'
    __table_args__=(
        db.UniqueConstraint('tenant','actor_id','request_key',name='uq_proposal_request'),
        db.CheckConstraint("state IN ('pending','executed','cancelled')",name='ck_proposal_state'),
        db.CheckConstraint("action IN ('propose_transfer','propose_adjustment')",name='ck_proposal_action'),
        {'mysql_engine':'InnoDB'})
    proposal_id=db.Column(db.String(32),primary_key=True)
    tenant=db.Column(db.String(128),nullable=False)
    actor_id=db.Column(db.Integer,nullable=False)
    branch_id=db.Column(db.Integer,db.ForeignKey('branches.branch_id'),nullable=False)
    action=db.Column(db.String(30),nullable=False)
    request_key=db.Column(db.String(80),nullable=False)
    request_hash=db.Column(db.String(64),nullable=False)
    envelope=db.Column(db.Text,nullable=False)
    payload_hash=db.Column(db.String(64),nullable=False)
    seal=db.Column(db.String(64),nullable=False)
    created_at=db.Column(db.DateTime,nullable=False)
    expires_at=db.Column(db.DateTime,nullable=False)
    state=db.Column(db.String(16),nullable=False)
    operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'),unique=True)
    result=db.Column(db.JSON)


class ProposalAudit(db.Model):
    __tablename__='proposal_audit'
    __table_args__=(db.UniqueConstraint('proposal_id','event',name='uq_proposal_audit_event'),
        db.CheckConstraint("event IN ('created','confirmed','cancelled')",name='ck_proposal_event'),
        {'mysql_engine':'InnoDB'})
    audit_id=db.Column(db.Integer,primary_key=True)
    proposal_id=db.Column(db.String(32),db.ForeignKey('operation_proposals.proposal_id'),nullable=False)
    actor_id=db.Column(db.Integer,nullable=False)
    event=db.Column(db.String(16),nullable=False)
    occurred_at=db.Column(db.DateTime,nullable=False)
    payload_hash=db.Column(db.String(64),nullable=False)
    operation_id=db.Column(db.Integer,db.ForeignKey('inventory_operations.operation_id'))


def trigger_sql(dialect):
    immutable=('proposal_id','tenant','actor_id','branch_id','action','request_key',
        'request_hash','envelope','payload_hash','seal','created_at','expires_at')
    conditions=[('t27_proposal_immutable','operation_proposals','UPDATE',
        ' OR '.join(('NEW.'+n+' IS NOT OLD.'+n) if dialect=='sqlite' else
                    '(NOT (NEW.'+n+' <=> OLD.'+n+'))' for n in immutable)),
        ('t27_proposal_transition','operation_proposals','UPDATE',
         "OLD.state <> 'pending' OR NEW.state NOT IN ('executed','cancelled') OR "
         "(NEW.state = 'executed' AND (NEW.operation_id IS NULL OR NEW.result IS NULL)) OR "
         "(NEW.state = 'cancelled' AND (NEW.operation_id IS NOT NULL OR NEW.result IS NOT NULL))"),
        ('t27_proposal_delete','operation_proposals','DELETE',None),
        ('t27_audit_update','proposal_audit','UPDATE',None),
        ('t27_audit_delete','proposal_audit','DELETE',None)]
    result=[]
    for name,table,action,condition in conditions:
        if dialect=='sqlite':
            result.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} '+
                (f'WHEN {condition} ' if condition else '')+
                "BEGIN SELECT RAISE(ABORT, 'Immutable proposal fact'); END;")
        else:
            body="SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable proposal fact';"
            if condition:body=f'IF {condition} THEN {body} END IF;'
            result.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} FOR EACH ROW BEGIN {body} END;')
    return result


for _dialect in ('sqlite','mysql'):
    for _statement in trigger_sql(_dialect):
        _table=_statement.split(' ON ')[1].split()[0]
        event.listen(db.metadata.tables[_table],'after_create',DDL(_statement).execute_if(dialect=_dialect))
