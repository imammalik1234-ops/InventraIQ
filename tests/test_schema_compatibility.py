"""SQLite-equivalent schema-copy validation, not a MySQL migration execution test."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from scripts.check_local_schema import inspect_local
from database import enforce_sqlite_foreign_keys


class SchemaCompatibilityTests(unittest.TestCase):
    def legacy_database(self, path, duplicate=False):
        connection=sqlite3.connect(path)
        enforce_sqlite_foreign_keys(connection)
        try:
            connection.executescript('''
                CREATE TABLE products (product_id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL,
                    category VARCHAR(80), supplier VARCHAR(100), unit_price NUMERIC(10,2), expiry_date DATE, reorder_level INT);
                CREATE TABLE inventory (inventory_id INTEGER PRIMARY KEY, product_id INTEGER, quantity INTEGER, last_updated DATETIME);
                CREATE TABLE sales (sale_id INTEGER PRIMARY KEY, product_id INTEGER, quantity INTEGER NOT NULL, sale_date DATE);
                INSERT INTO products VALUES (1,'Fixture item','Test','Test',2.50,'2030-01-01',5);
                INSERT INTO inventory VALUES (1,1,7,NULL);
                INSERT INTO sales VALUES (1,1,3,'2026-01-01');
            ''')
            if duplicate: connection.execute('INSERT INTO inventory VALUES (2,1,4,NULL)')
            connection.commit()
        finally: connection.close()

    def test_populated_legacy_copy_preserves_rows_and_reports_schema_gaps(self):
        with tempfile.TemporaryDirectory(prefix='inventraiq-schema-test-') as directory:
            path=Path(directory).resolve()/'legacy-test.db'
            self.assertEqual(path.resolve().parent,Path(directory).resolve())
            self.legacy_database(path)
            report=inspect_local(path)
            self.assertTrue(report['source_unchanged'])
            self.assertFalse(report['configured_database_opened'])
            self.assertIn('passed',report['disposable_latest_schema_copy'])
            self.assertEqual(report['missing_columns']['products'],['archived', 'archived_at', 'version'])
            self.assertIn('inventory_audit',report['missing_tables'])
            self.assertEqual(report['row_counts'],{'products':1,'inventory':1,'sales':1})
            self.assertIn('uq_inventory_product',report['missing_unique_constraints']['inventory'])
            self.assertIn('ck_inventory_nonnegative',report['missing_named_checks']['inventory'])
            self.assertIn('quantity',report['nullable_mismatches']['inventory'])

    def test_inconsistent_legacy_data_blocks_copy_without_altering_source(self):
        with tempfile.TemporaryDirectory(prefix='inventraiq-schema-test-') as directory:
            path=Path(directory).resolve()/'legacy-test.db'
            self.assertEqual(path.resolve().parent,Path(directory).resolve())
            self.legacy_database(path,duplicate=True)
            report=inspect_local(path)
            self.assertTrue(report['source_unchanged'])
            self.assertEqual(report['data_checks']['duplicate_inventory_products'],1)
            self.assertIn('blocked',report['disposable_latest_schema_copy'])
