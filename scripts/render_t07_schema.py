"""Offline expand-only T07 SQL. Never connects or executes migrations."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import mysql, sqlite
from database import db
from models import ledger_trigger_sql

TABLES = {'inventory_operations','inventory_movements','inventory_documents','inventory_reservations'}


def render(dialect):
    statements = ['-- T07 v001 EXPAND ONLY. Manual approval and disposable restore validation required.',
                  '-- No existing rows are changed. Opening reconciliation is a separate reviewed transaction.']
    for table in db.metadata.sorted_tables:
        if table.name in TABLES:
            statements.append(str(CreateTable(table).compile(dialect=dialect)).strip()+';')
            statements.extend(str(CreateIndex(i).compile(dialect=dialect))+';' for i in sorted(table.indexes,key=lambda i:i.name))
    triggers = ledger_trigger_sql(dialect.name)
    if dialect.name == 'mysql':
        statements += ['DELIMITER $$'] + [s[:-1]+'$$' for s in triggers] + ['DELIMITER ;']
    else: statements += triggers
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines())+'\n'


if __name__ == '__main__':
    for name, dialect in [('mysql',mysql.dialect()),('sqlite',sqlite.dialect())]:
        (Path(__file__).resolve().parents[1]/'migrations'/f't07_v001_expand_{name}.sql').write_text(render(dialect),encoding='utf-8')
