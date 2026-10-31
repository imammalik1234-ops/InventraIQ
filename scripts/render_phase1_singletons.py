"""Render new fresh-install versions; never overwrite historical SQL artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HISTORICAL = {
    't06_v001_expand_mysql.sql': '9fd827972f915af368d66cd633a95f88fc6ca48f224c38732f07c7f826f2f44e',
    't06_v001_expand_sqlite.sql': 'afb1f940f9325005d2c26e1c44b2e6069e4a9bf08f5b49baf6c468b8564ba096',
    't14_v001_expand_mysql.sql': '9678fb91d832543e723f90f8ef90ed71fc6f00346f681506597894e941bf4f92',
    't14_v001_expand_sqlite.sql': '09335ff1f4f8cb1af711a8f5837772a56c1ab8cafe8ecf2b81db7a1463764097',
    't15_v001_expand_mysql.sql': '294bb6712b95d793ade532525df546d0c490107a5de56993c8efc1a210144c6e',
    't16_v001_indexes_mysql.sql': 'bebfb379c62c7926ce494217e7b7129f904d232bff43ac1eafd841f06bb1fda1',
    't20_v001_expand_mysql.sql': '15aa0d436096bc11ae16d51a87ee60786a487e8cdccf8ee6b3eb43bcbabf278c',
    't27_v001_expand_mysql.sql': 'd44aac43bb48dbf6f87a86690ce465370ccdcf50a54d00129365531966d205f8',
}


def artifacts():
    from sqlalchemy.dialects import mysql
    from scripts.render_t06_schema import render as t06
    from ingestion.schema import render as t14
    return {'t06_v002_expand_mysql.sql': t06(mysql.dialect(), version='v002'),
            't14_v002_expand_mysql.sql': t14(mysql.dialect(), version='v002'),
            't15_v002_expand_mysql.sql': downstream_sql('t15', 'simulation_runs', 'run_id'),
            't20_v002_expand_mysql.sql': downstream_sql('t20', 'forecast_demo_target', 'singleton')}


def downstream_sql(milestone, table, key):
    """Version a confirmed singleton DDL failure without changing runtime code."""
    name = milestone + '_v001_expand_mysql.sql'
    raw = (ROOT / 'migrations' / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != HISTORICAL[name]:
        raise RuntimeError('Historical artifact drift: ' + name)
    sql = raw.decode('utf-8')
    newline = '\r\n' if '\r\n' in sql else '\n'
    original = f'CREATE TABLE {table} ({newline}\t{key} INTEGER NOT NULL AUTO_INCREMENT,'
    replacement = f'CREATE TABLE {table} ({newline}\t{key} INTEGER NOT NULL,'
    if sql.count(original) != 1:
        raise RuntimeError('Unexpected singleton definition: ' + name)
    return sql.replace(original, replacement, 1).replace(milestone.upper() + ' v001', milestone.upper() + ' v002', 1)


def main(check=False):
    for name, digest in HISTORICAL.items():
        if hashlib.sha256((ROOT / 'migrations' / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Historical artifact drift: ' + name)
    generated = artifacts()
    manifest = dict(historical=HISTORICAL, fresh_mysql={name: hashlib.sha256(sql.encode()).hexdigest()
                                                     for name, sql in generated.items()})
    generated['phase1_singleton_compatibility.json'] = json.dumps(manifest, indent=2, sort_keys=True) + '\n'
    for name, sql in generated.items():
        path = ROOT / 'migrations' / name
        if check:
            if not path.is_file() or path.read_bytes() != sql.encode():
                raise RuntimeError('Generated artifact drift: ' + name)
        else:
            path.write_bytes(sql.encode())
    print('Historical hashes preserved; fresh singleton artifacts ' + ('verified' if check else 'rendered'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    main(parser.parse_args().check)
