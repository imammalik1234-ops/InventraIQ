"""Authorized T06–T12 service orchestration; stock quantities never assigned here."""
import hashlib
import json
import random
from datetime import date
from sqlalchemy import select,func,insert,update
from database import db
from models import (User,UserHQScope,SKU,StockLot,BranchStock,SaleAllocation,SaleLine,
    InventoryOperation,InventoryMovement,PricedSale,SaleRefund,StockTransfer,TransferTransit)
from simulation.manifest import MANIFEST as M,validate,code_hash,canonical
from simulation.runtime import attest,history_hash,actor
from simulation.schema import SimulationRun,SimulationStep
import catalog_services as catalog
import branch_services
import inventory_ledger as ledger
import purchasing_services as purchasing
import sales_services as sales
import transfer_services as transfers
import transfer_execution as execution


def normalized(value):
    return json.loads(json.dumps(value,default=lambda v:v.isoformat() if hasattr(v,'isoformat') else str(v)))


def bootstrap(app,manifest):
    root,private=attest(app._t15_target)
    digest=validate(manifest);version=code_hash()
    with app.app_context():
        db.session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        try:
            existing=db.session.execute(select(SimulationRun)).mappings().first()
            if existing:
                if existing['manifest_hash']!=digest or existing['code_hash']!=version:
                    raise ValueError('Manifest/application version changed; use a new disposable target')
                db.session.rollback();return
            if db.session.scalar(select(func.count()).select_from(User)):
                raise ValueError('Never adopt or overwrite existing users')
            for name in ['stock_lots','branch_stock','products','inventory','sales','priced_sales','inventory_operations',
                         'stock_receipts','suppliers','purchase_orders','stock_transfers']:
                if db.session.scalar(select(func.count()).select_from(db.metadata.tables[name])):
                    raise ValueError('Never adopt existing operational records')
            if history_hash(root)!=private['history_hash']:raise ValueError('Historical observations changed')
            for key,password in sorted(private['credentials'].items()):
                uid=int(key)
                user=User(user_id=uid,name=f'Synthetic Demo User {uid}',email=f'synthetic-{uid}@demo.invalid',
                    role='admin' if uid in (901,902,903) else 'staff',enabled=True,session_version=1)
                user.set_password(password);db.session.add(user)
            db.session.flush()
            db.session.add_all([UserHQScope(user_id=uid,hq_role='admin',active=True) for uid in (901,902)])
            db.session.execute(insert(SimulationRun).values(run_id=1,manifest_hash=digest,manifest_json=canonical(manifest),
                code_hash=version,status='running',completed_steps=0,history_hash=private['history_hash']))
            db.session.commit()
        except Exception:db.session.rollback();raise


