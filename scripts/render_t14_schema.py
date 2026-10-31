"""Offline only: additive DDL artifacts; no engine or configured database access."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy.dialects import sqlite,mysql
from ingestion.schema import render

if __name__=='__main__':
    for name,dialect in [('sqlite',sqlite.dialect()),('mysql',mysql.dialect())]:
        (Path(__file__).resolve().parents[1]/'migrations'/f't14_v001_expand_{name}.sql').write_text(render(dialect),encoding='utf-8')
