"""Flask adapters for T06; services own capability checks and caller owns commit."""
import json
from uuid import uuid4
from flask import Blueprint, request, render_template, redirect, url_for, g
from flask_login import login_required
from sqlalchemy import select
from database import db
from models import SKU, Branch, CategoryExpiryPolicy, ProductExpiryPolicyOverride
from security import transaction, MutationError
from authz import resolve_scope
import catalog_services as catalog
import receiving_services as receiving
import inventory_ledger as ledger
import sales_services as sales
from expiry import POLICY_FIELDS

bp=Blueprint('catalog',__name__)


def body():
    data=request.get_json(silent=True)
    if not isinstance(data,dict):raise MutationError('A JSON object is required.')
    return data


def form_boolean(data,name,default=None):
    value=data.get(name)
    if value is None and default is not None:return default
    if value not in ('true','false'):raise MutationError(name+' must explicitly be true or false.')
    return value=='true'


@bp.get('/api/skus')
@login_required
def sku_list_api():return catalog.catalog_list()


@bp.post('/api/skus')
@login_required
@transaction
def sku_create_api():return catalog.save_sku(body())


@bp.patch('/api/skus/<int:sku_id>')
@login_required
@transaction
def sku_update_api(sku_id):return catalog.save_sku(body(),sku_id)


@bp.get('/api/categories')
@login_required
def category_list_api():return catalog.category_list()


@bp.post('/api/categories')
@login_required
@transaction
def category_create_api():return catalog.create_category(body())


@bp.put('/api/expiry-settings')
@login_required
@transaction
def settings_api():
    data=body()
    if set(data)!={'label_timezone','expected_version','reason'}:raise MutationError('Invalid timezone settings fields.')
    return catalog.save_settings(data['label_timezone'],data['expected_version'],data['reason'])


@bp.route('/api/expiry-policies/<kind>/<int:key>',methods=['PUT','DELETE'])
@login_required
@transaction
def policy_api(kind,key):
    data=body()
    if set(data)!={'policy','expected_revision','reason'}:raise MutationError('Supply policy, expected_revision and reason.')
    return catalog.save_policy(kind,key,data['policy'],data['expected_revision'],data['reason'],remove=request.method=='DELETE')


@bp.put('/api/assortments/<int:branch_id>/<int:sku_id>')
@login_required
@transaction
def assortment_api(branch_id,sku_id):return catalog.save_assortment(branch_id,sku_id,body())


@bp.patch('/api/lot-corrections/<int:lot_id>')
@login_required
@transaction
def lot_correction_api(lot_id):return catalog.correct_lot(lot_id,body())


@bp.post('/api/receipts')
@login_required
@transaction
def receipt_api():return receiving.receive(body())


@bp.get('/api/sku-stock')
@login_required
def stock_api():return receiving.holdings()


@bp.post('/api/sku-sales')
@login_required
@transaction
def native_sale_gate():
    return sales.single_sale_adapter(body())


@bp.get('/catalog')
@login_required
def catalog_page():
    scope=resolve_scope('inventory.read')
    hq=scope.role=='hq_admin'
    policy_rows=[]
    if hq:
        for kind,model,attribute in [('category',CategoryExpiryPolicy,'category_id'),('sku',ProductExpiryPolicyOverride,'sku_id')]:
            for row in db.session.scalars(select(model)):
                policy_rows.append(dict(kind=kind,key=getattr(row,attribute),revision=row.revision,
                                        **{field:getattr(row,field) for field in POLICY_FIELDS}))
    return render_template('sku_catalog.html',skus=catalog.catalog_list(include_archived=hq),categories=catalog.category_list(),
        assortments=catalog.assortment_list(),settings=catalog.settings(),policy_rows=policy_rows,
        branches=list(db.session.scalars(select(Branch).order_by(Branch.branch_id))) if hq else [],active_page='catalog')


