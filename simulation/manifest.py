"""Versioned deterministic fixture specification; no private credentials."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MANIFEST=dict(schema_version='inventraiq-operational-demo-v1',simulation_id='SYN-T15-V1',seed=42,
    provenance='synthetic_demo',opening_at='2031-01-01T08:00:00+00:00',as_of='2031-01-03T12:00:00+00:00',
    history_period='2001-01-01/2001-01-14',branches=['DEMO-01','DEMO-02','DEMO-03'],currency='MYR',
    sku_codes=['SYN-T15-DRINK','SYN-T15-RICE','SYN-T15-SNACK'],
    initial_counts=[[20,80,100,40],[4,6,5,5],[10,20,35,25]],
    daily_drink_demand=[[8,2,4],[6,2,5],[3,3,4]],daily_rice_demand=[[2,2,2],[3,1,3],[2,2,2]],
    lots=[dict(key='drink-early',sku=0,expiry='2031-01-04'),dict(key='drink-late',sku=0,expiry='2031-02-20'),
          dict(key='rice',sku=1,expiry=None),dict(key='snack',sku=2,expiry='2031-01-15')],
    prices=['4.50','8.20','2.30'],branch_02_drink_price='4.80',
    supplier='SYN-T15-SUPPLIER',full_po_units=12,partial_po_units=20,partial_receipts=[3,2],
    full_po_expiry='2031-01-30',po_expected_date='2031-01-02',transfer_arrival='2031-01-05T08:00:00+00:00',
    transit_units=10,transit_received=4,resolved_transfer_units=5,resolved_transfer_received=4,refund_units=2,writeoff_units=2)


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def validate(manifest):
    if canonical(manifest)!=canonical(MANIFEST):raise ValueError('Unknown or changed T15 simulation manifest')
    return hashlib.sha256(canonical(manifest).encode()).hexdigest()


def code_hash():
    names=['simulation_clock.py','expiry.py','branch_services.py','models.py','database.py','authz.py',
        'authentication.py','security.py','catalog_services.py','inventory_ledger.py','receiving_services.py',
        'sales_services.py','purchasing_services.py','transfer_services.py','transfer_execution.py','operational_services.py',
        'app.py','configuration.py','branch_foundation.py','scoped_inventory_queries.py','query_paging.py','historical_queries.py','operational_routes.py','simulation/manifest.py','simulation/schema.py',
        'simulation/runtime.py','simulation/runner.py','simulation/verify.py']
    from importlib.metadata import version
    import sys
    return hashlib.sha256(canonical(dict(files={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names},
        python=sys.version.split()[0],dependencies={n:version(n) for n in ['Flask','Flask-Login','SQLAlchemy','Werkzeug','tzdata']})).encode()).hexdigest()
