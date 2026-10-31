"""T27 human review transport; no write endpoint is exposed to any model."""
import json
import secrets
from flask import Blueprint, current_app, request, render_template, redirect, url_for
from authz import resolve_scope
from database import db
from security import MutationError, transaction
from assistant import proposals
from assistant.provider import FakeProvider
from assistant.proposal_provider import ProposalIntent
from assistant.gemini import reject_key
from assistant.conversations import Failure
from assistant.routes import _pairs

bp=Blueprint('assistant_proposals',__name__)


@bp.before_request
def enabled():
    if not current_app.config.get('ASSISTANT_PROPOSALS_ENABLED',False):
        raise MutationError('Assistant proposals are disabled pending schema and capability-policy approval.',503)
    if request.args:raise MutationError('Select the source branch using the normal branch selector.')


@bp.errorhandler(MutationError)
def failed(error):
    db.session.rollback()
    if (error.status == 503 and not current_app.config.get('ASSISTANT_PROPOSALS_ENABLED', False)
            and request.method == 'GET' and request.endpoint in {'assistant_proposals.page', 'assistant_proposals.review'}
            and request.accept_mimetypes.best_match(('application/json', 'text/html')) == 'text/html'):
        return render_template('error.html', code=503, proposals_disabled=True), 503
    return {'error':str(error)},error.status


def body(fields):
    if request.is_json:
        try:
            value=json.loads(request.get_data(),object_pairs_hook=_pairs,
                parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        except (ValueError,TypeError):raise MutationError('Invalid proposal request.') from None
    else:
        if any(len(request.form.getlist(k))!=1 for k in request.form):raise MutationError('Repeated fields are not allowed.')
        value={k:v for k,v in request.form.items() if k!='csrf_token'}
    if type(value) is not dict or set(value)!=set(fields):raise MutationError('Supply exact proposal fields.')
    return value


def create_intent(value):
    question=value['question']
    try:reject_key(question)
    except (Failure,TypeError):raise MutationError('Unsupported proposal question.') from None
    # Existing fake provider only: never selects Gemini or calls read/write tools.
    intent=FakeProvider().propose(question)
    if type(intent) is not ProposalIntent:raise MutationError('Invalid proposal intent.')
    return proposals.create(intent.action,intent.arguments,value['request_key'])


@bp.get('/assistant/proposals')
def page():
    scope=resolve_scope('inventory.read')
    return render_template('assistant_proposals.html',proposal=None,scope=scope,
        request_key=secrets.token_hex(16))


@bp.post('/api/assistant/proposals')
@bp.post('/assistant/proposals')
@transaction
def create():
    result=create_intent(body(('question','request_key')))
    if request.is_json:return result,201
    return redirect(url_for('assistant_proposals.review',key=result['proposal_id']))


@bp.get('/assistant/proposals/<key>')
def review(key):
    result=proposals.review(key)
    if request.accept_mimetypes.best=='application/json':return result
    return render_template('assistant_proposals.html',proposal=result,scope=resolve_scope('inventory.read'))


@bp.post('/assistant/proposals/<key>/confirm')
@transaction
def confirm(key):
    value=body(('review_token','confirmed'))
    confirmed=value['confirmed'] if request.is_json else value['confirmed']=='yes'
    result=proposals.confirm(key,value['review_token'],confirmed)
    if request.is_json:return result
    return redirect(url_for('assistant_proposals.review',key=key))


@bp.post('/assistant/proposals/<key>/cancel')
@transaction
def cancel(key):
    body(())
    result=proposals.cancel(key)
    if request.is_json:return result
    return redirect(url_for('assistant_proposals.review',key=key))
