"""Render reviewable additive DDL; never connect to or modify a database."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import sqlite,mysql
from assistant.proposal_models import OperationProposal,ProposalAudit,trigger_sql
import models  # Resolve existing-table foreign keys in metadata only.


def render(dialect):
    compiler={'sqlite':sqlite.dialect,'mysql':mysql.dialect}[dialect]()
    parts=['-- T27 additive schema. Review and obtain approval before applying to an existing DB.']
    for model in (OperationProposal,ProposalAudit):parts.append(str(CreateTable(model.__table__).compile(dialect=compiler)).strip()+';')
    if dialect=='mysql':
        parts.append('DELIMITER $$')
        parts.extend(statement[:-1]+'$$' for statement in trigger_sql(dialect))
        parts.append('DELIMITER ;')
    else:parts.extend(trigger_sql(dialect))
    return '\n'.join(line.rstrip() for line in '\n\n'.join(parts).splitlines())+'\n'


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]/'migrations'
    for name in ('sqlite','mysql'):
        path=root/f't27_v001_expand_{name}.sql'
        path.write_text(render(name),encoding='utf-8')
        print(path.name)
