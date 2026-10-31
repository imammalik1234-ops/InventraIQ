"""Additive T14 schema. Importing this module executes no DDL or connections."""
from sqlalchemy import (MetaData, Table, Column, Integer, String, Text, ForeignKey,
                        ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index, Date)
from models import Branch, SKU

metadata = MetaData()
# Separate metadata avoids changing the frozen T04–T12 renderers/application models.
DemoTarget = Table('historical_demo_target', metadata,
    Column('singleton', Integer, primary_key=True, autoincrement=False), Column('token_hash', String(64), nullable=False),
    Column('purpose', String(40), nullable=False), CheckConstraint('singleton = 1'))
ImportDataset = Table('historical_datasets', metadata,
    Column('source_id', String(80), primary_key=True), Column('active_run_id', Integer, ForeignKey('import_runs.run_id')),
    Column('version', Integer, nullable=False))
ImportRun = Table('import_runs', metadata,
    Column('run_id', Integer, primary_key=True), Column('source_id', String(80), nullable=False),
    Column('source_revision', String(80), nullable=False), Column('manifest_hash', String(64), nullable=False, unique=True),
    Column('manifest_json', Text, nullable=False), Column('checksums_json', Text, nullable=False),
    Column('configuration_json', Text, nullable=False), Column('base_run_id', Integer, ForeignKey('import_runs.run_id')),
    Column('status', String(20), nullable=False), Column('checkpoint_line', Integer, nullable=False),
    Column('checkpoint_offset', Integer, nullable=False), Column('counters_json', Text, nullable=False),
    Column('reconciliation_json', Text), Column('created_at_utc', String(32), nullable=False),
    Column('updated_at_utc', String(32), nullable=False), Column('published_at_utc', String(32)),
    UniqueConstraint('source_id', 'source_revision', name='uq_import_source_revision'),
    UniqueConstraint('run_id','source_id','source_revision',name='uq_import_run_identity'),
    CheckConstraint("status IN ('staging','reconciled','published','superseded')", name='ck_import_status'),
    CheckConstraint('checkpoint_line >= 0 AND checkpoint_offset >= 0', name='ck_import_checkpoint'))
# The active pointer and observation/run/branch/SKU relationships are DB-enforced.
SalesObservation = Table('sales_observations', metadata,
    Column('observation_id', Integer, primary_key=True),
    Column('run_id', Integer, ForeignKey('import_runs.run_id'), nullable=False),
    Column('source_id', String(80), nullable=False), Column('source_revision', String(80), nullable=False),
    Column('source_store_id', String(40), nullable=False), Column('source_item_id', String(40), nullable=False),
    Column('branch_id', Integer, ForeignKey(Branch.__table__.c.branch_id), nullable=False),
    Column('sku_id', Integer, ForeignKey(SKU.__table__.c.sku_id), nullable=False),
    Column('business_date', Date, nullable=False), Column('units', Integer),
    Column('coverage', String(12), nullable=False), Column('measure_type', String(30), nullable=False),
    Column('provenance', String(30), nullable=False), Column('price_minor', Integer), Column('price_currency', String(3)),
    UniqueConstraint('source_id','source_revision','branch_id','sku_id','business_date','measure_type', name='uq_observation_fact'),
    ForeignKeyConstraint(['run_id','source_id','source_revision'],['import_runs.run_id','import_runs.source_id','import_runs.source_revision']),
    CheckConstraint("(coverage='observed' AND units IS NOT NULL AND units >= 0) OR (coverage='missing' AND units IS NULL)", name='ck_observation_coverage'),
    CheckConstraint("provenance='synthetic_fixture' AND measure_type='daily_units'", name='ck_observation_origin'),
    CheckConstraint("(price_minor IS NULL AND price_currency IS NULL) OR (price_minor IS NOT NULL AND price_currency IS NOT NULL AND price_minor >= 0 AND price_currency='USD')", name='ck_observation_price'))
ImportQuarantine = Table('import_quarantine', metadata,
    Column('run_id', Integer, ForeignKey('import_runs.run_id'), primary_key=True),
    Column('line_number', Integer, primary_key=True), Column('reason', String(40), nullable=False),
    Column('record_hash', String(64), nullable=False), Column('numeric_units', Integer, nullable=False))
Index('ix_observation_run_branch_date', SalesObservation.c.run_id, SalesObservation.c.branch_id, SalesObservation.c.business_date)
for table in metadata.tables.values():
    table.dialect_options['mysql']['engine'] = 'InnoDB'
    table.dialect_options['mysql']['charset'] = 'utf8mb4'
    table.dialect_options['mysql']['collate'] = 'utf8mb4_bin'

VIEWS = """CREATE VIEW published_sales_observations AS
SELECT o.* FROM sales_observations o JOIN historical_datasets d
ON d.source_id=o.source_id AND d.active_run_id=o.run_id
JOIN import_runs r ON r.run_id=o.run_id WHERE r.status='published';"""


def trigger_sql(dialect):
    statements=[]
    for table in ('sales_observations','import_quarantine'):
        for action in ('UPDATE','DELETE','INSERT'):
            condition="COALESCE((SELECT status FROM import_runs WHERE run_id=NEW.run_id),'absent') <> 'staging'" if action=='INSERT' else None
            message='Immutable historical fact; staging inserts only'
            name=f't14_{table}_{action.lower()}'
            if dialect=='sqlite':
                statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} '+
                    (f'WHEN {condition} ' if condition else '')+f"BEGIN SELECT RAISE(ABORT, '{message}'); END;")
            else:
                body=f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{message}';"
                if condition:body=f'IF {condition} THEN {body} END IF;'
                statements.append(f'CREATE TRIGGER {name} BEFORE {action} ON {table} FOR EACH ROW BEGIN {body} END;')
    return statements


def render(dialect, version='v001'):
    if version not in ('v001', 'v002'):
        raise ValueError('Unknown T14 artifact version')
    from sqlalchemy.schema import CreateTable, CreateIndex
    statements = [f'-- T14 {version} ADDITIVE ONLY. Synthetic disposable targets only; never auto-apply.']
    for table in metadata.sorted_tables:
        if table is DemoTarget:
            table = table.to_metadata(MetaData())
            table.c.singleton.autoincrement = version == 'v001'
        ddl=str(CreateTable(table).compile(dialect=dialect)).strip()
        if dialect.name=='mysql':
            # SQLAlchemy's MySQL option iteration can vary with process hash seed.
            ddl=ddl.rsplit(')',1)[0]+') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin'
        statements.append(ddl + ';')
        statements.extend(str(CreateIndex(i).compile(dialect=dialect))+';' for i in sorted(table.indexes,key=lambda i:i.name))
    statements.append(VIEWS)
    triggers=trigger_sql(dialect.name)
    statements.extend(['DELIMITER $$']+[s[:-1]+'$$' for s in triggers]+['DELIMITER ;'] if dialect.name=='mysql' else triggers)
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines()) + '\n'
