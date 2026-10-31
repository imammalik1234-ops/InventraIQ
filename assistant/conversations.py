"""App-local, expiring Stage A history. No database schema or business writes.

Public operations belong to dedicated read-only HTTP requests. Fresh guards end
only these requests' read transactions, so reporting snapshots cannot pin stale
authorization. Never call this layer inside a business transaction.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
import re
import secrets
from threading import Event, Lock, Thread
import time
from weakref import WeakSet
from flask import current_app
from werkzeug.exceptions import HTTPException
from authz import resolve_scope
from database import db
from assistant import tools

TTL=1800
MAX_HISTORY=12
MAX_HISTORY_BYTES=128*1024
MAX_STORE_BYTES=4*1024*1024
MAX_RESPONSE_BYTES=96*1024
MAX_CONVERSATIONS=64
MAX_OWNER=4
_init_lock=Lock()
_stores=WeakSet()
_reaper_started=False


def _reap():
    # One process-local daemon, no Flask context and no references keeping an
    # abandoned app/store alive. Expired idle histories are also discarded.
    pause=Event()
    while not pause.wait(30):
        with _init_lock:targets=list(_stores)
        for target in targets:
            with target.lock:target.prune()


class Failure(Exception):
    def __init__(self, code, status=400):self.code,self.status=code,status


def encoded(value):return json.dumps(value,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()


def authority():
    # No ORM changes are permitted in this read-only boundary. The assistant
    # itself never creates any. Remove the read snapshot before fresh checks.
    if db.session.new or db.session.dirty or db.session.deleted:raise Failure('unsafe_transaction',409)
    db.session.remove()
    scope=resolve_scope('inventory.read')
    if not scope.branches or len(scope.branches)>50:raise Failure('scope_unavailable',403)
    grants=tuple((cap,resolve_scope(cap)) for cap in sorted({t.capability for t in tools.CATALOG.values()}))
    tenant=current_app.config.get('ASSISTANT_TENANT_ID','single-company')
    if type(tenant) is not str or not 0<len(tenant)<=128:raise Failure('unavailable',503)
    return tenant,scope,grants


@dataclass
class Conversation:
    key: str
    binding: tuple
    expires_at: str
    deadline: float
    messages: list = field(default_factory=list)
    cancelled: Event = field(default_factory=Event)
    running: bool = False


class Store:
    def __init__(self):self.lock=Lock();self.items=OrderedDict()
    def prune(self):
        now=datetime.now(timezone.utc)
        for key,item in list(self.items.items()):
            if time.monotonic()>=item.deadline or now>=datetime.fromisoformat(item.expires_at):
                item.cancelled.set();del self.items[key]


def store():
    global _reaper_started
    with _init_lock:
        target=current_app.extensions.get('inventraiq_t26_history')
        if target is None:
            target=Store();current_app.extensions['inventraiq_t26_history']=target;_stores.add(target)
        if not _reaper_started:
            Thread(target=_reap,daemon=True,name='inventraiq-history-expiry').start();_reaper_started=True
        return target


def _guard(item,binding):
    if item.binding!=binding:
        # Only an owner may invalidate their history; a guessed foreign ID must
        # neither reveal nor destroy another user's conversation.
        if item.binding[1].actor_id==binding[1].actor_id:
            item.cancelled.set();store().items.pop(item.key,None)
        raise Failure('conversation_unavailable',404)


def _get(key,binding):
    if type(key) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{32}',key):raise Failure('conversation_unavailable',404)
    target=store();target.prune();item=target.items.get(key)
    if item is None:raise Failure('conversation_unavailable',404)
    _guard(item,binding)
    return item


def binding():
    try:return authority()
    except HTTPException:
        from flask_login import current_user
        if current_user.is_authenticated:
            target=store()
            with target.lock:
                for key,item in list(target.items.items()):
                    if item.binding[1].actor_id==current_user.user_id:
                        item.cancelled.set();del target.items[key]
        raise Failure('conversation_unavailable',404) from None


def describe(item):
    scope=item.binding[1]
    result=dict(id=item.key,expires_at=item.expires_at,scope=tools._scope(scope),
                provider=('gemini-synthetic-development' if os.environ.get('ASSISTANT_PROVIDER','fake')=='gemini'
                          else 'local-deterministic-demo'),retention='memory only; at most 30 minutes',
                messages=deepcopy(item.messages),running=item.running)
    if len(encoded(result))>MAX_RESPONSE_BYTES:raise Failure('output_limit',413)
    return result


def create():
    grant=binding();target=store()
    with target.lock:
        target.prune()
        owned=[i for i in target.items.values() if i.binding[1].actor_id==grant[1].actor_id]
        if len(owned)>=MAX_OWNER or len(target.items)>=MAX_CONVERSATIONS:raise Failure('conversation_limit',429)
        key=secrets.token_urlsafe(24)
        item=Conversation(key,grant,(datetime.now(timezone.utc)+timedelta(seconds=TTL)).isoformat(),time.monotonic()+TTL)
        target.items[key]=item
        return describe(item)


def read(key):
    grant=binding();target=store()
    with target.lock:return describe(_get(key,grant))


def begin(key):
    grant=binding();target=store()
    with target.lock:
        item=_get(key,grant)
        if item.running:raise Failure('conversation_busy',409)
        if len(item.messages)+2>MAX_HISTORY:raise Failure('history_limit',413)
        # One in-flight turn per owner across all conversations.
        if any(i.running and i.binding[1].actor_id==grant[1].actor_id for i in target.items.values()):raise Failure('conversation_busy',409)
        item.running=True;item.cancelled=Event()
        return item,deepcopy(item.messages)


def check(item):
    grant=binding();target=store()
    with target.lock:
        if _get(item.key,grant) is not item:raise Failure('conversation_unavailable',404)
    if item.cancelled.is_set():raise Failure('cancelled',409)


def finish(item,messages=None):
    if messages is None:
        with store().lock:item.running=False
        return
    check(item)
    target=store()
    with target.lock:
        if item.cancelled.is_set():raise Failure('cancelled',409)
        candidate=item.messages+messages
        if len(encoded(candidate))>MAX_HISTORY_BYTES:raise Failure('history_limit',413)
        total=sum(len(encoded(i.messages)) for i in target.items.values() if i is not item)
        if total+len(encoded(candidate))>MAX_STORE_BYTES:raise Failure('history_limit',413)
        response=describe(item);response.update(messages=deepcopy(candidate),running=False)
        if len(encoded(response))>MAX_RESPONSE_BYTES:raise Failure('output_limit',413)
        item.messages=candidate
        return response


def cancel(key):
    grant=binding();target=store()
    with target.lock:
        item=_get(key,grant);item.cancelled.set()
        return dict(status='cancel_requested' if item.running else 'idle')


def forget(key):
    grant=binding();target=store()
    with target.lock:
        item=_get(key,grant);item.cancelled.set();del target.items[key]
    return dict(status='forgotten')
