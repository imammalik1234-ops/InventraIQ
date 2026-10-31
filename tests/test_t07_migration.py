"""Exact additive DDL rehearsal on disposable SQLite; MySQL rendering only."""
import sqlite3
import unittest
from pathlib import Path
from sqlalchemy.dialects import sqlite, mysql
from sqlalchemy.schema import CreateTable
from database import db
import models
from scripts.render_t07_schema import TABLES, render


class LedgerMigrationTests(unittest.TestCase):
    def test_exact_artifacts_and_existing_row_preservation(self):
        root=Path(__file__).resolve().parents[1]
        for name,dialect in [('sqlite',sqlite.dialect()),('mysql',mysql.dialect())]:
            self.assertEqual((root/'migrations'/f't07_v001_expand_{name}.sql').read_text(encoding='utf-8'),render(dialect))
        sql=render(sqlite.dialect())
        self.assertNotIn('ALTER TABLE',sql);self.assertNotIn('DROP TABLE',sql)
        with sqlite3.connect(':memory:') as connection:
            connection.execute('PRAGMA foreign_keys=ON')
            for table in db.metadata.sorted_tables:
                if table.name not in TABLES:
                    connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
            connection.execute("INSERT INTO users(user_id,email,password_hash,role,enabled,session_version) VALUES(1,'old@test.invalid','retained','admin',1,1)")
            before=connection.execute('SELECT * FROM users').fetchall()
            connection.executescript(sql)
            self.assertEqual(connection.execute('SELECT * FROM users').fetchall(),before)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM inventory_movements').fetchone()[0],0)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND (name LIKE 'immutable_inventory_%' OR name LIKE 'sealed_inventory_%')").fetchone()[0],8)
        connection.close()
        mysql_sql=render(mysql.dialect())
        self.assertIn('COLLATE utf8mb4_bin',mysql_sql);self.assertIn('SIGNAL SQLSTATE',mysql_sql)
        self.assertIn('ENGINE=InnoDB',mysql_sql)
