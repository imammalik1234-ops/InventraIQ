"""App-local bounded artifact leases, never business DB rows or filesystem paths."""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta
from hashlib import sha256
import re
import secrets
from threading import Lock
from time import monotonic
from flask import current_app
from werkzeug.exceptions import NotFound
from authz import resolve_scope,identity

TTL_SECONDS=900
MAX_ITEMS=64
MAX_TOTAL_BYTES=1024*1024
MAX_OWNER_ITEMS=8
MAX_ARTIFACT_BYTES=48*1024
_initialization=Lock()


class OutputLimit(ValueError):
    pass


@dataclass(frozen=True)
class Artifact:
    owner: int
    branches: tuple
    role: str
    consolidated: bool
    capability: str
    content: bytes
    media_type: str
    deadline: float
    expires_at: str


def expired(item):
    # Enforce the advertised UTC expiry as well as elapsed lease time. Neither
    # wall-clock rollback nor suspension/forward clock changes extend a lease.
    return (item.deadline<=monotonic() or
            datetime.now(timezone.utc)>=datetime.fromisoformat(item.expires_at))


class Store:
    def __init__(self):
        self.lock=Lock()
        self.items=OrderedDict()

    def prune(self):
        for key in [k for k,v in self.items.items() if expired(v)]:self.items.pop(key)


def store():
    with _initialization:
        if 'inventraiq_t25_artifacts' not in current_app.extensions:
            current_app.extensions['inventraiq_t25_artifacts']=Store()
        return current_app.extensions['inventraiq_t25_artifacts']


def create(scope,capability,content,media_type):
    if type(content) is not bytes or len(content)>MAX_ARTIFACT_BYTES:raise OutputLimit()
    check(scope,capability)
    expires=(datetime.now(timezone.utc)+timedelta(seconds=TTL_SECONDS)).isoformat()
    item=Artifact(scope.actor_id,scope.branches,scope.role,scope.consolidated,capability,content,media_type,monotonic()+TTL_SECONDS,expires)
    pool=store()
    with pool.lock:
        pool.prune()
        owned=[k for k,v in pool.items.items() if v.owner==scope.actor_id]
        while len(owned)>=MAX_OWNER_ITEMS:pool.items.pop(owned.pop(0))
        while pool.items and (len(pool.items)>=MAX_ITEMS or sum(len(v.content) for v in pool.items.values())+len(content)>MAX_TOTAL_BYTES):pool.items.popitem(last=False)
        key=secrets.token_urlsafe(24)
        pool.items[key]=item
    return dict(artifact_id=key,media_type=media_type,expires_at=expires,bytes=len(content),sha256=sha256(content).hexdigest(),
                download_path='/assistant/artifacts/'+key)


def check(scope,capability):
    selected=None if scope.consolidated else scope.branches[0]
    for cap in (capability,'export.read'):
        fresh=resolve_scope(cap,branch_id=selected,consolidated=scope.consolidated)
        if fresh!=scope:raise NotFound('Artifact unavailable.')


def retrieve(key):
    actor=identity()
    if type(key) is not str or re.fullmatch(r'[A-Za-z0-9_-]{32}',key) is None:raise NotFound('Artifact unavailable.')
    pool=store()
    with pool.lock:
        pool.prune();item=pool.items.get(key)
    if item is None or item.owner!=actor.user_id:raise NotFound('Artifact unavailable.')
    try:
        from authz import Scope
        check(Scope(item.owner,item.branches,item.role,item.consolidated),item.capability)
    except Exception:
        # Non-disclosing across absent IDs, revoked roles/scopes and owners.
        raise NotFound('Artifact unavailable.') from None
    if expired(item):raise NotFound('Artifact unavailable.')
    return item
