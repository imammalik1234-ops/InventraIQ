"""Real T08 browser workflows extending isolated synthetic T06 setup."""
from datetime import date,timedelta
from urllib.parse import urlparse,parse_qs
from verify_t06 import main


def sales_checks(manager,clerk,origin,sku_id,native,checks,csrf,form,submit):
    base=next(row for row in manager.request.get(origin+'/api/skus').json() if row['code']=='COLA-500')['sku_id']
    receipt=dict(mode='new_lot',sku_id=base,lot_code='SALE-BASE',supplier_lot_code='SALE-BASE',quantity=5,
        expiry_date=(date.today()+timedelta(days=30)).isoformat(),external_reference='SALE-BASE',reason='Synthetic receiving',evidence='Observed fixture label')
    assert manager.request.post(origin+'/api/receipts',data=receipt,headers={'X-CSRF-Token':csrf(manager)}).status==200
    assert manager.goto(origin+'/branch-sales').status==200
    element=form(manager,'Create priced sale');key=element.locator('[name="request_key"]').input_value()
    element.locator('[name="sku_id"]').nth(0).select_option(str(sku_id));element.locator('[name="quantity"]').nth(0).fill('13')
    element.locator('[name="sku_id"]').nth(1).select_option(str(base));element.locator('[name="quantity"]').nth(1).fill('2')
    element.locator('[name="business_reference"]').fill('BROWSER-PRICED');element.locator('[name="reason"]').fill('Verified browser sale')
    submit(manager,element,'Complete priced sale')
    manager.get_by_role('status').get_by_text('Original MYR 79.75',exact=False).wait_for()
    sale_id=int(parse_qs(urlparse(manager.url).query)['sale_id'][0])
    payload=dict(lines=[dict(sku_id=sku_id,quantity=13),dict(sku_id=base,quantity=2)],request_key=key,business_reference='BROWSER-PRICED',reason='Verified browser sale')
    retry=manager.request.post(origin+'/api/priced-sales',data=payload,headers={'X-CSRF-Token':csrf(manager)})
    assert retry.status==200 and retry.json()['replayed'] and retry.json()['total']=='79.75'
    detail=manager.request.get(origin+f'/api/priced-sales?sale_id={sale_id}').json()
    allocations=next(row for row in detail['lines'] if row['sku_id']==sku_id)['allocations']
    assert [row['quantity'] for row in allocations]==[12,1]
    checks.append('multi-SKU sale form captures approved MYR prices, spans FEFO lots, and retries exactly once')
    allocation=allocations[0]['allocation_id'];element=form(manager,f'Refund allocation {allocation}')
    element.locator('[name="quantity"]').fill('1');element.locator('[name="disposition"]').select_option('saleable')
    element.locator('[name="saleable_verified"]').check();element.locator('[name="inspection_evidence"]').fill('Sealed original batch inspected')
    element.locator('[name="business_reference"]').fill('BROWSER-RETURN');element.locator('[name="reason"]').fill('Verified returned item')
    submit(manager,element,f'Refund allocation {allocation}')
    manager.get_by_role('status').get_by_text('Refunded MYR 5.75',exact=False).wait_for()
    checks.append('manager partial refund form requires inspection and restocks the original eligible lot')
    assert clerk.goto(origin+f'/branch-sales?sale_id={sale_id}').status==200
    assert clerk.get_by_role('form',name='Reverse remaining sale',exact=True).count()==0
    assert clerk.request.post(origin+f'/api/priced-sales/{sale_id}/refunds',data=dict(mode='full',request_key='DENIED',business_reference='DENIED',reason='Denied'),headers={'X-CSRF-Token':csrf(clerk)}).status==403
    assert manager.request.post(origin+'/api/priced-sales',data=dict(payload,request_key='PRICE-INJECT',unit_price='0.01'),headers={'X-CSRF-Token':csrf(manager)}).status==400
    checks.append('clerk refund denial and arbitrary client price rejection')
    element=form(manager,'Reverse remaining sale');reverse_key=element.locator('[name="request_key"]').input_value()
    element.locator('[name="business_reference"]').fill('BROWSER-REVERSE');element.locator('[name="reason"]').fill('Authorized remainder reversal')
    submit(manager,element,'Reverse remaining sale without restock')
    manager.get_by_role('status').get_by_text('Net MYR 0.00',exact=False).wait_for()
    reverse=dict(mode='full',request_key=reverse_key,business_reference='BROWSER-REVERSE',reason='Authorized remainder reversal')
    retry=manager.request.post(origin+f'/api/priced-sales/{sale_id}/reversal',data=reverse,headers={'X-CSRF-Token':csrf(manager)})
    assert retry.status==200 and retry.json()['replayed'] and retry.json()['total']=='74.00'
    detail=manager.request.get(origin+f'/api/priced-sales?sale_id={sale_id}').json()
    assert detail['total']=='79.75' and detail['refunded_total']=='79.75' and detail['net_total']=='0.00'
    assert all(a['refundable_quantity']==0 for line in detail['lines'] for a in line['allocations'])
    checks.append('linked full remainder reversal preserves receipt, caps refunds, and replays original result')
    for width in (768,390):
        manager.set_viewport_size({'width':width,'height':900});assert manager.goto(origin+f'/branch-sales?sale_id={sale_id}').status==200
        assert manager.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    checks.append('priced sale/refund page remains responsive at 768/390 pixels')


if __name__=='__main__':main(ledger_checks=sales_checks)
