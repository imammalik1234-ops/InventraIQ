"""Exact additive T20 artifacts, exercised only in new in-memory SQLite."""
from pathlib import Path
import unittest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql,sqlite
from database import db
from ingestion.schema import metadata as history
from forecasting.schema import render


class ForecastMigrationTests(unittest.TestCase):
    def test_sqlite_artifact_exact_additive_and_foreign_keys(self):
        text=(Path(__file__).resolve().parents[1]/'migrations/t20_v001_expand_sqlite.sql').read_text(encoding='utf-8')
        self.assertEqual(text,render(sqlite.dialect()));self.assertNotIn('DROP TABLE',text);self.assertNotIn('ALTER TABLE',text)
        engine=create_engine('sqlite:///:memory:')
        try:
            db.metadata.create_all(engine);history.create_all(engine)
            with engine.connect() as conn:
                conn.exec_driver_sql("INSERT INTO products(product_id,name,version,archived) VALUES(88,'Preserved',1,0)");conn.commit()
                before=conn.exec_driver_sql('SELECT * FROM products').fetchall()
                conn.connection.driver_connection.executescript(text)
                self.assertEqual(conn.exec_driver_sql('SELECT * FROM products').fetchall(),before)
                self.assertEqual(conn.exec_driver_sql('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 't20_%'").scalar(),11)
                from sqlalchemy.exc import IntegrityError
                with self.assertRaises(IntegrityError):conn.exec_driver_sql("INSERT INTO forecast_publications VALUES('bad',999,0)")
                conn.rollback()
        finally:engine.dispose()

    def test_mysql_rendering_only_not_live_verification(self):
        text=(Path(__file__).resolve().parents[1]/'migrations/t20_v001_expand_mysql.sql').read_text(encoding='utf-8')
        self.assertEqual(text,render(mysql.dialect()))
        for part in ('ENGINE=InnoDB','uq_forecast_series','FOREIGN KEY(branch_id)','FOREIGN KEY(sku_id)',
            'FOREIGN KEY(source_run_id)','DELIMITER $$','SIGNAL SQLSTATE','ck_forecast_qualification'):
            self.assertIn(part,text)
