"""Offline additive artifact generation; no database connections."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy.dialects import sqlite,mysql
from forecasting.schema import render

if __name__=='__main__':
    for name,dialect in [('sqlite',sqlite.dialect()),('mysql',mysql.dialect())]:
        (Path(__file__).resolve().parents[1]/'migrations'/f't20_v001_expand_{name}.sql').write_text(render(dialect),encoding='utf-8')