class Runner:
    def __init__(self,app,manifest):
        validate(manifest);self.app=app;self.state={};self.index=0;self.new_steps=0;self.random=random.Random(manifest['seed'])

    def step(self,name,uid,branch,instant,fn):
        index=self.index;self.index+=1
        common=dict(request_key=f'SYN-T15-V1:{index}:{name}',business_reference='SYNTHETIC-DEMO:'+name+':'+str(self.random.randrange(1000000)),
            reason='Synthetic demo T15: '+name)
        with actor(self.app,uid,branch,instant):
            db.session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            try:
                row=db.session.execute(select(SimulationStep).where(SimulationStep.c.step_number==index)).mappings().first()
                if row:
                    if row['step_key']!=common['request_key']:raise ValueError('Step plan changed')
                    result=json.loads(row['result_json']);db.session.rollback()
                else:
                    result=normalized(fn(common))
                    db.session.execute(insert(SimulationStep).values(run_id=1,step_number=index,step_key=common['request_key'],
                        actor_id=uid,branch_id=branch,at_utc=instant,action=name,payload_json=canonical(common),result_json=canonical(result)))
                    db.session.execute(update(SimulationRun).where(SimulationRun.c.run_id==1).values(completed_steps=index+1))
                    db.session.commit();self.new_steps+=1
                self.state[name]=result
                return result
            except Exception:db.session.rollback();raise

    def sku(self,index):return self.state['sku-'+str(index)]['sku_id']

    def opening(self,branch,number,common):
        from expiry import canonical_expiry
        item=M['lots'][number];sku=self.sku(item['sku']);code='SYN-T15-'+item['key'].upper()
        lot=db.session.scalar(select(StockLot).where(StockLot.sku_id==sku,StockLot.lot_code==code))
        if lot is None:
            instant=canonical_expiry(date.fromisoformat(item['expiry']),'Asia/Singapore') if item['expiry'] else None
            lot=StockLot(sku_id=sku,lot_code=code,expiry_date=date.fromisoformat(item['expiry']) if item['expiry'] else None,
                expiry_timezone='Asia/Singapore' if instant else None,expires_at_utc=instant.replace(tzinfo=None) if instant else None,
                validation_state='validated',version=1,provenance=dict(source='synthetic_demo',simulation_id=M['simulation_id'],
                    label='Explicit generated fixture label; not manufacturer evidence'))
            db.session.add(lot);db.session.flush()
        # Zero-only dimension scaffold. Every positive quantity uses guarded T07 apply.
        stock=BranchStock(branch_id=branch,lot_id=lot.lot_id,sku_id=sku,on_hand=0,reserved=0,
            quarantined_quantity=0,version=1,first_received_at_utc=None)
        db.session.add(stock);db.session.flush()
        opening=ledger.opening(dict(common,lot_id=lot.lot_id,expected_revision=1,on_hand=0,reserved=0,
            quarantined_quantity=0,source_receipt_ids=[],approval_evidence='Synthetic zero opening at explicit manifest date'))
        counted=ledger.adjust(dict(common,request_key=common['request_key']+':count',sku_id=sku,lot_id=lot.lot_id,
            expected_revision=1,quantity=M['initial_counts'][branch-1][number],kind='adjustment',direction='increase'))
        return dict(lot_id=lot.lot_id,opening_operation_id=opening['operation_id'],count=counted)

    def po_action(self,key,action,common,delivery=None):
        row=purchasing.detail(self.state[key]['order_id'])
        data=dict(common,expected_version=row['version'])
        if delivery:
            data.update(line_id=row['lines'][0]['line_id'],delivery=delivery)
        return purchasing.transition(row['order_id'],action,data)

    def transfer_action(self,key,action,common,quantity=None):
        row=transfers.detail(self.state[key]['transfer_id']);data=dict(common,expected_version=row['version'])
        if action=='dispatch':
            data['lines']=[dict(allocation_id=a['allocation_id'],expected_revision=db.session.get(BranchStock,(row['source_branch_id'],a['lot_id'])).version) for a in row['reservations']]
        elif action=='receive':
            a=row['transit'][0];stock=db.session.get(BranchStock,(row['destination_branch_id'],a['lot_id']))
            data['lines']=[dict(allocation_id=a['allocation_id'],quantity=quantity,expected_revision=stock.version if stock else 0,disposition='saleable')]
        elif action=='discrepancy':data.update(allocation_id=row['transit'][0]['allocation_id'],quantity=1)
        elif action=='resolve':data.update(discrepancy_id=row['discrepancies'][0]['discrepancy_id'],decision='loss')
        return transfers.transition(row['transfer_id'],action,data)

    def plan(self):
        opening=M['opening_at']
        yield ('hq-analyst',901,None,opening,lambda c:branch_services.manage_hq_scope(903,'analyst',True))
        for branch in (1,2,3):
            for uid,role in [(910+branch,'manager'),(920+branch,'clerk')]:
                yield (f'assign-{uid}',901,None,opening,lambda c,u=uid,b=branch,r=role:branch_services.manage_assignment(u,b,r,True))
        for key in ('food','household'):
            yield ('category-'+key,901,None,opening,lambda c,k=key:catalog.create_category(dict(code='SYN-T15-'+k.upper(),display_name='Synthetic Demo '+k,reason=c['reason'])))
            policy=dict(require_expiry=key=='food',early_days=7,high_days=3,critical_days=1,expected_shelf_life_days=None,
                min_remaining_days_on_receipt=0,min_remaining_days_on_transfer_receipt=1)
            yield ('policy-'+key,901,None,opening,lambda c,k=key,pol=policy:catalog.save_policy('category',self.state['category-'+k]['category_id'],pol,0,c['reason']))
        yield ('label-zone',901,None,opening,lambda c:catalog.save_settings('Asia/Singapore',0,c['reason']))
        for i in range(3):
            category='household' if i==1 else 'food'
            yield (f'sku-{i}',901,None,opening,lambda c,i=i,k=category:catalog.save_sku(dict(code=M['sku_codes'][i],
                name=['Synthetic Demo Oat Drink 500ml','Synthetic Demo Rice 1kg','Synthetic Demo Snack 100g'][i],
                category_id=self.state['category-'+k]['category_id'],unit='unit',variant_attributes=dict(provenance='synthetic_demo',simulation=M['simulation_id']),
                default_price=M['prices'][i],active=True,expected_version=0,reason=c['reason'])))
            for branch in (1,2,3):
                yield (f'assortment-{i}-{branch}',901,None,opening,lambda c,i=i,b=branch:catalog.save_assortment(b,self.sku(i),
                    dict(enabled=True,reorder_level_override=12,sale_price_override=M['branch_02_drink_price'] if (i,b)==(0,2) else None,expected_version=0,reason=c['reason'])))
        yield ('supplier',901,None,opening,lambda c:purchasing.save_supplier(dict(code=M['supplier'],display_name='Synthetic Demo Supplies',active=True,expected_version=0,reason=c['reason'])))
        for branch in (1,2,3):
            for number in range(4):
                yield (f'opening-{branch}-{number}',901,branch,opening,lambda c,b=branch,n=number:self.opening(b,n,c))
        for name,branch,sku,qty,cost in [('po-full',1,2,12,'1.10'),('po-partial',2,1,20,'5.00')]:
            time='2031-01-01T09:00:00+00:00'
            yield (name,901,branch,time,lambda c,s=sku,q=qty,cost=cost:purchasing.create(dict(c,supplier_id=self.state['supplier']['supplier_id'],
                expected_date=M['po_expected_date'],lines=[dict(sku_id=self.sku(s),quantity=q,unit_cost=cost)])))
            for action,uid in [('submit',901),('approve',902)]:
                yield (name+'-'+action,uid,branch,time,lambda c,k=name,a=action:self.po_action(k,a,c))
            delivery=dict(mode='new_lot',lot_code='SYN-T15-'+name.upper(),supplier_lot_code='SYN-T15-'+name.upper(),
                quantity=12 if name=='po-full' else 3,expiry_date=M['full_po_expiry'] if name=='po-full' else None,
                external_reference='SYN-T15-'+name+'-first',receipt_kind='normal',evidence='Explicit synthetic demo delivery, not real goods')
            yield (name+'-receipt',910+branch,branch,'2031-01-01T10:00:00+00:00',lambda c,k=name,d=delivery:self.po_action(k,'receive',c,d))
        for day in range(3):
            if day==1:
                def second_receipt(c):
                    receipt=self.state['po-partial-receipt']['receipt']
                    return self.po_action('po-partial','receive',c,dict(mode='existing_lot',lot_id=receipt['lot_id'],quantity=2,expiry_date=None,
                        external_reference='SYN-T15-po-partial-second',receipt_kind='normal',evidence='Synthetic second partial delivery inspected'))
                yield ('po-partial-second',912,2,'2031-01-02T09:00:00+00:00',second_receipt)
            for branch in (1,2,3):
                yield (f'sale-{day}-{branch}',920+branch,branch,f'2031-01-0{day+1}T11:00:00+00:00',
                    lambda c,d=day,b=branch:sales.create_sale(dict(c,lines=[dict(sku_id=self.sku(0),quantity=M['daily_drink_demand'][d][b-1]),
                        dict(sku_id=self.sku(1),quantity=M['daily_rice_demand'][d][b-1])])) )
            if day==1:
                def refund(c):
                    sale=self.state['sale-0-1'];allocation=next(l for l in sale['lines'] if l['sku_id']==self.sku(0))['allocations'][0]
                    return sales.refund_sale(sale['sale_id'],dict(c,lines=[dict(allocation_id=allocation['allocation_id'],quantity=2,
                        disposition='saleable',saleable_verified=True,inspection_evidence='Synthetic demo sealed goods inspected saleable')]))
                yield ('partial-refund',911,1,'2031-01-02T12:00:00+00:00',refund)
                for key,destination,sku,qty in [('transfer-transit',2,0,10),('transfer-resolved',3,2,5)]:
                    time='2031-01-02T13:00:00+00:00'
                    yield (key,911,1,time,lambda c,d=destination,s=sku,q=qty:transfers.create(dict(c,destination_branch_id=d,
                        planned_arrival_utc=M['transfer_arrival'],lines=[dict(sku_id=self.sku(s),quantity=q)])))
                    for action,uid in [('submit',911),('approve',902)]:
                        yield (key+'-'+action,uid,1,time,lambda c,k=key,a=action:self.transfer_action(k,a,c))
                    yield (key+'-dispatch',911,1,'2031-01-03T08:00:00+00:00',lambda c,k=key:self.transfer_action(k,'dispatch',c))
                    qty_received=4
                    yield (key+'-receive',910+destination,destination,'2031-01-03T09:00:00+00:00',lambda c,k=key:self.transfer_action(k,'receive',c,4))
                    if key=='transfer-resolved':
                        yield (key+'-issue',913,3,'2031-01-03T09:10:00+00:00',lambda c:self.transfer_action('transfer-resolved','discrepancy',c))
                        yield (key+'-resolve',902,3,'2031-01-03T10:00:00+00:00',lambda c:self.transfer_action('transfer-resolved','resolve',c))
        def writeoff(c):
            lot=self.state['opening-3-3']['lot_id'];stock=db.session.get(BranchStock,(3,lot))
            return ledger.adjust(dict(c,sku_id=self.sku(2),lot_id=lot,quantity=2,expected_revision=stock.version,kind='write_off',direction='decrease'))
        yield ('damage-writeoff',913,3,'2031-01-03T11:30:00+00:00',writeoff)


def run(app,manifest=M,*,max_new_steps=None):
    if max_new_steps is not None and (type(max_new_steps) is not int or max_new_steps<1):raise ValueError('Positive step budget required')
    bootstrap(app,manifest);runner=Runner(app,manifest)
    for args in sorted(runner.plan(),key=lambda step:step[3]):
        runner.step(*args)
        if max_new_steps is not None and runner.new_steps>=max_new_steps:
            return dict(status='interrupted',completed_steps=runner.index)
    with app.app_context():
        root,info=attest(app._t15_target)
        if history_hash(root)!=info['history_hash']:raise ValueError('Historical observations changed')
        db.session.execute(update(SimulationRun).where(SimulationRun.c.run_id==1).values(status='complete'))
        db.session.commit()
    return dict(status='complete',completed_steps=runner.index,new_steps=runner.new_steps)
