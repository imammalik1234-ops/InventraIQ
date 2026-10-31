"""Exact additive transfer schema rehearsal, never an existing DB."""
import sqlite3
import unittest
from pathlib import Path
from contextlib import closing
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import sqlite,mysql
from database import db
import models
from scripts.render_t12_schema import TABLES,render


class PurchasingMigrationTests(unittest.TestCase):
    def test_exact_additive_sql_preserves_unknown_legacy_history(self):
        root=Path(__file__).resolve().parents[1]
        for name,dialect in [('sqlite',sqlite.dialect()),('mysql',mysql.dialect())]:
            self.assertEqual((root/'migrations'/f't12_v001_expand_{name}.sql').read_text(encoding='utf-8'),render(dialect))
        sql=render(sqlite.dialect());self.assertIn('ALTER TABLE stock_lots ADD COLUMN supplier_id',sql);self.assertNotIn('DROP TABLE',sql)
        with closing(sqlite3.connect(':memory:')) as connection:
            connection.execute('PRAGMA foreign_keys=ON')
            for table in db.metadata.sorted_tables:
                if table.name not in TABLES:
                    from scripts.render_t04_schema import legacy_lot_table
                    old=legacy_lot_table() if table.name=='stock_lots' else table
                    connection.execute(str(CreateTable(old).compile(dialect=sqlite.dialect())))
            connection.execute("INSERT INTO categories(category_id,code,display_name) VALUES(1,'DEMO','Demo')")
            connection.execute("INSERT INTO skus(sku_id,code,name,category_id,unit,active,version) VALUES(1,'SKU','SKU',1,'unit',1,1)")
            connection.execute("INSERT INTO stock_lots(lot_id,sku_id,lot_code,provenance,validation_state,version) VALUES(41,1,'LEGACY','{\"original_supplier\":\"Unmapped text\"}','review',1)")
            lot_before=connection.execute('SELECT * FROM stock_lots').fetchall()
            connection.execute("INSERT INTO products(product_id,name,version,archived) VALUES(17,'Old price unknown',1,0)")
            connection.execute("INSERT INTO sales(sale_id,product_id,quantity,sale_date) VALUES(19,17,7,'2020-01-02')")
            connection.execute("INSERT INTO inventory(inventory_id,product_id,quantity) VALUES(31,17,9)")
            connection.execute("INSERT INTO inventory_audit(audit_id,actor_id,product_id,action,timestamp) VALUES(7,999,17,'legacy','2020-01-02')")
            before={name:connection.execute('SELECT * FROM '+name).fetchall() for name in ('products','sales','inventory','inventory_audit')}
            connection.executescript(sql)
            for name,rows in before.items():self.assertEqual(connection.execute('SELECT * FROM '+name).fetchall(),rows)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM purchase_orders').fetchone()[0],0)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(connection.execute('SELECT supplier_id FROM stock_lots').fetchall(),[(None,)])
            self.assertEqual([row[:-1] for row in connection.execute('SELECT * FROM stock_lots').fetchall()],lot_before)
            with self.assertRaises(sqlite3.IntegrityError):connection.execute("INSERT INTO stock_lots(lot_id,sku_id,lot_code,provenance,validation_state,version,supplier_id) VALUES(1,1,'LOT','{}','review',1,999)")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 't12_%'").fetchone()[0],9)
        self.assertIn('ENGINE=InnoDB',render(mysql.dialect()));self.assertIn('SIGNAL SQLSTATE',render(mysql.dialect()))