@bp.post('/catalog/actions')
@login_required
@transaction
def catalog_action():
    data=request.form;action=data.get('catalog_action');reason=data.get('reason')
    if action=='category':catalog.create_category(dict(code=data.get('code'),display_name=data.get('display_name'),reason=reason))
    elif action=='archive':
        catalog.save_sku(dict(active=False,expected_version=data.get('expected_version'),reason=reason),data.get('sku_id'))
    elif action=='sku':
        try:attrs=json.loads(data.get('variant_attributes','{}'))
        except (ValueError,TypeError):raise MutationError('Variant attributes must be a JSON object.') from None
        catalog.save_sku(dict(code=data.get('code'),name=data.get('name'),category_id=data.get('category_id'),unit=data.get('unit'),
            variant_attributes=attrs,default_price=data.get('default_price'),active=form_boolean(data,'active'),
            expected_version=data.get('expected_version'),reason=reason),data.get('sku_id') or None)
    elif action=='settings':catalog.save_settings(data.get('label_timezone'),data.get('expected_version'),reason)
    elif action=='policy':
        values={field:data.get(field) for field in POLICY_FIELDS}
        values['require_expiry']=form_boolean(data,'require_expiry')
        catalog.save_policy(data.get('kind'),data.get('key'),values,data.get('expected_revision'),reason,remove=form_boolean(data,'remove',False))
    elif action=='assortment':
        catalog.save_assortment(data.get('branch_id'),data.get('sku_id'),dict(enabled=form_boolean(data,'enabled'),
            sale_price_override=data.get('sale_price_override'),reorder_level_override=data.get('reorder_level_override'),
            expected_version=data.get('expected_version'),reason=reason))
    else:raise MutationError('Invalid catalog action.')
    return redirect(url_for('catalog.catalog_page'))


@bp.route('/receiving',methods=['GET','POST'])
@login_required
@transaction
def receiving_page():
    if request.method=='POST':
        data={key:value for key,value in request.form.items() if key!='csrf_token'}
        result=receiving.receive(data)
        return redirect(url_for('catalog.receiving_page',branch_id=request.args.get('branch_id'),receipt=result['receipt_id']))
    scope = resolve_scope('stock.receive')
    if scope.consolidated:
        import branch_services
        return render_template('scope.html', scopes=branch_services.available_scopes(),
                               receiving_selection=True, active_page='receiving')
    return render_template('receiving.html',choices=receiving.receiving_choices(),holdings=receiving.holdings(),
                           receipts=receiving.receipts(),settings=catalog.settings(),active_page='receiving')


@bp.route('/sku-inventory',methods=['GET','POST'])
@login_required
@transaction
def stock_page():
    if request.method=='POST':
        data={key:value for key,value in request.form.items() if key!='csrf_token'}
        action=data.pop('stock_action',None)
        if action=='sale':result=sales.single_sale_adapter(data)
        elif action=='adjustment':result=ledger.adjust(data)
        else:raise MutationError('Invalid inventory action.')
        from flask import current_app,flash
        if not (current_app.testing and current_app.config.get('LEGACY_STOCK_REHEARSAL',False)):
            flash(f"Committed operation #{result['operation_id']}.",'success')
            return redirect(url_for('catalog.stock_page',branch_id=request.args.get('branch_id')))
        return redirect(url_for('catalog.stock_page',operation=result['operation_id']))
    scope=resolve_scope('inventory.read')
    holdings=receiving.holdings()
    choices={row['sku_id']:row for row in holdings}
    result=None
    if request.args.get('operation'):
        from models import InventoryOperation
        from authz import integer_id
        operation=db.session.scalar(select(InventoryOperation).where(
            InventoryOperation.operation_id==integer_id(request.args['operation']),
            InventoryOperation.branch_id.in_(scope.branches),InventoryOperation.status=='committed'))
        if operation is None:
            from werkzeug.exceptions import NotFound
            raise NotFound('Resource not found.')
        result=operation.result
    return render_template('sku_inventory.html',holdings=holdings,sku_choices=choices.values(),result=result,
        can_sell=not scope.consolidated and scope.role in ('hq_admin','manager','clerk'),
        can_adjust=not scope.consolidated and scope.role in ('hq_admin','manager'),
        operation_key=lambda:str(uuid4()),active_page='inventory')


@bp.post('/api/stock-adjustments')
@login_required
@transaction
def adjustment_api():return ledger.adjust(body())


@bp.post('/api/stock-reservations')
@login_required
@transaction
def reservation_api():return ledger.allocate(body(),reserve=True)


@bp.post('/api/stock-reservations/release')
@login_required
@transaction
def release_api():return ledger.settle_reservation(body())


@bp.post('/api/stock-reservations/fulfil')
@login_required
@transaction
def reservation_sale_api():return sales.reserved_sale_adapter(body())
