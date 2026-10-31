"""Reconcile actual authorized service results and stable synthetic business facts."""
import hashlib
from sqlalchemy import select,func
from database import db
from models import (InventoryOperation,InventoryMovement,BranchStock,Branch,SKU,SaleLine,PricedSale,SaleRefund,
    PurchaseOrder,PurchaseOrderLine,StockTransfer,TransferExecution,TransferTransit)
from simulation.runtime import actor,attest,history_hash
from simulation.manifest import MANIFEST as M,canonical
from simulation.runner import normalized
from simulation.schema import SimulationRun,SimulationStep
import inventory_ledger as ledger
import sales_services as sales
import purchasing_services as purchasing
import transfer_execution as execution
import operational_services as reports


def verify(app):
    root,private=attest(app._t15_target)
    preserved=history_hash(root)==private['history_hash']
    with actor(app,901):
        state=db.session.execute(select(SimulationRun)).mappings().first()
        if not state or state['status']!='complete':raise ValueError('Simulation is not complete')
        ledger_rows=ledger.reconcile();sale_rows=sales.reconcile_sales();po_rows=purchasing.reconcile();transfer_rows=execution.reconcile()
        report=reports.report(dict(start='2031-01-01',end='2031-01-03',page_size=100))
        operation_counts=dict(db.session.execute(select(InventoryOperation.operation_type,func.count()).group_by(InventoryOperation.operation_type)).all())
        gross=db.session.scalar(select(func.sum(PricedSale.total_minor))) or 0
        refunds=db.session.scalar(select(func.sum(SaleRefund.total_minor))) or 0
        stocks=db.session.scalar(select(func.sum(BranchStock.on_hand))) or 0
        transit=db.session.scalar(select(func.sum(TransferTransit.remaining))) or 0
        loss=db.session.scalar(select(func.sum(TransferTransit.lost))) or 0
        sales_units=db.session.scalar(select(func.sum(SaleLine.quantity))) or 0
        # Stable facts exclude private password hashes/sessions and real T14 import times.
        names=['authorization_audit','catalog_audit','skus','sku_identities','category_expiry_policies',
            'sku_expiry_policy_overrides','branch_products','stock_lots','branch_stock','stock_receipts',
            'inventory_operations','inventory_movements','inventory_documents','priced_sales','sale_lines','sale_allocations',
            'sale_refunds','refund_lines','suppliers','purchase_orders','purchase_order_lines','purchase_receipt_links',
            'purchasing_events','stock_transfers','stock_transfer_lines','stock_transfer_allocations','stock_transfer_events',
            'transfer_executions','transfer_transit','transfer_transit_facts','transfer_discrepancies','transfer_resolutions']
        tables={name:[dict(row) for row in db.session.execute(select(db.metadata.tables[name])).mappings()] for name in names}
        for name,rows in tables.items():rows.sort(key=lambda row:canonical(normalized(row)))
        fingerprint=hashlib.sha256(canonical(normalized(tables)).encode()).hexdigest()
        sales_info=dict(gross_minor=gross,refund_minor=refunds,net_minor=gross-refunds,currency='MYR',
            provenance='simulated_not_real_world_revenue',sold_units=sales_units)
        conservation=dict(initial=350,po_received=17,sold=56,saleable_refund=2,writeoff=2,authorized_loss=loss,
            current_on_hand=stocks,in_transit=transit,company_physical_plus_transit=stocks+transit,
            balanced=350+17-56+2-2-loss==stocks+transit)
        statuses=[dict(transfer_id=t.transfer_id,state=db.session.get(TransferExecution,t.transfer_id).state)
            for t in db.session.scalars(select(StockTransfer).order_by(StockTransfer.transfer_id))]
        branch_codes=list(db.session.scalars(select(Branch.code).order_by(Branch.branch_id)))
        if branch_codes!=M['branches']:raise ValueError('Demo branch set differs')
        valid=preserved and conservation['balanced'] and all(row['balanced'] for group in [ledger_rows,sale_rows,po_rows,transfer_rows] for row in group)
        valid=valid and (gross,refunds,gross-refunds)==(report['total']['gross_minor'],report['total']['refund_minor'],report['total']['gross_minor']-report['total']['refund_minor'])
        valid=valid and report['total']['on_hand']==stocks and report['total']['outgoing_transit']==transit
        return normalized(dict(ok=bool(valid),provenance='synthetic_demo',simulation_id=M['simulation_id'],as_of=M['as_of'],
            business_sha256=fingerprint,completed_steps=state['completed_steps'],branch_codes=branch_codes,
            historical_observations_unchanged=preserved,operations=operation_counts,ledger=ledger_rows,sales=sale_rows,
            purchasing=po_rows,transfers=transfer_rows,transfer_states=statuses,financial=sales_info,conservation=conservation,
            branches=report['branches'],total=report['total'],alerts=report['alerts'],report_provenance=report['provenance'],
            real_m5_ingestion='BLOCKED',mysql='Unverified; disposable SQLite only'))
