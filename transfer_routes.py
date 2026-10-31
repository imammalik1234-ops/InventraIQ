"""Minimal CSRF-protected T10 adapters; services recheck every permission."""
from uuid import uuid4
from flask import Blueprint,request,render_template,redirect,url_for,flash
from flask_login import login_required
from authz import resolve_scope
from security import transaction,MutationError
import transfer_services as transfers

bp=Blueprint('transfers',__name__)


@bp.errorhandler(MutationError)
def invalid_request(exc):
    return {'error':str(exc)},exc.status


@bp.before_request
def validate_parameters():
    allowed={'branch_id','page','transfer_id'} if request.method=='GET' else {'branch_id'}
    if set(request.args)-allowed or any(len(request.args.getlist(k))!=1 for k in request.args):
        raise MutationError('Invalid or repeated transfer query parameters.')
    if request.form and any(len(request.form.getlist(k))!=1 for k in request.form if k not in {'sku_id','quantity'}):
        raise MutationError('Repeated transition fields are not allowed.')


def body():
    data=request.get_json(silent=True)
    if not isinstance(data,dict):raise MutationError('A JSON object is required.')
    return data


@bp.get('/api/transfers')
@login_required
def list_api():return transfers.listing(request.args.get('page',1))


@bp.get('/api/transfers/<int:transfer_id>')
@login_required
def detail_api(transfer_id):return transfers.detail(transfer_id)


@bp.post('/api/transfers')
@login_required
@transaction
def create_api():return transfers.create(body())


@bp.post('/api/transfers/<int:transfer_id>/<action>')
@login_required
@transaction
def action_api(transfer_id,action):return transfers.transition(transfer_id,action,body())


@bp.get('/transfers')
@login_required
def page():
    scope=resolve_scope('transfer.read')
    try:
        detail=transfers.detail(request.args['transfer_id']) if 'transfer_id' in request.args else None
        return render_template('transfers.html',data=transfers.listing(request.args.get('page',1)),detail=detail,
            choices=transfers.choices() if scope.role in ('hq_admin','manager') else None,
            scope=scope,execution_forms=execution_forms(detail,scope),request_key=lambda:str(uuid4()),active_page='transfers')
    except MutationError as exc:return {'error':str(exc)},exc.status


@bp.post('/transfers/create')
@login_required
@transaction
def create_form():
    allowed={'csrf_token','destination_branch_id','planned_arrival_utc','sku_id','quantity','request_key','business_reference','reason'}
    if set(request.form)-allowed:raise MutationError('Invalid proposal form.')
    ids=request.form.getlist('sku_id');qty=request.form.getlist('quantity')
    if len(ids)!=len(qty):raise MutationError('Mismatched SKU lines.')
    data={key:request.form.get(key) for key in ('destination_branch_id','planned_arrival_utc','request_key','business_reference','reason')}
    data['lines']=[dict(sku_id=sku,quantity=q) for sku,q in zip(ids,qty) if sku or q]
    result=transfers.create(data)
    return redirect(url_for('transfers.page',branch_id=request.args.get('branch_id'),transfer_id=result['transfer_id']))


@bp.post('/transfers/<int:transfer_id>/<action>')
@login_required
@transaction
def action_form(transfer_id,action):
    if action in ('dispatch','receive','discrepancy','resolve','return'):
        import json
        if set(request.form)-{'csrf_token','payload','reason','quantity','disposition','decision','planned_arrival_utc'}:raise MutationError('Invalid execution form.')
        try:data=json.loads(request.form.get('payload',''))
        except (ValueError,TypeError):raise MutationError('Invalid execution payload.') from None
        data['reason']=request.form.get('reason')
        if action in ('receive','return'):data['lines'][0]['quantity']=request.form.get('quantity')
        if action=='receive':data['lines'][0]['disposition']=request.form.get('disposition')
        if action=='discrepancy':data['quantity']=request.form.get('quantity')
        if action=='resolve':data['decision']=request.form.get('decision')
        if action=='return':data['planned_arrival_utc']=request.form.get('planned_arrival_utc')
        result=transfers.transition(transfer_id,action,data)
        flash(f"Transfer #{result['transfer_id']}: {result['state']}, operation #{result['operation_id']}.",'success')
        return redirect(url_for('transfers.page',branch_id=request.args.get('branch_id'),transfer_id=result['transfer_id']))
    if set(request.form)-{'csrf_token','expected_version','request_key','business_reference','reason'}:raise MutationError('Invalid transition form.')
    result=transfers.transition(transfer_id,action,{key:request.form.get(key) for key in ('expected_version','request_key','business_reference','reason')})
    flash(f"Transfer #{result['transfer_id']}: {result['state']} (version {result['version']}), operation #{result['operation_id']}.",'success')
    return redirect(url_for('transfers.page',branch_id=request.args.get('branch_id'),transfer_id=result['transfer_id']))


def execution_forms(detail,scope):
    if not detail or scope.consolidated:return []
    from database import db
    from models import BranchStock
    source=detail['source_branch_id'] in scope.branches;dest=detail['destination_branch_id'] in scope.branches
    common=dict(expected_version=detail['version'],business_reference='TRANSFER-'+str(detail['transfer_id']),reason='')
    result=[]
    if source and scope.role in ('hq_admin','manager') and detail['state']=='Approved':
        result.append(dict(action='dispatch',data=dict(common,request_key=str(uuid4()),lines=[dict(allocation_id=a['allocation_id'],expected_revision=db.session.get(BranchStock,(detail['source_branch_id'],a['lot_id'])).version) for a in detail['reservations']])))
    if dest and scope.role in ('hq_admin','manager') and detail['state'] in ('Dispatched','PartiallyReceived','Exception'):
        for a in detail['transit']:
            if not a['remaining']:continue
            stock=db.session.get(BranchStock,(detail['destination_branch_id'],a['lot_id']))
            result.append(dict(action='receive',data=dict(common,request_key=str(uuid4()),lines=[dict(allocation_id=a['allocation_id'],quantity=a['remaining'],expected_revision=stock.version if stock else 0,disposition='saleable')])))
            result.append(dict(action='discrepancy',data=dict(common,request_key=str(uuid4()),allocation_id=a['allocation_id'],quantity=a['remaining'])))
    if dest and scope.role=='hq_admin':
        for d in detail['discrepancies']:
            if not d['resolution']:result.append(dict(action='resolve',data=dict(common,request_key=str(uuid4()),discrepancy_id=d['discrepancy_id'],decision='quarantine_acknowledged' if d['location']=='quarantine' else 'found')))
    if dest and scope.role in ('hq_admin','manager') and any(a['received'] for a in detail['transit']):
        for a in detail['transit']:
            if a['received']:result.append(dict(action='return',data=dict(common,request_key=str(uuid4()),planned_arrival_utc='',lines=[dict(allocation_id=a['allocation_id'],quantity=a['received'])])))
    return result


@bp.get('/api/transfers/reconciliation')
@login_required
def reconciliation_api():
    import transfer_execution
    return dict(items=transfer_execution.reconcile())
