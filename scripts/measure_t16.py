"""Reproducible single-reader measurements; creates ONLY new synthetic targets.

Not a load test. No database URL/target argument is accepted.
"""
import argparse
from contextlib import closing, ExitStack
import json
import platform
import sqlite3
import statistics
import sys
import time
import subprocess
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def fixture(parent, profile, indexes=False):
    from simulation import runtime
    from simulation.runner import run
    from ingestion.target import ACK
    original = runtime.generate_fixture
    with patch.object(runtime, 'generate_fixture', side_effect=lambda root, **kw: original(root, profile=profile)):
        root = runtime.initialize(parent, acknowledgment=ACK)
    app = runtime.create_app(root)
    run(app)
    if profile == 'medium':
        from database import db
        from receiving_services import receive
        from sales_services import create_sale
        # Every positive balance and sale goes through the existing ledger services.
        for branch in (1, 2, 3):
            for n in range(40):
                key = f'SYN-T16-{branch}-{n}'
                with runtime.actor(app, 901, branch):
                    receive(dict(mode='new_lot', sku_id=121, lot_code=key, supplier_lot_code=key,
                        quantity=10, expiry_date='2031-02-28', external_reference=key,
                        request_key=key, reason='Synthetic T16 measurement', evidence='Generated label'))
                    db.session.commit()
            for n in range(60):
                with runtime.actor(app, 901, branch):
                    create_sale(dict(lines=[dict(sku_id=121, quantity=1)], request_key=f'SYN-T16-SALE-{branch}-{n}',
                        business_reference='SYN-T16', reason='Synthetic measurement sale'))
                    db.session.commit()
    if indexes:
        # Only this newly created target; no existing-target/URL option exists.
        from database import db
        with app.app_context(), db.engine.begin() as connection:
            ddl=(Path(__file__).resolve().parents[1]/'migrations/t16_v001_indexes_sqlite.sql').read_text()
            for statement in ddl.split(';'):
                if statement.strip():connection.exec_driver_sql(statement)
    return root, app


def client(app, uid=901, branch=None):
    from authentication import session_token
    from database import db
    from models import User
    with app.app_context():
        token = session_token(db.session.get(User, uid))
    result = app.test_client()
    with result.session_transaction() as state:
        state['_user_id'] = token
        if branch:
            state['active_branch_id'] = branch
    return result


def reference_reader(stack, app):
    """Read-only reconstruction for repeatable pre-T16/branch measurements.

    Does not check out files or change Git. Only queries newly generated targets.
    Phase4 reader + the T15 provenance-only addition equals the pre-T16 reader.
    """
    import operational_routes
    from flask.templating import _render
    revision='38f8be2f0016c3f8a1f1a9171efbfc11f290c7f3'
    def source(name):
        return subprocess.run(['git','show',revision+':'+name],check=True,capture_output=True,
            encoding='utf-8',cwd=Path(__file__).resolve().parents[1]).stdout
    module=types.ModuleType('t16_reference_reader')
    exec(compile(source('operational_services.py'),'<T16 reference reader>','exec'),module.__dict__)
    export_module=types.ModuleType('t16_reference_export')
    exec(compile(source('operational_routes.py'),'<T16 reference export>','exec'),export_module.__dict__)
    template=source('templates/pages/operations.html')
    compiled=None
    def report(values=None,*,capability='reports.read',sections=None):
        result=module.report(values,capability=capability)
        result['provenance']['simulation']='Synthetic demo SYN-T15-V1: generated operational facts; all MYR amounts are simulated, not real-world revenue. Historical observations are separate.'
        return result
    original=operational_routes.render_template
    def render(name,**values):
        nonlocal compiled
        if name!='pages/operations.html':return original(name,**values)
        if compiled is None:compiled=app.jinja_env.from_string(template)
        return _render(app,compiled,values)
    stack.enter_context(patch.object(operational_routes.queries,'report',report))
    stack.enter_context(patch.object(operational_routes,'render_template',render))
    stack.enter_context(patch.object(operational_routes,'export',export_module.export))


