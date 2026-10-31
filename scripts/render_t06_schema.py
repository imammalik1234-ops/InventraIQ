"""Offline T06 expand-only SQL rendering. Never connects or executes DDL."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy.schema import CreateTable
from sqlalchemy import MetaData
from sqlalchemy.dialects import mysql, sqlite
from database import db
import models
TABLES={'sku_identities','catalog_audit','company_expiry_settings','stock_receipts','lot_identities'}


def render(dialect, version='v001'):
    if version not in ('v001', 'v002'):
        raise ValueError('Unknown T06 artifact version')
    statements=['-- T06 v001 EXPAND ONLY: review on a disposable restore before any approved use.',
                '-- No legacy rows, SKU definitions, policies, settings or stock are backfilled automatically.']
    statements[0] = statements[0].replace('v001', version)
    for table in db.metadata.sorted_tables:
        if table.name not in TABLES:
            continue
        if table.name == 'company_expiry_settings':
            table = table.to_metadata(MetaData())
            # Preserve historical v001 bytes; only v002 is a valid fresh MySQL
            # singleton definition. Never mutate live application metadata.
            table.c.settings_id.autoincrement = version == 'v001'
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip()+';')
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines())+'\n'


if __name__=='__main__':
    for name,dialect in [('mysql',mysql.dialect()),('sqlite',sqlite.dialect())]:
        (Path(__file__).resolve().parents[1]/'migrations'/('t06_v001_expand_'+name+'.sql')).write_text(render(dialect),encoding='utf-8')
