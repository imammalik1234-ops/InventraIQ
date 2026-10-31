from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
import sqlite3


@event.listens_for(Engine, 'connect')
def enforce_sqlite_foreign_keys(connection, _record=None):
    """Cover every SQLAlchemy SQLite connection, including separate test engines."""
    if not isinstance(connection, sqlite3.Connection):
        return
    # Python 3.12+ autocommit=False would otherwise prevent changing the PRAGMA.
    previous = getattr(connection, 'autocommit', None)
    cursor = None
    try:
        if previous is not None:
            connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        if cursor.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise RuntimeError('SQLite foreign-key enforcement could not be enabled.')
    finally:
        if cursor is not None:
            cursor.close()
        if previous is not None:
            connection.autocommit = previous

db = SQLAlchemy()
