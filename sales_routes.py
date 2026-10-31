"""Thin Flask/Jinja adapters. Services authorize independently."""
from uuid import uuid4
from flask import Blueprint,request,render_template,redirect,url_for
from flask_login import login_required
from security import transaction,MutationError
from authz import resolve_scope
import sales_services as sales
import receiving_services as receiving

bp=Blueprint('sales_native',__name__)


def body():
    data=request.get_json(silent=True)
    if not isinstance(data,dict):raise MutationError('A JSON object is required.')
    return data


@bp.get('/api/priced-sales')
@login_required
@transaction
def list_api():
    try:return sales.sale_detail(request.args['sale_id']) if 'sale_id' in request.args else sales.sales_list()
    except MutationError as exc:return {'error':str(exc)},exc.status


@bp.post('/api/priced-sales')
@login_required
@transaction
def create_api():return sales.create_sale(body())


@bp.post('/api/priced-sales/<int:sale_id>/refunds')
@login_required
@transaction
def refund_api(sale_id):return sales.refund_sale(sale_id,body())


@bp.post('/api/priced-sales/<int:sale_id>/reversal')
@login_required
@transaction
def reversal_api(sale_id):return sales.refund_sale(sale_id,body(),reversal=True)


@bp.get('/branch-sales')
@login_required
def sales_page():
    scope=resolve_scope('sales.read')
    try:
        detail=sales.sale_detail(request.args['sale_id']) if 'sale_id' in request.args else None
        choices=receiving.sales_choices(request.args)
        data=sales.sales_list()
    except MutationError as exc:return {'error':str(exc)},exc.status
    return render_template('branch_sales.html',data=data,detail=detail,choices=choices['items'],choice_page=choices,
        can_sell=not scope.consolidated and scope.role in ('hq_admin','manager','clerk'),
        can_refund=not scope.consolidated and scope.role in ('hq_admin','manager'),request_key=lambda:str(uuid4()),active_page='sales')


@bp.post('/branch-sales/create')
@login_required
@transaction
def create_form():
    if set(request.form)-{'csrf_token','sku_id','quantity','request_key','business_reference','reason'}:
        raise MutationError('Invalid sale form fields.')
    ids=request.form.getlist('sku_id');quantities=request.form.getlist('quantity')
    if len(ids)!=len(quantities):raise MutationError('Mismatched sale lines.')
    lines=[]
    for sku,quantity in zip(ids,quantities):
        if not sku and not quantity:continue
        lines.append(dict(sku_id=sku,quantity=quantity))
    result=sales.create_sale(dict(lines=lines,**{k:request.form.get(k) for k in ('request_key','business_reference','reason')}))
    return redirect(url_for('sales_native.sales_page',branch_id=request.args.get('branch_id'),sale_id=result['sale_id']))


@bp.post('/branch-sales/<int:sale_id>/refund')
@login_required
@transaction
def refund_form(sale_id):
    allowed={'csrf_token','request_key','business_reference','reason','mode','refund_action','allocation_id','quantity','disposition','saleable_verified','inspection_evidence'}
    if set(request.form)-allowed:raise MutationError('Invalid refund form fields.')
    data={k:request.form.get(k) for k in ('request_key','business_reference','reason','mode')}
    action=request.form.get('refund_action','refund')
    if action not in ('refund','reversal'):raise MutationError('Invalid refund action.')
    if data['mode']=='partial':
        verified=request.form.get('saleable_verified','false')
        if verified not in ('true','false'):raise MutationError('Invalid inspection verification.')
        data['lines']=[dict(allocation_id=request.form.get('allocation_id'),quantity=request.form.get('quantity'),
            disposition=request.form.get('disposition'),saleable_verified=verified=='true',inspection_evidence=request.form.get('inspection_evidence',''))]
    sales.refund_sale(sale_id,data,reversal=action=='reversal')
    return redirect(url_for('sales_native.sales_page',branch_id=request.args.get('branch_id'),sale_id=sale_id))
