"""T09 adapters for the existing navigation, bounded read APIs and CSV."""
import csv
import io
from flask import Blueprint,request,render_template,Response,current_app,stream_with_context,g
from flask_login import login_required
from authz import resolve_scope
from security import MutationError
import operational_services as queries

bp=Blueprint('operations',__name__)
PAGES={'home':'Dashboard','admin_dashboard':'Dashboard','inventory_page':'Inventory','admin_inventory':'Inventory',
    'catalog.stock_page':'Inventory','all_products_page':'Inventory','low_stock_page':'Inventory','reorder_needed_page':'Inventory',
    'sales':'Sales','reports':'Analytics','analytics':'Analytics','admin_alerts':'Alerts',
    'expiry_alerts_page':'Alerts','expiring_soon_page':'Alerts','staff':'Staff'}
CAPS={'Dashboard':'inventory.read','Inventory':'inventory.read','Sales':'sales.read','Reports':'reports.read',
      'Analytics':'reports.read','Alerts':'alerts.read','Staff':'inventory.read'}


def literal(value):
    text='' if value is None else str(value)
    if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')):return "'"+text
    return text


def export():
    data=queries.report(capability='export.read',sections=())
    def recheck():
        from database import db
        from werkzeug.exceptions import Forbidden
        db.session.rollback()
        g.pop('_login_user',None)  # Reload the revocable session actor, including across streamed context boundaries.
        fresh=resolve_scope('export.read',branch_id=request.args.get('branch_id'))
        if list(fresh.branches)!=data['scope']['branch_ids']:raise Forbidden('Access denied.')
    def generate():
        recheck()
        yield csv_row(['InventraIQ recorded branch facts','MYR','integer units'])
        yield csv_row(['branch_ids',','.join(map(str,data['scope']['branch_ids'])),'consolidated',data['scope']['consolidated']])
        yield csv_row(['start_business_date',data['filters']['start'],'end_business_date_inclusive',data['filters']['end']])
        yield csv_row(['inventory_as_of_utc',data['as_of_utc']])
        yield csv_row(['export_kind','authorized_branch_summary','included_branch_rows',len(data['branches'])])
        yield csv_row(['lot_search_does_not_filter_summary',data['filters']['search'],'lot_status',data['filters']['status']])
        for key,value in data['provenance'].items():yield csv_row([key,value])
        yield csv_row(['branch_id','code','name','physical_units','available_units','reserved_units','quarantined_units',
        'sold_units','unknown_revenue_units','gross_MYR','refunds_MYR','net_MYR','expired_lots','warning_lots','review_lots','low_stock_skus','outgoing_transit','incoming_transit','company_owned_physical','authorized_transfer_loss','approved_po_inbound_units'])
        for index,row in enumerate(data['branches']):
            if index and index%100==0:recheck()
            yield csv_row([row.get(key) for key in ('branch_id','code','name','on_hand','available','reserved','quarantined',
            'sold_units','unknown_revenue_units','recorded_gross','recorded_refunds','recorded_net','expired_lots','warning_lots','review_lots','low_stock_skus','outgoing_transit','incoming_transit','company_owned_physical','authorized_transfer_loss','approved_po_inbound')])
    return Response(stream_with_context(generate()),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="inventraiq-branch-report.csv"','Cache-Control':'no-store'})


def csv_row(values):
    output=io.StringIO(newline='')
    csv.writer(output).writerow([literal(v) for v in values])
    return output.getvalue()


@bp.get('/api/observations')
@login_required
def history_api():
    from historical_queries import read
    try:return read()
    except MutationError as exc:return {'error':str(exc)},exc.status


@bp.get('/operations/observations.csv')
@login_required
def history_export():
    from historical_queries import read
    from database import db
    values=request.args.to_dict(flat=True)
    try:
        # Validate repeated parameters, authority, source revision and first page
        # before sending headers. Clients cannot choose an unbounded chunk size.
        queries.filters(request.args)
        values.update(page=1,page_size=100)
        first=read(values,capability='export.read',aggregate=False)
    except MutationError as exc:return {'error':str(exc)},exc.status
    fields=('observation_id','source_id','source_revision','source_store_id','source_item_id','branch_id','sku_id',
        'business_date','units','coverage','measure_type','provenance','price_minor','price_currency')
    def generate():
        try:
            yield csv_row(['InventraIQ historical observations','synthetic_fixture','not operational POS sales or MYR revenue'])
            yield csv_row(['branch_ids',','.join(map(str,first['scope']['branch_ids'])),'start',first['filters']['start'],'end_inclusive',first['filters']['end']])
            yield csv_row(['active_source_revisions',str(first['source_revisions']),'price_metadata_currency','USD when recorded; otherwise unknown'])
            yield csv_row(fields)
            batch=first;count=0
            while True:
                for row in batch['items']:
                    yield csv_row([row[k] for k in fields]);count+=1
                token=batch['next_cursor']
                if not token:break
                # Release the read snapshot, then re-resolve current assignments
                # and active publication before every further bounded chunk.
                db.session.rollback()
                g.pop('_login_user',None)
                values['cursor']=token
                batch=read(values,capability='export.read',aggregate=False)
            yield csv_row(['export_complete',count,'synthetic_fixture'])
        finally:
            db.session.rollback()
    return Response(stream_with_context(generate()),mimetype='text/csv',headers={
        'Content-Disposition':'attachment; filename="inventraiq-synthetic-observations.csv"','Cache-Control':'no-store'})


@bp.get('/api/observations/summary')
@login_required
def history_summary():
    from historical_queries import read
    try:return read(grouping=True)
    except MutationError as exc:return {'error':str(exc)},exc.status


@bp.get('/api/operations')
@login_required
def api():
    try:return queries.report()
    except MutationError as exc:return {'error':str(exc)},exc.status


@bp.get('/api/forecast-data')
@login_required
def forecast_data():
    from forecasting.data import load_series
    from forecasting.compat import withheld_forecast
    try:
        values=forecast_arguments()
        if not {'sku_id','as_of'}<=set(values):raise MutationError('SKU and historical as_of are required.')
        data=load_series(**values)
        data['forecast']=withheld_forecast(data)
        from forecasting.reader import page
        batch=page({k:data['contract'][k] for k in ('branch_id','sku_id')})
        data['forecast_run_id']=None
        if batch['as_of']==data['contract']['forecast_origin'] and batch['manifest_hash']==data['contract']['manifest_hash'] and batch['items']:
            data['forecast']=batch['items'][0]['forecast'];data['forecast_run_id']=batch['run_id']
        return Response(current_app.json.dumps(data),mimetype='application/json',headers={'Cache-Control':'no-store'})
    except MutationError as exc:return {'error':str(exc)},exc.status


def forecast_arguments():
    allowed={'branch_id','sku_id','as_of','start','training_end','source_revision','manifest_hash'}
    if set(request.args)-allowed or any(len(request.args.getlist(k))!=1 for k in request.args):
        raise MutationError('Unsupported or repeated forecast data parameters.')
    values=request.args.to_dict();values.setdefault('branch_id',None)
    return values


@bp.get('/api/forecast-snapshots')
@login_required
def forecast_snapshots():
    from forecasting.reader import page
    try:return Response(current_app.json.dumps(page(request.args)),mimetype='application/json',headers={'Cache-Control':'no-store'})
    except MutationError as exc:return {'error':str(exc)},exc.status


@bp.get('/api/forecast-monitoring')
@login_required
def forecast_monitoring():
    from forecasting.monitoring import read
    try:
        if set(request.args)-{'branch_id','run_id'} or 'run_id' not in request.args or any(len(request.args.getlist(k))!=1 for k in request.args):
            raise MutationError('A forecast run and optional branch are required.')
        return Response(current_app.json.dumps(read(request.args['run_id'],request.args.get('branch_id'))),
            mimetype='application/json',headers={'Cache-Control':'no-store'})
    except MutationError as exc:return {'error':str(exc)},exc.status


@bp.get('/api/recommendations')
@login_required
def recommendations_api():
    from intelligence import branch_recommendations
    try:return Response(current_app.json.dumps(branch_recommendations(request.args)),mimetype='application/json',headers={'Cache-Control':'no-store'})
    except MutationError as exc:return {'error':str(exc)},exc.status


@bp.get('/operations/export.csv')
@login_required
def csv_export():
    try:return export()
    except MutationError as exc:return {'error':str(exc)},exc.status


def init_operations(app):
    app.register_blueprint(bp)
    from services.planning_presentation import branch_name,missing_inputs
    app.jinja_env.filters['branch_name']=branch_name
    app.jinja_env.filters['planning_inputs']=missing_inputs
    @app.context_processor
    def shell_scope():
        from flask_login import current_user
        from flask import session,g
        from werkzeug.exceptions import Forbidden
        if not current_user.is_authenticated:return {}
        import branch_services
        try:
            from urllib.parse import urlencode
            effective=getattr(g,'authz_scope',None)
            selected=effective.branches[0] if request.args.get('branch_id') and effective and not effective.consolidated else session.get('active_branch_id')
            kept={key:request.args[key] for key in ('start','end','search','status','sort','page_size') if key in request.args}
            target=request.path+('?' + urlencode(kept) if kept else '')
            scopes=branch_services.available_scopes()
            names={str(b['branch_id']):branch_name(b['display_name']) for b in scopes['branches']}
            return dict(shell_scopes=scopes,shell_selected=selected,shell_return_to=target,branch_names=names)
        except Forbidden:return {}
    @app.before_request
    def existing_pages():
        # Historical layouts remain available solely for explicit disposable
        # pre-cutover regressions; normal application navigation uses T09 DTOs.
        if current_app.testing and current_app.config.get('LEGACY_STOCK_REHEARSAL',False):return
        if request.method!='GET':return
        endpoint=request.endpoint
        if endpoint in ('record_sale_page','admin_record_sale'):
            from flask import redirect,url_for
            return redirect(url_for('sales_native.sales_page'))
        try:
            if endpoint in ('api_predictions','ai_predictions'):
                resolve_scope('predictions.read')
                from forecasting.reader import page
                if endpoint=='api_predictions':
                    data=page(request.args)
                    return Response(current_app.json.dumps(data['items']),mimetype='application/json',headers={'Cache-Control':'no-store',
                        'X-InventraIQ-Forecast-State':data['state'],'X-InventraIQ-Forecast-Run':str(data['run_id'] or ''),
                        'X-InventraIQ-Feature-Gate':'synthetic-provisional-only' if data['run_id'] else 'forecast-snapshots-pending-T20'})
                from forecasting.data import view_context
                values=forecast_arguments()
                clock=view_context(values['branch_id'],values.get('as_of'))
                # Explicit data exploration remains /api/forecast-data; ordinary pages read no observation history.
                if set(values)-{'branch_id','as_of','sku_id'}:raise MutationError('Use the historical data API for history filters.')
                data=page({k:v for k,v in values.items() if k in ('branch_id','sku_id') and v is not None})
                from intelligence import branch_recommendations
                recommendation=branch_recommendations({k:v for k,v in values.items() if k in ('branch_id','sku_id') and v is not None})
                return render_template('pages/forecast_unavailable.html',active_page='intelligence',
                    forecast_clock=clock,forecast_history=None,forecast_batch=data,recommendation_report=recommendation)
            if endpoint=='export_data':return export()
            if endpoint in ('get_inventory','branch_stock_api','catalog.stock_api'):
                return queries.report(capability='inventory.read',sections=('inventory',))['inventory']
            if endpoint=='get_alerts':return queries.report(capability='alerts.read',sections=('alerts',))['alerts']
            if endpoint=='lot_api':return queries.lot_detail(request.view_args['lot_id'])
            title=PAGES.get(endpoint)
            if title:
                values=request.args
                if endpoint in ('low_stock_page','reorder_needed_page','expiring_soon_page'):
                    values=request.args.copy()
                    values.setdefault('status','warning' if endpoint=='expiring_soon_page' else 'low_stock')
                    title='Alerts'
                sections={'Inventory':('inventory',),'Sales':('sales',),'Alerts':('alerts',),'Dashboard':('inventory','alerts','sales')}.get(title,())
                if values.get('status')=='low_stock':sections=('inventory','alerts')
                data=queries.report(values,capability=CAPS[title],sections=sections)
                staff=queries.staff_page() if title=='Staff' else None
                # Planning evidence belongs in the dedicated intelligence workspace.
                forecast=None
                recommendation=None
                planning_cards=[]
                if title=='Dashboard' and data['scope']['role'] in ('hq_admin','hq_analyst'):
                    from intelligence import branch_recommendations
                    from services.planning_presentation import review_cards,branch_name
                    recommendation=branch_recommendations({'page_size':'3',**({'branch_id':values['branch_id']} if values.get('branch_id') else {})})
                    names={str(b['branch_id']):branch_name(b['name']) for b in data['branches']}
                    planning_cards=review_cards(recommendation,names)
                visuals=None
                if title in ('Dashboard','Analytics','Reports','Sales'):
                    from services.workspace_views import read
                    visuals=read(data)
                return render_template('pages/operations.html',title=title,report=data,roster=staff,active_page=title.lower(),forecast_batch=forecast,recommendation_report=recommendation,workspace_visuals=visuals,planning_cards=planning_cards)
        except MutationError as exc:
            if (exc.status == 400 and (endpoint in PAGES or endpoint == 'ai_predictions')
                    and not request.is_json and request.accept_mimetypes.best != 'application/json'):
                from flask import url_for
                return render_template('error.html', code=400, description=str(exc),
                    reset_url=url_for(endpoint, branch_id=request.args.get('branch_id'))), 400
            return {'error':str(exc)},exc.status
