"""Only an authenticated explicit review can cross this durable T27 gate.

Creation snapshots read services; confirmation calls normal services in the SAME
transaction as the conditional claim and audit. Caller owns commit/rollback.
No model output, arbitrary identity or client revision is authoritative.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
from flask import current_app, session
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import select, update, func, text
from werkzeug.exceptions import NotFound
from authz import identity, resolve_scope
from authentication import security_stamp
from database import db
from models import AuthorizationAudit
from security import MutationError
from assistant.schemas import obj, integer, string, validate, Invalid
from assistant.proposal_models import OperationProposal, ProposalAudit
import inventory_ledger as ledger
import transfer_services as transfers

TTL_SECONDS=600
VERSION='inventraiq-proposal-v1'
SCHEMAS={
    'propose_transfer':obj(dict(sku_id=integer(),quantity=integer(),destination_branch_id=integer(),
        planned_arrival_utc=string(40),reason=string(255)),
        ('sku_id','quantity','destination_branch_id','planned_arrival_utc','reason')),
    # No increases, arbitrary receipts, expiry corrections or sale/refund tools.
    'propose_adjustment':obj(dict(sku_id=integer(),lot_id=integer(),quantity=integer(),reason=string(255)),
        ('sku_id','lot_id','quantity','reason')),
}
CAPABILITY={'propose_transfer':'transfer.propose','propose_adjustment':'stock.adjust'}


def now():return datetime.now(timezone.utc).replace(tzinfo=None)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)


def _digest(value):return hashlib.sha256(value.encode()).hexdigest()


def _seal(value):
    secret=current_app.secret_key
    return hmac.new(secret.encode() if isinstance(secret,str) else secret,value.encode(),hashlib.sha256).hexdigest()


def _tenant():
    tenant=current_app.config.get('ASSISTANT_TENANT_ID','single-company')
    if type(tenant) is not str or not 0<len(tenant)<=128:raise MutationError('Proposal unavailable.',503)
    return tenant


def _authority(action):
    if type(action) is not str or action not in SCHEMAS:raise MutationError('Unsupported proposal type.')
    actor=identity()
    scope=resolve_scope(CAPABILITY[action],write=True,shared=True)
    if scope.consolidated or scope.role not in ('manager','hq_admin'):raise MutationError('Select one authorized source branch.',403)
    # Conservatively invalidate after ANY ordinary authorization audit change.
    audit=db.session.scalar(select(func.max(AuthorizationAudit.audit_id))) or 0
    return scope,dict(actor_id=actor.user_id,tenant=_tenant(),branch_id=scope.branches[0],
        role=scope.role,stamp=security_stamp(actor),authorization_revision=audit)


def _record(row,event,operation_id=None):
    db.session.add(ProposalAudit(proposal_id=row.proposal_id,actor_id=row.actor_id,event=event,
        occurred_at=now(),payload_hash=row.payload_hash,operation_id=operation_id))


def _columns(row):
    def value(v):
        if isinstance(v,datetime):return v.isoformat()
        if hasattr(v,'isoformat'):return v.isoformat()
        if type(v) in (str,int,float,bool,list,dict) or v is None:return v
        return str(v)
    return {c.name:value(getattr(row,c.name)) for c in row.__table__.columns}


def _snapshot(action,args,scope):
    locked=ledger._locked_stock(scope,args['sku_id'])
    sku,offer,stocks,lots,policy,branch=locked
    if not sku.active or not offer.enabled or policy is None:raise MutationError('SKU or expiry policy unavailable.',409)
    instant=ledger.utcnow()
    common=dict(sku=_columns(sku),assortment=_columns(offer),branch=_columns(branch),
        policy=policy.snapshot(),stocks=[_columns(s) for s in stocks],
        lots=[_columns(lots[k]) for k in sorted(lots)])
    facts=dict(source_branch=branch.display_name,source_branch_id=branch.branch_id,
        sku=sku.code,product=sku.name,quantity=args['quantity'],unit=sku.unit,
        as_of=instant.isoformat(),sources=['inventory_ledger._locked_stock','catalog_services._effective_policy'],
        warnings=['Local stock facts only; no forecast or donor-surplus recommendation is established.',
                  'Changed stock, catalog, policy, permissions or expiry require fresh review.'])
    if action=='propose_transfer':
        arrival=transfers._arrival(args['planned_arrival_utc'])
        destination=transfers._validate_catalog(scope.branches[0],args['destination_branch_id'],
            [dict(sku_id=sku.sku_id,quantity=args['quantity'])])
        eligible=ledger.fefo_plan(transfers._arrival_eligible(locked,destination,arrival,instant),args['quantity'],instant)
        common['destination']=_columns(destination)
        from models import BranchProduct
        common['destination_assortment']=_columns(db.session.get(BranchProduct,(destination.branch_id,sku.sku_id),populate_existing=True))
        facts.update(destination_branch=destination.display_name,destination_branch_id=destination.branch_id,
            planned_arrival_utc=arrival.isoformat(),available=sum(s.on_hand-s.reserved-s.quarantined_quantity for *_,s,state in eligible),
            effect='Create a Draft transfer only. No stock is reserved or moved. Normal submission, independent HQ approval, dispatch and receipt remain required.')
    else:
        stock=next((s for s in stocks if s.lot_id==args['lot_id']),None)
        if stock is None:raise MutationError('Lot unavailable.',404)
        if stock.on_hand-stock.reserved<args['quantity']:raise MutationError('Insufficient unreserved physical stock.',409)
        _,classification=ledger._eligibility(sku,offer,lots[stock.lot_id],policy,branch,instant)
        facts.update(lot_id=stock.lot_id,lot_code=lots[stock.lot_id].lot_code,
            stock_revision=stock.version,available=stock.on_hand-stock.reserved,
            quarantine=stock.quarantined_quantity,physical_before=stock.on_hand,
            physical_after=stock.on_hand-args['quantity'],reserved=stock.reserved,
            expiry_status=classification['status'],days_remaining=classification['days_remaining'],
            effect='Write off the stated physical units through the ledger. Quarantined units are consumed first; reserved stock cannot be consumed.')
    return common,facts


def create(action,arguments,request_key):
    try:
        if type(action) is not str or action not in SCHEMAS:raise Invalid()
        validate(arguments,SCHEMAS[action])
        if type(request_key) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',request_key):raise Invalid()
        if not arguments['reason'].strip():raise Invalid()
    except (Invalid,KeyError,TypeError):raise MutationError('Invalid proposal fields.') from None
    scope,binding=_authority(action)
    request_hash=_digest(canonical(dict(action=action,arguments=arguments)))
    prior=db.session.scalar(select(OperationProposal).where(OperationProposal.tenant==binding['tenant'],
        OperationProposal.actor_id==scope.actor_id,OperationProposal.request_key==request_key))
    if prior:
        if prior.request_hash!=request_hash:raise MutationError('Request key already used for different proposal.',409)
        _verify(prior)
        return _view(prior)
    snapshot,facts=_snapshot(action,arguments,scope)
    # Existing MySQL DATETIME columns use second precision. Seal the persisted
    # representation, so a reload cannot invalidate an otherwise intact payload.
    key=secrets.token_hex(16);created=now().replace(microsecond=0);expires=created+timedelta(seconds=TTL_SECONDS)
    envelope=canonical(dict(schema_version=VERSION,proposal_id=key,action=action,arguments=arguments,binding=binding,
        snapshot=snapshot,facts=facts,created_at=created.isoformat(),expires_at=expires.isoformat()))
    if len(envelope.encode())>60*1024:raise MutationError('Proposal exceeds review budget.',413)
    row=OperationProposal(proposal_id=key,tenant=binding['tenant'],actor_id=scope.actor_id,
        branch_id=scope.branches[0],action=action,request_key=request_key,request_hash=request_hash,
        envelope=envelope,payload_hash=_digest(envelope),seal=_seal(envelope),created_at=created,
        expires_at=expires,state='pending')
    db.session.add(row);db.session.flush();_record(row,'created')
    ledger.guard(lambda:_authority_unchanged(action,binding))
    return _view(row)


def _authority_unchanged(action,binding):
    if _authority(action)[1]!=binding:raise MutationError('Proposal authority changed. Create a new proposal.',409)


def _verify(row):
    if not hmac.compare_digest(_digest(row.envelope),row.payload_hash) or not hmac.compare_digest(_seal(row.envelope),row.seal):
        raise MutationError('Proposal integrity failed.',409)
    data=json.loads(row.envelope)
    if (data.get('schema_version')!=VERSION or data['proposal_id']!=row.proposal_id or data['action']!=row.action or
        data['binding']['tenant']!=row.tenant or data['binding']['actor_id']!=row.actor_id or
        data['binding']['branch_id']!=row.branch_id or data['created_at']!=row.created_at.isoformat() or
        data['expires_at']!=row.expires_at.isoformat()):raise MutationError('Proposal integrity failed.',409)
    _authority_unchanged(row.action,data['binding'])
    if now()>=row.expires_at:raise MutationError('Proposal expired. Create a new proposal.',409)
    return data


def _get(key):
    actor=identity()
    if type(key) is not str or not re.fullmatch(r'[0-9a-f]{32}',key):raise NotFound()
    row=db.session.scalar(select(OperationProposal).where(OperationProposal.proposal_id==key,
        OperationProposal.actor_id==actor.user_id,OperationProposal.tenant==_tenant())
        .with_for_update().execution_options(populate_existing=True))
    if row is None:raise NotFound()
    return row


def _token(row):
    return URLSafeTimedSerializer(current_app.secret_key,salt='t27-explicit-review-v1').dumps(
        [row.proposal_id,row.payload_hash,row.actor_id,_digest(session.get('_csrf_token',''))])


def _view(row,review=False):
    data=json.loads(row.envelope)
    value=dict(proposal_id=row.proposal_id,action=row.action,state=row.state,payload_hash=row.payload_hash,
        expires_at=row.expires_at.isoformat()+'Z',arguments=data['arguments'],facts=data['facts'],
        result=row.result,operation_id=row.operation_id)
    if review and row.state=='pending':value['review_token']=_token(row)
    return value


def review(key):
    row=_get(key);_verify(row)
    return _view(row,True)


def cancel(key):
    row=_get(key);_verify(row)
    if row.state!='pending':raise MutationError('Proposal is already closed.',409)
    changed=db.session.execute(update(OperationProposal).where(OperationProposal.proposal_id==key,
        OperationProposal.state=='pending').values(state='cancelled').execution_options(synchronize_session=False)).rowcount
    if changed!=1:raise MutationError('Proposal already changed.',409)
    _record(row,'cancelled');db.session.refresh(row)
    return _view(row)


def confirm(key,token,confirmed):
    if confirmed is not True or type(token) is not str:raise MutationError('Explicit review confirmation is required.')
    identity()
    if db.engine.dialect.name=='sqlite':
        # Acquire the SQLite writer lock BEFORE reading any proposal/stock facts.
        # This changes no row and fires no audit trigger. Busy/snapshot conflicts
        # fail through the existing transaction boundary, without internal retry.
        db.session.execute(text('UPDATE proposal_audit SET audit_id=audit_id WHERE 1=0'))
    row=_get(key);data=_verify(row)
    try:
        bound=URLSafeTimedSerializer(current_app.secret_key,salt='t27-explicit-review-v1').loads(token,max_age=TTL_SECONDS)
        expected=[row.proposal_id,row.payload_hash,row.actor_id,_digest(session.get('_csrf_token',''))]
        if bound!=expected:raise BadSignature('Review binding')
    except BadSignature:raise MutationError('Review unavailable. Review this exact proposal again.',409) from None
    if row.state=='cancelled':raise MutationError('Proposal cancelled.',409)
    if row.state=='executed':return dict(_view(row),replayed=True)
    scope,binding=_authority(row.action)
    snapshot,_=_snapshot(row.action,data['arguments'],scope)
    if snapshot!=data['snapshot']:raise MutationError('Proposal facts changed. Create a new proposal.',409)
    # MySQL holds SELECT FOR UPDATE; SQLite holds its writer lock. The final CAS,
    # business idempotency record and audit all commit or roll back together.
    args=data['arguments']
    common=dict(request_key='T27-'+row.proposal_id,business_reference='T27-'+row.proposal_id,
        reason=args['reason'])
    if row.action=='propose_transfer':
        result=transfers.create(dict(common,destination_branch_id=args['destination_branch_id'],
            planned_arrival_utc=args['planned_arrival_utc'],lines=[dict(sku_id=args['sku_id'],quantity=args['quantity'])]))
    else:
        target=next(s for s in snapshot['stocks'] if s['lot_id']==args['lot_id'])
        result=ledger.adjust(dict(common,sku_id=args['sku_id'],lot_id=args['lot_id'],
            quantity=args['quantity'],expected_revision=target['version'],direction='decrease',kind='write_off'))
    if result.get('replayed'):
        raise MutationError('Unexpected prior business operation. Reconcile before creating a new proposal.',409)
    changed=db.session.execute(update(OperationProposal).where(OperationProposal.proposal_id==key,
        OperationProposal.state=='pending').values(state='executed',operation_id=result['operation_id'],result=result)
        .execution_options(synchronize_session=False)).rowcount
    if changed!=1:raise MutationError('Proposal already changed. Reload its result.',409)
    _record(row,'confirmed',result['operation_id'])
    def final_check():
        _authority_unchanged(row.action,binding)
        if now()>=row.expires_at:raise MutationError('Proposal expired before commit.',409)
    ledger.guard(final_check)
    db.session.refresh(row)
    return dict(_view(row),replayed=False)
