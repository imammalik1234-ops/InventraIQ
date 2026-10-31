"""Fresh-only singleton migration rehearsal; uses existing metadata/SQL registry.

WSL root socket access is required for --mysql. No existing target is adopted,
no historical checksum is updated, and a failed prerequisite stops the sequence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify_registry(connection, expected):
    actual = dict(connection.exec_driver_sql('SELECT version,checksum FROM schema_migrations').all())
    if actual != expected:
        raise RuntimeError('Migration registry divergence or existing target; stop without changes')


def verify_bytes(path, digest):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RuntimeError('Migration artifact checksum divergence: ' + path.name)
    return raw


def operator_owner():
    """Resolve the explicitly selected or sudo-invoking non-root Unix operator."""
    import pwd
    name = os.environ.get('INVENTRAIQ_OPERATOR_USER') or os.environ.get('SUDO_USER')
    if not name:
        raise RuntimeError('Run via sudo or set INVENTRAIQ_OPERATOR_USER to a non-root operator.')
    try:
        owner = pwd.getpwnam(name)
    except KeyError:
        raise RuntimeError('The selected operator must be an existing Unix account.') from None
    if owner.pw_uid == 0 or not Path(owner.pw_dir).is_absolute():
        raise RuntimeError('The selected operator must be non-root with an absolute home directory.')
    return owner


def mysql_rehearsal():
    import subprocess
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.engine import URL
    from sqlalchemy.exc import DBAPIError
    from database import db
    import models
    import assistant.proposal_models
    from scripts.render_t06_schema import TABLES
    from scripts.render_phase1_singletons import main as check_artifacts
    if os.geteuid() != 0:
        raise RuntimeError('Requires existing approved WSL root socket operator')
    owner = operator_owner()
    check_artifacts(check=True)
    manifest = json.loads((ROOT / 'migrations/phase1_singleton_compatibility.json').read_text())
    suffix = time.strftime('%Y%m%d') + '_' + secrets.token_hex(4)
    name, user = 'inventraiq_phase1_singletons_' + suffix, 'iqp1s_' + secrets.token_hex(4)
    password = secrets.token_urlsafe(32)
    socket = '/var/run/mysqld/mysqld.sock'
    operator = create_engine(URL.create('mysql+pymysql', username='root', host='localhost', query={'unix_socket': socket}), hide_parameters=True)
    with operator.connect() as c:
        version, comment, isolation, literal_grants = c.exec_driver_sql('SELECT VERSION(),@@version_comment,@@transaction_isolation,@@partial_revokes').one()
        assert 'mariadb' not in (version + comment).lower() and tuple(map(int, version.split('-')[0].split('.'))) >= (8, 0, 16)
        assert c.exec_driver_sql('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (name,)).scalar() == 0
        assert c.exec_driver_sql('SELECT COUNT(*) FROM mysql.user WHERE User=%s', (user,)).scalar() == 0
        c.exec_driver_sql(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
        c.exec_driver_sql('CREATE USER %s@localhost IDENTIFIED BY %s', (user, password))
        grant_name = name if literal_grants else name.replace('_', r'\_')
        c.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE,DELETE ON `{grant_name}`.* TO %s@localhost', (user,))
        c.commit()
    operator.dispose()
    private = Path(owner.pw_dir) / '.local/state/inventraiq-phase1' / ('singletons-' + suffix)
    private.mkdir(parents=True, mode=0o700)
    os.chown(private, owner.pw_uid, owner.pw_gid)
    credential_path = private / 'credentials.json'
    descriptor = os.open(credential_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(dict(schema=name, user=user, password=password), stream)
    os.chown(credential_path, owner.pw_uid, owner.pw_gid)
    engine = create_engine(URL.create('mysql+pymysql', username='root', host='localhost', database=name, query={'unix_socket': socket}), hide_parameters=True)
    application = create_engine(URL.create('mysql+pymysql', username=user, password=password, host='localhost', database=name, query={'unix_socket': socket}), hide_parameters=True)
    output = ROOT / 'data/private/phase1' / ('singletons-' + suffix)
    output.mkdir(parents=True)
    evidence = dict(schema=name, version=version, version_comment=comment, isolation=isolation,
                    applied=[], singleton_tests=[], result='BLOCKED', ai_proposals_enabled=False)
    try:
        assert inspect(engine).get_table_names() == []
        # Build a previous-T06 fixture by excluding T06 and all FK dependents.
        # Fresh latest metadata after T06 then supplies downstream operational
        # prerequisites, without replaying obsolete operational expansions.
        deferred = set(TABLES) | {'operation_proposals', 'proposal_audit'}
        while True:
            enlarged = deferred | {t.name for t in db.metadata.tables.values()
                                    if any(f.column.table.name in deferred for f in t.foreign_keys)}
            if enlarged == deferred:
                break
            deferred = enlarged
        db.metadata.create_all(engine, tables=[t for t in db.metadata.sorted_tables if t.name not in deferred])
        with engine.begin() as c:
            c.exec_driver_sql("INSERT INTO products(product_id,name,version,archived) VALUES(900002,'Singleton preserved fixture',1,0)")
        expected = {}
        plan = ['t06_v002_expand_mysql.sql', 't14_v002_expand_mysql.sql',
                't15_v002_expand_mysql.sql', 't16_v001_indexes_mysql.sql',
                't20_v002_expand_mysql.sql', 't27_v001_expand_mysql.sql']
        evidence['planned_sequence'] = plan
        sequence_complete = False
        for filename in plan:
            path = ROOT / 'migrations' / filename
            digest = manifest['fresh_mysql'].get(filename)
            if digest is None:
                digest = manifest['historical'][filename]
            raw = verify_bytes(path, digest)
            with engine.connect() as c:
                verify_registry(c, expected)
            applied = subprocess.run(['mysql', '--user=root', '--socket=' + socket, name], input=raw, capture_output=True)
            if applied.returncode:
                evidence['blocked_at'] = filename
                evidence['mysql_error'] = applied.stderr.decode(errors='replace')
                break
            if filename.startswith('t06_'):
                db.metadata.create_all(engine, tables=[t for t in db.metadata.sorted_tables if t.name not in ('operation_proposals', 'proposal_audit')])
            singleton = None
            if filename.startswith('t06_'):
                singleton = ('company_expiry_settings', 'settings_id', "label_timezone,version", "'UTC',1")
            elif filename.startswith('t14_'):
                singleton = ('historical_demo_target', 'singleton', 'token_hash,purpose', "'synthetic-only','phase1-singleton-test'")
            elif filename.startswith('t15_'):
                singleton = ('simulation_runs', 'run_id', 'manifest_hash,manifest_json,code_hash,status,completed_steps,history_hash',
                             "'phase1-synthetic','{}','phase1-code','running',0,'phase1-history'")
            elif filename.startswith('t20_'):
                singleton = ('forecast_demo_target', 'singleton', 'purpose', "'phase1-singleton-test'")
            if singleton:
                table, key, other_columns, values = singleton
                with application.connect() as c:
                    identity = c.exec_driver_sql('SELECT DATABASE(),CURRENT_USER()').one()
                    assert identity == (name, user + '@localhost')
                    evidence['connection'] = dict(dialect=application.dialect.name, schema=identity[0], principal=identity[1])
                for value in (-1, 0, 2, None):
                    try:
                        with application.begin() as c:
                            c.exec_driver_sql(f'INSERT INTO {table}({key},{other_columns}) VALUES(%s,{values})', (value,))
                    except DBAPIError as error:
                        code = error.orig.args[0]
                        assert code == (1048 if value is None else 3819), (table, value, code)
                        evidence['singleton_tests'].append(dict(table=table, rejected_id=value, mysql_error=code))
                    else:
                        raise AssertionError('Invalid singleton accepted')
                with application.begin() as c:
                    c.exec_driver_sql(f'INSERT INTO {table}({key},{other_columns}) VALUES(1,{values})')
                try:
                    with application.begin() as c:
                        c.exec_driver_sql(f'INSERT INTO {table}({key},{other_columns}) VALUES(1,{values})')
                except DBAPIError as error:
                    assert error.orig.args[0] == 1062
                    evidence['singleton_tests'].append(dict(table=table, accepted_id=1, duplicate_error=1062))
                else:
                    raise AssertionError('Duplicate singleton accepted')
            version_id = filename.removesuffix('_mysql.sql')
            with engine.begin() as c:
                verify_registry(c, expected)
                c.exec_driver_sql('INSERT INTO schema_migrations(version,checksum,applied_at) VALUES(%s,%s,UTC_TIMESTAMP())', (version_id, digest))
            expected[version_id] = digest
            evidence['applied'].append(dict(artifact=filename, version=version_id, sha256=digest))
        else:
            sequence_complete = True
        with engine.connect() as c:
            verify_registry(c, expected)
            assert c.exec_driver_sql('SELECT name FROM products WHERE product_id=900002').scalar() == 'Singleton preserved fixture'
            evidence['registry'] = expected
            evidence['tables'] = inspect(c).get_table_names()
            evidence['foreign_key_count'] = c.exec_driver_sql('SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()').scalar()
            evidence['t27_prerequisite_tables'] = {t: inspect(c).has_table(t) for t in ('branches', 'inventory_operations')}
            trigger_counts = {}
            for prefix, count in (('t14_', 6), ('t15_', 2), ('t20_', 11), ('t27_', 5)):
                if any(item['artifact'].startswith(prefix[:3]) for item in evidence['applied']):
                    actual = c.exec_driver_sql('SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND LEFT(TRIGGER_NAME,4)=%s', (prefix,)).scalar()
                    assert actual == count, (prefix, actual)
                    trigger_counts[prefix] = actual
            evidence['trigger_counts'] = trigger_counts
            if sequence_complete:
                indexes = set(c.exec_driver_sql('SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE()').scalars())
                required = {'ix_t16_sale_branch_id_date', 'ix_t16_observation_run_branch_id_date'}
                assert required <= indexes
                evidence['t16_indexes'] = sorted(required)
        if sequence_complete:
            from readiness import database_readiness
            report = database_readiness(application, db.metadata, allow_mysql=True)
            assert report['status'] == 'ready', report
            evidence['readiness'] = report
            evidence['result'] = 'PASS'
        return int(evidence['result'] != 'PASS')
    finally:
        engine.dispose()
        application.dispose()
        (output / 'summary.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mysql', action='store_true', required=True)
    parser.parse_args()
    raise SystemExit(mysql_rehearsal())
