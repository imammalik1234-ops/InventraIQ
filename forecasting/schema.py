"""T20 additive schema; importing this module never connects or executes DDL."""
from sqlalchemy import (MetaData,Table,Column,Integer,String,Text,Date,ForeignKey,
                        CheckConstraint,UniqueConstraint,Index)
from models import User,Branch,SKU
from ingestion.schema import ImportRun

metadata=MetaData()
ForecastTarget=Table('forecast_demo_target',metadata,
    Column('singleton',Integer,primary_key=True),Column('purpose',String(50),nullable=False),
    CheckConstraint('singleton=1'))
ForecastRun=Table('forecast_runs',metadata,
    Column('run_id',Integer,primary_key=True),Column('request_key',String(100),nullable=False,unique=True),
    Column('payload_hash',String(64),nullable=False),Column('actor_id',Integer,ForeignKey(User.__table__.c.user_id),nullable=False),
    Column('source_run_id',Integer,ForeignKey(ImportRun.c.run_id),nullable=False),
    Column('source_id',String(80),nullable=False),Column('source_revision',String(80),nullable=False),
    Column('manifest_hash',String(64),nullable=False),Column('as_of',Date,nullable=False),
    Column('horizon',Integer,nullable=False),Column('model_id',String(60),nullable=False),
    Column('model_version',String(30),nullable=False),Column('qualification',String(40),nullable=False),
    Column('artifact_json',Text,nullable=False),Column('artifact_hash',String(64),nullable=False),
    Column('configuration_json',Text,nullable=False),Column('base_version',Integer,nullable=False),
    Column('status',String(16),nullable=False),Column('expected_count',Integer,nullable=False),
    Column('eligible_count',Integer),Column('batch_hash',String(64)),
    Column('generated_at_utc',String(32),nullable=False),Column('published_at_utc',String(32)),
    Column('error_type',String(50)),
    CheckConstraint("status IN ('running','published','failed')",name='ck_forecast_run_status'),
    CheckConstraint("horizon=7 AND expected_count BETWEEN 1 AND 360 AND base_version>=0",name='ck_forecast_run_bounds'),
    CheckConstraint("qualification='provisional_synthetic'",name='ck_forecast_qualification'))
ForecastPublication=Table('forecast_publications',metadata,
    Column('source_id',String(80),primary_key=True),
    Column('active_run_id',Integer,ForeignKey(ForecastRun.c.run_id)),Column('version',Integer,nullable=False),
    CheckConstraint('version>=0'))
ForecastSnapshot=Table('forecast_snapshots',metadata,
    Column('snapshot_id',Integer,primary_key=True),Column('run_id',Integer,ForeignKey(ForecastRun.c.run_id),nullable=False),
    Column('branch_id',Integer,ForeignKey(Branch.__table__.c.branch_id),nullable=False),
    Column('sku_id',Integer,ForeignKey(SKU.__table__.c.sku_id),nullable=False),
    Column('payload_json',Text,nullable=False),Column('payload_hash',String(64),nullable=False),
    UniqueConstraint('run_id','branch_id','sku_id',name='uq_forecast_series'))
ForecastEvent=Table('forecast_events',metadata,
    Column('event_id',Integer,primary_key=True),Column('run_id',Integer,ForeignKey(ForecastRun.c.run_id),nullable=False),
    Column('actor_id',Integer,ForeignKey(User.__table__.c.user_id),nullable=False),
    Column('event',String(40),nullable=False),Column('at_utc',String(32),nullable=False),
    Column('reason',String(240),nullable=False))
ForecastMonitor=Table('forecast_monitors',metadata,
    Column('monitor_id',Integer,primary_key=True),Column('run_id',Integer,ForeignKey(ForecastRun.c.run_id),nullable=False),
    Column('observation_run_id',Integer,ForeignKey(ImportRun.c.run_id),nullable=False),
    Column('as_of',Date,nullable=False),Column('evaluated_at_utc',String(32),nullable=False),
    Column('status',String(40),nullable=False),
    UniqueConstraint('run_id','observation_run_id','as_of',name='uq_forecast_monitor'))
ForecastMetric=Table('forecast_metrics',metadata,
    Column('monitor_id',Integer,ForeignKey(ForecastMonitor.c.monitor_id),primary_key=True),
    Column('branch_id',Integer,ForeignKey(Branch.__table__.c.branch_id),primary_key=True),
    Column('segment',String(40),primary_key=True),Column('metrics_json',Text,nullable=False))
Index('ix_forecast_run_created',ForecastRun.c.generated_at_utc,ForecastRun.c.run_id)
Index('ix_forecast_snapshot_scope',ForecastSnapshot.c.run_id,ForecastSnapshot.c.branch_id,ForecastSnapshot.c.sku_id)
for table in metadata.tables.values():
    table.dialect_options['mysql'].update(engine='InnoDB',charset='utf8mb4',collate='utf8mb4_bin')


def triggers(dialect):
    result=[]
    for table in ('forecast_snapshots','forecast_events','forecast_metrics','forecast_monitors'):
        for action in ('UPDATE','DELETE'):
            prefix=f'CREATE TRIGGER t20_{table}_{action.lower()} BEFORE {action} ON {table} '
            result.append(prefix+("BEGIN SELECT RAISE(ABORT,'Immutable forecast fact'); END;" if dialect=='sqlite' else
                "FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable forecast fact'; END;"))
    # Snapshots may only be inserted into an unpublished run, never retrofitted.
    condition="COALESCE((SELECT status FROM forecast_runs WHERE run_id=NEW.run_id),'missing') <> 'running'"
    prefix='CREATE TRIGGER t20_snapshot_insert BEFORE INSERT ON forecast_snapshots '
    result.append(prefix+(f"WHEN {condition} BEGIN SELECT RAISE(ABORT,'Run is not staging'); END;" if dialect=='sqlite' else
        f"FOR EACH ROW BEGIN IF {condition} THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Run is not staging'; END IF; END;"))
    for action in ('UPDATE','DELETE'):
        prefix=f'CREATE TRIGGER t20_run_{action.lower()} BEFORE {action} ON forecast_runs '
        condition="OLD.status='published'"
        result.append(prefix+(f"WHEN {condition} BEGIN SELECT RAISE(ABORT,'Immutable published run'); END;" if dialect=='sqlite' else
            f"FOR EACH ROW BEGIN IF {condition} THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable published run'; END IF; END;"))
    return result


def render(dialect):
    from sqlalchemy.schema import CreateTable,CreateIndex
    statements=['-- T20 v001 ADDITIVE ONLY; never auto-apply to existing databases.']
    for table in metadata.sorted_tables:
        ddl=str(CreateTable(table).compile(dialect=dialect)).strip()
        if dialect.name=='mysql':ddl=ddl.rsplit(')',1)[0]+') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin'
        statements.append(ddl+';')
        statements.extend(str(CreateIndex(i).compile(dialect=dialect))+';' for i in sorted(table.indexes,key=lambda i:i.name))
    values=triggers(dialect.name)
    statements+=['DELIMITER $$']+[s[:-1]+'$$' for s in values]+['DELIMITER ;'] if dialect.name=='mysql' else values
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines())+'\n'
