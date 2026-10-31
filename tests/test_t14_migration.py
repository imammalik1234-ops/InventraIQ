"""Exact synthetic additive DDL rehearsed only against a new in-memory database."""
from pathlib import Path
import unittest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql,sqlite
from database import db
from ingestion.schema import metadata,render


class HistoricalMigrationTests(unittest.TestCase):
    def test_exact_sqlite_artifact_preserves_core_and_enforces_fks_checks(self):
        root=Path(__file__).resolve().parents[1]
        sql=(root/'migrations/t14_v001_expand_sqlite.sql').read_text(encoding='utf-8')
        self.assertEqual(sql,render(sqlite.dialect()))
        self.assertNotIn('DROP TABLE',sql);self.assertNotIn('ALTER TABLE',sql)
        engine=create_engine('sqlite:///:memory:')
        try:
            db.metadata.create_all(engine)
            with engine.connect() as conn:
                conn.exec_driver_sql("INSERT INTO products(product_id,name,version,archived) VALUES(77,'Untouched',1,0)")
                conn.commit()
                before=conn.exec_driver_sql('SELECT * FROM products').fetchall()
                conn.connection.driver_connection.executescript(sql)
                self.assertEqual(conn.exec_driver_sql('SELECT * FROM products').fetchall(),before)
                self.assertEqual(conn.exec_driver_sql('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 't14_%'").scalar(),6)
                self.assertEqual(conn.exec_driver_sql('SELECT COUNT(*) FROM published_sales_observations').scalar(),0)
                import sqlalchemy.exc
                with self.assertRaises(sqlalchemy.exc.IntegrityError):
                    conn.exec_driver_sql("INSERT INTO historical_datasets VALUES('source',999,1)")
                conn.rollback()
        finally:engine.dispose()

    def test_mysql_offline_artifact_matches_renderer_without_claiming_execution(self):
        root=Path(__file__).resolve().parents[1]
        text=render(mysql.dialect())
        self.assertEqual((root/'migrations/t14_v001_expand_mysql.sql').read_text(encoding='utf-8'),text)
        for part in ['ENGINE=InnoDB','FOREIGN KEY(branch_id)','FOREIGN KEY(sku_id)',
                     'uq_observation_fact','ck_observation_coverage','DELIMITER $$','SIGNAL SQLSTATE']:
            self.assertIn(part,text)
