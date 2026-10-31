"""Additive schema verified only against NEW disposable SQLite, plus MySQL DDL."""
from pathlib import Path
import unittest
from sqlalchemy import create_engine
from database import db
from assistant.proposal_models import OperationProposal,ProposalAudit
from scripts.render_t27_schema import render


class ProposalMigrationTests(unittest.TestCase):
    def test_additive_sqlite_migration_preserves_existing_rows_and_foreign_keys(self):
        root=Path(__file__).resolve().parents[1]
        script=(root/'migrations/t27_v001_expand_sqlite.sql').read_text(encoding='utf-8')
        self.assertEqual(script,render('sqlite'))
        engine=create_engine('sqlite:///:memory:')
        try:
            # Simulate the previous schema on an entirely new database.
            tables=[t for t in db.metadata.sorted_tables if t.name not in ('operation_proposals','proposal_audit')]
            db.metadata.create_all(engine,tables=tables)
            with engine.connect() as c:
                c.exec_driver_sql("INSERT INTO products(product_id,name,version,archived) VALUES(88,'Preserved',1,0)")
                c.commit();before=c.exec_driver_sql('SELECT * FROM products').fetchall()
                c.connection.driver_connection.executescript(script)
                self.assertEqual(c.exec_driver_sql('SELECT * FROM products').fetchall(),before)
                self.assertEqual(c.exec_driver_sql('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(c.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 't27_%'").scalar(),5)
        finally:engine.dispose()

    def test_mysql_artifact_is_exact_additive_innodb_with_immutable_triggers(self):
        path=Path(__file__).resolve().parents[1]/'migrations/t27_v001_expand_mysql.sql'
        script=path.read_text(encoding='utf-8')
        self.assertEqual(script,render('mysql'))
        self.assertNotIn('DROP TABLE',script);self.assertNotIn('ALTER TABLE',script)
        self.assertIn('ENGINE=InnoDB',script)
        self.assertIn('uq_proposal_request',script)
        self.assertIn('t27_proposal_transition',script)
        self.assertIn('SIGNAL SQLSTATE',script)
