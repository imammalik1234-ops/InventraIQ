"""Bounded structured diagnostics: never log exception messages, SQL or inputs."""
import json
import secrets
import traceback
from pathlib import Path
from flask import current_app, g, has_request_context, request, make_response
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException
from database import db


def correlation_id():
    if not getattr(g, 'correlation_id', None):
        g.correlation_id = secrets.token_hex(16)
    return g.correlation_id


def log_event(event, exception=None, level='error'):
    record = {'event': event, 'correlation_id': correlation_id()}
    if has_request_context():
        record.update(endpoint=request.endpoint, method=request.method)
    if exception is not None:
        record['error_type'] = type(exception).__name__
        record['frames'] = [{'module': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
                            for frame in traceback.extract_tb(exception.__traceback__)[-8:]]
    getattr(current_app.logger, level)(json.dumps(record, separators=(',', ':')))


def safe_failure(event, exception, status=503):
    try:
        db.session.rollback()
    except SQLAlchemyError:
        db.session.remove()
    log_event(event, exception)
    reference = correlation_id()
    message = 'Service temporarily unavailable. Try again later.' if status == 503 else 'The request could not be completed.'
    if request.is_json:
        return make_response({'error': message, 'reference': reference}, status)
    # Do not invoke template context processors which might query the unavailable DB.
    template = current_app.jinja_env.get_template('service_unavailable.html')
    return make_response(template.render(message=message, reference=reference), status)


def init_operational_errors(app):
    @app.before_request
    def assign_reference():
        correlation_id()  # Ignore untrusted caller-supplied identifiers.

    @app.after_request
    def add_reference(response):
        response.headers['X-Request-ID'] = correlation_id()
        if response.status_code >= 500:
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(SQLAlchemyError)
    def database_unavailable(exception):
        return safe_failure('database_unavailable', exception)

    @app.errorhandler(Exception)
    def unexpected_failure(exception):
        if isinstance(exception, HTTPException):
            return exception
        return safe_failure('unexpected_failure', exception, 500)
