"""Read-only schema diagnostics. Never create tables, migrate, or fall back."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import click
from sqlalchemy import Boolean, Enum, CheckConstraint, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.dialects.mysql import TINYINT, ENUM
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from database import db


def normalized(expression):
    # MySQL reflects character-set introducers on literal CHECK operands.
    expression = re.sub(r"\b_(?:utf8mb4|utf8mb3|utf8|latin1|ascii)(?=')", '', str(expression), flags=re.I)
    return re.sub(r'[\s`"()]', '', expression).lower()


@contextmanager
def inspection_connection(engine):
    """File SQLite uses a separate mode=ro connection, even if app URL is rw."""
    temporary_engine = None
    if engine.dialect.name == 'sqlite' and engine.url.database not in {None, '', ':memory:'}:
        # Refuse SQLite URI options here: resolve only ordinary file URLs safely.
        if engine.url.query or engine.url.database.startswith('file:'):
            raise ValueError('unsupported_sqlite_uri')
        path = Path(engine.url.database).resolve()
        if not path.is_file():
            raise FileNotFoundError()
        temporary_engine = create_engine('sqlite://', poolclass=NullPool,
            creator=lambda: sqlite3.connect(path.as_uri() + '?mode=ro', uri=True))
    try:
        with (temporary_engine or engine).connect() as connection:
            if engine.dialect.name == 'sqlite':
                previous_query_only = int(connection.exec_driver_sql('PRAGMA query_only').scalar_one())
                connection.exec_driver_sql('PRAGMA query_only=ON')
            else:
                connection.exec_driver_sql('SET SESSION TRANSACTION READ ONLY')
            try:
                yield connection
            finally:
                connection.rollback()
                if temporary_engine is None and engine.dialect.name == 'sqlite':
                    # A pooled in-memory app connection must remain usable afterward.
                    connection.exec_driver_sql('PRAGMA query_only=' + str(previous_query_only))
                    connection.rollback()
                elif engine.dialect.name == 'mysql':
                    # Do not return a session-read-only connection to app writers.
                    connection.invalidate()
    finally:
        if temporary_engine is not None:
            temporary_engine.dispose()


def database_readiness(engine, metadata, allow_mysql=False):
    backend = engine.dialect.name
    if backend == 'mysql' and not allow_mysql:
        return {'status': 'not_checked', 'backend': backend,
                'message': 'MySQL inspection requires explicit --allow-mysql; no connection attempted.'}
    report = {'status': 'ready', 'backend': backend}
    try:
        with inspection_connection(engine) as connection:
            if backend == 'sqlite':
                report['foreign_keys'] = connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one()
                if report['foreign_keys'] != 1:
                    return dict(report, status='incompatible', message='SQLite foreign keys are disabled.')
                # Counts only; never emit row contents or personal information.
                if connection.exec_driver_sql('PRAGMA foreign_key_check').first() is not None:
                    report['foreign_key_violations'] = True
            else:
                report['foreign_keys'] = int(connection.exec_driver_sql('SELECT @@SESSION.foreign_key_checks').scalar_one())
                if report['foreign_keys'] != 1:
                    return dict(report, status='incompatible', message='MySQL foreign-key checks are disabled.')
                version = str(connection.execute(text('SELECT VERSION()')).scalar_one())
                numbers = re.match(r'^(\d+)\.(\d+)\.(\d+)', version)
                if 'mariadb' in version.lower() or not numbers or tuple(map(int, numbers.groups())) < (8, 0, 16):
                    return dict(report, status='incompatible', message='Requires MySQL >=8.0.16; MariaDB is not verified.')
                engines = dict(connection.exec_driver_sql(
                    'SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()').all())
                report['non_innodb_tables'] = sorted(name for name in metadata.tables
                                                     if name in engines and engines[name] != 'InnoDB')
                report['unenforced_checks'] = sorted(row[1] for row in connection.exec_driver_sql(
                    "SELECT TABLE_NAME, CONSTRAINT_NAME, ENFORCED FROM information_schema.TABLE_CONSTRAINTS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND CONSTRAINT_TYPE = 'CHECK'").all()
                    if row[0] in metadata.tables and row[2] != 'YES')
            schema = inspect(connection)
            report['missing_tables'] = []
            report['schema_gaps'] = {}
            for table in metadata.sorted_tables:
                if not schema.has_table(table.name):
                    report['missing_tables'].append(table.name)
                    continue
                columns = {c['name']: c for c in schema.get_columns(table.name)}
                gaps = []
                if tuple(schema.get_pk_constraint(table.name)['constrained_columns']) != tuple(table.primary_key.columns.keys()):
                    gaps.append('primary_key')
                for col in table.columns:
                    actual = columns.get(col.name)
                    if actual is None:
                        gaps.append('missing_column:' + col.name)
                    else:
                        if not col.nullable and not col.primary_key and actual['nullable']:
                            gaps.append('nullable_column:' + col.name)
                        reflected = actual['type']
                        mysql_boolean = backend == 'mysql' and isinstance(col.type, Boolean) and isinstance(reflected, TINYINT) and reflected.display_width == 1
                        mysql_enum = backend == 'mysql' and isinstance(col.type, Enum) and isinstance(reflected, ENUM) and col.type.enums == reflected.enums
                        if col.type._type_affinity != reflected._type_affinity and not (mysql_boolean or mysql_enum):
                            gaps.append('column_type:' + col.name)
                uniques = {tuple(c['column_names']) for c in schema.get_unique_constraints(table.name)}
                uniques.update(tuple(c['column_names']) for c in schema.get_indexes(table.name) if c['unique'])
                checks = {normalized(c['sqltext']) for c in schema.get_check_constraints(table.name)}
                for constraint in table.constraints:
                    if isinstance(constraint, UniqueConstraint) and tuple(constraint.columns.keys()) not in uniques:
                        gaps.append('missing_unique:' + ','.join(constraint.columns.keys()))
                    if isinstance(constraint, CheckConstraint) and normalized(constraint.sqltext) not in checks:
                        gaps.append('missing_check:' + str(constraint.name))
                foreign_keys = {(tuple(f['constrained_columns']), f['referred_table'], tuple(f['referred_columns']))
                                for f in schema.get_foreign_keys(table.name)}
                for fk in table.foreign_key_constraints:
                    expected = (tuple(fk.columns.keys()), fk.referred_table.name,
                                tuple(element.column.name for element in fk.elements))
                    if expected not in foreign_keys:
                        gaps.append('missing_foreign_key:' + ','.join(fk.columns.keys()))
                if gaps:
                    report['schema_gaps'][table.name] = gaps
            if any(report.get(key) for key in ('missing_tables', 'schema_gaps', 'foreign_key_violations',
                                               'non_innodb_tables', 'unenforced_checks')):
                report['status'] = 'incompatible'
    except FileNotFoundError:
        return dict(report, status='missing_database', message='SQLite database file does not exist; nothing was created.')
    except ValueError:
        return dict(report, status='not_checked', message='Readiness supports ordinary SQLite file URLs, not SQLite URI options.')
    except (SQLAlchemyError, sqlite3.Error, OSError):
        return dict(report, status='unavailable', message='Database inspection failed. Check connectivity, access and configuration; no fallback was attempted.')
    return report


def register_readiness(app):
    @app.cli.command('db-readiness')
    @click.option('--allow-mysql', is_flag=True, help='Explicitly allow read-only inspection of the configured MySQL target.')
    def readiness_command(allow_mysql):
        """Check current model compatibility without creating or upgrading a database."""
        report = database_readiness(db.engine, db.metadata, allow_mysql)
        click.echo(json.dumps(report, indent=2))
        raise click.exceptions.Exit(0 if report['status'] == 'ready' else 1)
