"""Offline additive T11 DDL renderer; no connections or automatic migrations."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects import mysql,sqlite
from database import db
from models import transit_trigger_sql

TABLES={'transfer_executions','transfer_transit','transfer_transit_facts','transfer_discrepancies','transfer_resolutions','transfer_return_links'}


def render(dialect):
    statements=['-- T11 v001 EXPAND ONLY. Owner approval and disposable restore rehearsal required.',
                '-- Existing balances and history are untouched. No automatic transfer creation or reservations.']
    for table in db.metadata.sorted_tables:
        if table.name in TABLES:
            statements.append(str(CreateTable(table).compile(dialect=dialect)).strip()+';')
            statements.extend(str(CreateIndex(i).compile(dialect=dialect))+';' for i in sorted(table.indexes,key=lambda i:i.name))
    triggers=transit_trigger_sql(dialect.name)
    if dialect.name=='mysql':statements+=['DELIMITER $$']+[s[:-1]+'$$' for s in triggers]+['DELIMITER ;']
    else:statements+=triggers
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines())+'\n'


if __name__=='__main__':
    for name,dialect in [('sqlite',sqlite.dialect()),('mysql',mysql.dialect())]:
        (Path(__file__).resolve().parents[1]/'migrations'/f't11_v001_expand_{name}.sql').write_text(render(dialect),encoding='utf-8')
