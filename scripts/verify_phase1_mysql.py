"""Opt-in WSL MySQL rehearsal; creates new isolated targets, never drops targets.

Run --provision as WSL root with the existing socket-admin account. Credentials
stay in a mode-0600 file outside the repository. --tests uses only the DML account.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def provision():
    from scripts.verify_phase1_singletons import operator_owner
    import pymysql
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.engine import URL
    if os.geteuid() != 0:
        raise RuntimeError('Provisioning requires the approved WSL socket operator.')
    owner = operator_owner()
    suffix = time.strftime('%Y%m%d') + '_' + secrets.token_hex(4)
    names = ['inventraiq_phase1_' + suffix, 'inventraiq_phase1_recovery_' + suffix]
    app_user, operator = 'iqp1a_' + secrets.token_hex(4), 'iqp1o_' + secrets.token_hex(4)
    app_password, operator_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    admin = pymysql.connect(user='root', unix_socket='/var/run/mysqld/mysqld.sock', autocommit=True)
    with admin.cursor() as c:
        c.execute('SELECT VERSION(), @@version_comment, @@transaction_isolation')
        version, comment, isolation = c.fetchone()
        c.execute('SELECT @@partial_revokes')
        literal_grants = bool(c.fetchone()[0])
        assert 'mariadb' not in (version + comment).lower() and tuple(map(int, version.split('-')[0].split('.')[:3])) >= (8, 0, 16)
        for name in names:
            c.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (name,))
            assert c.fetchone() is None, 'Refuse existing target'
        for user in (app_user, operator):
            c.execute('SELECT User FROM mysql.user WHERE User=%s', (user,))
            assert c.fetchone() is None, 'Refuse existing account'
        for name in names:
            c.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin')
        for user, password in ((app_user, app_password), (operator, operator_password)):
            c.execute('CREATE USER %s@localhost IDENTIFIED BY %s', (user, password))
        for name in names:
            grant_name = name if literal_grants else name.replace('_', r'\_')
            c.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON `{grant_name}`.* TO %s@localhost', (app_user,))
            c.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, TRIGGER ON `{grant_name}`.* TO %s@localhost', (operator,))
    private = Path(owner.pw_dir) / '.local/state/inventraiq-phase1' / suffix
    private.mkdir(parents=True, mode=0o700)
    os.chown(private, owner.pw_uid, owner.pw_gid)
    credentials = private / 'credentials.json'
    payload = dict(schema=names[0], recovery=names[1], user=app_user, password=app_password)
    fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(payload, f)
    os.chown(credentials, owner.pw_uid, owner.pw_gid)
    evidence = dict(version=version, version_comment=comment, isolation=isolation,
                    schemas=names, application_user=app_user, operator_user=operator,
                    credential_file=str(credentials), migrations=[], tests=None)
    # Existing metadata initialization; omit T27 so its reviewed additive artifact
    # can be rehearsed against the supported previous-schema fixture.
    from database import db
    import models
    from assistant.proposal_models import OperationProposal, ProposalAudit
    # Binary-logged MySQL requires privileged trigger creation. Use the existing
    # socket administrator for DDL, never weaken server settings or app grants.
    url = URL.create('mysql+pymysql', username='root',
                     host='localhost', database=names[0], query={'unix_socket': '/var/run/mysqld/mysqld.sock'})
    engine = create_engine(url)
    assert inspect(engine).get_table_names() == []
    tables = [t for t in db.metadata.sorted_tables if t.name not in ('operation_proposals', 'proposal_audit')]
    db.metadata.create_all(engine, tables=tables)
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO products(product_id,name,version,archived) VALUES(900001,'Phase1-preserved',1,0)")
    # The standard client handles DELIMITER directives. Operator secrets go only
    # in a private defaults file, never argv or output.
    defaults = private / 'operator.cnf'
    fd = os.open(defaults, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('[client]\nuser=root\nsocket=/var/run/mysqld/mysqld.sock\n')
    for artifact in ('t27_v001_expand_mysql.sql', 't14_v001_expand_mysql.sql',
                     't15_v001_expand_mysql.sql', 't16_v001_indexes_mysql.sql', 't20_v001_expand_mysql.sql'):
        raw = (ROOT / 'migrations' / artifact).read_bytes()
        result = subprocess.run(['mysql', '--defaults-extra-file=' + str(defaults), names[0]],
                                input=raw, capture_output=True)
        if result.returncode:
            evidence['migration_failure'] = artifact
            evidence['mysql_error'] = result.stderr.decode(errors='replace').replace(operator_password, '[redacted]')
            break
        evidence['migrations'].append(dict(artifact=artifact, sha256=hashlib.sha256(raw).hexdigest()))
        with engine.begin() as c:
            c.exec_driver_sql('INSERT INTO schema_migrations(version,checksum,applied_at) VALUES(%s,%s,UTC_TIMESTAMP())',
                              (artifact.removesuffix('_expand_mysql.sql').removesuffix('_indexes_mysql.sql'), hashlib.sha256(raw).hexdigest()))
    defaults.unlink()  # Only the newly created temporary operator credential file.
    engine.dispose()
    with admin.cursor() as c:
        validation = {}
        for label, table in (('tables', 'TABLES'), ('constraints', 'TABLE_CONSTRAINTS'), ('indexes', 'STATISTICS'), ('foreign_keys', 'REFERENTIAL_CONSTRAINTS')):
            column = 'CONSTRAINT_SCHEMA' if table == 'REFERENTIAL_CONSTRAINTS' else 'TABLE_SCHEMA'
            c.execute(f'SELECT COUNT(*) FROM information_schema.{table} WHERE {column}=%s', (names[0],))
            validation[label] = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s AND LEFT(TRIGGER_NAME,4)='t27_'", (names[0],))
        validation['t27_triggers'] = c.fetchone()[0]
        assert validation['t27_triggers'] == 5
        c.execute('SHOW GRANTS FOR %s@localhost', (app_user,))
        evidence['application_grants'] = [r[0] for r in c.fetchall()]
        evidence['schema_validation'] = validation
    output = ROOT / 'data/private/phase1' / suffix
    output.mkdir(parents=True)
    if any(m['artifact'] == 't27_v001_expand_mysql.sql' for m in evidence['migrations']):
        result = subprocess.run(['runuser', '-u', owner.pw_name, '--', sys.executable, '-B', str(Path(__file__).resolve()),
                                 '--tests', str(credentials)], capture_output=True, text=True)
        (output / 'mysql-tests.log').write_text(result.stdout + result.stderr)
        evidence['tests_exit_code'] = result.returncode
        for line in result.stdout.splitlines():
            if line.startswith('PHASE1_RESULT='):
                evidence['tests'] = json.loads(line.split('=', 1)[1])
        # Restore a transactionally consistent dump to a different empty target.
        backup = subprocess.run(['mysqldump', '--user=root', '--socket=/var/run/mysqld/mysqld.sock',
                                 '--single-transaction', '--no-tablespaces', '--skip-add-drop-table',
                                 '--skip-add-locks', '--set-gtid-purged=OFF', names[0]], capture_output=True)
        assert backup.returncode == 0, 'Disposable backup failed'
        restored = subprocess.run(['mysql', '--user=root', '--socket=/var/run/mysqld/mysqld.sock', names[1]],
                                  input=backup.stdout, capture_output=True)
        assert restored.returncode == 0, 'Disposable recovery failed'
        with admin.cursor() as c:
            counts = {}
            for schema in names:
                c.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE=%s', (schema, 'BASE TABLE'))
                tables = [r[0] for r in c.fetchall()]
                counts[schema] = {}
                for table in tables:
                    c.execute(f'SELECT * FROM `{schema}`.`{table}`')
                    rows = sorted(json.dumps(row, default=str, separators=(',', ':')) for row in c.fetchall())
                    counts[schema][table] = dict(rows=len(rows), sha256=hashlib.sha256('\n'.join(rows).encode()).hexdigest())
                c.execute('SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s', (schema,))
                counts[schema]['[triggers]'] = c.fetchone()[0]
            assert counts[names[0]] == counts[names[1]], 'Recovery reconciliation mismatch'
            evidence['recovery'] = dict(passed=True, tables=len(counts[names[0]]) - 1, triggers=counts[names[0]]['[triggers]'], counts=counts[names[0]])
    admin.close()
    (output / 'summary.json').write_text(json.dumps(evidence, indent=2) + '\n')
    displayed = dict(evidence)
    if 'recovery' in displayed:
        displayed['recovery'] = {k: v for k, v in evidence['recovery'].items() if k != 'counts'}
    print(json.dumps(displayed, indent=2))
    return int('migration_failure' in evidence or evidence.get('tests_exit_code', 1) != 0)


def run_tests(credential_path):
    credentials = Path(credential_path)
    assert credentials.stat().st_mode & 0o077 == 0, 'Credentials must be private'
    cfg = json.loads(credentials.read_text())
    assert cfg['schema'].startswith('inventraiq_phase1_')
    os.environ.update(PYTHON_DOTENV_DISABLED='1', APP_ENV='testing',
                      DB_HOST='localhost', DB_USER=cfg['user'], DB_PASSWORD=cfg['password'], DB_NAME=cfg['schema'],
                      ASSISTANT_PROPOSALS_ENABLED='false', SECRET_KEY=secrets.token_hex(32),
                      OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    os.environ.pop('DATABASE_URL', None)
    from app import app
    from database import db
    from models import User, Branch, UserHQScope, UserBranch, BranchStock, StockReceipt, InventoryMovement, InventoryOperation, InventoryDocument, Product, Inventory, SKU
    from sqlalchemy import text, select, func, inspect
    from sqlalchemy.exc import DBAPIError
    from unittest.mock import patch
    from datetime import datetime
    from expiry import UTC
    from readiness import database_readiness
    now = datetime(2030, 1, 1, tzinfo=UTC)
    password = secrets.token_urlsafe(24)
    app.config.update(TESTING=True)
    connection_evidence = {}
    concurrency_evidence = {}
    with app.app_context():
        assert db.engine.dialect.name == 'mysql' and db.engine.url.database == cfg['schema']
        with db.engine.connect() as c:
            row = c.exec_driver_sql('SELECT DATABASE(), CURRENT_USER(), VERSION(), @@transaction_isolation').one()
            assert row[0] == cfg['schema'] and row[1] == cfg['user'] + '@localhost'
            connection_evidence.update(schema=row[0], principal=row[1], version=row[2], isolation=row[3], dialect='mysql')
        db.session.add_all([Branch(branch_id=i, code=f'P1-{i}', display_name=f'Phase1 {i}', timezone='Asia/Singapore') for i in (1, 2, 3)])
        for i in range(1, 7):
            user = User(user_id=i, name=f'Synthetic {i}', email=f'p1-{i}@example.invalid', role='admin' if i in (1, 5) else 'staff')
            user.set_password(password)
            db.session.add(user)
        db.session.flush()
        db.session.add_all([UserHQScope(user_id=1, hq_role='admin'), UserHQScope(user_id=2, hq_role='analyst'), UserHQScope(user_id=5, hq_role='admin'),
                            UserBranch(user_id=3, branch_id=1, branch_role='manager'), UserBranch(user_id=4, branch_id=1, branch_role='clerk'),
                            UserBranch(user_id=6, branch_id=2, branch_role='manager')])
        db.session.commit()
        tokens = {i: db.session.get(User, i).get_id() for i in range(1, 7)}

    def client(uid=1, branch=1):
        from flask import g
        g.pop('_login_user', None)
        result = app.test_client()
        with result.session_transaction() as state:
            state.update(_user_id=tokens[uid], _fresh=True, _csrf_token='phase1-csrf', active_branch_id=branch)
        return result

    class MysqlTests(unittest.TestCase):
        def setUp(self):
            self.context = app.app_context()
            self.context.push()
            self.addCleanup(self.context.pop)
            for module in ('inventory_ledger', 'receiving_services', 'transfer_services', 'transfer_execution'):
                try:
                    p = patch(module + '.utcnow', return_value=now)
                    p.start(); self.addCleanup(p.stop)
                except (ModuleNotFoundError, AttributeError):
                    pass
            self.key = self._testMethodName
            if self.key == 'test_01_connection_schema_constraints_and_privileges':
                self.request('put', '/api/expiry-settings', dict(label_timezone='Asia/Singapore', expected_version=0, reason='Synthetic label timezone'))

        def request(self, method, path, payload=None, uid=1, branch=1, expected=200):
            response = getattr(client(uid, branch), method)(path, json=payload, headers={'X-CSRF-Token': 'phase1-csrf'})
            self.assertEqual(response.status_code, expected, (path, response.status_code, response.get_json()))
            return response.get_json()

        def fixture(self, quantity=8):
            category = self.request('post', '/api/categories', dict(code=self.key, display_name=self.key, reason='Synthetic Phase1'))['category_id']
            policy = dict(require_expiry=True, early_days=7, high_days=3, critical_days=1, expected_shelf_life_days=30,
                          min_remaining_days_on_receipt=1, min_remaining_days_on_transfer_receipt=1)
            self.request('put', f'/api/expiry-policies/category/{category}', dict(policy=policy, expected_revision=0, reason='Synthetic policy'))
            sku = self.request('post', '/api/skus', dict(code=self.key, name=self.key, category_id=category, default_price='2.50', reason='Synthetic SKU'))['sku_id']
            for branch in (1, 2):
                self.request('put', f'/api/assortments/{branch}/{sku}', dict(enabled=True, expected_version=0, reason='Synthetic assortment'))
            payload = dict(mode='new_lot', sku_id=sku, lot_code=self.key, supplier_lot_code=self.key, quantity=quantity,
                           expiry_date='2030-01-08', external_reference=self.key, reason='Synthetic delivery', evidence='Synthetic label fixture')
            receipt = self.request('post', '/api/receipts', payload, uid=3)
            return sku, receipt, payload

        def count(self, model):
            return db.session.scalar(select(func.count()).select_from(model))

        def stock(self, lot, branch=1):
            db.session.expire_all()
            return db.session.get(BranchStock, (branch, lot))

        def test_01_connection_schema_constraints_and_privileges(self):
            self.assertEqual(db.engine.dialect.name, 'mysql')
            report = database_readiness(db.engine, db.metadata, allow_mysql=True)
            self.assertEqual(report['status'], 'ready', report)
            with db.engine.connect() as c:
                self.assertEqual(c.exec_driver_sql("SELECT name FROM products WHERE product_id=900001").scalar(), 'Phase1-preserved')
                self.assertTrue(inspect(c).has_table('operation_proposals'))
                self.assertEqual(c.exec_driver_sql("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND ENGINE <> 'InnoDB'").scalar(), 0)
                self.assertEqual(c.exec_driver_sql("SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_TYPE='CHECK' AND ENFORCED='NO'").scalar(), 0)
                for statement, code in (('CREATE TABLE phase1_forbidden(id INT)', 1142), ("INSERT INTO inventory(product_id,quantity) VALUES(999999,1)", 1452), ("INSERT INTO inventory(product_id,quantity) VALUES(900001,-1)", 3819)):
                    with self.assertRaises(DBAPIError) as caught:
                        c.exec_driver_sql(statement)
                    self.assertEqual(caught.exception.orig.args[0], code)
                    c.rollback()
            self.assertFalse(app.config['ASSISTANT_PROPOSALS_ENABLED'])

        def test_02_actual_login_and_branch_role_isolation(self):
            browser = app.test_client()
            with browser.session_transaction() as state:
                state['_csrf_token'] = 'phase1-csrf'
            response = browser.post('/login/staff', data=dict(email='p1-3@example.invalid', password=password, csrf_token='phase1-csrf'))
            self.assertIn(response.status_code, (302, 303))
            with browser.session_transaction() as state:
                self.assertIn('_user_id', state)
            sku, receipt, payload = self.fixture()
            self.request('post', '/api/receipts', dict(payload, external_reference='denied-' + self.key), uid=2, expected=403)
            self.request('post', '/api/receipts', dict(payload, branch_id=2), uid=3, expected=403)
            source = self.request('get', '/api/sku-stock', uid=3)
            destination = self.request('get', '/api/sku-stock', uid=6, branch=2)
            self.assertIn(receipt['lot_id'], [r['lot_id'] for r in source['items']])
            self.assertNotIn(receipt['lot_id'], [r['lot_id'] for r in destination['items']])

        def test_03_receipt_sale_idempotency_and_persistence(self):
            sku, receipt, payload = self.fixture()
            self.assertTrue(self.request('post', '/api/receipts', payload, uid=3)['replayed'])
            sale = dict(sku_id=sku, quantity=3, request_key=self.key, business_reference=self.key, reason='Synthetic sale')
            self.request('post', '/api/sku-sales', sale, uid=3)
            self.assertTrue(self.request('post', '/api/sku-sales', sale, uid=3)['replayed'])
            self.request('post', '/api/sku-sales', dict(sale, quantity=4), uid=3, expected=409)
            self.assertEqual(self.stock(receipt['lot_id']).on_hand, 5)
            with db.engine.connect() as c:
                self.assertEqual(c.execute(select(SKU.code).where(SKU.sku_id == sku)).scalar(), self.key.upper())
                self.assertEqual(c.execute(select(BranchStock.on_hand).where(BranchStock.lot_id == receipt['lot_id'])).scalar(), 5)

        def test_04_audit_failure_rolls_back_ledger_and_business(self):
            sku, receipt, _ = self.fixture()
            before = [self.count(m) for m in (InventoryOperation, InventoryMovement, InventoryDocument)]
            db.session.rollback()
            with patch('catalog_services._record', side_effect=RuntimeError('Synthetic Phase1 audit failure')):
                self.request('post', '/api/sku-sales', dict(sku_id=sku, quantity=2, request_key=self.key, business_reference=self.key, reason='Synthetic sale'), uid=3, expected=500)
            self.assertEqual(self.stock(receipt['lot_id']).on_hand, 8)
            self.assertEqual([self.count(m) for m in (InventoryOperation, InventoryMovement, InventoryDocument)], before)

        def transfer(self):
            sku, receipt, _ = self.fixture()
            t = self.request('post', '/api/transfers', dict(destination_branch_id=2, planned_arrival_utc='2030-01-02T00:00:00Z',
                             lines=[dict(sku_id=sku, quantity=3)], request_key=self.key, business_reference=self.key, reason='Synthetic transfer'), uid=3)
            t = self.action(t, 'submit', uid=3)
            t = self.action(t, 'approve')
            return t, receipt

        def action(self, t, action, uid=1, branch=1, **extra):
            payload = dict(expected_version=t['version'], request_key=self.key + action, business_reference=self.key, reason='Synthetic ' + action)
            payload.update(extra)
            return self.request('post', f"/api/transfers/{t['transfer_id']}/{action}", payload, uid=uid, branch=branch)

        def test_05_transfer_approve_dispatch_receive(self):
            t, receipt = self.transfer()
            self.assertEqual(self.stock(receipt['lot_id']).reserved, 3)
            t = self.action(t, 'dispatch', uid=3, lines=[dict(allocation_id=a['allocation_id'], expected_revision=self.stock(a['lot_id']).version) for a in t['reservations']])
            self.assertEqual(t['state'], 'Dispatched')
            t = self.action(t, 'receive', uid=6, branch=2, lines=[dict(allocation_id=t['transit'][0]['allocation_id'], quantity=3, expected_revision=0, disposition='saleable')])
            self.assertEqual(t['state'], 'Received')
            self.assertEqual(self.stock(receipt['lot_id']).on_hand, 5)
            self.assertEqual(self.stock(receipt['lot_id'], 2).on_hand, 3)

        def test_06_transfer_cancel_releases_reservations(self):
            t, receipt = self.transfer()
            t = self.action(t, 'cancel')
            self.assertEqual(t['state'], 'Cancelled')
            self.assertEqual((self.stock(receipt['lot_id']).on_hand, self.stock(receipt['lot_id']).reserved), (8, 0))

        def compete(self, requests):
            from concurrent.futures import ThreadPoolExecutor
            from threading import Barrier
            barrier = Barrier(2)
            db.session.rollback()
            def run(item):
                path, data = item
                with app.app_context():
                    connection = db.engine.connect()
                    try:
                        self.assertEqual(connection.exec_driver_sql('SELECT DATABASE()').scalar(), cfg['schema'])
                    finally:
                        connection.close()
                    browser = client(3)
                    barrier.wait(timeout=15)
                    return browser.post(path, json=data, headers={'X-CSRF-Token': 'phase1-csrf'}).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(run, requests))

        def test_07_concurrent_same_receipt_has_one_effect(self):
            sku, receipt, payload = self.fixture()
            payload = dict(mode='existing_lot', sku_id=sku, lot_id=receipt['lot_id'], quantity=4, expiry_date='2030-01-08',
                           external_reference='race-' + self.key, reason='Synthetic concurrent delivery', evidence='Synthetic label')
            statuses = self.compete([('/api/receipts', payload)] * 2)
            concurrency_evidence['duplicate_receipt_http_statuses'] = statuses
            self.assertIn(200, statuses)
            self.assertTrue(all(s in (200, 409) for s in statuses), statuses)
            self.assertEqual(self.stock(receipt['lot_id']).on_hand, 12)
            self.assertEqual(db.session.scalar(select(func.count()).select_from(StockReceipt).where(StockReceipt.external_reference == payload['external_reference'])), 1)

        def test_08_conflicting_sales_never_oversell(self):
            sku, receipt, _ = self.fixture(quantity=5)
            data = dict(sku_id=sku, quantity=4, business_reference=self.key, reason='Synthetic contention')
            statuses = self.compete([('/api/sku-sales', dict(data, request_key=self.key + str(i))) for i in (1, 2)])
            concurrency_evidence['conflicting_sales_http_statuses'] = statuses
            self.assertEqual(sorted(statuses), [200, 409])
            self.assertEqual(self.stock(receipt['lot_id']).on_hand, 1)

        def test_09_transaction_rollback_and_immutable_ledger(self):
            sku, receipt, _ = self.fixture()
            with db.engine.connect() as c:
                c.execute(Product.__table__.insert().values(name='Phase1 rolled back', version=1, archived=False))
                c.rollback()
                self.assertEqual(c.exec_driver_sql("SELECT COUNT(*) FROM products WHERE name='Phase1 rolled back'").scalar(), 0)
                with self.assertRaises(DBAPIError):
                    c.exec_driver_sql('UPDATE inventory_movements SET quantity_delta=quantity_delta+1 WHERE lot_id=%s', (receipt['lot_id'],))
                c.rollback()

        def test_10_final_ledger_reconciliation(self):
            from flask_login import login_user
            import inventory_ledger as ledger
            with app.test_request_context():
                from flask import session
                login_user(db.session.get(User, 1))
                rows = ledger.reconcile()
                self.assertTrue(rows)
                self.assertTrue(all(row['balanced'] for row in rows), rows)

    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MysqlTests))
    summary = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                   elapsed_seconds=round(time.monotonic() - started, 3), connection=connection_evidence, concurrency=concurrency_evidence)
    print('PHASE1_RESULT=' + json.dumps(summary))
    return int(not result.wasSuccessful())


def sqlite_tests():
    """Existing focused regressions, with explicit SQLite and private evidence."""
    os.environ.update(PYTHON_DOTENV_DISABLED='1', APP_ENV='testing', DATABASE_URL='sqlite:///:memory:',
                      PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
                      ASSISTANT_PROPOSALS_ENABLED='false')
    sys.path.insert(0, str(ROOT / 'tests'))
    modules = ['test_database_configuration', 'test_catalog_receiving', 'test_inventory_ledger',
               'test_concurrent_receiving', 'test_transfers', 'test_transfer_execution', 'test_t27_migration',
               'test_operational_reporting']
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    output = ROOT / 'data/private/phase1' / ('sqlite-' + time.strftime('%Y%m%d') + '-' + secrets.token_hex(4))
    output.mkdir(parents=True)
    started = time.monotonic()
    with (output / 'tests.log').open('w') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    summary = dict(modules=modules, tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped), elapsed_seconds=round(time.monotonic() - started, 3), evidence=str(output))
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))
    return int(not result.wasSuccessful())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--provision', action='store_true')
    group.add_argument('--tests', type=Path)
    group.add_argument('--sqlite', action='store_true')
    args = parser.parse_args()
    raise SystemExit(provision() if args.provision else sqlite_tests() if args.sqlite else run_tests(args.tests))
