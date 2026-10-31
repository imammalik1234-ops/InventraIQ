"""T07 browser workflows, extending the disposable T06 setup without user DBs."""
from verify_t06 import main


def ledger_checks(manager,clerk,origin,sku_id,native,checks,csrf,form,submit):
    assert manager.goto(origin+'/sku-inventory').status==200
    element=form(manager,'Record FEFO quantity sale')
    key=element.locator('[name="request_key"]').input_value()
    element.locator('[name="sku_id"]').select_option(str(sku_id))
    for name,value in [('quantity','13'),('business_reference','BROWSER-SALE'),('reason','Observed quantity sale')]:
        element.locator('[name="'+name+'"]').fill(value)
    submit(manager,element,'Record quantity sale')
    manager.get_by_role('status').get_by_text('Committed operation',exact=False).wait_for()
    payload=dict(sku_id=sku_id,quantity=13,request_key=key,business_reference='BROWSER-SALE',reason='Observed quantity sale')
    replay=manager.request.post(origin+'/api/sku-sales',data=payload,headers={'X-CSRF-Token':csrf(manager)})
    assert replay.status==200 and replay.json()['replayed']
    assert [line['quantity_delta'] for line in replay.json()['allocations']]==[-12,-1]
    assert manager.request.post(origin+'/api/sku-sales',data=dict(payload,quantity=14),headers={'X-CSRF-Token':csrf(manager)}).status==409
    checks.append('native sale form spans FEFO lots; same key replays committed result; changed quantity conflicts')
    stock=next(r for r in native if r['lot_code']=='BROWSER-2')
    adjustment=form(manager,'Adjust lot BROWSER-2')
    old_revision=adjustment.locator('[name="expected_revision"]').input_value()
    for name,value in [('quantity','1'),('business_reference','COUNT-BROWSER'),('reason','Verified count discrepancy')]:
        adjustment.locator('[name="'+name+'"]').fill(value)
    submit(manager,adjustment,'Record adjustment')
    stale=dict(sku_id=sku_id,lot_id=stock['lot_id'],quantity=1,request_key='STALE-BROWSER',business_reference='COUNT',reason='Stale count',expected_revision=int(old_revision))
    assert manager.request.post(origin+'/api/stock-adjustments',data=stale,headers={'X-CSRF-Token':csrf(manager)}).status==409
    checks.append('manager adjustment form records delta; stale count cannot overwrite balance')
    reserve=dict(sku_id=sku_id,quantity=1,request_key='RESERVE-BROWSER',business_reference='HOLD',reason='Temporary hold')
    response=manager.request.post(origin+'/api/stock-reservations',data=reserve,headers={'X-CSRF-Token':csrf(manager)})
    assert response.status==200
    sale=dict(reserve,quantity=2,request_key='CLERK-BROWSER')
    assert clerk.goto(origin+'/sku-inventory').status==200
    denied=clerk.request.post(origin+'/api/sku-sales',data=sale,headers={'X-CSRF-Token':csrf(clerk)})
    assert denied.status==409,(denied.status,denied.text())
    assert clerk.request.post(origin+'/api/stock-reservations',data=reserve,headers={'X-CSRF-Token':csrf(clerk)}).status==403
    release=dict(reserve,request_key='RELEASE-BROWSER',reservation_id=response.json()['operation_id'])
    assert manager.request.post(origin+'/api/stock-reservations/release',data=release,headers={'X-CSRF-Token':csrf(manager)}).status==200
    assert clerk.request.post(origin+'/api/sku-sales',data=dict(sale,quantity=1),headers={'X-CSRF-Token':csrf(clerk)}).status==200
    assert clerk.request.post(origin+'/api/sku-sales',data=dict(sale,quantity=1,branch_id=2),headers={'X-CSRF-Token':csrf(clerk)}).status==403
    checks.append('reservation excludes units; manager release restores availability; clerk scope and capability enforced')


if __name__=='__main__':main(ledger_checks=ledger_checks)
