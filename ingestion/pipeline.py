"""Bounded Validate -> Stage -> Reconcile -> Publish; synthetic fixtures only."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from ingestion import synthetic as syn
from ingestion.target import checked_root,connect

MAX_FILE_BYTES=20_000_000
MAX_MANIFEST_BYTES=64_000
MAX_LINE_BYTES=1024
MAX_ROWS=40_000


def now():return datetime.now(timezone.utc).isoformat()


def dumps(value):return syn.encoded(value).decode('ascii')


def directory(root,identity):
    root,_,_=checked_root(root)
    path=root/'fixtures'/identity
    if path.absolute()!=path.resolve() or not path.is_relative_to(root):raise ValueError('Unsafe fixture path')
    return path


def generate_fixture(root,**config):
    with connect(root):pass
    manifest=syn.specification(**config);identity=syn.validate_spec(manifest)
    path=directory(root,identity)
    path.mkdir(exist_ok=False)
    for name in syn.FILES:
        with (path/name).open('xb') as handle:
            for line in syn.lines(manifest['configuration'],name):handle.write(line)
    with (path/'manifest.json').open('xb') as handle:handle.write(syn.encoded(manifest)+b'\n')
    return path/'manifest.json'


def load_manifest(root,path):
    root,_,_=checked_root(root);path=Path(path)
    if path.name!='manifest.json' or path.absolute()!=path.resolve() or not path.is_relative_to(root/'fixtures'):
        raise ValueError('Manifest must be generated inside this disposable target')
    if path.stat().st_size>MAX_MANIFEST_BYTES:raise ValueError('Oversized manifest')
    manifest=json.loads(path.read_bytes())
    identity=syn.validate_spec(manifest)
    if path.parent!=directory(root,identity):raise ValueError('Manifest directory identity mismatch')
    return manifest


def validate_files(root,manifest):
    identity=syn.validate_spec(manifest)  # Refuse M5 before reading any input bytes.
    path=directory(root,identity)
    if not path.is_dir() or {p.name for p in path.iterdir()}!=set(syn.FILES)|{'manifest.json'}:
        raise ValueError('Missing or unexpected fixture files')
    for name in syn.FILES:
        file=path/name;expected=manifest['files'][name]
        if file.is_symlink() or file.resolve()!=file.absolute() or not file.is_file():raise ValueError('Unsafe input path')
        size=file.stat().st_size
        if size>MAX_FILE_BYTES or size!=expected['size_bytes']:raise ValueError('Input size mismatch or oversized input')
        digest=hashlib.sha256()
        with file.open('rb') as handle:
            for block in iter(lambda:handle.read(65536),b''):digest.update(block)
        if digest.hexdigest()!=expected['sha256']:raise ValueError('Corrupt fixture checksum')
        if expected['rows']>MAX_ROWS:raise ValueError('Input row ceiling exceeded')
    return identity,path


def json_rows(path):
    with path.open('rb') as handle:
        for line in iter(lambda:handle.readline(MAX_LINE_BYTES+1),b''):
            if len(line)>MAX_LINE_BYTES:raise ValueError('Oversized input record')
            yield json.loads(line)


def dimensions(conn,manifest):
    branch_codes=[r['branch_code'] for r in manifest['branches']]
    sku_codes=[r['sku_code'] for r in manifest['skus']]
    branches={r['code']:r['branch_id'] for r in conn.execute('SELECT branch_id,code FROM branches WHERE active=1 AND code IN ('+','.join('?' for _ in branch_codes)+')',branch_codes)}
    skus={r['code']:r['sku_id'] for r in conn.execute('SELECT sku_id,code FROM skus WHERE active=1 AND code IN ('+','.join('?' for _ in sku_codes)+')',sku_codes)}
    try:
        return ({r['store_id']:branches[r['branch_code']] for r in manifest['branches']},
                {r['item_id']:skus[r['sku_code']] for r in manifest['skus']})
    except KeyError as exc:raise ValueError('Unknown/inactive branch or SKU mapping') from exc


def joins(path,manifest):
    days=syn.PROFILES[manifest['configuration']['profile']][1]
    calendar={};invalid=set()
    for row in json_rows(path/'calendar.jsonl'):
        key=row['day_id']
        if key in calendar:invalid.add(key)
        calendar[key]=row
    for d in range(days):
        key=f'syn_d_{d}'
        if calendar.get(key)!=dict(day_id=key,date=syn.day(d),week=d//7):invalid.add(key)
    prices={};ambiguous=set()
    for row in json_rows(path/'prices.jsonl'):
        key=(row['store_id'],row['item_id'],row['week'])
        if key in prices:ambiguous.add(key)
        prices[key]=row['price_minor']
    return calendar,invalid,prices,ambiguous


def classify(raw,branches,skus,calendar,invalid,prices,ambiguous):
    try:row=json.loads(raw)
    except (ValueError,UnicodeError):return None,'malformed_json',0
    numeric=row.get('units') if isinstance(row,dict) else None
    numeric=numeric if type(numeric) is int and 0<=numeric<=2147483647 else 0
    if not isinstance(row,dict) or set(row)!={'store_id','item_id','day_id','date','coverage','units','price_week','provenance'}:
        return None,'invalid_fields',numeric
    if row['store_id'] not in branches:return None,'unknown_branch',numeric
    if row['item_id'] not in skus:return None,'unknown_sku',numeric
    day=calendar.get(row['day_id'])
    if not day or row['day_id'] in invalid or row['date']!=day['date']:return None,'calendar_mismatch',numeric
    if row['price_week']!=day['week']:return None,'price_join_mismatch',numeric
    if row['provenance']!='synthetic_fixture':return None,'invalid_provenance',numeric
    if not ((row['coverage']=='observed' and type(row['units']) is int and 0<=row['units']<=2147483647) or
            (row['coverage']=='missing' and row['units'] is None)):
        return None,'invalid_units_coverage',numeric
    key=(row['store_id'],row['item_id'],row['price_week'])
    if key in ambiguous:return None,'ambiguous_price',numeric
    return dict(row,branch_id=branches[row['store_id']],sku_id=skus[row['item_id']],price_minor=prices.get(key)),None,numeric


def initial_counters():
    return dict(input_rows=0,accepted_rows=0,rejected_rows=0,observed_rows=0,missing_rows=0,
                zero_rows=0,units=0,input_numeric_units=0,rejected_numeric_units=0,price_unknown_rows=0)


def start(conn,manifest,identity):
    conn.execute('BEGIN IMMEDIATE')
    try:
        dimensions(conn,manifest)
        row=conn.execute('SELECT * FROM import_runs WHERE manifest_hash=?',(identity,)).fetchone()
        if row:
            conn.commit();return row['run_id']
        if conn.execute('SELECT 1 FROM import_runs WHERE source_id=? AND source_revision=?',
                        (syn.SOURCE,manifest['source_revision'])).fetchone():raise ValueError('Source revision already bound to a different manifest')
        conn.execute('INSERT OR IGNORE INTO historical_datasets VALUES (?,NULL,1)',(syn.SOURCE,))
        active=conn.execute('SELECT active_run_id FROM historical_datasets WHERE source_id=?',(syn.SOURCE,)).fetchone()[0]
        stamp=now()
        cursor=conn.execute('''INSERT INTO import_runs(source_id,source_revision,manifest_hash,manifest_json,checksums_json,
            configuration_json,base_run_id,status,checkpoint_line,checkpoint_offset,counters_json,created_at_utc,updated_at_utc)
            VALUES (?,?,?,?,?,?,?,'staging',0,0,?,?,?)''',
            (syn.SOURCE,manifest['source_revision'],identity,dumps(manifest),dumps(manifest['files']),
             dumps(manifest['configuration']),active,dumps(initial_counters()),stamp,stamp))
        conn.commit();return cursor.lastrowid
    except Exception:
        conn.rollback();raise


def stage_chunk(conn,run_id,manifest,path,context,chunk_size):
    conn.execute('BEGIN IMMEDIATE')
    try:
        run=conn.execute('SELECT * FROM import_runs WHERE run_id=?',(run_id,)).fetchone()
        if run['status']!='staging':conn.commit();return True
        counts=json.loads(run['counters_json']);line_no=run['checkpoint_line']
        offset=run['checkpoint_offset'];eof=False
        with (path/'observations.jsonl').open('rb') as handle:
            handle.seek(offset)
            for _ in range(chunk_size):
                raw=handle.readline(MAX_LINE_BYTES+1)
                if not raw:eof=True;break
                if len(raw)>MAX_LINE_BYTES:raise ValueError('Oversized observation record')
                line_no+=1
                if line_no>MAX_ROWS:raise ValueError('Observation row ceiling exceeded')
                row,reason,numeric=classify(raw,*context)
                counts['input_rows']+=1;counts['input_numeric_units']+=numeric
                if reason is None:
                    try:
                        conn.execute('''INSERT INTO sales_observations(run_id,source_id,source_revision,source_store_id,
                            source_item_id,branch_id,sku_id,business_date,units,coverage,measure_type,provenance,price_minor,price_currency)
                            VALUES (?,?,?,?,?,?,?,?,?,?,'daily_units','synthetic_fixture',?,?)''',
                            (run_id,syn.SOURCE,manifest['source_revision'],row['store_id'],row['item_id'],
                             row['branch_id'],row['sku_id'],row['date'],row['units'],row['coverage'],
                             row['price_minor'],'USD' if row['price_minor'] is not None else None))
                    except sqlite3.IntegrityError as exc:
                        if 'UNIQUE constraint failed' not in str(exc):raise
                        reason='duplicate_fact'
                if reason:
                    conn.execute('INSERT INTO import_quarantine VALUES (?,?,?,?,?)',
                        (run_id,line_no,reason,hashlib.sha256(raw).hexdigest(),numeric))
                    counts['rejected_rows']+=1;counts['rejected_numeric_units']+=numeric
                else:
                    counts['accepted_rows']+=1;counts['units']+=row['units'] or 0
                    counts['observed_rows']+=row['coverage']=='observed';counts['missing_rows']+=row['coverage']=='missing'
                    counts['zero_rows']+=row['coverage']=='observed' and row['units']==0
                    counts['price_unknown_rows']+=row['price_minor'] is None
            offset=handle.tell()
        conn.execute('UPDATE import_runs SET checkpoint_line=?,checkpoint_offset=?,counters_json=?,updated_at_utc=? WHERE run_id=?',
                     (line_no,offset,dumps(counts),now(),run_id))
        conn.commit();return eof
    except Exception:
        conn.rollback();raise


def reconciliation(conn,run_id):
    run=conn.execute('SELECT * FROM import_runs WHERE run_id=?',(run_id,)).fetchone()
    if not run:raise ValueError('Unknown import run')
    manifest=json.loads(run['manifest_json']);counts=json.loads(run['counters_json'])
    actual=dict(conn.execute('''SELECT COUNT(*) accepted_rows,COALESCE(SUM(units),0) units,
        COALESCE(SUM(coverage='observed'),0) observed_rows,COALESCE(SUM(coverage='missing'),0) missing_rows,
        COALESCE(SUM(coverage='observed' AND units=0),0) zero_rows,
        COALESCE(SUM(price_minor IS NULL),0) price_unknown_rows FROM sales_observations WHERE run_id=?''',(run_id,)).fetchone())
    rejected=conn.execute('SELECT COUNT(*),COALESCE(SUM(numeric_units),0) FROM import_quarantine WHERE run_id=?',(run_id,)).fetchone()
    actual.update(rejected_rows=rejected[0],rejected_numeric_units=rejected[1])
    expected_cells=3*syn.PROFILES[manifest['configuration']['profile']][0]*syn.PROFILES[manifest['configuration']['profile']][1]
    reasons=dict(conn.execute('SELECT reason,COUNT(*) FROM import_quarantine WHERE run_id=? GROUP BY reason ORDER BY reason',(run_id,)))
    branch=[dict(r) for r in conn.execute('''SELECT source_store_id,branch_id,COUNT(*) rows,COALESCE(SUM(units),0) units,
        SUM(coverage='missing') missing_rows,MIN(business_date) first_date,MAX(business_date) last_date
        FROM sales_observations WHERE run_id=? GROUP BY source_store_id,branch_id ORDER BY source_store_id''',(run_id,))]
    checks=[all(counts[k]==v for k,v in actual.items()),
        counts['input_rows']==counts['accepted_rows']+counts['rejected_rows'],
        counts['input_numeric_units']==counts['units']+counts['rejected_numeric_units'],
        counts['input_rows']==manifest['files']['observations.jsonl']['rows'],
        run['checkpoint_line']==counts['input_rows'],
        run['checkpoint_offset']==manifest['files']['observations.jsonl']['size_bytes'],
        counts['observed_rows']+counts['missing_rows']==counts['accepted_rows'],
        sum(b['units'] for b in branch)==counts['units']]
    return dict(ok=all(checks),provenance='synthetic_fixture',source_revision=run['source_revision'],
        manifest_hash=run['manifest_hash'],counters=counts,rejection_reasons=reasons,branches=branch,
        coverage=dict(expected_cells=expected_cells,represented_cells=actual['accepted_rows'],
            unrepresented_cells=expected_cells-actual['accepted_rows'],explicit_missing_cells=actual['missing_rows'],
            first_date=manifest['coverage']['first_date'],last_date=manifest['coverage']['last_date']))


def reconcile(root,run_id):
    with connect(root) as conn:
        conn.execute('BEGIN')
        return reconciliation(conn,run_id)


def publish(conn,run_id):
    conn.execute('BEGIN IMMEDIATE')
    try:
        run=conn.execute('SELECT * FROM import_runs WHERE run_id=?',(run_id,)).fetchone()
        if run['status'] in ('published','superseded'):conn.commit();return
        if run['status']!='reconciled':raise ValueError('Reconciliation required before publication')
        evidence=reconciliation(conn,run_id)
        if not evidence['ok']:raise ValueError('Reconciliation changed before publication')
        active=conn.execute('SELECT active_run_id FROM historical_datasets WHERE source_id=?',(syn.SOURCE,)).fetchone()[0]
        if active!=run['base_run_id']:raise ValueError('Active revision changed; stale replacement cannot publish')
        if active is not None:conn.execute("UPDATE import_runs SET status='superseded',updated_at_utc=? WHERE run_id=?",(now(),active))
        conn.execute("UPDATE import_runs SET status='published',published_at_utc=?,updated_at_utc=? WHERE run_id=?",(now(),now(),run_id))
        conn.execute('UPDATE historical_datasets SET active_run_id=?,version=version+1 WHERE source_id=?',(run_id,syn.SOURCE))
        conn.commit()
    except Exception:
        conn.rollback();raise


def ingest(root,manifest,*,chunk_size=250,max_chunks=None,publish_result=True):
    if type(chunk_size) is not int or not 1<=chunk_size<=1000:raise ValueError('Chunk size must be 1..1000')
    if max_chunks is not None and (type(max_chunks) is not int or max_chunks<1):raise ValueError('Invalid chunk budget')
    if type(publish_result) is not bool:raise ValueError('Explicit publication flag required')
    # Target identity before source processing; source gate before fixture-file reads.
    with connect(root) as conn:
        identity,path=validate_files(root,manifest)
        branches,skus=dimensions(conn,manifest)
        context=(branches,skus,*joins(path,manifest))
        run_id=start(conn,manifest,identity);chunks=0
        while True:
            status=conn.execute('SELECT status FROM import_runs WHERE run_id=?',(run_id,)).fetchone()[0]
            if status!='staging':break
            eof=stage_chunk(conn,run_id,manifest,path,context,chunk_size);chunks+=1
            if eof:break
            if max_chunks is not None and chunks>=max_chunks:return dict(run_id=run_id,status='staging',published=False)
        validate_files(root,manifest)  # Recheck source integrity before reconciliation/publication.
        conn.execute('BEGIN IMMEDIATE')
        try:
            evidence=reconciliation(conn,run_id)
            if not evidence['ok']:raise ValueError('Staged facts failed reconciliation')
            conn.execute("UPDATE import_runs SET status='reconciled',reconciliation_json=?,updated_at_utc=? WHERE run_id=? AND status='staging'",
                (dumps(evidence),now(),run_id))
            conn.commit()
        except Exception:
            conn.rollback();raise
        if publish_result:publish(conn,run_id)
        status=conn.execute('SELECT status FROM import_runs WHERE run_id=?',(run_id,)).fetchone()[0]
        return dict(run_id=run_id,status=status,published=status=='published',reconciliation=evidence)


def published_summary(root):
    with connect(root) as conn:
        conn.execute('BEGIN')
        active=conn.execute('SELECT active_run_id FROM historical_datasets WHERE source_id=?',(syn.SOURCE,)).fetchone()
        return dict(active_run_id=active[0] if active else None,**dict(conn.execute(
            'SELECT COUNT(*) rows,COALESCE(SUM(units),0) units FROM published_sales_observations').fetchone()))


def observations(root,*,limit=100,offset=0):
    if type(limit) is not int or not 1<=limit<=1000 or type(offset) is not int or not 0<=offset<=MAX_ROWS:
        raise ValueError('Bounded pagination required')
    with connect(root) as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM published_sales_observations ORDER BY branch_id,sku_id,business_date LIMIT ? OFFSET ?',(limit,offset))]
