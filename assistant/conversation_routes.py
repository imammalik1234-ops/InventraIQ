"""Authenticated local-only Stage A conversation transport; ordinary CSRF applies."""
import json
import os
from flask import Blueprint, request
from assistant import conversations, orchestrator
from assistant.routes import _pairs

bp=Blueprint('assistant_conversations',__name__)


@bp.app_context_processor
def provider_disclosure():
    return dict(assistant_gemini_selected=os.environ.get('ASSISTANT_PROVIDER','fake')=='gemini')


@bp.errorhandler(conversations.Failure)
def failure(error):
    # Never echo provider exceptions, submitted text, arguments or identifiers.
    return dict(status='error',error=dict(code=error.code,message={
        'conversation_unavailable':'Conversation unavailable. Start a new local conversation in your current scope.',
        'cancelled':'Request cancelled. No conversation turn was saved.',
        'timeout':'Request timed out. No conversation turn was saved.',
        'history_limit':'History budget reached. Start a new conversation.',
        'conversation_busy':'A conversation request is already running.',
    }.get(error.code,'The request could not be completed within the permitted read-only contract.'))),error.status


@bp.errorhandler(413)
def too_large(error):return failure(conversations.Failure('input_limit',413))


def body(fields=()):
    if request.args or not request.is_json:raise conversations.Failure('invalid_arguments')
    try:
        raw=request.get_data(cache=True)
        if len(raw)>8192:raise conversations.Failure('input_limit',413)
        value=json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):raise conversations.Failure('invalid_arguments') from None
    if type(value) is not dict or set(value)-set(fields):raise conversations.Failure('invalid_arguments')
    return value


@bp.post('/api/assistant/conversations')
def create():
    body();return conversations.create(),201


@bp.get('/api/assistant/conversations/<key>')
def history(key):
    if request.args:raise conversations.Failure('invalid_arguments')
    return conversations.read(key)


@bp.post('/api/assistant/conversations/<key>/messages')
def send(key):
    value=body(('text','context'))
    for name,maximum in (('text',2000),('context',4000)):
        text=value.get(name,'')
        if type(text) is not str or len(text)>maximum or any(ord(c)<32 and c not in '\n\t\r' for c in text):raise conversations.Failure('invalid_arguments')
    if not value.get('text','').strip():raise conversations.Failure('invalid_arguments')
    return orchestrator.send(key,value['text'],value.get('context',''))


@bp.post('/api/assistant/conversations/<key>/cancel')
def cancel(key):
    body();return conversations.cancel(key)


@bp.post('/api/assistant/conversations/<key>/forget')
def forget(key):
    body();return conversations.forget(key)
