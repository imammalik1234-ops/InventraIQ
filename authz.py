"""Server-resolved authorization. No client actor/scope is ever authoritative."""
from dataclasses import dataclass
from flask import current_app, g, has_request_context, request, session
from flask_login import current_user
from sqlalchemy import select
from werkzeug.exceptions import Forbidden, NotFound, Conflict
from database import db
from models import User, Branch, UserBranch, UserHQScope, LegacyProductMapping
from authentication import security_stamp

READ = frozenset({'inventory.read', 'sales.read', 'alerts.read', 'reports.read',
                  'predictions.read', 'export.read'})
CAPABILITIES = {
    'hq_admin': READ | {'consolidated.read', 'catalog.manage', 'branches.manage',
                        'assignments.manage', 'forecasts.publish', 'stock.receive', 'stock.adjust', 'sales.create',
                        'alerts.evaluate', 'profile.manage', 'expiry_policy.manage', 'batch.correct', 'sales.refund',
                        'transfer.read', 'transfer.propose', 'transfer.approve', 'transfer.dispatch', 'transfer.receive', 'transfer.resolve', 'supplier.manage', 'purchasing.read', 'purchasing.manage', 'purchasing.receive'},
    'hq_analyst': READ | {'consolidated.read', 'transfer.read', 'purchasing.read'},
    'manager': READ | {'stock.receive', 'stock.adjust', 'sales.create', 'sales.refund', 'transfer.read', 'transfer.propose', 'transfer.dispatch', 'transfer.receive', 'purchasing.read', 'purchasing.receive'},
    'clerk': READ | {'sales.create'},
}


@dataclass(frozen=True)
class Scope:
    actor_id: int
    branches: tuple
    role: str
    consolidated: bool


def identity(*, lock=False, shared=False):
    if not has_request_context() or not current_user.is_authenticated:
        raise Forbidden('Access denied.')
    # Never trust Flask-Login's cached identity after a same-request security edit.
    actor = db.session.get(User, current_user.user_id, populate_existing=True,
                          with_for_update={'read': True} if shared else (True if lock else None))
    token = session.get('_user_id')
    from authentication import serializer
    try:
        payload = serializer().loads(token, max_age=current_app.config['AUTH_ABSOLUTE_SECONDS'])
        valid = (actor and actor.enabled and payload['id'] == actor.user_id
                 and payload['version'] == actor.session_version
                 and payload['stamp'] == security_stamp(actor))
    except Exception:
        valid = False
    if not valid:
        raise Forbidden('Access denied.')
    return actor


