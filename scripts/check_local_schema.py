"""Read-only workspace SQLite inspection; validate copying into disposable latest schema.

Does not import app.py, open configured MySQL, or execute migrations/step4_mysql.sql.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hashlib
import json
import sqlite3
from sqlalchemy import create_engine, event, UniqueConstraint, CheckConstraint
from database import db, enforce_sqlite_foreign_keys
import models  # Registers metadata only; no application engine is initialized.


def inspect_local(path):
    path = Path(path).resolve()
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    source = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
    enforce_sqlite_foreign_keys(source)
    source.execute('PRAGMA query_only=ON')
    snapshot = sqlite3.connect(':memory:')
    enforce_sqlite_foreign_keys(snapshot)
    try:
        source.backup(snapshot)
    finally:
        source.close()
    legacy_tables = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing_columns, missing_tables, legacy_counts = {}, [], {}
    missing_uniques, missing_checks, nullable_mismatches = {}, {}, {}
    for table in db.metadata.sorted_tables:
        if table.name not in legacy_tables:
            missing_tables.append(table.name)
            continue
        column_info = list(snapshot.execute(f'PRAGMA table_info("{table.name}")'))
        columns = {row[1] for row in column_info}
        actual_nullable = {row[1]: not row[3] and not row[5] for row in column_info}
        mismatch = [c.name for c in table.columns if c.name in columns and not c.nullable and actual_nullable[c.name]]
        if mismatch: nullable_mismatches[table.name] = mismatch
        unique_columns = set()
        for index in snapshot.execute(f'PRAGMA index_list("{table.name}")').fetchall():
            if index[2]:
                escaped_name = index[1].replace('"', '""')
                unique_columns.add(tuple(row[2] for row in snapshot.execute(f'PRAGMA index_info("{escaped_name}")')))
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and tuple(c.name for c in constraint.columns) not in unique_columns:
                missing_uniques.setdefault(table.name, []).append(constraint.name)
            if isinstance(constraint, CheckConstraint):
                ddl = snapshot.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table.name,)).fetchone()[0]
                if constraint.name not in ddl:
                    missing_checks.setdefault(table.name, []).append(constraint.name)
        missing = sorted(set(table.columns.keys())-columns)
        if missing: missing_columns[table.name] = missing
        legacy_counts[table.name] = snapshot.execute(f'SELECT COUNT(*) FROM "{table.name}"').fetchone()[0]
    report = dict(path=str(path), source_access='read-only', configured_database_opened=False,
                  quick_check=snapshot.execute('PRAGMA quick_check').fetchone()[0],
                  missing_tables=missing_tables, missing_columns=missing_columns, missing_unique_constraints=missing_uniques,
                  missing_named_checks=missing_checks, nullable_mismatches=nullable_mismatches, row_counts=legacy_counts)
    checks = {
        'invalid_inventory': 'SELECT COUNT(*) FROM inventory WHERE product_id IS NULL OR quantity IS NULL OR quantity < 0',
        'duplicate_inventory_products': 'SELECT COUNT(*) FROM (SELECT product_id FROM inventory GROUP BY product_id HAVING COUNT(*) > 1)',
        'orphan_inventory': 'SELECT COUNT(*) FROM inventory WHERE product_id NOT IN (SELECT product_id FROM products)',
        'nonpositive_sales': 'SELECT COUNT(*) FROM sales WHERE quantity <= 0',
        'missing_or_future_sale_dates': "SELECT COUNT(*) FROM sales WHERE sale_date IS NULL OR date(sale_date) > date('now','localtime')",
        'orphan_sales': 'SELECT COUNT(*) FROM sales WHERE product_id IS NULL OR product_id NOT IN (SELECT product_id FROM products)',
        'duplicate_complete_batches': 'SELECT COUNT(*) FROM (SELECT name,category,supplier,expiry_date FROM products WHERE category IS NOT NULL AND supplier IS NOT NULL AND expiry_date IS NOT NULL GROUP BY name,category,supplier,expiry_date HAVING COUNT(*) > 1)',
        'missing_or_nonpositive_prices': 'SELECT COUNT(*) FROM products WHERE unit_price IS NULL OR unit_price <= 0',
    }
    report['data_checks'] = {}
    for name, query in checks.items():
        try: report['data_checks'][name] = snapshot.execute(query).fetchone()[0]
        except sqlite3.Error: report['data_checks'][name] = 'unavailable: legacy table/column missing'
    # Build the SQLite equivalent of the latest models; never execute the MySQL script.
    engine = create_engine('sqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _): connection.execute('PRAGMA foreign_keys=ON')
    try:
        db.metadata.create_all(engine)
        with engine.begin() as destination:
            for table in db.metadata.sorted_tables:
                if table.name not in legacy_tables: continue
                legacy_columns = {row[1] for row in snapshot.execute(f'PRAGMA table_info("{table.name}")')}
                common = [c.name for c in table.columns if c.name in legacy_columns]
                quoted = ','.join('"'+name+'"' for name in common)
                rows = snapshot.execute(f'SELECT {quoted} FROM "{table.name}"').fetchall()
                if rows:
                    destination.exec_driver_sql(f'INSERT INTO "{table.name}" ({quoted}) VALUES ({",".join("?" for _ in common)})', rows)
            copied_counts = {table: destination.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar_one() for table in legacy_counts}
            if copied_counts != legacy_counts: raise ValueError('Row counts did not match')
            if destination.exec_driver_sql('PRAGMA foreign_key_check').fetchall(): raise ValueError('Foreign key violations')
        report['disposable_latest_schema_copy'] = 'passed; row counts preserved, foreign keys and new constraints enforced'
    except Exception as exc:
        # Report exception category, not row contents or user/password values.
        report['disposable_latest_schema_copy'] = 'blocked: '+type(exc).__name__
    finally:
        engine.dispose(); snapshot.close()
    report['source_unchanged'] = hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
    return report

if __name__ == '__main__':
    print(json.dumps(inspect_local(Path(__file__).resolve().parents[1]/'instance'/'database.db'), indent=2))
