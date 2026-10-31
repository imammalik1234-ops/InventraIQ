"""T17 bounded measurement worker. Never accepts an existing database target."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sqlite3
import threading
import time
from unittest.mock import patch

PROFILES = {'small': ('small', 0, 0), 'medium': ('medium', 0, 0),
            'large-operational-proxy': ('medium', 80, 120)}
CURRENT = ContextVar('t17_sql_sample', default=None)


def limits(samples=20, rounds=2, workers=10, seconds=240, memory_mb=1536, disk_mb=512):
    values=dict(samples=samples,rounds=rounds,workers=workers,seconds=seconds,memory_mb=memory_mb,disk_mb=disk_mb)
    bounds=dict(samples=(2,60),rounds=(1,3),workers=(1,25),seconds=(30,900),memory_mb=(256,4096),disk_mb=(64,2048))
    for key,value in values.items():
        if type(value) is not int or not bounds[key][0]<=value<=bounds[key][1]:raise ValueError('Invalid bounded '+key)
    return values


def percentile(values, p):
    """Nearest-rank empirical quantile, not an interpolated tail prediction."""
    if not values:return None
    if not 0<p<=100:raise ValueError('Percentile outside (0,100]')
    return sorted(values)[max(0,math.ceil(p/100*len(values))-1)]


def summary(samples, elapsed=None):
    times=[s['ms'] for s in samples];statuses=Counter(str(s['status']) for s in samples)
    return dict(n=len(samples),p50_ms=percentile(times,50),p95_ms=percentile(times,95),p99_ms=percentile(times,99),
        throughput_per_second=len(samples)/(elapsed if elapsed is not None else sum(times)/1000) if times and sum(times)>0 else 0,
        statuses=dict(statuses),expected_denials=sum(s.get('expected_denial',False) for s in samples),
        errors=sum(not (200<=s['status']<400) and not s.get('expected_denial',False) for s in samples),
        conflicts=sum(s['status']==409 for s in samples),
        server_errors=sum(s['status']>=500 for s in samples),
        timeouts=sum(s.get('timeout',False) for s in samples),max_queries=max((s['queries'] for s in samples),default=0),
        max_slow_sql_ms=max((s.get('slow_sql_ms',0) for s in samples),default=0),
        max_body_bytes=max((s['bytes'] for s in samples),default=0),
        max_rss_bytes=max((s.get('rss',0) for s in samples),default=0),
        tail_note='Empirical nearest-rank; fewer than 100 samples cannot establish a stable p99',samples=samples)


def rss(pid=None):
    """Current/OS peak working set. No optional dependency or WMI access needed."""
    if os.name=='nt':
        from ctypes import wintypes
        class Memory(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in
                ('peak','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000|0x10,False,pid or os.getpid())
        if not handle:return dict(current=0,peak=0,available=False)
        try:
            value=Memory();value.cb=ctypes.sizeof(value)
            psapi=ctypes.WinDLL('psapi',use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Memory),wintypes.DWORD]
            if not psapi.GetProcessMemoryInfo(handle,ctypes.byref(value),value.cb):return dict(current=0,peak=0,available=False)
            return dict(current=value.working,peak=value.peak,available=True)
        finally:kernel.CloseHandle(handle)
    try:
        lines=Path(f'/proc/{pid or os.getpid()}/status').read_text().splitlines()
        fields={line.split(':')[0]:int(line.split()[1])*1024 for line in lines if line.startswith(('VmRSS:','VmHWM:'))}
        return dict(current=fields['VmRSS'],peak=fields['VmHWM'],available=True)
    except (OSError,KeyError):return dict(current=0,peak=0,available=False)


def disk_bytes(root):
    total=0
    for path in Path(root).rglob('*'):
        try:
            if path.is_file() and not path.is_symlink():total+=path.stat().st_size
        except FileNotFoundError:pass  # SQLite may checkpoint/remove a WAL concurrently.
    return total


def physical_memory():
    if os.name=='nt':
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in
                ('total','available','pagefile','available_pagefile','virtual','available_virtual','extended')]
        value=Memory();value.length=ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return dict(total_bytes=value.total,available_bytes=value.available)
    return dict(total_bytes=None,available_bytes=None)


def workload(samples):
    roles=((901,None),(903,None),(911,1),(921,1),(912,2),(922,2),(913,3),(923,3))
    return [roles[i%len(roles)] for i in range(samples)]


@contextmanager
def instrument(engine):
    from sqlalchemy import event
    def before(conn,cursor,statement,parameters,context,many):
        context._t17_started=time.perf_counter()
        value=CURRENT.get()
        if value is not None:value['queries']+=1
    def after(conn,cursor,statement,parameters,context,many):
        value=CURRENT.get()
        if value is not None:
            elapsed=(time.perf_counter()-context._t17_started)*1000
            value['slow_sql_ms']=max(value['slow_sql_ms'],elapsed)
            if elapsed>=25:
                # Fingerprint only, no bound values, SQL text, identities or secrets.
                value.setdefault('slow_queries',[])
                if len(value['slow_queries'])<8:value['slow_queries'].append(dict(ms=round(elapsed,3),sha256=hashlib.sha256(statement.encode()).hexdigest()))
    event.listen(engine,'before_cursor_execute',before);event.listen(engine,'after_cursor_execute',after)
    try:yield
    finally:event.remove(engine,'before_cursor_execute',before);event.remove(engine,'after_cursor_execute',after)


def request_sample(user, path, *, expected=(200,), check=None):
    sample=dict(queries=0,slow_sql_ms=0,bytes=0,status=0)
    token=CURRENT.set(sample);start=time.perf_counter()
    try:
        response=user.get(path,buffered=False);sample['status']=response.status_code
        if check:check(response)
        digest=hashlib.sha256()
        try:
            for chunk in response.response:sample['bytes']+=len(chunk);digest.update(chunk)
        finally:response.close()
        sample['body_sha256']=digest.hexdigest()
        sample['expected_status_match']=sample['status'] in expected
        sample['expected_denial']=sample['status'] in expected and sample['status']>=300
    except Exception as exc:
        sample['status']=599;sample['error_type']=type(exc).__name__;sample['timeout']=isinstance(exc,TimeoutError)
    finally:
        sample['ms']=round((time.perf_counter()-start)*1000,3);sample['rss']=rss()['peak'];CURRENT.reset(token)
    return sample


def common(key):return dict(request_key='SYN-T17:'+key,business_reference='SYN-T17:'+key,reason='Generated T17 benchmark')


def call(app,uid,branch,fn):
    from database import db
    from simulation.runtime import actor
    from security import MutationError
    from sqlalchemy.exc import OperationalError,IntegrityError
    from werkzeug.exceptions import HTTPException
    sample=dict(queries=0,slow_sql_ms=0,bytes=0,status=200)
    token=CURRENT.set(sample);start=time.perf_counter()
    try:
        with actor(app,uid,branch):
            try:
                result=fn();db.session.commit()
                sample['replayed']=bool(result.get('replayed',False)) if isinstance(result,dict) else False
                return result,sample
            except Exception:
                db.session.rollback();raise
    except MutationError as exc:sample['status']=exc.status
    except HTTPException as exc:sample['status']=exc.code
    except OperationalError as exc:
        busy=(getattr(exc.orig,'sqlite_errorcode',0)&255) in (5,6)
        sample.update(status=409 if busy else 599,error_type=type(exc).__name__,sqlite_contention=busy)
    except IntegrityError as exc:
        duplicate='inventory_operations.actor_id, inventory_operations.request_key' in str(exc.orig)
        sample.update(status=409 if duplicate else 599,error_type=type(exc).__name__,operation_key_conflict=duplicate)
    except Exception as exc:sample.update(status=599,error_type=type(exc).__name__)
    finally:
        sample['ms']=round((time.perf_counter()-start)*1000,3);sample['rss']=rss()['peak'];CURRENT.reset(token)
    return None,sample


def race(app,functions,workers):
    barrier=threading.Barrier(workers)
    def task(i):
        barrier.wait(timeout=15)
        uid,branch,fn=functions[i]
        return call(app,uid,branch,fn)[1]
    start=time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:results=list(pool.map(task,range(workers)))
    return summary(results,time.perf_counter()-start)


def reconcile(app):
    from database import db
    from sqlalchemy import select,func
    from models import BranchStock,InventoryOperation,InventoryMovement,PricedSale,SaleRefund,PurchaseOrderLine,TransferTransit,InventoryAudit,SaleLine,RefundLine,StockReceipt,CatalogAudit,AuthorizationAudit,InventoryDocument
    from simulation.runtime import actor
    import inventory_ledger,sales_services,purchasing_services,transfer_execution
    with actor(app,901):
        groups={name:fn() for name,fn in [('ledger',inventory_ledger.reconcile),('sales',sales_services.reconcile_sales),
            ('purchasing',purchasing_services.reconcile),('transfers',transfer_execution.reconcile)]}
        negative=db.session.scalar(select(func.count()).select_from(BranchStock).where(
            (BranchStock.on_hand<0)|(BranchStock.reserved<0)|(BranchStock.quarantined_quantity<0)|
            (BranchStock.reserved+BranchStock.quarantined_quantity>BranchStock.on_hand)))
        po_over=db.session.scalar(select(func.count()).select_from(PurchaseOrderLine).where(PurchaseOrderLine.received+PurchaseOrderLine.cancelled>PurchaseOrderLine.ordered))
        staging=db.session.scalar(select(func.count()).select_from(InventoryOperation).where(InventoryOperation.status!='committed'))
        result=dict(ok=not negative and not po_over and not staging and all(r['balanced'] for rows in groups.values() for r in rows),
            negative_balances=negative,over_received_po_lines=po_over,uncommitted_operations=staging,
            groups={k:dict(rows=len(v),unbalanced=sum(not r['balanced'] for r in v)) for k,v in groups.items()},
            stock=db.session.scalar(select(func.sum(BranchStock.on_hand))) or 0,
            transit=db.session.scalar(select(func.sum(TransferTransit.remaining))) or 0,
            loss=db.session.scalar(select(func.sum(TransferTransit.lost))) or 0,
            operations=db.session.scalar(select(func.count()).select_from(InventoryOperation)),
            movements=db.session.scalar(select(func.count()).select_from(InventoryMovement)),
            audit_records=db.session.scalar(select(func.count()).select_from(InventoryAudit)),
            native_audit_records=db.session.scalar(select(func.count()).select_from(CatalogAudit)),
            authorization_audit_records=db.session.scalar(select(func.count()).select_from(AuthorizationAudit)),
            inventory_documents=db.session.scalar(select(func.count()).select_from(InventoryDocument)),
            sale_lines=db.session.scalar(select(func.count()).select_from(SaleLine)),
            refund_lines=db.session.scalar(select(func.count()).select_from(RefundLine)),
            receipts=db.session.scalar(select(func.count()).select_from(StockReceipt)),
            gross_minor=db.session.scalar(select(func.sum(PricedSale.total_minor))) or 0,
            refund_minor=db.session.scalar(select(func.sum(SaleRefund.total_minor))) or 0)
        if not result['ok']:raise AssertionError('Synthetic workload reconciliation failed')
        return result


def new_item(app,key):
    from database import db
    from models import SKU
    from simulation.runtime import actor
    import catalog_services as catalog
    with actor(app,901):
        category=db.session.get(SKU,122).category_id
        row=catalog.save_sku(dict(code='SYN-T17-'+key,name='Generated benchmark '+key,category_id=category,unit='unit',
            variant_attributes={'synthetic':'T17','identity':key},default_price='1.25',active=True,expected_version=0,reason='Generated benchmark'))
        for branch in (1,2,3):catalog.save_assortment(branch,row['sku_id'],dict(enabled=True,expected_version=0,reason='Generated benchmark'))
        db.session.commit();return row['sku_id']


def concurrency(app,levels):
    from database import db
    from sqlalchemy import select,func
    from models import BranchStock,InventoryOperation,RefundLine,PurchaseOrder,PurchaseOrderLine
    from simulation.runtime import actor
    import receiving_services as receiving,sales_services as sales,inventory_ledger as ledger
    import purchasing_services as purchasing,transfer_services as transfers
    results={}
    for n in levels:
        sku=new_item(app,'RACE-'+str(n))
        delivery=dict(mode='new_lot',sku_id=sku,lot_code='SYN-T17-RACE-'+str(n),supplier_lot_code='SYN-T17-RACE-'+str(n),
            quantity=1,expiry_date=None,external_reference='SYN-T17-OPEN-'+str(n),evidence='Generated non-perishable lot',reason='Generated benchmark')
        receipt,_=call(app,911,1,lambda:receiving.receive(delivery))
        if receipt is None:raise AssertionError('Race setup failed')
        # Transfer approval vs sale competes for exactly one eligible unit.
        proposal,_=call(app,911,1,lambda:transfers.create(dict(common('PROPOSE-'+str(n)),destination_branch_id=2,
            planned_arrival_utc='2031-01-04T08:00:00+00:00',lines=[dict(sku_id=sku,quantity=1)])))
        submitted,_=call(app,911,1,lambda:transfers.transition(proposal['transfer_id'],'submit',dict(common('SUBMIT-'+str(n)),expected_version=proposal['version'])))
        functions=[]
        for i in range(n):
            if i%2: functions.append((902,1,lambda i=i:transfers.transition(proposal['transfer_id'],'approve',dict(common(f'APPROVE-{n}-{i}'),expected_version=submitted['version']))))
            else:functions.append((921,1,lambda i=i:sales.create_sale(dict(common(f'SALE-{n}-{i}'),lines=[dict(sku_id=sku,quantity=1)]))))
        result=race(app,functions,n)
        committed=sum(s['status']==200 and not s.get('replayed') for s in result['samples'])
        if committed!=1:raise AssertionError('Final-unit race did not have exactly one winner')
        result['invariant']='Exactly one sale or transfer reservation; all others conflict';results[f'final_unit_approval_sale_{n}']=result
        # Concurrent receipt retries share one key and must add exactly three.
        retry=dict(mode='existing_lot',sku_id=sku,lot_id=receipt['lot_id'],quantity=3,expiry_date=None,
            external_reference='SYN-T17-RETRY-'+str(n),request_key='SYN-T17:RECEIPT-'+str(n),reason='Generated benchmark',evidence='Generated inspected delivery')
        with actor(app,901):before=db.session.get(BranchStock,(1,receipt['lot_id'])).on_hand
        result=race(app,[(911,1,lambda:receiving.receive(retry)) for _ in range(n)],n)
        replay,_=call(app,911,1,lambda:receiving.receive(retry))
        with actor(app,901):after=db.session.get(BranchStock,(1,receipt['lot_id'])).on_hand
        if replay is None or after-before!=3:raise AssertionError('Duplicate receipt affected stock twice')
        result['invariant']='Receipt retry/recovery adds three units exactly once';results[f'duplicate_receipt_{n}']=result
        # Multi-line checkout followed by concurrent full-quantity financial refunds.
        sale,_=call(app,921,1,lambda:sales.create_sale(dict(common('REFUND-SALE-'+str(n)),lines=[dict(sku_id=sku,quantity=1),dict(sku_id=122,quantity=1)])))
        allocation=next(l for l in sale['lines'] if l['sku_id']==sku)['allocations'][0]['allocation_id']
        result=race(app,[(911,1,lambda i=i:sales.refund_sale(sale['sale_id'],dict(common(f'REFUND-{n}-{i}'),
            lines=[dict(allocation_id=allocation,quantity=1,disposition='no_restock')]))) for i in range(n)],n)
        with actor(app,901):refunded=db.session.scalar(select(func.sum(RefundLine.quantity)).where(RefundLine.allocation_id==allocation))
        if refunded!=1:raise AssertionError('Refund quantity not conserved')
        result['invariant']='Exactly one original unit refunded; no unsaleable restock';results[f'refund_{n}']=result
        results[f'reconciliation_{n}']=reconcile(app)
    # PO concurrent receipt and transfer destination receipt on original T15 docs.
    with actor(app,902,2):po=purchasing.detail(2);transfer=transfers.detail(1)
    n=max(levels)
    delivery=dict(mode='existing_lot',lot_id=6,quantity=10,expiry_date=None,receipt_kind='normal',evidence='Generated T17 PO delivery',external_reference='SYN-T17-PO')
    # Resolve actual receipt lot from order detail, rather than assuming a batch ID.
    with actor(app,902,2):
        from models import StockReceipt,PurchaseReceiptLink
        lot=db.session.scalar(select(StockReceipt.lot_id).join(PurchaseReceiptLink,PurchaseReceiptLink.receipt_id==StockReceipt.receipt_id).where(PurchaseReceiptLink.line_id==po['lines'][0]['line_id']).limit(1))
    delivery['lot_id']=lot
    result=race(app,[(912,2,lambda i=i:purchasing.transition(2,'receive',dict(common(f'PO-RACE-{i}'),expected_version=po['version'],line_id=po['lines'][0]['line_id'],delivery=delivery))) for i in range(n)],n)
    with actor(app,902,2):po_after=purchasing.detail(2)
    if po_after['lines'][0]['received']-po['lines'][0]['received']!=10:raise AssertionError('Concurrent PO over/under receipt')
    results['po_partial_receipt']=result
    allocation=transfer['transit'][0]
    with actor(app,902,2):stock=db.session.get(BranchStock,(2,allocation['lot_id']));revision=stock.version if stock else 0
    result=race(app,[(912,2,lambda i=i:transfers.transition(1,'receive',dict(common(f'TRANSFER-RACE-{i}'),expected_version=transfer['version'],
        lines=[dict(allocation_id=allocation['allocation_id'],quantity=6,expected_revision=revision,disposition='saleable')]))) for i in range(n)],n)
    if sum(s['status']==200 for s in result['samples'])!=1:raise AssertionError('Transfer receipt did not settle once')
    results['transfer_receipt']=result
    # An error after balance mutations must roll back every fact and audit.
    before=reconcile(app)
    with patch('inventory_ledger.finish',side_effect=RuntimeError('T17 injected failure')):
        _,injected=call(app,921,1,lambda:sales.create_sale(dict(common('INJECT'),lines=[dict(sku_id=122,quantity=1)])))
    after=reconcile(app)
    if injected['status']!=599 or before!=after:raise AssertionError('Injected failure left partial effects')
    results['injected_rollback']=dict(status=injected['status'],error_type=injected['error_type'],unchanged=True)
    results['final_reconciliation']=after
    return results


def execute(parent,profile,config):
    if profile not in PROFILES:raise ValueError('Unknown synthetic profile')
    limits(**config)
    from database import db
    from sqlalchemy import event,select,func
    from models import BranchStock,PricedSale
    from scripts.measure_t16 import fixture,client
    from simulation import runtime,runner
    from ingestion import pipeline
    import receiving_services,sales_services
    history_profile,extra_receipts,extra_sales=PROFILES[profile]
    output=dict(schema_version='inventraiq-t17-v1',profile=profile,limits=config,rounds=[],
        profile_contract=dict(history_profile=history_profile,extra_receipts_per_branch=extra_receipts,extra_sales_per_branch=extra_sales,
            source_revision='synthetic-r1',provenance='synthetic_fixture + synthetic_demo',seed=13),
        untested_audit_tiers=[dict(observations=657000,status='NOT RUN: T14 generated-source cap 40000'),
            dict(observations=17754327,status='NOT RUN: T14 cap and bounded local resource envelope')],
        mysql='UNVERIFIED: no approved disposable MySQL target',forecast_baseline=dict(status='historical evidence only; not rerun, no training',
            source='VERIFICATION.md performance table; scripts/profile_intelligence.py retained unchanged',
            small=dict(products=100,sales=9000,api_median_ms=397,dashboard_median_ms=489,queries=8,fits=200),
            larger=dict(products=500,sales=45000,api_median_ms=2236,dashboard_median_ms=2418,queries=8,fits=1000)))
    def checkpoint(stage):
        output['stage']=stage
        temporary=Path(parent)/'partial.tmp'
        temporary.write_text(json.dumps(output),encoding='utf-8');temporary.replace(Path(parent)/'partial.json')
    for round_number in range(config['rounds']):
        imports=[];transactions={}
        original_ingest=pipeline.ingest
        def timed_import(*a,**kw):
            start=time.perf_counter();result=original_ingest(*a,**kw)
            imports.append(dict(ms=round((time.perf_counter()-start)*1000,3),**result['reconciliation']))
            return result
        original_step=runner.Runner.step
        def step(self,name,uid,branch,instant,fn):
            sample=dict(queries=0,slow_sql_ms=0,bytes=0,status=200)
            token=CURRENT.set(sample);start=time.perf_counter()
            try:return original_step(self,name,uid,branch,instant,fn)
            except Exception as exc:sample.update(status=599,error_type=type(exc).__name__);raise
            finally:
                sample['ms']=round((time.perf_counter()-start)*1000,3);sample['rss']=rss()['peak'];CURRENT.reset(token)
                kind=('po_'+name.rsplit('-',1)[-1] if name.startswith('po-') else 'transfer_'+name.rsplit('-',1)[-1] if name.startswith('transfer-') else
                      'multiline_checkout' if name.startswith('sale-') else 'refund' if name=='partial-refund' else 'adjustment' if name=='damage-writeoff' else 'setup')
                transactions.setdefault(kind,[]).append(sample)
        original_create=runtime.create_app
        def create(root):
            app=original_create(root)
            with app.app_context():
                # Only this new attested target; configure every new connection.
                def configure(conn,record):
                    conn.execute('PRAGMA busy_timeout=3000');conn.execute('PRAGMA temp_store=FILE')
                event.listen(db.engine,'connect',configure)
                connection=db.session.connection();connection.exec_driver_sql('PRAGMA busy_timeout=3000');connection.exec_driver_sql('PRAGMA temp_store=FILE')
                db.session.rollback()
                capture=instrument(db.engine);capture.__enter__();app._t17_capture=capture
            return app
        start=time.perf_counter()
        with patch.object(runtime,'ingest',side_effect=timed_import),patch.object(runner.Runner,'step',new=step),patch.object(runtime,'create_app',side_effect=create):
            root,app=fixture(parent,history_profile,indexes=True)
        try:
            for branch in (1,2,3):
                for n in range(extra_receipts):
                    key=f'LARGE-{branch}-{n}'
                    _,sample=call(app,910+branch,branch,lambda key=key:receiving_services.receive(dict(mode='new_lot',sku_id=121,lot_code='SYN-T17-'+key,
                        supplier_lot_code='SYN-T17-'+key,quantity=10,expiry_date='2031-02-28',external_reference='SYN-T17-'+key,
                        request_key='SYN-T17:'+key,reason='Generated benchmark',evidence='Generated actual fixture label')))
                    if sample['status']!=200:raise AssertionError('Large receipt setup failed')
                    transactions.setdefault('native_receipt',[]).append(sample)
                for n in range(extra_sales):
                    _,sample=call(app,920+branch,branch,lambda n=n,branch=branch:sales_services.create_sale(dict(common(f'LARGE-SALE-{branch}-{n}'),lines=[dict(sku_id=121,quantity=1)])))
                    if sample['status']!=200:raise AssertionError('Large checkout setup failed')
                    transactions.setdefault('single_checkout',[]).append(sample)
            setup_ms=(time.perf_counter()-start)*1000
            # Receiving/deduction latency for every profile, apart from setup plan.
            for branch in (1,2,3):
                key='RECEIVE-'+str(branch)
                _,sample=call(app,910+branch,branch,lambda key=key:receiving_services.receive(dict(mode='new_lot',sku_id=122,lot_code='SYN-T17-'+key,
                    supplier_lot_code='SYN-T17-'+key,quantity=5,expiry_date=None,external_reference='SYN-T17-'+key,
                    reason='Generated benchmark',evidence='Generated non-perishable fixture')))
                transactions.setdefault('native_receipt',[]).append(sample)
            users={pair:client(app,*pair) for pair in set(workload(config['samples']))|{(901,None),(912,2),(921,1)}}
            reads={}
            current=dict(round=round_number,setup_ms=round(setup_ms,3),imports=imports,reads=reads)
            output['rounds'].append(current);checkpoint('read_setup')
            paths=dict(branch_dashboard='/dashboard?branch_id=2',hq_dashboard='/dashboard',inventory='/inventory-page',
                inventory_first='/api/operations?page_size=10',
                inventory_search='/api/operations?search=DRINK&page_size=10',deep_inventory='/api/operations?page=5&page_size=10',
                sales='/sales',reports='/reports',analytics='/analytics',summary_export='/operations/export.csv',
                observations='/api/observations?start=2001-01-01&end=2001-03-31',
                observation_export='/operations/observations.csv?start=2001-01-01&end=2001-03-31')
            for name,path in paths.items():
                cold=request_sample(users[(901,None)],path)
                count=2 if name=='observation_export' else config['samples']
                warm=[request_sample(users[(901,None)],path) for _ in range(count)]
                reads[name]=dict(first=cold,warm=summary(warm))
                checkpoint('read_'+name)
            mixed={}
            levels=sorted({min(2,config['workers']),min(10,config['workers']),config['workers']})
            for workers in levels:
                roles=workload(max(config['samples'],workers));barrier=threading.Barrier(workers)
                # Clients belong to individual tasks, never shared cookie jars.
                clients=[client(app,*role) for role in roles]
                def task(i):
                    if i<workers:barrier.wait(timeout=15)
                    role=roles[i]
                    def check(response):
                        data=response.get_json()
                        expected={1,2,3} if role[1] is None else {role[1]}
                        if response.status_code!=200 or set(data['scope']['branch_ids'])!=expected:raise AssertionError('Scope mismatch')
                        if any(r['branch_id'] not in expected for r in data['inventory']['items']):raise AssertionError('Cross-branch result')
                    return request_sample(clients[i],'/api/operations?page_size=10',check=check)
                began=time.perf_counter()
                with ThreadPoolExecutor(max_workers=workers) as pool:samples=list(pool.map(task,range(len(roles))))
                mixed[str(workers)]=summary(samples,time.perf_counter()-began)
                if any(s.get('error_type')=='AssertionError' for s in samples):raise AssertionError('Mixed-user scope invariant failed')
            denials=[request_sample(users[(912,2)],'/api/operations?branch_id=1',expected=(403,)),
                request_sample(users[(921,1)],'/purchasing',expected=(403,))]
            if not all(s.get('expected_status_match') for s in denials):raise AssertionError('Forbidden request was not denied')
            from branch_services import manage_assignment
            _,changed=call(app,901,None,lambda:manage_assignment(921,1,'clerk',False))
            revoked=request_sample(users[(921,1)],'/api/operations',expected=(302,403))
            if changed['status']!=200 or not revoked.get('expected_status_match'):raise AssertionError('Revoked assignment remained authorized')
            call(app,901,None,lambda:manage_assignment(921,1,'clerk',True))
            with app.app_context():
                facts=dict(lots=db.session.scalar(select(func.count()).select_from(BranchStock)),sales=db.session.scalar(select(func.count()).select_from(PricedSale)))
                settings={key:db.session.connection().exec_driver_sql('PRAGMA '+key).scalar() for key in
                    ('journal_mode','foreign_keys','busy_timeout','temp_store','page_size','synchronous','cache_size','locking_mode')}
                database_bytes=db.session.connection().exec_driver_sql('PRAGMA page_count').scalar()*settings['page_size']
                db.session.rollback()
            races=concurrency(app,levels)
            from ingestion.pipeline import published_summary
            current.update(dict(facts=facts,
                observations=published_summary(root),sqlite=settings,reads=reads,mixed=mixed,
                transactions={k:summary(v) for k,v in transactions.items()},expected_denials=denials,
                revoked_assignment=revoked,concurrency=races,rss=rss(),temporary_bytes=disk_bytes(root),
                database_bytes=database_bytes,wal_bytes=(root/'disposable.sqlite-wal').stat().st_size if (root/'disposable.sqlite-wal').exists() else 0))
            checkpoint('round_complete')
        finally:
            with app.app_context():
                app._t17_capture.__exit__(None,None,None);db.session.remove();db.engine.dispose()
    from importlib.metadata import version
    output['environment']=dict(os=platform.platform(),cpu=platform.processor(),logical_cpus=os.cpu_count(),
        python=platform.python_version(),sqlite=sqlite3.sqlite_version,dependencies={n:version(n) for n in ('Flask','SQLAlchemy','tzdata')},
        clock='2031-01-03T12:00:00Z operational; original synthetic history 2001',
        execution='in-process Flask WSGI test clients; threads, not a production HTTP/WSGI capacity claim',
        cold='first application request in new target, not flushed OS cache',warm='repeated requests in same worker',
        cpu_limit='OPENBLAS/OMP threads=1; no enforced OS CPU quota',memory_limit='parent samples RSS and terminates over limit',
        users=9,processes=1,physical_memory=physical_memory())
    from simulation.manifest import code_hash,MANIFEST,validate,ROOT
    output['versions']=dict(application_sha256=code_hash(),simulation_manifest_sha256=validate(MANIFEST),
        files={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ('benchmarks/t17.py','scripts/benchmark_t17.py','migrations/t16_v001_indexes_sqlite.sql')})
    return output
