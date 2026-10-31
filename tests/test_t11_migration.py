"""Exact additive transfer schema rehearsal, never an existing DB."""
import sqlite3
import unittest
from pathlib import Path
from contextlib import closing
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import sqlite,mysql
from database import db
import models
from scripts.render_t11_schema import TABLES,render


class TransferExecutionMigrationTests(unittest.TestCase):
    def test_exact_additive_sql_preserves_unknown_legacy_history(self):
        root=Path(__file__).resolve().parents[1]
        for name,dialect in [('sqlite',sqlite.dialect()),('mysql',mysql.dialect())]:
            self.assertEqual((root/'migrations'/f't11_v001_expand_{name}.sql').read_text(encoding='utf-8'),render(dialect))
        sql=render(sqlite.dialect());self.assertNotIn('ALTER TABLE',sql);self.assertNotIn('DROP TABLE',sql)
        with closing(sqlite3.connect(':memory:')) as connection:
            connection.execute('PRAGMA foreign_keys=ON')
            for table in db.metadata.sorted_tables:
                if table.name not in TABLES:connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
            connection.execute("INSERT INTO products(product_id,name,version,archived) VALUES(17,'Old price unknown',1,0)")
            connection.execute("INSERT INTO sales(sale_id,product_id,quantity,sale_date) VALUES(19,17,7,'2020-01-02')")
            connection.execute("INSERT INTO inventory(inventory_id,product_id,quantity) VALUES(31,17,9)")
            connection.execute("INSERT INTO inventory_audit(audit_id,actor_id,product_id,action,timestamp) VALUES(7,999,17,'legacy','2020-01-02')")
            before={name:connection.execute('SELECT * FROM '+name).fetchall() for name in ('products','sales','inventory','inventory_audit')}
            connection.executescript(sql)
            for name,rows in before.items():self.assertEqual(connection.execute('SELECT * FROM '+name).fetchall(),rows)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM stock_transfers').fetchone()[0],0)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 't11_%'").fetchone()[0],12)
        self.assertIn('ENGINE=InnoDB',render(mysql.dialect()));self.assertIn('SIGNAL SQLSTATE',render(mysql.dialect()))
