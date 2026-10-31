"""T14 synthetic-only tests. Every database is newly created in TemporaryDirectory."""
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ingestion import pipeline as p, synthetic as s
from ingestion.target import initialize_target,connect,ACK


class HistoricalIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=initialize_target(self.temp.name,acknowledgment=ACK)

    def fixture(self,**config):
        path=p.generate_fixture(self.root,**config)
        return p.load_manifest(self.root,path)

    def raw(self):
        conn=sqlite3.connect(self.root/'disposable.sqlite')
        self.addCleanup(conn.close);return conn

    def count_runs(self):
        with connect(self.root) as conn:return conn.execute('SELECT COUNT(*) FROM import_runs').fetchone()[0]

    def test_correct_small_import_dates_units_coverage_and_optional_prices(self):
        m=self.fixture();result=p.ingest(self.root,m,chunk_size=31)
        self.assertEqual(result['status'],'published');r=result['reconciliation'];self.assertTrue(r['ok'])
        self.assertEqual(r['counters'],dict(input_rows=252,accepted_rows=252,rejected_rows=0,observed_rows=242,
            missing_rows=10,zero_rows=36,units=732,input_numeric_units=732,rejected_numeric_units=0,price_unknown_rows=7))
        self.assertEqual(p.published_summary(self.root)['units'],732)
        rows=p.observations(self.root,limit=1000)
        self.assertEqual({r['provenance'] for r in rows},{'synthetic_fixture'})
        self.assertEqual(min(r['business_date'] for r in rows),'2001-01-01')
        self.assertEqual(max(r['business_date'] for r in rows),'2001-01-14')
        self.assertEqual([b['units'] for b in r['branches']],[242,246,244])
        self.assertEqual(r['coverage']['unrepresented_cells'],0)
        self.assertTrue(all(row['price_currency']=='USD' for row in rows if row['price_minor'] is not None))

    def test_explicit_missing_is_distinct_from_observed_zero(self):
        p.ingest(self.root,self.fixture())
        rows=p.observations(self.root,limit=1000)
        missing=[r for r in rows if r['coverage']=='missing'];zeros=[r for r in rows if r['units']==0]
        self.assertEqual(len(missing),10);self.assertEqual(len(zeros),36)
        self.assertTrue(all(r['units'] is None for r in missing))
        self.assertTrue(all(r['coverage']=='observed' for r in zeros))

    def test_repeat_same_manifest_replays_without_duplicate_facts(self):
        m=self.fixture();first=p.ingest(self.root,m);second=p.ingest(self.root,m)
        self.assertEqual(first['run_id'],second['run_id']);self.assertEqual(self.count_runs(),1)
        with connect(self.root) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM sales_observations').fetchone()[0],252)

    def test_interruption_resume_durable_offset_and_invisible_staging(self):
        m=self.fixture();result=p.ingest(self.root,m,chunk_size=17,max_chunks=2)
        self.assertEqual(result['status'],'staging');self.assertEqual(p.published_summary(self.root)['rows'],0)
        with connect(self.root) as conn:
            row=conn.execute('SELECT checkpoint_line,checkpoint_offset FROM import_runs').fetchone()
            self.assertEqual(row[0],34);self.assertGreater(row[1],0)
        # A separate process has no in-memory checkpoint or Python object capability.
        manifest_path=p.directory(self.root,s.validate_spec(m))/'manifest.json'
        script=Path(__file__).resolve().parents[1]/'scripts/import_history.py'
        result=subprocess.run([sys.executable,str(script),'run','--target',str(self.root),
            '--manifest',str(manifest_path),'--chunk-size','19'],capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout)['status'],'published')
        self.assertEqual(p.published_summary(self.root)['rows'],252)

    def test_mid_chunk_error_rolls_back_rows_counters_and_checkpoint(self):
        m=self.fixture();original=p.classify;calls=0
        def fail(*args):
            nonlocal calls
            calls+=1
            if calls==5:raise RuntimeError('Injected interruption')
            return original(*args)
        with patch.object(p,'classify',side_effect=fail),self.assertRaises(RuntimeError):p.ingest(self.root,m,chunk_size=20)
        with connect(self.root) as conn:
            self.assertEqual(conn.execute('SELECT checkpoint_line FROM import_runs').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sales_observations').fetchone()[0],0)
        self.assertEqual(p.ingest(self.root,m)['status'],'published')

    def test_checksum_corruption_rejected_before_staging(self):
        m=self.fixture();path=p.directory(self.root,s.validate_spec(m))/'observations.jsonl'
        raw=path.read_bytes();path.write_bytes(raw.replace(b'2001-01-01',b'2001-01-02',1))
        with self.assertRaisesRegex(ValueError,'checksum'):p.ingest(self.root,m)
        self.assertEqual(self.count_runs(),0)

    def test_changed_fixture_during_run_is_caught_before_publication(self):
        m=self.fixture();path=p.directory(self.root,s.validate_spec(m))/'observations.jsonl'
        original=p.stage_chunk
        def change(*args):
            eof=original(*args)
            if eof:path.write_bytes(path.read_bytes().replace(b'2001-01-01',b'2001-01-02',1))
            return eof
        with patch.object(p,'stage_chunk',side_effect=change),self.assertRaisesRegex(ValueError,'checksum'):p.ingest(self.root,m)
        self.assertEqual(p.published_summary(self.root)['rows'],0)

    def test_duplicate_source_fact_quarantined_once_across_resume(self):
        m=self.fixture(faults=['duplicate_fact']);p.ingest(self.root,m,chunk_size=100,max_chunks=1)
        r=p.ingest(self.root,m,chunk_size=3)['reconciliation']
        self.assertEqual(r['counters']['input_rows'],253);self.assertEqual(r['counters']['accepted_rows'],252)
        self.assertEqual(r['rejection_reasons'],{'duplicate_fact':1});self.assertTrue(r['ok'])

    def test_invalid_rows_have_reproducible_quarantine_causes(self):
        cases={'invalid_date':('calendar_mismatch',1),'unknown_branch':('unknown_branch',1),
            'unknown_sku':('unknown_sku',1),'negative_units':('invalid_units_coverage',1),
            'bad_price_join':('price_join_mismatch',1),'malformed_json':('malformed_json',1),
            'bad_calendar':('calendar_mismatch',18),'duplicate_price':('ambiguous_price',7)}
        for fault,(reason,count) in cases.items():
            with self.subTest(fault=fault):
                m=self.fixture(revision='synthetic-'+fault.replace('_','-'),faults=[fault])
                r=p.ingest(self.root,m)['reconciliation']
                self.assertTrue(r['ok']);self.assertEqual(r['rejection_reasons'],{reason:count})
                self.assertEqual(r['counters']['rejected_rows'],count)
                self.assertEqual(r['coverage']['unrepresented_cells'],count)

    def test_unknown_database_branch_or_sku_mapping_fails_before_start(self):
        m=self.fixture();conn=self.raw()
        conn.execute("UPDATE skus SET active=0 WHERE sku_id=1");conn.commit()
        with self.assertRaisesRegex(ValueError,'mapping'):p.ingest(self.root,m)
        self.assertEqual(self.count_runs(),0)
        conn.execute('UPDATE skus SET active=1 WHERE sku_id=1');conn.execute('UPDATE branches SET active=0 WHERE branch_id=1');conn.commit()
        with self.assertRaisesRegex(ValueError,'mapping'):p.ingest(self.root,m)

    def test_absent_optional_prices_stay_unknown(self):
        result=p.ingest(self.root,self.fixture(prices=False))
        self.assertEqual(result['reconciliation']['counters']['price_unknown_rows'],252)
        self.assertTrue(all(r['price_minor'] is None and r['price_currency'] is None for r in p.observations(self.root)))

    def test_staged_replacement_invisible_then_atomic_switch_and_old_retry(self):
        old=self.fixture();first=p.ingest(self.root,old)
        new=self.fixture(revision='synthetic-r2',seed=14)
        p.ingest(self.root,new,max_chunks=1,chunk_size=10)
        self.assertEqual(p.published_summary(self.root)['active_run_id'],first['run_id'])
        staged=p.ingest(self.root,new,publish_result=False)
        self.assertEqual(staged['status'],'reconciled');self.assertEqual(p.published_summary(self.root)['units'],732)
        second=p.ingest(self.root,new)
        self.assertNotEqual(second['run_id'],first['run_id'])
        self.assertEqual(p.published_summary(self.root)['units'],second['reconciliation']['counters']['units'])
        self.assertEqual(p.ingest(self.root,old)['status'],'superseded')
        self.assertEqual(p.published_summary(self.root)['active_run_id'],second['run_id'])

    def test_reader_snapshot_sees_complete_old_revision_after_switch(self):
        first=p.ingest(self.root,self.fixture())
        new=self.fixture(revision='synthetic-r2',seed=14)
        with connect(self.root) as reader:
            reader.execute('BEGIN')
            before=reader.execute('SELECT SUM(units),COUNT(*) FROM published_sales_observations').fetchone()
            p.ingest(self.root,new)
            after=reader.execute('SELECT SUM(units),COUNT(*) FROM published_sales_observations').fetchone()
            self.assertEqual(tuple(before),tuple(after));self.assertEqual(before[0],732)
        self.assertNotEqual(p.published_summary(self.root)['active_run_id'],first['run_id'])

    def test_publication_failure_rolls_back_old_status_and_active_pointer(self):
        first=p.ingest(self.root,self.fixture());m=self.fixture(revision='synthetic-r2')
        result=p.ingest(self.root,m,publish_result=False)
        raw=self.raw();raw.execute("CREATE TRIGGER test_fail_publish BEFORE UPDATE ON historical_datasets BEGIN SELECT RAISE(ABORT,'Injected failure'); END;");raw.commit()
        with self.assertRaises(sqlite3.IntegrityError):p.ingest(self.root,m)
        self.assertEqual(p.published_summary(self.root)['active_run_id'],first['run_id'])
        with connect(self.root) as conn:self.assertEqual(conn.execute('SELECT status FROM import_runs WHERE run_id=?',(first['run_id'],)).fetchone()[0],'published')
        raw.execute('DROP TRIGGER test_fail_publish');raw.commit()
        self.assertEqual(p.ingest(self.root,m)['status'],'published')

    def test_stale_replacement_cannot_overwrite_newer_publication(self):
        p.ingest(self.root,self.fixture())
        a=self.fixture(revision='synthetic-r2');b=self.fixture(revision='synthetic-r3')
        p.ingest(self.root,a,publish_result=False);p.ingest(self.root,b,publish_result=False)
        published=p.ingest(self.root,a)
        with self.assertRaisesRegex(ValueError,'stale replacement'):p.ingest(self.root,b)
        self.assertEqual(p.published_summary(self.root)['active_run_id'],published['run_id'])

    def test_same_revision_changed_parameters_conflict(self):
        p.ingest(self.root,self.fixture())
        changed=self.fixture(seed=14)
        with self.assertRaisesRegex(ValueError,'different manifest'):p.ingest(self.root,changed)
        self.assertEqual(self.count_runs(),1)

    def test_operational_stock_sales_and_all_core_records_unchanged(self):
        conn=self.raw()
        conn.executescript("""INSERT INTO products(product_id,name,version,archived) VALUES(501,'Sentinel',1,0);
            INSERT INTO inventory(inventory_id,product_id,quantity) VALUES(501,501,77);
            INSERT INTO sales(sale_id,product_id,quantity,sale_date) VALUES(501,501,3,'2000-12-01');
            INSERT INTO stock_lots(lot_id,sku_id,lot_code,validation_state,provenance,version) VALUES(501,1,'SYN-SENTINEL','validated','{}',1);
            INSERT INTO branch_stock(branch_id,lot_id,sku_id,on_hand,reserved,quarantined_quantity,version) VALUES(1,501,1,77,0,0,1);""")
        from database import db
        tables=sorted(db.metadata.tables)
        before={table:conn.execute('SELECT * FROM '+table).fetchall() for table in tables}
        p.ingest(self.root,self.fixture())
        after={table:conn.execute('SELECT * FROM '+table).fetchall() for table in tables}
        self.assertEqual(after,before)

    def test_write_firewall_denies_operational_writes_and_attach(self):
        with connect(self.root) as conn:
            for sql in ["UPDATE branches SET active=0", "DELETE FROM sales", "UPDATE branch_stock SET on_hand=0",
                        "PRAGMA foreign_keys=OFF", "ATTACH DATABASE ':memory:' AS extra"]:
                with self.subTest(sql=sql),self.assertRaises(sqlite3.DatabaseError):conn.execute(sql)

    def test_unknown_existing_or_configured_target_cannot_be_adopted(self):
        for root in [Path(self.temp.name),Path(self.temp.name)/'production.sqlite','sqlite:///:memory:','mysql://localhost/db']:
            with self.subTest(root=root),self.assertRaises((ValueError,OSError)):p.ingest(root,{'source_id':'real'})
        with self.assertRaises(ValueError):initialize_target(self.temp.name,acknowledgment='yes')
        descriptor=self.root/'target.json';info=json.loads(descriptor.read_text());info['token']='0'*64;descriptor.write_text(json.dumps(info))
        with self.assertRaisesRegex(ValueError,'marker mismatch'):
            with connect(self.root):pass

    def test_real_m5_unknown_source_and_relabelled_inputs_rejected_before_file_read(self):
        for m in [{'source_id':'kaggle/m5-forecasting-accuracy','provenance':'synthetic_fixture'},
                  {'source_id':'unknown','provenance':'synthetic_fixture'},
                  {'source_id':s.SOURCE,'provenance':'m5_historical_observation'}]:
            with self.subTest(m=m),self.assertRaisesRegex(ValueError,'BLOCKED'):p.ingest(self.root,m)
        m=self.fixture();m['files']['observations.jsonl']['sha256']='a'*64
        with self.assertRaisesRegex(ValueError,'generator contract'):p.ingest(self.root,m)
        self.assertEqual(self.count_runs(),0)

    def test_unsafe_external_paths_formats_and_oversized_inputs(self):
        m=self.fixture();path=p.directory(self.root,s.validate_spec(m))
        for bad in [Path(self.temp.name)/'manifest.json',path/'../manifest.json',path/'calendar.jsonl']:
            with self.subTest(path=bad),self.assertRaises((ValueError,OSError)):p.load_manifest(self.root,bad)
        (path/'unexpected.zip').write_bytes(b'not an archive')
        with self.assertRaisesRegex(ValueError,'unexpected'):p.ingest(self.root,m)
        (path/'unexpected.zip').unlink()
        with patch.object(p,'MAX_FILE_BYTES',32),self.assertRaisesRegex(ValueError,'oversized'):p.ingest(self.root,m)
        for chunk in [0,1001,True]:
            with self.assertRaises(ValueError):p.ingest(self.root,m,chunk_size=chunk)

    def test_observation_database_constraints_and_committed_immutability(self):
        p.ingest(self.root,self.fixture());raw=self.raw();raw.execute('PRAGMA foreign_keys=ON')
        for statement in ['UPDATE sales_observations SET units=999','DELETE FROM sales_observations',
            'INSERT INTO sales_observations SELECT * FROM sales_observations LIMIT 1']:
            with self.subTest(statement=statement),self.assertRaises(sqlite3.IntegrityError):raw.execute(statement)
        raw.rollback()
        with connect(self.root) as conn:
            with self.assertRaises(ValueError):p.observations(self.root,limit=1001)

    def test_reconciliation_failure_prevents_publication(self):
        m=self.fixture();r=p.ingest(self.root,m,max_chunks=1,chunk_size=40)
        raw=self.raw();counts=json.loads(raw.execute('SELECT counters_json FROM import_runs').fetchone()[0]);counts['units']+=1
        raw.execute('UPDATE import_runs SET counters_json=?',(json.dumps(counts),));raw.commit()
        with self.assertRaisesRegex(ValueError,'reconciliation'):p.ingest(self.root,m)
        self.assertEqual(p.published_summary(self.root)['rows'],0)

    def test_medium_import_reconciles_independent_formula_and_original_dates(self):
        m=self.fixture(profile='medium');r=p.ingest(self.root,m,chunk_size=700)['reconciliation']
        units=sum((store+item+day+13)%7 for store in range(3) for item in range(120) for day in range(90) if (store+item+day)%17)
        missing=sum((store+item+day)%17==0 for store in range(3) for item in range(120) for day in range(90))
        self.assertEqual(r['counters']['input_rows'],32400);self.assertEqual(r['counters']['units'],units)
        self.assertEqual(r['counters']['missing_rows'],missing);self.assertTrue(r['ok'])
        self.assertEqual(r['coverage']['last_date'],'2001-03-31')

    def test_concurrent_same_manifest_creates_one_run_and_one_fact_set(self):
        m=self.fixture()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:p.ingest(self.root,m,chunk_size=21),range(2)))
        self.assertEqual(results[0]['run_id'],results[1]['run_id'])
        self.assertEqual(self.count_runs(),1);self.assertEqual(p.published_summary(self.root)['rows'],252)

    def test_configured_database_is_never_opened_or_used(self):
        production=Path(self.temp.name)/'existing-production.db'
        production.write_bytes(b'Private existing database sentinel; do not open')
        before=production.read_bytes()
        with patch.dict(os.environ,{'DATABASE_URL':'sqlite:///'+production.as_posix(),'APP_ENV':'production'}):
            self.assertEqual(p.ingest(self.root,self.fixture())['status'],'published')
        self.assertEqual(production.read_bytes(),before)

    def test_staging_database_coverage_price_and_referential_constraints(self):
        m=self.fixture();result=p.ingest(self.root,m,chunk_size=1,max_chunks=1)
        raw=self.raw();raw.execute('PRAGMA foreign_keys=ON')
        original=raw.execute('SELECT * FROM sales_observations LIMIT 1')
        fields=[c[0] for c in original.description];source=dict(zip(fields,original.fetchone()))
        source.pop('observation_id');source['business_date']='2001-02-01'
        for change in [dict(branch_id=999),dict(sku_id=999),dict(units=None,coverage='observed'),
                       dict(units=0,coverage='missing'),dict(price_minor=None,price_currency='USD'),
                       dict(source_revision='synthetic-wrong')]:
            row=dict(source,**change)
            sql='INSERT INTO sales_observations('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')'
            with self.subTest(change=change),self.assertRaises(sqlite3.IntegrityError):raw.execute(sql,list(row.values()))
            raw.rollback()

    def test_example_manifest_matches_generator_and_remains_synthetic(self):
        example=Path(__file__).resolve().parents[1]/'data/t14_synthetic_small.example.json'
        m=json.loads(example.read_text(encoding='utf-8'))
        self.assertEqual(m,s.specification());self.assertEqual(m['provenance'],'synthetic_fixture')
        from data_manifest import require_real_ingestion
        with self.assertRaisesRegex(ValueError,'BLOCKED'):require_real_ingestion(m)
        repo=Path(__file__).resolve().parents[1]
        for path in ['t14-demo-example/target.json','data/scratch/t14-demo-example/fixtures/hash/observations.jsonl']:
            self.assertEqual(subprocess.run(['git','check-ignore','-q',path],cwd=repo).returncode,0)
