"""Session CSRF protection and a rollback boundary for write requests."""
from functools import wraps
from hmac import compare_digest
import secrets
from urllib.parse import urljoin, urlsplit
from flask import current_app, request, session, make_response
from flask_login import current_user
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.exceptions import HTTPException
from database import db
from operations import log_event, safe_failure

SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe(32)
    return session['_csrf_token']


def init_security(app, login_manager):
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
    app.jinja_env.globals['csrf_token'] = csrf_token

    @app.after_request
    def private_responses(response):
        # Enhanced forms need the redirect destination without fetching its GET
        # first: that extra GET consumes Flask's one-use success messages. The
        # normal server form still receives its original redirect. This runs
        # after the transaction wrapper has made its commit/rollback decision.
        if (request.method == 'POST' and request.headers.get('X-InventraIQ-Form') == '1'
                and response.status_code in (302, 303) and response.location):
            destination = urlsplit(urljoin(request.host_url, response.location))
            origin = urlsplit(request.host_url)
            if ((destination.scheme, destination.netloc) == (origin.scheme, origin.netloc)
                    and not destination.username and not destination.password):
                target = response.location
                response.status_code = 200
                response.headers.pop('Location', None)
                response.set_data(current_app.json.dumps({'redirect_to': target}))
                response.mimetype = 'application/json'
        if '_user_id' in session or '_csrf_token' in session:
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.before_request
    def protect_writes():
        if request.method in SAFE_METHODS or request.endpoint is None:
            return
        if request.endpoint not in {'admin_login', 'staff_login'}:
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in {'admin', 'staff'}:
                return {'error': 'Role is not authorized'}, 403
        provided = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
        expected = session.get('_csrf_token', '')
        if not isinstance(provided, str) or not expected or not compare_digest(provided.encode(), expected.encode()):
            return {'error': 'CSRF token missing or invalid. Reload the page.'}, 400


def transaction(view):
    """One commit for stock, product, sale, alerts and audit; no helper commits."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.method in SAFE_METHODS:
            return view(*args, **kwargs)
        try:
            response = make_response(view(*args, **kwargs))
            if response.status_code < 400:
                db.session.commit()
            else:
                db.session.rollback()
            return response
        except MutationError as exc:
            db.session.rollback()
            return {'error': str(exc)}, exc.status
        except IntegrityError as exc:
            db.session.rollback()
            log_event('database_conflict', exc)
            return {'error': 'Database conflict. Reload and retry; no changes were committed.'}, 409
        except OperationalError as exc:
            original = exc.orig
            code = getattr(original, 'sqlite_errorcode', None)
            mysql_code = original.args[0] if getattr(original, 'args', ()) else None
            if code in {5, 6} or mysql_code in {1205, 1213}:
                db.session.rollback()
                log_event('database_conflict', exc)
                return {'error': 'Database conflict. Reload and retry; no changes were committed.'}, 409
            return safe_failure('database_unavailable', exc)
        except HTTPException:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            log_event('transaction_failed', exc)
            return {'error': 'Transaction failed; no changes were committed.'}, 500
    return wrapped


class MutationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status
