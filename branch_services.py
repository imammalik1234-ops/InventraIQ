"""HQ administration and scoped foundation reads. Caller owns write transaction."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy import select
from werkzeug.exceptions import BadRequest, NotFound
from flask import session
from database import db
from models import Branch, User, UserBranch, UserHQScope, AuthorizationAudit, BranchStock, StockLot
from authz import resolve_scope, integer_id, identity
from expiry import utcnow


def _audit(scope, action, key, before, after):
    db.session.add(AuthorizationAudit(actor_id=scope.actor_id, action=action, entity_key=key,
        old_value=before, new_value=after, occurred_at=utcnow().replace(tzinfo=None)))


def boolean(value):
    if type(value) is not bool:
        raise BadRequest('A boolean is required.')
    return value


def manage_branch(branch_id=None, **values):
    scope = resolve_scope('branches.manage', consolidated=True, lock=bool(values))
    if not values:
        return [dict(branch_id=b.branch_id, code=b.code, display_name=b.display_name,
                     timezone=b.timezone, active=b.active)
                for b in db.session.scalars(select(Branch).order_by(Branch.branch_id))]
    if set(values) - {'code', 'display_name', 'timezone', 'active'}:
        raise BadRequest('Unsupported branch field.')
    branch = db.session.get(Branch, integer_id(branch_id), with_for_update=True) if branch_id is not None else None
    if branch_id is not None and branch is None: raise NotFound('Resource not found.')
    before = dict(code=branch.code, display_name=branch.display_name, timezone=branch.timezone, active=branch.active) if branch else None
    if branch is None:
        branch = Branch(); db.session.add(branch)
    elif 'code' in values and values['code'] != branch.code:
        raise BadRequest('Branch codes are stable and cannot be changed.')
    for field, limit in [('code', 40), ('display_name', 100), ('timezone', 64)]:
        value = values.get(field, getattr(branch, field))
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise BadRequest('Invalid branch definition.')
        if field == 'code' and not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]*', value):
            raise BadRequest('Use a stable uppercase branch code.')
        if field == 'timezone':
            try: ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError): raise BadRequest('Unknown IANA timezone.') from None
        setattr(branch, field, value.strip())
    branch.active = boolean(values.get('active', branch.active if before else True))
    db.session.flush()
    after=dict(code=branch.code, display_name=branch.display_name, timezone=branch.timezone, active=branch.active)
    _audit(scope, 'branch.update' if before else 'branch.create', str(branch.branch_id), before, after)
    return dict(branch_id=branch.branch_id, **after)


def manage_assignment(user_id, branch_id, branch_role, active):
    scope = resolve_scope('assignments.manage', consolidated=True, lock=True)
    user_id, branch_id = integer_id(user_id), integer_id(branch_id)
    target = db.session.get(User, user_id, with_for_update=True)
    branch = db.session.get(Branch, branch_id, with_for_update=True)
    if target is None or branch is None: raise NotFound('Resource not found.')
    if not isinstance(branch_role, str) or branch_role not in {'manager', 'clerk'}: raise BadRequest('Invalid branch role.')
    active = boolean(active)
    assignment = db.session.get(UserBranch, (user_id, branch_id), with_for_update=True)
    before = dict(branch_role=assignment.branch_role, active=assignment.active) if assignment else None
    if assignment is None:
        assignment = UserBranch(user_id=user_id, branch_id=branch_id); db.session.add(assignment)
    assignment.branch_role, assignment.active = branch_role, active
    target.session_version += 1  # Revoke old tokens when authority changes.
    _audit(scope, 'assignment.set', f'{user_id}:{branch_id}', before, dict(branch_role=branch_role, active=active))
    db.session.flush()
    return dict(user_id=user_id, branch_id=branch_id, branch_role=branch_role, active=active)


def manage_hq_scope(user_id, hq_role, active):
    scope = resolve_scope('assignments.manage', consolidated=True, lock=True)
    user_id = integer_id(user_id)
    target = db.session.get(User, user_id, with_for_update=True)
    if target is None: raise NotFound('Resource not found.')
    if not isinstance(hq_role, str) or hq_role not in {'admin', 'analyst'}: raise BadRequest('Invalid HQ role.')
    active = boolean(active)
    assignment = db.session.get(UserHQScope, user_id, with_for_update=True)
    before = dict(hq_role=assignment.hq_role, active=assignment.active) if assignment else None
    if assignment is None:
        assignment = UserHQScope(user_id=user_id); db.session.add(assignment)
    assignment.hq_role, assignment.active = hq_role, active
    target.session_version += 1
    _audit(scope, 'hq_scope.set', str(user_id), before, dict(hq_role=hq_role, active=active))
    db.session.flush()
    return dict(user_id=user_id, hq_role=hq_role, active=active)


def assignment_directory():
    resolve_scope('assignments.manage', consolidated=True)
    return dict(users=[dict(user_id=u.user_id, name=u.name, enabled=u.enabled)
                       for u in db.session.scalars(select(User).order_by(User.user_id))],
                assignments=[dict(user_id=a.user_id, branch_id=a.branch_id, branch_role=a.branch_role, active=a.active)
                             for a in db.session.scalars(select(UserBranch))],
                hq_scopes=[dict(user_id=a.user_id, hq_role=a.hq_role, active=a.active)
                           for a in db.session.scalars(select(UserHQScope))])


def select_scope(branch_id=None, consolidated=False):
    scope = resolve_scope('inventory.read', branch_id=branch_id, consolidated=consolidated)
    if scope.consolidated: session.pop('active_branch_id', None)
    else: session['active_branch_id'] = scope.branches[0]
    return dict(branch_ids=list(scope.branches), role=scope.role, consolidated=scope.consolidated)


def available_scopes():
    actor = identity()
    hq = db.session.get(UserHQScope, actor.user_id, populate_existing=True)
    query = select(Branch).where(Branch.active.is_(True))
    if not hq or not hq.active:
        query = query.join(UserBranch).where(UserBranch.user_id == actor.user_id, UserBranch.active.is_(True))
    return dict(branches=[dict(branch_id=b.branch_id, code=b.code, display_name=b.display_name)
                          for b in db.session.scalars(query.order_by(Branch.branch_id))],
                consolidated_allowed=bool(hq and hq.active))


def stock_rows():
    scope = resolve_scope('inventory.read')
    return [dict(branch_id=s.branch_id, lot_id=s.lot_id, sku_id=s.sku_id, on_hand=s.on_hand,
                 reserved=s.reserved, quarantined_quantity=s.quarantined_quantity, version=s.version)
            for s in db.session.scalars(select(BranchStock).where(BranchStock.branch_id.in_(scope.branches)))]


def lot_detail(lot_id):
    scope = resolve_scope('inventory.read')
    lot = db.session.scalar(select(StockLot).join(BranchStock, BranchStock.lot_id == StockLot.lot_id)
            .where(StockLot.lot_id == lot_id, BranchStock.branch_id.in_(scope.branches)).distinct())
    if lot is None: raise NotFound('Resource not found.')
    # No unscoped provenance JSON: it may name another branch's receipt/actor.
    return dict(lot_id=lot.lot_id, sku_id=lot.sku_id, lot_code=lot.lot_code,
                expiry_date=lot.expiry_date.isoformat() if lot.expiry_date else None,
                expiry_timezone=lot.expiry_timezone, validation_state=lot.validation_state)
