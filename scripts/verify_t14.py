"""Reproducible synthetic-only imports in fresh temporary demo databases."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
from tempfile import TemporaryDirectory
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ingestion.target import initialize_target,connect,ACK
from ingestion import pipeline as p,synthetic as s
from database import db


def core_hash(root):
    with connect(root) as conn:
        facts={table:[tuple(r) for r in conn.execute('SELECT * FROM '+table)] for table in sorted(db.metadata.tables)}
    return hashlib.sha256(s.encoded(facts)).hexdigest()


def verify():
    evidence=dict(provenance='synthetic_fixture',real_m5_ingestion='BLOCKED',profiles={})
    for profile,(sku_count,days) in s.PROFILES.items():
        with TemporaryDirectory(prefix='inventraiq-t14-check-') as temp:
            root=initialize_target(temp,acknowledgment=ACK)
            before=core_hash(root)
            path=p.generate_fixture(root,profile=profile)
            manifest=p.load_manifest(root,path)
            interrupted=p.ingest(root,manifest,chunk_size=73,max_chunks=1)
            assert interrupted['status']=='staging' and p.published_summary(root)['rows']==0
            complete=p.ingest(root,manifest,chunk_size=317)
            replay=p.ingest(root,manifest,chunk_size=17)
            r=p.reconcile(root,complete['run_id']);summary=p.published_summary(root)
            expected_units=sum((store+item+day+13)%7 for store in range(3) for item in range(sku_count)
                for day in range(days) if (store+item+day)%17)
            assert r['ok'] and r['counters']['units']==expected_units
            assert r['counters']['accepted_rows']==3*sku_count*days
            assert replay['run_id']==complete['run_id'] and summary['rows']==3*sku_count*days
            assert core_hash(root)==before
            digest=hashlib.sha256()
            for offset in range(0,summary['rows'],1000):
                for row in p.observations(root,limit=1000,offset=offset):digest.update(s.encoded(row)+b'\n')
            evidence['profiles'][profile]=dict(manifest_hash=s.validate_spec(manifest),files=manifest['files'],
                reconciliation=r,published=summary,replay_added_rows=0,interrupted_after_rows=73,
                operational_records_unchanged=True,core_sha256=before,observation_sha256=digest.hexdigest())
    return evidence


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',help='Optional new JSON evidence file; never overwrites')
    args=parser.parse_args();result=verify();text=json.dumps(result,indent=2)+'\n'
    if args.output:
        with Path(args.output).open('x',encoding='utf-8') as handle:handle.write(text)
    print(text)
