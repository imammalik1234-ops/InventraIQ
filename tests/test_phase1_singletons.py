"""Singleton constraints and immutable artifact/version checks; SQLite only."""
import hashlib
import unittest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.schema import CreateTable
from sqlalchemy.exc import IntegrityError
from database import db
import models
from ingestion.schema import DemoTarget, render as historical_render
from scripts.render_t06_schema import render as catalog_render
from scripts.render_phase1_singletons import HISTORICAL, main as check_artifacts, downstream_sql
from scripts.verify_phase1_singletons import verify_registry, verify_bytes

ROOT = Path(__file__).resolve().parents[1]


class SingletonCompatibilityTests(unittest.TestCase):
    def test_historical_hashes_and_new_artifacts_are_pinned(self):
        check_artifacts(check=True)
        for name, digest in HISTORICAL.items():
            self.assertEqual(hashlib.sha256((ROOT / 'migrations' / name).read_bytes()).hexdigest(), digest)

    def test_fresh_mysql_ddl_retains_checks_without_auto_increment(self):
        for text, key in ((catalog_render(mysql.dialect(), version='v002'), 'settings_id'),
                          (historical_render(mysql.dialect(), version='v002'), 'singleton')):
            self.assertIn(key + ' INTEGER NOT NULL,', text)
            self.assertNotIn(key + ' INTEGER NOT NULL AUTO_INCREMENT', text)
            self.assertIn(key + ' = 1', text)
        self.assertFalse(DemoTarget.c.singleton.autoincrement)
        self.assertFalse(models.CompanyExpirySettings.__table__.c.settings_id.autoincrement)

    def test_historical_render_does_not_mutate_live_metadata(self):
        self.assertIn('settings_id INTEGER NOT NULL AUTO_INCREMENT', catalog_render(mysql.dialect()))
        self.assertIn('singleton INTEGER NOT NULL AUTO_INCREMENT', historical_render(mysql.dialect()))
        self.assertFalse(DemoTarget.c.singleton.autoincrement)
        self.assertFalse(models.CompanyExpirySettings.__table__.c.settings_id.autoincrement)

    def test_downstream_fresh_sql_changes_only_singleton_and_version(self):
        for milestone, table, key in (('t15', 'simulation_runs', 'run_id'), ('t20', 'forecast_demo_target', 'singleton')):
            original = (ROOT / 'migrations' / (milestone + '_v001_expand_mysql.sql')).read_bytes().decode()
            corrected = downstream_sql(milestone, table, key)
            newline = '\r\n' if '\r\n' in original else '\n'
            restored = corrected.replace(milestone.upper() + ' v002', milestone.upper() + ' v001', 1).replace(
                f'CREATE TABLE {table} ({newline}\t{key} INTEGER NOT NULL,',
                f'CREATE TABLE {table} ({newline}\t{key} INTEGER NOT NULL AUTO_INCREMENT,', 1)
            self.assertEqual(restored, original)

    def test_sqlite_singletons_reject_invalid_and_duplicate_keys(self):
        from simulation.schema import SimulationRun
        from forecasting.schema import ForecastTarget
        for table, key, other, values in ((models.CompanyExpirySettings.__table__, 'settings_id', 'label_timezone,version', "'UTC',1"),
                                         (DemoTarget, 'singleton', 'token_hash,purpose', "'synthetic-only','phase1-test'"),
                                         (SimulationRun, 'run_id', 'manifest_hash,manifest_json,code_hash,status,completed_steps,history_hash',
                                          "'phase1-synthetic','{}','phase1-code','running',0,'phase1-history'"),
                                         (ForecastTarget, 'singleton', 'purpose', "'phase1-test'")):
            engine = create_engine('sqlite:///:memory:')
            try:
                table.create(engine)
                for invalid in (-1, 0, 2):
                    with self.assertRaises(IntegrityError), engine.begin() as c:
                        c.exec_driver_sql(f'INSERT INTO {table.name}({key},{other}) VALUES(?,{values})', (invalid,))
                with engine.begin() as c:
                    c.exec_driver_sql(f'INSERT INTO {table.name}({key},{other}) VALUES(1,{values})')
                with self.assertRaises(IntegrityError), engine.begin() as c:
                    c.exec_driver_sql(f'INSERT INTO {table.name}({key},{other}) VALUES(1,{values})')
                with engine.connect() as c:
                    self.assertEqual(c.exec_driver_sql(f'SELECT {key} FROM {table.name}').all(), [(1,)])
            finally:
                engine.dispose()

    def test_registry_divergence_fails_without_updating_history(self):
        engine = create_engine('sqlite:///:memory:')
        try:
            models.SchemaMigration.__table__.create(engine)
            with engine.begin() as c:
                c.exec_driver_sql("INSERT INTO schema_migrations VALUES('t06_v001_expand','unchanged','2026-10-29 00:00:00')")
            with engine.connect() as c:
                before = c.exec_driver_sql('SELECT * FROM schema_migrations').all()
                for expected in ({}, {'t06_v001_expand': 'different'}, {'t06_v002_expand': 'unchanged'}):
                    with self.assertRaises(RuntimeError):
                        verify_registry(c, expected)
                self.assertEqual(c.exec_driver_sql('SELECT * FROM schema_migrations').all(), before)
                verify_registry(c, {'t06_v001_expand': 'unchanged'})
        finally:
            engine.dispose()

    def test_artifact_divergence_is_rejected(self):
        path = ROOT / 'migrations/t14_v001_expand_mysql.sql'
        with self.assertRaises(RuntimeError):
            verify_bytes(path, '0' * 64)
