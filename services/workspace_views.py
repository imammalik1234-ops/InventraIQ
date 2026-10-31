"""Bounded, scoped presentation data. No business decisions, writes or fitting."""
from datetime import timedelta
from sqlalchemy import select,func,or_,and_
from flask import url_for
from database import db
from authz import resolve_scope
from security import MutationError
from models import PricedSale,SaleRefund,StockTransfer,TransferExecution,PurchaseOrder
from sales_services import amount


def read(report):
    ids=report['scope']['branch_ids']
    scope=resolve_scope('reports.read')
    if list(scope.branches)!=ids:
        # Explicit request-local branch selection is already resolved by authz.
        scope=resolve_scope('reports.read',branch_id=ids[0] if len(ids)==1 else None)
    if list(scope.branches)!=ids:raise MutationError('Workspace scope changed.',409)
    from datetime import date
    start=date.fromisoformat(report['filters']['start']);end=date.fromisoformat(report['filters']['end'])
    # Match the operational report's existing inclusive 366-day limit. A chart
    # must not reject a date range accepted by the report and its CSV export.
    if not 0<=(end-start).days<=365:raise MutationError('Unsupported chart date window.')
    days=[start+timedelta(days=i) for i in range((end-start).days+1)]
    gross=dict(db.session.execute(select(PricedSale.business_date,func.sum(PricedSale.total_minor))
        .where(PricedSale.branch_id.in_(ids),PricedSale.business_date.between(start,end))
        .group_by(PricedSale.business_date)).all())
    refunds=dict(db.session.execute(select(SaleRefund.business_date,func.sum(SaleRefund.total_minor))
        .where(SaleRefund.branch_id.in_(ids),SaleRefund.business_date.between(start,end))
        .group_by(SaleRefund.business_date)).all())
    sales=[dict(date=d.isoformat(),gross_minor=int(gross.get(d,0)),refund_minor=int(refunds.get(d,0)),
        gross=amount(int(gross.get(d,0))),refunds=amount(int(refunds.get(d,0)))) for d in days]
    stock=[dict(label=b['name'],code=b['code'],branch_id=b['branch_id'],available=b['available'],
        reserved=b['reserved'],quarantined=b['quarantined'],on_hand=b['on_hand'],
        unavailable=max(0,b['on_hand']-b['available']-b['reserved']-b['quarantined'])) for b in report['branches']]
    queues=dict(transfers=[],purchases=[],transfer_count=0,purchase_count=0)
    if scope.role in ('hq_admin','hq_analyst','manager'):
        # Same source/destination visibility as native T10 detail/list services.
        transfer_visible=or_(StockTransfer.source_branch_id.in_(ids),and_(
            StockTransfer.destination_branch_id.in_(ids),
            StockTransfer.transfer_id.in_(select(TransferExecution.transfer_id))))
        tq=select(StockTransfer,TransferExecution.state.label('execution_state')).outerjoin(
            TransferExecution,TransferExecution.transfer_id==StockTransfer.transfer_id).where(
            transfer_visible,StockTransfer.state.in_(('Submitted','Approved')),
            or_(TransferExecution.state.is_(None),TransferExecution.state!='Received'))
        pq=select(PurchaseOrder).where(PurchaseOrder.branch_id.in_(ids),
            PurchaseOrder.state.in_(('Submitted','Approved','PartiallyReceived')))
        queues['transfer_count']=db.session.scalar(select(func.count()).select_from(tq.subquery())) or 0
        queues['purchase_count']=db.session.scalar(select(func.count()).select_from(pq.subquery())) or 0
        for t,execution_state in db.session.execute(tq.order_by(StockTransfer.transfer_id.desc()).limit(4)):
            branch=t.source_branch_id if t.source_branch_id in ids else t.destination_branch_id
            queues['transfers'].append(dict(id=t.transfer_id,state=execution_state or t.state,source=t.source_branch_id,
                destination=t.destination_branch_id,url=url_for('transfers.page',branch_id=branch,transfer_id=t.transfer_id)))
        for p in db.session.scalars(pq.order_by(PurchaseOrder.order_id.desc()).limit(4)):
            queues['purchases'].append(dict(id=p.order_id,state=p.state,
                url=url_for('purchasing.page',branch_id=p.branch_id,order_id=p.order_id)))
    fresh=resolve_scope('reports.read',branch_id=ids[0] if len(ids)==1 else None)
    if fresh!=scope:raise MutationError('Workspace scope changed.',409)
    return dict(stock_branches=stock,sales_days=sales,queues=queues,
        has_recorded_sales=bool(gross or refunds),
        stock_max=max([s['on_hand'] for s in stock]+[1]),
        sales_max=max([max(d['gross_minor'],d['refund_minor']) for d in sales]+[1]),
        period=dict(start=start.isoformat(),end=end.isoformat()),as_of=report['as_of_utc'],
        synthetic='simulation' in report['provenance'])
