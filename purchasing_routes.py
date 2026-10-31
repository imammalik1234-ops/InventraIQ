"""T12 Flask/Jinja adapters. All services authorize independently."""
from uuid import uuid4
from flask import Blueprint,request,render_template,redirect,url_for,flash
from flask_login import login_required
from sqlalchemy import select
from database import db
from models import SKU,BranchProduct,StockLot,BranchStock
from authz import resolve_scope
from security import MutationError,transaction
import purchasing_services as purchasing

bp=Blueprint('purchasing',__name__)


@bp.errorhandler(MutationError)
def invalid(exc):return {'error':str(exc)},exc.status


@bp.before_request
def parameters():
    allowed={'branch_id','page','order_id'} if request.method=='GET' else {'branch_id'}
    if set(request.args)-allowed or any(len(request.args.getlist(k))!=1 for k in request.args):raise MutationError('Invalid purchasing query.')
    if request.form and any(len(request.form.getlist(k))!=1 for k in request.form if k not in {'sku_id','quantity','unit_cost'}):raise MutationError('Repeated purchasing fields.')


def body():
    data=request.get_json(silent=True)
    if not isinstance(data,dict):raise MutationError('Supply a JSON object.')
    return data


@bp.get('/api/suppliers')
@login_required
def suppliers_api():return purchasing.suppliers()


@bp.post('/api/suppliers')
@login_required
@transaction
def supplier_create_api():return purchasing.save_supplier(body())


@bp.patch('/api/suppliers/<int:supplier_id>')
@login_required
@transaction
def supplier_update_api(supplier_id):return purchasing.save_supplier(body(),supplier_id)


@bp.get('/api/purchase-orders')
@login_required
def list_api():return purchasing.listing(request.args.get('page',1))


@bp.get('/api/purchase-orders/<int:order_id>')
@login_required
def detail_api(order_id):return purchasing.detail(order_id)


@bp.post('/api/purchase-orders')
@login_required
@transaction
def create_api():return purchasing.create(body())


@bp.post('/api/purchase-orders/<int:order_id>/<action>')
@login_required
@transaction
def action_api(order_id,action):return purchasing.transition(order_id,action,body())


@bp.get('/api/purchase-orders/reconciliation')
@login_required
def reconciliation_api():return dict(items=purchasing.reconcile())


@bp.get('/purchasing')
@login_required
def page():
    scope=resolve_scope('purchasing.read')
    detail=purchasing.detail(request.args['order_id']) if request.args.get('order_id') else None
    skus=list(db.session.scalars(select(SKU).join(BranchProduct).where(BranchProduct.branch_id.in_(scope.branches),
        SKU.active.is_(True),BranchProduct.enabled.is_(True)).distinct().order_by(SKU.code).limit(1000))) if not scope.consolidated else []
    # Presentation hints only: bounded, already authorized branch/SKU/supplier lots.
    # IDs remain directly enterable for a known lot outside the suggestion window.
    lot_choices={}
    if detail and not scope.consolidated and scope.role in ('hq_admin','manager') and detail['state'] in ('Approved','PartiallyReceived'):
        for line in detail['lines']:
            if line['outstanding']:
                lot_choices[line['line_id']]=list(db.session.scalars(select(StockLot).join(BranchStock,BranchStock.lot_id==StockLot.lot_id).where(
                    BranchStock.branch_id==detail['branch_id'],BranchStock.branch_id.in_(scope.branches),
                    StockLot.sku_id==line['sku_id'],StockLot.supplier_id==detail['supplier_id']).order_by(StockLot.lot_id).limit(100)))
    return render_template('purchasing.html',scope=scope,detail=detail,lot_choices=lot_choices,data=purchasing.listing(request.args.get('page',1)),
        suppliers=purchasing.suppliers(),skus=skus,request_key=lambda:str(uuid4()),active_page='purchasing')


@bp.post('/purchasing/supplier')
@login_required
@transaction
def supplier_form():
    allowed={'csrf_token','supplier_id','code','display_name','active','expected_version','reason'}
    if set(request.form)-allowed:raise MutationError('Invalid supplier form.')
    if request.form.get('active') not in ('true','false'):raise MutationError('Explicit supplier active status required.')
    data={k:request.form.get(k) for k in ('display_name','expected_version','reason')};data['active']=request.form['active']=='true'
    key=request.form.get('supplier_id') or None
    if key is None:data['code']=request.form.get('code')
    purchasing.save_supplier(data,key)
    return redirect(url_for('purchasing.page',branch_id=request.args.get('branch_id')))


@bp.post('/purchasing/create')
@login_required
@transaction
def create_form():
    if set(request.form)-{'csrf_token','supplier_id','expected_date','sku_id','quantity','unit_cost','request_key','business_reference','reason'}:raise MutationError('Invalid order form.')
    ids=request.form.getlist('sku_id');qty=request.form.getlist('quantity');cost=request.form.getlist('unit_cost')
    if not len(ids)==len(qty)==len(cost):raise MutationError('Mismatched PO lines.')
    data={k:request.form.get(k) for k in ('supplier_id','expected_date','request_key','business_reference','reason')}
    data['lines']=[dict(sku_id=s,quantity=q,unit_cost=c) for s,q,c in zip(ids,qty,cost) if s or q or c]
    result=purchasing.create(data)
    return redirect(url_for('purchasing.page',branch_id=request.args.get('branch_id'),order_id=result['order_id']))


@bp.post('/purchasing/<int:order_id>/<action>')
@login_required
@transaction
def action_form(order_id,action):
    fields={'csrf_token','expected_version','request_key','business_reference','reason'}
    if action=='receive':fields|={'line_id','mode','lot_id','lot_code','supplier_lot_code','quantity','expiry_date','external_reference','receipt_kind','evidence'}
    if set(request.form)-fields:raise MutationError('Invalid purchasing transition form.')
    data={k:request.form.get(k) for k in ('expected_version','request_key','business_reference','reason')}
    if action=='receive':
        data['line_id']=request.form.get('line_id')
        keys=('mode','quantity','expiry_date','external_reference','receipt_kind','evidence')
        data['delivery']={k:request.form.get(k) for k in keys}
        mode=data['delivery']['mode']
        if mode=='new_lot':data['delivery'].update(lot_code=request.form.get('lot_code'),supplier_lot_code=request.form.get('supplier_lot_code'))
        else:data['delivery']['lot_id']=request.form.get('lot_id')
    result=purchasing.transition(order_id,action,data)
    flash(f"PO #{result['order_id']}: {result['state']}, operation #{result['operation_id']}.",'success')
    return redirect(url_for('purchasing.page',branch_id=request.args.get('branch_id'),order_id=result['order_id']))
