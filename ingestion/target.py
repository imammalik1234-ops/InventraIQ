"""Explicit disposable SQLite target capability; never consumes DATABASE_URL."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4

APP_ID=0x49515414
PURPOSE='inventraiq-t14-synthetic-only'
ACK='CREATE DISPOSABLE SYNTHETIC DATABASE'
WRITABLE={'import_runs','sales_observations','import_quarantine','historical_datasets'}


def initialize_target(parent,*,acknowledgment):
    if acknowledgment!=ACK:raise ValueError('Explicit disposable target acknowledgment required')
    parent=Path(parent)
    if not parent.is_dir() or parent.absolute()!=parent.resolve():raise ValueError('Existing non-symlink parent required')
    parent=parent.resolve()
    root=parent/f't14-demo-{uuid4().hex}'
    root.mkdir()  # Always a new directory; no overwrite/adoption path exists.
    path=root/'disposable.sqlite'
    token=secrets.token_hex(32)
    path.touch(exist_ok=False)
    from sqlalchemy import create_engine
    from database import db
    import models
    from ingestion.schema import metadata,VIEWS,trigger_sql
    from branch_foundation import DEMO_BRANCHES
    from ingestion.synthetic import items
    engine=create_engine('sqlite:///'+path.as_posix())
    try:
        db.metadata.create_all(engine)
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql('PRAGMA journal_mode=WAL')
            conn.exec_driver_sql(f'PRAGMA application_id={APP_ID}')
            conn.exec_driver_sql(VIEWS)
            for statement in trigger_sql('sqlite'):conn.exec_driver_sql(statement)
            conn.exec_driver_sql('INSERT INTO historical_demo_target VALUES (1,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(),PURPOSE))
            for i,(code,name,tz) in enumerate(DEMO_BRANCHES,1):
                conn.exec_driver_sql('INSERT INTO branches(branch_id,code,display_name,timezone,active) VALUES (?,?,?,?,1)',(i,code,name,tz))
            conn.exec_driver_sql("INSERT INTO categories(category_id,code,display_name) VALUES (1,'SYNTHETIC','Synthetic fixture only')")
            for i,item in enumerate(items('medium'),1):
                conn.exec_driver_sql("INSERT INTO skus(sku_id,code,name,category_id,unit,active,version) VALUES (?,?,?,1,'unit',1,1)",
                    (i,item.replace('ITEM','SKU'),'Synthetic '+item))
    finally:engine.dispose()
    descriptor=dict(format=PURPOSE,database='disposable.sqlite',token=token)
    with (root/'target.json').open('x',encoding='utf-8') as handle:json.dump(descriptor,handle)
    (root/'fixtures').mkdir()
    return root


def checked_root(root):
    root=Path(root)
    if root.absolute()!=root.resolve() or not root.is_dir() or not root.name.startswith('t14-demo-'):
        raise ValueError('Unverified disposable target')
    root=root.resolve()
    descriptor=root/'target.json'
    path=root/'disposable.sqlite'
    if descriptor.is_symlink() or path.is_symlink() or not path.is_file() or not descriptor.is_file() or descriptor.stat().st_size>1024:
        raise ValueError('Unverified target files')
    info=json.loads(descriptor.read_text(encoding='utf-8'))
    if set(info)!={'format','database','token'} or info['format']!=PURPOSE or info['database']!=path.name:
        raise ValueError('Wrong target purpose')
    if not isinstance(info['token'],str) or len(info['token'])!=64:raise ValueError('Invalid target token')
    return root,path,info


@contextmanager
def connect(root):
    root,path,info=checked_root(root)
    # Verify read-only first: even an unverified DB's hot journal must not be recovered
    # by a writable connection. No configured application target is consulted.
    probe=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10,isolation_level=None)
    try:
        if probe.execute('PRAGMA application_id').fetchone()[0]!=APP_ID:raise ValueError('Wrong database application identity')
        marker=probe.execute('SELECT token_hash,purpose FROM historical_demo_target WHERE singleton=1').fetchone()
        if not marker or marker[1]!=PURPOSE or marker[0]!=hashlib.sha256(info['token'].encode()).hexdigest():
            raise ValueError('Disposable database marker mismatch')
    finally:probe.close()
    # mode=rw never creates a missing file; only the attested disposable target opens.
    conn=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=10,isolation_level=None)
    conn.row_factory=sqlite3.Row
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        if conn.execute('PRAGMA foreign_keys').fetchone()[0]!=1:raise ValueError('Foreign keys required')
        def authorize(action,first,second,database,trigger):
            if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
                return sqlite3.SQLITE_OK if first in WRITABLE else sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH,sqlite3.SQLITE_PRAGMA,
                          sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_DROP_TABLE,sqlite3.SQLITE_ALTER_TABLE,
                          sqlite3.SQLITE_CREATE_TRIGGER,sqlite3.SQLITE_DROP_TRIGGER,sqlite3.SQLITE_CREATE_VIEW,
                          sqlite3.SQLITE_DROP_VIEW):return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        conn.set_authorizer(authorize)
        yield conn
    finally:
        if conn.in_transaction:conn.rollback()
        conn.close()