def measure(profile, indexes=False, reference=False):
    from database import db
    from sqlalchemy import event
    from ingestion.pipeline import observations
    with TemporaryDirectory(prefix='inventraiq-t16-') as temp, ExitStack() as stack:
        root, app = fixture(temp, profile, indexes)
        if reference:reference_reader(stack,app)
        user = client(app)
        endpoints = dict(inventory='/inventory-page', sales='/sales', dashboard='/dashboard',
                         reports='/reports', analytics='/analytics', export='/operations/export.csv',
                         api='/api/operations',branch_dashboard='/dashboard?branch_id=2')
        if not reference and 'operations.history_api' in app.view_functions:
            endpoints['historical'] = '/api/observations?start=2001-01-01&end=2001-03-31'
        counts = [];inventory_sql=[]
        def count(conn,cursor,statement,parameters,context,many):
            counts.append(1)
            if statement.startswith('WITH native_classification') and not inventory_sql:
                inventory_sql.append((statement,parameters))
        with app.app_context():
            engine = db.engine
            event.listen(engine, 'before_cursor_execute', count)
        results = {}
        api_totals=None
        try:
            for name, url in endpoints.items():
                samples = []
                for repeat in range(4):
                    counts.clear()
                    start = time.perf_counter()
                    response = user.get(url)
                    payload = response.get_data()  # Consume streaming output too.
                    elapsed = (time.perf_counter() - start) * 1000
                    if response.status_code != 200:
                        raise AssertionError((name, response.status_code, payload[:200]))
                    samples.append(dict(ms=round(elapsed, 3), queries=len(counts), bytes=len(payload)))
                    if name=='api' and repeat==3:api_totals=response.get_json()['total']
                results[name] = dict(first=samples[0], warm=samples[1:],
                    warm_median_ms=round(statistics.median(s['ms'] for s in samples[1:]), 3))
            if 'historical' not in results:
                start = time.perf_counter()
                rows = observations(root, limit=25)
                results['historical_offline_only'] = dict(ms=round((time.perf_counter()-start)*1000, 3),
                    rows=len(rows), bytes=len(json.dumps(rows)), note='No authorized HTTP reader in baseline')
            with closing(sqlite3.connect(root/'disposable.sqlite')) as connection:
                results['facts'] = {table: connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                    for table in ['branch_stock', 'priced_sales', 'sales_observations']}
                queries = {
                    'sales': 'SELECT sale_id,branch_id,business_date,total_minor FROM priced_sales WHERE branch_id=1 AND business_date BETWEEN \'2031-01-01\' AND \'2031-01-03\' ORDER BY sale_id LIMIT 25',
                    'observations': 'SELECT observation_id FROM sales_observations WHERE run_id=1 AND branch_id=1 AND business_date BETWEEN \'2001-01-01\' AND \'2001-03-31\' ORDER BY observation_id LIMIT 25',
                    'movement_opening': "SELECT movement_id FROM inventory_movements WHERE branch_id=1 AND lot_id=1",
                }
                results['plans'] = {key:[list(r) for r in connection.execute('EXPLAIN QUERY PLAN '+sql)] for key,sql in queries.items()}
                results['database_bytes']=connection.execute('PRAGMA page_count').fetchone()[0]*connection.execute('PRAGMA page_size').fetchone()[0]
                raw=dict(on_hand=connection.execute('SELECT SUM(on_hand) FROM branch_stock').fetchone()[0],
                    gross_minor=connection.execute('SELECT SUM(total_minor) FROM priced_sales').fetchone()[0],
                    refund_minor=connection.execute('SELECT SUM(total_minor) FROM sale_refunds').fetchone()[0],
                    known_sold_units=connection.execute('SELECT SUM(quantity) FROM sale_lines').fetchone()[0])
                if any(api_totals[k]!=v for k,v in raw.items()):raise AssertionError('Report differs from recorded fixture facts')
                results['reconciliation']=dict(ok=True,**raw,net_minor=raw['gross_minor']-raw['refund_minor'])
            if inventory_sql:
                with engine.connect() as connection:
                    sql,parameters=inventory_sql[0]
                    results['plans']['actual_inventory']=[list(r) for r in connection.exec_driver_sql('EXPLAIN QUERY PLAN '+sql,parameters)]
            return results
        finally:
            event.remove(engine, 'before_cursor_execute', count)
            with app.app_context(): db.session.remove(); db.engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--indexes', action='store_true',help='Apply T16 indexes ONLY to newly generated temporary fixtures')
    parser.add_argument('--reference',action='store_true',help='Reconstruct the pre-T16 reader from the local Phase4 checkpoint, without editing the worktree')
    args = parser.parse_args()
    if Path(args.output).suffix.lower()!='.json' or Path(args.output).is_symlink():
        parser.error('Measurement output must be a regular JSON evidence file, never a database or symlink.')
    result = dict(environment=dict(python=sys.version.split()[0], sqlite=sqlite3.sqlite_version,
        platform=platform.platform(), processor=platform.processor(), concurrency=1,
        method='First application read plus three warm reads; OS disk cache not flushed; milliseconds include full response consumption'),
        indexes=args.indexes,reference=args.reference,profiles={p:measure(p,args.indexes,args.reference) for p in ('small', 'medium')})
    from importlib.metadata import version
    import os
    result['environment']['logical_cpus']=os.cpu_count()
    result['environment']['dependencies']={name:version(name) for name in ('Flask','Flask-SQLAlchemy','SQLAlchemy','Werkzeug','tzdata')}
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