def integer_id(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and 0 < len(value) <= 10 and int(value) > 0:
        return int(value)
    raise Forbidden('Access denied.')


def resolve_scope(capability='inventory.read', *, branch_id=None, consolidated=None, write=False, lock=False, shared=False):
    """Re-resolve every time, including direct services. Scope objects aren't inputs.
    HQ defaults to explicit consolidated.read; every query still has an IN filter.
    Single active assignment is selected automatically, multiple require selection.
    """
    actor = identity(lock=write or lock, shared=shared)
    hq_query = select(UserHQScope).where(UserHQScope.user_id == actor.user_id,
                       UserHQScope.active.is_(True)).execution_options(populate_existing=True)
    hq = db.session.scalar(hq_query.with_for_update(read=shared) if write or lock else hq_query)
    active = tuple(db.session.scalars(select(Branch.branch_id).where(Branch.active.is_(True)).order_by(Branch.branch_id)))
    if hq:
        role = 'hq_' + hq.hq_role
        assignments = {}
        allowed = active
    else:
        assignment_query = select(UserBranch.branch_id, UserBranch.branch_role)
        assignment_query = (assignment_query
            .join(Branch, Branch.branch_id == UserBranch.branch_id)
            .where(UserBranch.user_id == actor.user_id, UserBranch.active.is_(True), Branch.active.is_(True)))
        assignments = dict(db.session.execute(assignment_query.with_for_update(read=shared) if write else assignment_query).all())
        allowed = tuple(sorted(assignments))
        role = None
    selected = branch_id
    if selected is None:
        selected = getattr(g, 'requested_branch_id', None)
    if selected is None:
        selected = session.get('active_branch_id')
    if selected is None and not hq and len(allowed) == 1:
        selected = allowed[0]
    want_all = consolidated is True or (selected is None and hq is not None and consolidated is not False)
    if want_all:
        if not hq or 'consolidated.read' not in CAPABILITIES.get(role, ()) or write:
            raise Forbidden('Select an authorized active branch.')
        branches = allowed
    else:
        if selected is None:
            raise Forbidden('Select an authorized active branch.')
        selected = integer_id(selected)
        if selected not in allowed:
            if branch_id is None and getattr(g,'requested_branch_id',None) is None:
                session.pop('active_branch_id',None)
            raise Forbidden('Access denied.')
        branches = (selected,)
        if not hq:
            role = assignments[selected]
    if capability not in CAPABILITIES.get(role, ()):
        raise Forbidden('Access denied.')
    if write:
        locked = tuple(db.session.scalars(select(Branch.branch_id).where(
            Branch.branch_id.in_(branches), Branch.active.is_(True)).with_for_update(read=shared)))
        if set(locked) != set(branches):
            raise Forbidden('Access denied.')
    return Scope(actor.user_id, branches, role, bool(want_all))


def authorize(capability, *, branch_id=None, write=False):
    return resolve_scope(capability, branch_id=branch_id, write=write)


def legacy_predicate(model, capability='inventory.read'):
    scope = resolve_scope(capability)
    mappings = select(LegacyProductMapping.legacy_product_id).where(LegacyProductMapping.branch_id.in_(scope.branches))
    # Historical sales remain facts. Operational legacy readers must not compare
    # obsolete Product dates once a SKU enters the shared catalog/policy contract.
    if model.__tablename__ != 'sales':
        mappings = mappings.where(LegacyProductMapping.sku_id.not_in(managed_sku_ids()))
    return model.product_id.in_(mappings)


def managed_sku_ids():
    from models import SKU, SKUIdentity, CategoryExpiryPolicy, ProductExpiryPolicyOverride, StockReceipt
    from sqlalchemy import or_
    return select(SKU.sku_id).where(or_(
        SKU.sku_id.in_(select(SKUIdentity.sku_id)),
        SKU.category_id.in_(select(CategoryExpiryPolicy.category_id)),
        SKU.sku_id.in_(select(ProductExpiryPolicyOverride.sku_id)),
        SKU.sku_id.in_(select(StockReceipt.sku_id))))


def legacy_resource(product_id, capability='inventory.read', *, write=False):
    scope = resolve_scope(capability, write=write)
    link = db.session.scalar(select(LegacyProductMapping).where(
        LegacyProductMapping.legacy_product_id == product_id,
        LegacyProductMapping.branch_id.in_(scope.branches)))
    if link is None:
        raise NotFound('Resource not found.')
    if write:
        from models import SKU
        if db.session.scalar(select(SKU.sku_id).where(SKU.sku_id == link.sku_id, SKU.sku_id.in_(managed_sku_ids()))) is not None:
            raise Conflict('This SKU uses lot-based inventory. Legacy stock writes are unavailable pending the allocation cutover.')
    return scope


def scoped_query(model, capability='inventory.read'):
    return model.query.filter(legacy_predicate(model, capability))


# Explicit endpoint inventory: unknown authenticated application endpoints fail closed.
READ_ENDPOINTS = {
    'purchasing.page':'purchasing.read','purchasing.list_api':'purchasing.read','purchasing.detail_api':'purchasing.read',
    'purchasing.suppliers_api':'purchasing.read','purchasing.reconciliation_api':'purchasing.read',
    'transfers.reconciliation_api':'transfer.read','transfers.page':'transfer.read','transfers.list_api':'transfer.read','transfers.detail_api':'transfer.read',
    'operations.api':'reports.read','operations.csv_export':'export.read',
    'operations.history_api':'reports.read','operations.history_export':'export.read',
    'operations.history_summary':'reports.read',
    'operations.forecast_data':'predictions.read',
    'operations.forecast_snapshots':'predictions.read','operations.forecast_monitoring':'predictions.read',
    'operations.recommendations_api':'predictions.read',
    'home': 'inventory.read', 'admin_dashboard': 'inventory.read', 'staff': 'inventory.read',
    'get_products': 'inventory.read', 'get_inventory': 'inventory.read',
    'inventory_page': 'inventory.read', 'all_products_page': 'inventory.read',
    'low_stock_page': 'inventory.read', 'reorder_needed_page': 'inventory.read',
    'expiring_soon_page': 'alerts.read', 'get_alerts': 'alerts.read',
    'expiry_alerts_page': 'alerts.read', 'admin_inventory': 'inventory.read',
    'admin_alerts': 'alerts.read', 'archived_products': 'inventory.read',
    'sales': 'sales.read', 'reports': 'reports.read', 'analytics': 'reports.read',
    'api_predictions': 'predictions.read', 'ai_predictions': 'predictions.read',
    'export_data': 'export.read', 'admin_record_sale': 'sales.create',
    'record_sale_page': 'sales.create', 'admin_settings': 'profile.manage',
    'add_product': 'catalog.manage', 'admin_add_product': 'catalog.manage',
    'edit_product': 'catalog.manage', 'admin_edit_product': 'catalog.manage',
}
WRITE_ENDPOINTS = {
    'purchasing.create_api':'purchasing.manage','purchasing.action_api':'purchasing.read',
    'purchasing.create_form':'purchasing.manage','purchasing.action_form':'purchasing.read',
    'transfers.create_api':'transfer.propose','transfers.create_form':'transfer.propose',
    'transfers.action_api':'transfer.read','transfers.action_form':'transfer.read',
    'add_product_api': 'catalog.manage', 'update_product_api': 'catalog.manage',
    'delete_product_api': 'catalog.manage', 'add_product': 'catalog.manage',
    'admin_add_product': 'catalog.manage', 'edit_product': 'catalog.manage',
    'admin_edit_product': 'catalog.manage', 'delete_product': 'catalog.manage',
    'admin_delete_product': 'catalog.manage', 'remove_stock': 'stock.adjust',
    'record_sale': 'sales.create', 'run_alert_check': 'alerts.evaluate',
    'admin_settings': 'profile.manage',
    'notify_manager': 'unavailable', 'reassign_tasks': 'unavailable', 'request_replacement': 'unavailable',
}
PUBLIC = {'static', 'index', 'role_selection', 'admin_login', 'staff_login', 'logout'}
# These transport capabilities never replace the tool/source-specific checks.
# POST dispatch reads only; existing CSRF middleware still protects the request.
READ_ENDPOINTS.update({'assistant_tools.catalog_route':'inventory.read','assistant_tools.download':'export.read'})
WRITE_ENDPOINTS.update({'assistant_tools.invoke_route':'inventory.read'})
READ_ENDPOINTS.update({'assistant_conversations.history':'inventory.read'})
WRITE_ENDPOINTS.update({f'assistant_conversations.{name}':'inventory.read' for name in ('create','send','cancel','forget')})
READ_ENDPOINTS.update({f'assistant_proposals.{name}':'inventory.read' for name in ('page','review')})
WRITE_ENDPOINTS.update({f'assistant_proposals.{name}':'inventory.read' for name in ('create','confirm','cancel')})
ADMIN_ENDPOINTS = {'branch_admin': 'branches.manage', 'assignment_admin': 'assignments.manage',
                   'hq_scope_admin': 'assignments.manage', 'branch_administration': 'assignments.manage'}


# T06 global catalog writes use explicit HQ capability and consolidated scope.
ADMIN_ENDPOINTS.update({
    'purchasing.supplier_create_api':'supplier.manage','purchasing.supplier_update_api':'supplier.manage','purchasing.supplier_form':'supplier.manage',
    'catalog.sku_create_api': 'catalog.manage', 'catalog.sku_update_api': 'catalog.manage',
    'catalog.category_create_api': 'catalog.manage', 'catalog.assortment_api': 'catalog.manage',
    'catalog.settings_api': 'expiry_policy.manage', 'catalog.policy_api': 'expiry_policy.manage',
    'catalog.lot_correction_api': 'batch.correct', 'catalog.catalog_action': 'catalog.manage',
})
READ_ENDPOINTS.update({
    'catalog.sku_list_api': 'inventory.read', 'catalog.category_list_api': 'inventory.read',
    'catalog.catalog_page': 'inventory.read', 'catalog.stock_api': 'inventory.read',
    'sales_native.sales_page':'sales.read', 'sales_native.list_api':'sales.read', 'sales_native.detail_api':'sales.read',
    'catalog.stock_page': 'inventory.read', 'catalog.receiving_page': 'stock.receive',
})
WRITE_ENDPOINTS.update({'sales_native.create_api':'sales.create', 'sales_native.create_form':'sales.create',
                        'sales_native.refund_api':'sales.refund','sales_native.reversal_api':'sales.refund','sales_native.refund_form':'sales.refund',
                        'catalog.receipt_api': 'stock.receive',
                        'catalog.receiving_page': 'stock.receive', 'catalog.stock_page':'sales.create', 'catalog.native_sale_gate': 'sales.create', 'catalog.adjustment_api':'stock.adjust',
                        'catalog.reservation_api':'stock.adjust','catalog.release_api':'stock.adjust',
                        'catalog.reservation_sale_api':'stock.adjust'})


def init_authorization(app):
    @app.before_request
    def authorize_endpoint():
        g.pop('requested_branch_id', None)
        g.pop('authz_scope', None)
        if request.endpoint is None or request.endpoint in PUBLIC:
            return
        if not current_user.is_authenticated:
            return app.login_manager.unauthorized()
        endpoint = request.endpoint
        if any(len(request.form.getlist(key)) > 1 for key in ('branch_id', 'product_id', 'user_id')):
            raise Forbidden('Access denied.')
        if any(key in request.args for key in ('branch_ids', 'scope', 'consolidated', 'actor_id')):
            raise Forbidden('Access denied.')
        if endpoint in {'scope_selection', 'scope_page'}:
            return  # Selection itself resolves database grants.
        if endpoint in {'branch_stock_api', 'lot_api'}:
            requested = request.args.getlist('branch_id')
            if len(requested) > 1: raise Forbidden('Access denied.')
            if requested: g.requested_branch_id = integer_id(requested[0])
            resolve_scope('inventory.read')
            return
        if endpoint in ADMIN_ENDPOINTS:
            g.authz_scope = resolve_scope(ADMIN_ENDPOINTS[endpoint], consolidated=True)
            return
        if endpoint=='assistant_tools.invoke_route' or endpoint.startswith(('assistant_conversations.','assistant_proposals.')):
            # A CSRF-protected POST carrying a read-tool request is not a
            # business write. The typed dispatcher resolves its capability and
            # untrusted branch selector afresh; do not impose write-scope rules
            # or pre-parse its bounded JSON as a mutation payload here.
            identity()
            return
        safe = request.method in {'GET', 'HEAD', 'OPTIONS'}
        capability = (READ_ENDPOINTS if safe else WRITE_ENDPOINTS).get(endpoint)
        if capability is None:
            raise Forbidden('Access denied.')
        requested = request.args.getlist('branch_id')
        if len(requested) > 1:
            raise Forbidden('Access denied.')
        if requested:
            # Request-local selection only; service calls use the same validated selector.
            g.requested_branch_id = integer_id(requested[0])
        scope = resolve_scope(capability, branch_id=requested[0] if requested else None,
                              write=not safe and capability != 'profile.manage', shared=endpoint.startswith(('sales_native.','transfers.','purchasing.')) or endpoint in {'catalog.receipt_api','catalog.receiving_page','catalog.stock_page','catalog.native_sale_gate','catalog.adjustment_api','catalog.reservation_api','catalog.release_api','catalog.reservation_sale_api'})
        g.authz_scope = scope
        data = request.get_json(silent=True) if request.is_json else request.form
        if data:
            def validate_input(value):
                if isinstance(value, dict) or hasattr(value, 'items'):
                    for key, item in value.items():
                        if key in {'branch_ids', 'scope', 'actor_id', 'consolidated',
                                   'items', 'products', 'product_ids', 'operations'}:
                            raise Forbidden('Access denied.')
                        if key == 'product_id' and request.view_args and 'product_id' in request.view_args:
                            if integer_id(item) != request.view_args['product_id']:
                                raise NotFound('Resource not found.')
                        if key == 'branch_id' and (scope.consolidated or integer_id(item) not in scope.branches):
                            raise Forbidden('Access denied.')
                        validate_input(item)
                elif isinstance(value, list):
                    for item in value: validate_input(item)
            validate_input(data)
        if request.view_args and 'product_id' in request.view_args:
            legacy_resource(request.view_args['product_id'], capability, write=not safe)
