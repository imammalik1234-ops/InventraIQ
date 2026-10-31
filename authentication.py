"""Identity validation, revocable timed tokens and shared database throttling."""
from datetime import timedelta
import hashlib
import hmac
import json
import re
import unicodedata
from flask import current_app, session
from itsdangerous import URLSafeTimedSerializer, BadData
from sqlalchemy import case, select, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.mysql import insert as mysql_insert
from database import db
from models import User, LoginThrottle


class LoginThrottled(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


class AuthenticationUnavailable(Exception):
    pass


def normalize_email(value):
    value = value.strip().lower()
    if len(value) > 100 or value.count('@') != 1 or not value.isascii():
        raise ValueError('Enter a valid email address of at most 100 characters.')
    local, domain = value.split('@')
    if not 1 <= len(local) <= 64 or local.startswith('.') or local.endswith('.') or '..' in local:
        raise ValueError('Enter a valid email address of at most 100 characters.')
    if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local):
        raise ValueError('Enter a valid email address of at most 100 characters.')
    labels = domain.split('.')
    if len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
        raise ValueError('Enter a valid email address of at most 100 characters.')
    return value


def validated_profile(name, email):
    name = unicodedata.normalize('NFC', name.strip())
    if not name or len(name) > 100 or any(unicodedata.category(c).startswith('C') for c in name):
        raise ValueError('Name must contain 1 to 100 characters without control characters.')
    return name, normalize_email(email)


def security_stamp(user):
    state = [user.password_hash, user.email, user.role, user.enabled, user.session_version]
    return hashlib.sha256(json.dumps(state, separators=(',', ':')).encode()).hexdigest()


def serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='inventraiq-auth-v1')


def session_token(user):
    return serializer().dumps({'id': user.user_id, 'version': user.session_version, 'stamp': security_stamp(user)})


def load_authenticated_user(token):
    try:
        payload = serializer().loads(token, max_age=current_app.config['AUTH_ABSOLUTE_SECONDS'])
        if not isinstance(payload, dict) or type(payload.get('id')) is not int:
            raise ValueError()
        user = db.session.get(User, payload['id'], populate_existing=True)
        if user and user.enabled and user.session_version == payload.get('version') and hmac.compare_digest(security_stamp(user), payload.get('stamp', '')):
            return user
    except (BadData, ValueError, TypeError):
        pass
    # Invalidate old CSRF and other session state as well as the login identifier.
    session.clear()
    return None


def init_authentication(app):
    from flask_login import user_logged_in
    app.config.update(PERMANENT_SESSION_LIFETIME=timedelta(seconds=app.config['AUTH_IDLE_SECONDS']),
                      SESSION_REFRESH_EACH_REQUEST=True)

    @user_logged_in.connect_via(app)
    def permanent_login(sender, user):
        session.permanent = True


def consume_login_attempt(email, address):
    """Fixed-window atomic counters, bounded cardinality; no raw email/IP stored."""
    if current_app.config.get('AUTH_THROTTLE_BACKEND') != 'database':
        raise AuthenticationUnavailable()
    engine = db.engine
    if engine.dialect.name not in {'sqlite', 'mysql'}:
        raise AuthenticationUnavailable()
    width = current_app.config['AUTH_THROTTLE_WINDOW_SECONDS']
    slots = current_app.config['AUTH_THROTTLE_SLOTS']
    secret = current_app.secret_key
    secret = secret.encode() if isinstance(secret, str) else secret
    def slot(kind, value):
        digest = hmac.new(secret, ('login:' + kind + ':' + value).encode(), hashlib.sha256).digest()
        return int.from_bytes(digest[:8], 'big') % slots
    blocked = False
    with engine.begin() as connection:
        if engine.dialect.name == 'sqlite':
            now = int(connection.execute(select(func.strftime('%s', 'now'))).scalar_one())
            insert = sqlite_insert
        else:
            transactional = connection.exec_driver_sql(
                "SELECT ENGINE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'login_throttle'").scalar_one_or_none()
            if transactional != 'InnoDB' or connection.exec_driver_sql('SELECT @@SESSION.autocommit').scalar_one() != 0:
                raise AuthenticationUnavailable()
            now = int(connection.exec_driver_sql("SELECT TIMESTAMPDIFF(SECOND, '1970-01-01', UTC_TIMESTAMP())").scalar_one())
            insert = mysql_insert
        window = now - now % width
        for kind, value, limit in [('address', address, current_app.config['AUTH_IP_LIMIT']),
                                   ('account', email, current_app.config['AUTH_ACCOUNT_LIMIT'])]:
            key = slot(kind, value)
            table = LoginThrottle.__table__
            count = case((table.c.window_start != window, 1),
                         (table.c.attempts < limit + 1, table.c.attempts + 1), else_=table.c.attempts)
            statement = insert(table).values(kind=kind, slot=key, window_start=window, attempts=1)
            changes = dict(attempts=count, window_start=window)
            if engine.dialect.name == 'sqlite':
                statement = statement.on_conflict_do_update(index_elements=['kind', 'slot'], set_=changes)
            else:
                # attempts must be evaluated before replacing window_start in MySQL.
                statement = statement.on_duplicate_key_update([('attempts', count), ('window_start', window)])
            connection.execute(statement)
            attempts = connection.execute(select(table.c.attempts).where(table.c.kind == kind, table.c.slot == key)).scalar_one()
            if attempts > limit:
                blocked = True
                break
    # Commit the attempt count even when blocked. Never reset on login success.
    if blocked:
        raise LoginThrottled(width - now % width)
