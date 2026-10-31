"""Run/step journal, installed only while creating a brand-new T15 target."""
from sqlalchemy import MetaData,Table,Column,Integer,String,Text,ForeignKey,CheckConstraint
from models import User,Branch
metadata=MetaData()
SimulationRun=Table('simulation_runs',metadata,
    Column('run_id',Integer,primary_key=True),Column('manifest_hash',String(64),nullable=False,unique=True),
    Column('manifest_json',Text,nullable=False),Column('code_hash',String(64),nullable=False),
    Column('status',String(20),nullable=False),Column('completed_steps',Integer,nullable=False),
    Column('history_hash',String(64),nullable=False),
    CheckConstraint("run_id=1 AND completed_steps>=0 AND status IN ('running','complete')",name='ck_simulation_run'))
SimulationStep=Table('simulation_steps',metadata,
    Column('run_id',Integer,ForeignKey('simulation_runs.run_id'),primary_key=True),
    Column('step_number',Integer,primary_key=True),Column('step_key',String(100),nullable=False,unique=True),
    Column('actor_id',Integer,ForeignKey(User.__table__.c.user_id),nullable=False),
    Column('branch_id',Integer,ForeignKey(Branch.__table__.c.branch_id)),Column('at_utc',String(32),nullable=False),
    Column('action',String(80),nullable=False),Column('payload_json',Text,nullable=False),Column('result_json',Text,nullable=False),
    CheckConstraint('step_number>=0',name='ck_simulation_step'))


def triggers(dialect):
    result=[]
    for action in ['UPDATE','DELETE']:
        prefix=f'CREATE TRIGGER t15_step_{action.lower()} BEFORE {action} ON simulation_steps '
        result.append(prefix+("BEGIN SELECT RAISE(ABORT,'Immutable simulation step'); END;" if dialect=='sqlite' else
            "FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Immutable simulation step'; END;"))
    return result


def render(dialect):
    from sqlalchemy.schema import CreateTable
    sql=['-- T15 v001: synthetic NEW disposable target only. No existing-target migration authorized.']
    for table in metadata.sorted_tables:
        text=str(CreateTable(table).compile(dialect=dialect)).strip()
        if dialect.name=='mysql':text=text.rsplit(')',1)[0]+') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin'
        sql.append(text+';')
    if dialect.name=='mysql':sql+=['DELIMITER $$']+[t[:-1]+'$$' for t in triggers('mysql')]+['DELIMITER ;']
    else:sql+=triggers('sqlite')
    return '\n'.join(line.rstrip() for line in '\n\n'.join(sql).splitlines())+'\n'
