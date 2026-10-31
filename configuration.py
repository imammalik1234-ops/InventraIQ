"""Validate configuration without connecting or including supplied values in errors."""
import os
from pathlib import Path
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(ValueError):
    pass


def bounded_integer(env, name, default, minimum, maximum):
    try:
        value = int(env.get(name, str(default)))
    except ValueError:
        raise ConfigurationError(f'{name} must be an integer.') from None
    if not minimum <= value <= maximum:
        raise ConfigurationError(f'{name} is outside its supported range.')
    return value


def boolean(env, name, default=False):
    value = env.get(name, str(default)).strip().lower()
    if value not in {'true', 'false', '1', '0'}:
        raise ConfigurationError(f'{name} must be true/false (or 1/0).')
    return value in {'true', '1'}


def application_config(env=None):
    env = os.environ if env is None else env
    mode = env.get('APP_ENV', 'development').strip().lower()
    if mode not in {'development', 'testing', 'production'}:
        raise ConfigurationError('APP_ENV must be development, testing or production.')
    production = mode == 'production'
    if env.get('AUTH_THROTTLE_BACKEND', 'database') != 'database':
        raise ConfigurationError('AUTH_THROTTLE_BACKEND must be database; local-memory or disabled enforcement is unsupported.')
    idle = bounded_integer(env, 'AUTH_IDLE_SECONDS', 1800, 60, 86400)
    absolute = bounded_integer(env, 'AUTH_ABSOLUTE_SECONDS', 28800, idle, 604800)
    try:
        if 'DATABASE_URL' in env:
            if not env['DATABASE_URL'].strip():
                raise ConfigurationError('DATABASE_URL is empty; select a database explicitly.')
            url = make_url(env['DATABASE_URL'])
        else:
            required = ('DB_USER', 'DB_PASSWORD', 'DB_HOST', 'DB_NAME')
            if any(not env.get(key, '').strip() for key in required):
                raise ConfigurationError('Set DATABASE_URL or all of DB_USER, DB_PASSWORD, DB_HOST and DB_NAME.')
            port = int(env.get('DB_PORT', '3306'))
            if not 1 <= port <= 65535:
                raise ConfigurationError('DB_PORT must be between 1 and 65535.')
            url = URL.create('mysql+pymysql', username=env['DB_USER'], password=env['DB_PASSWORD'],
                             host=env['DB_HOST'], port=port, database=env['DB_NAME'])
    except (ArgumentError, ValueError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError('Invalid database URL or port; check configuration syntax.') from None

    if url.drivername not in {'sqlite', 'sqlite+pysqlite', 'mysql', 'mysql+pymysql'}:
        raise ConfigurationError('Only SQLite/pysqlite and MySQL/PyMySQL are supported.')
    mysql = url.get_backend_name() == 'mysql'
    if mysql:
        if not all((url.host, url.username, url.password, url.database)):
            raise ConfigurationError('MySQL requires host, username, password and database.')
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ConfigurationError('Database port must be between 1 and 65535.')
        url = url.set(drivername='mysql+pymysql')
    elif url.host or url.username or url.password or not url.database:
        raise ConfigurationError('SQLite requires a file path or :memory: without network credentials.')

    secret = env.get('SECRET_KEY')
    secure = boolean(env, 'SESSION_COOKIE_SECURE', production)
    https = boolean(env, 'HTTPS_REQUIRED', production)
    debug = boolean(env, 'FLASK_DEBUG')
    if production:
        if not secret or len(secret.strip()) < 32:
            raise ConfigurationError('Production requires an explicit stable SECRET_KEY of at least 32 characters.')
        if not secure or not https or debug:
            raise ConfigurationError('Production requires secure session cookies, HTTPS_REQUIRED=true and FLASK_DEBUG=false.')
        if not mysql:
            raise ConfigurationError('Production requires the supported MySQL/InnoDB deployment configuration.')

    options = {}
    if mysql:
        connect = {'connect_timeout': 5, 'read_timeout': 5, 'write_timeout': 5}
        ca = env.get('DB_SSL_CA')
        if production and (not ca or not Path(ca).is_file()):
            raise ConfigurationError('Production requires DB_SSL_CA pointing to an existing trusted CA file.')
        if production and any(key.lower().startswith('ssl') for key in url.query):
            raise ConfigurationError('Configure production database TLS through DB_SSL_CA, not URL SSL parameters.')
        if ca:
            if not Path(ca).is_file():
                raise ConfigurationError('DB_SSL_CA must point to an existing trusted CA file.')
            connect.update(ssl_ca=ca, ssl_verify_cert=True, ssl_verify_identity=True)
        options['connect_args'] = connect
    return dict(APP_ENV=mode, SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_ENGINE_OPTIONS=options,
                ASSISTANT_PROPOSALS_ENABLED=boolean(env,'ASSISTANT_PROPOSALS_ENABLED'),
                AUTH_THROTTLE_BACKEND='database', AUTH_IDLE_SECONDS=idle, AUTH_ABSOLUTE_SECONDS=absolute,
                AUTH_ACCOUNT_LIMIT=bounded_integer(env, 'AUTH_ACCOUNT_LIMIT', 10, 1, 100000),
                AUTH_IP_LIMIT=bounded_integer(env, 'AUTH_IP_LIMIT', 50, 1, 100000),
                AUTH_THROTTLE_WINDOW_SECONDS=bounded_integer(env, 'AUTH_THROTTLE_WINDOW_SECONDS', 900, 60, 3600),
                AUTH_THROTTLE_SLOTS=bounded_integer(env, 'AUTH_THROTTLE_SLOTS', 16384, 1024, 262144),
                SQLALCHEMY_TRACK_MODIFICATIONS=False, SECRET_KEY=secret or os.urandom(32),
                SESSION_COOKIE_SECURE=secure, HTTPS_REQUIRED=https,
                DEBUG=debug,
                PREFERRED_URL_SCHEME='https' if https else 'http')
