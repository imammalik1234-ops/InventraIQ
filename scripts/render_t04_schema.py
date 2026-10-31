"""Offline DDL renderer: writes SQL artifacts, never connects to a database."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy import MetaData
from sqlalchemy.dialects import mysql, sqlite
from database import db
import models

LEGACY_TABLES = {'users', 'staff', 'products', 'inventory', 'sales', 'alerts',
                 'predictions', 'inventory_audit', 'login_throttle'}


def legacy_lot_table():
    metadata=MetaData()
    for table in db.metadata.sorted_tables:table.to_metadata(metadata)
    lot=metadata.tables['stock_lots']
    column=lot.c.supplier_id
    for constraint in list(lot.constraints):
        if column in set(constraint.columns):lot.constraints.remove(constraint)
    lot._columns.remove(column)
    original=db.metadata.tables['stock_lots']
    orders={(type(c).__name__,c.name,tuple(c.columns.keys())):c._creation_order for c in original.constraints}
    for constraint in lot.constraints:
        constraint._creation_order=orders[(type(constraint).__name__,constraint.name,tuple(constraint.columns.keys()))]
    return lot


def render(dialect):
    statements = ['-- T04 v001 EXPAND ONLY. Manual review required; never run at startup.',
                  '-- Existing legacy tables are untouched. Backfill is separately reviewed.']
    for table in db.metadata.sorted_tables:
        if table.name in {'operation_proposals','proposal_audit'}:continue  # T27 has its own additive migration.
        if table.name not in LEGACY_TABLES | {'authorization_audit', 'sku_identities', 'catalog_audit', 'company_expiry_settings', 'stock_receipts', 'lot_identities', 'inventory_operations', 'inventory_movements', 'inventory_reservations', 'inventory_documents', 'priced_sales', 'sale_lines', 'sale_allocations', 'sale_refunds', 'refund_lines', 'stock_transfers', 'stock_transfer_lines', 'stock_transfer_allocations', 'stock_transfer_events', 'transfer_executions', 'transfer_transit', 'transfer_transit_facts', 'transfer_discrepancies', 'transfer_resolutions', 'transfer_return_links', 'suppliers', 'purchase_orders', 'purchase_order_lines', 'purchase_receipt_links', 'purchasing_events'}:
            ddl_table=legacy_lot_table() if table.name=='stock_lots' else table
            statements.append(str(CreateTable(ddl_table).compile(dialect=dialect)).strip()+';')
            statements.extend(str(CreateIndex(index).compile(dialect=dialect))+';'
                              for index in sorted(table.indexes, key=lambda index: index.name))
    return '\n'.join(line.rstrip() for line in '\n\n'.join(statements).splitlines())+'\n'


if __name__ == '__main__':
    for name, dialect in [('mysql', mysql.dialect()), ('sqlite', sqlite.dialect())]:
        target = Path(__file__).resolve().parents[1]/'migrations'/('t04_v001_expand_'+name+'.sql')
        target.write_text(render(dialect), encoding='utf-8')
