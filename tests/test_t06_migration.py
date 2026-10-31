"""T06 DDL rehearsal on in-memory fixtures only; no configured database."""
import sqlite3
import unittest
from pathlib import Path
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import mysql, sqlite
from database import db
import models
from scripts.render_t06_schema import render, TABLES


class CatalogMigrationTests(unittest.TestCase):
    def test_versioned_artifacts_match_metadata_and_preserve_legacy_fixture(self):
        for name,dialect in [('mysql',mysql.dialect()),('sqlite',sqlite.dialect())]:
            sql=Path('migrations/t06_v001_expand_'+name+'.sql').read_text(encoding='utf-8')
            self.assertEqual(sql,render(dialect));self.assertNotIn('DROP ',sql);self.assertNotIn('ALTER ',sql)
            if name=='mysql':self.assertIn('COLLATE utf8mb4_bin',sql)
        connection=sqlite3.connect(':memory:')
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            for table in db.metadata.sorted_tables:
                if table.name not in TABLES:connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
            connection.execute("INSERT INTO products (product_id,name,unit_price,expiry_date) VALUES (1,'Preserved legacy',NULL,NULL)")
            connection.execute('INSERT INTO inventory (product_id,quantity) VALUES (1,7)')
            connection.execute("INSERT INTO sales (product_id,quantity,sale_date) VALUES (1,3,'2026-01-01')")
            tables=('products','inventory','sales')
            before={table:connection.execute('SELECT * FROM '+table).fetchall() for table in tables}
            connection.executescript(render(sqlite.dialect()))
            self.assertEqual({table:connection.execute('SELECT * FROM '+table).fetchall() for table in tables},before)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM stock_receipts').fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM company_expiry_settings').fetchone()[0],0)
        finally:connection.close()
