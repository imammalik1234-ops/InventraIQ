"""UI actions for the transformation verifier's fresh disposable fixture only."""
from urllib.parse import urlparse, parse_qs
from sqlalchemy import select, func
from database import db
from models import (BranchStock, StockLot, PricedSale, SaleRefund, StockTransfer, SKU, Category,
                    InventoryOperation, PurchaseOrder, PurchaseOrderLine, TransferExecution, TransferTransit)


def run(app, origin, sessions, output, *, open_form, fill, submit, select_branch):
    assert hasattr(app, '_t15_target'), 'Only an attested newly created demonstration is accepted'
    from simulation.runtime import attest
    attest(app._t15_target)
    hq=sessions[901][1]; approver=sessions[902][1]; manager=sessions[912][1]; clerk=sessions[922][1]
    results=[]
    with app.app_context():
        original_prices=dict(db.session.execute(select(PricedSale.sale_id,PricedSale.total_minor)).all())
        supplier=db.session.execute(select(db.metadata.tables['suppliers'].c.supplier_id)).scalar()
        category=db.session.scalar(select(Category.category_id).where(Category.code=='SYN-T15-HOUSEHOLD'))

    hq.goto(origin+'/catalog')
    form=open_form(hq,'Create category')
    fill(form,code='SYN-TRANSFORM-CATEGORY',display_name='Synthetic review category',reason='Synthetic catalog verification')
    submit(hq,form)
    form=open_form(hq,'Create SKU')
    fill(form,code='SYN-TRANSFORM-SKU',name='Synthetic review product',category_id=category,unit='unit',
         default_price='12.34',variant_attributes='{}',reason='Synthetic product definition')
    submit(hq,form)
    hq.locator('[data-catalog-query]').fill('SYN-TRANSFORM-SKU')
    form=open_form(hq,'Edit SKU SYN-TRANSFORM-SKU')
    fill(form,name='Synthetic reviewed product',reason='Synthetic corrected display label')
    submit(hq,form)
    hq.locator('[data-catalog-query]').fill('SYN-TRANSFORM-SKU')
    form=open_form(hq,'Archive SKU SYN-TRANSFORM-SKU');fill(form,reason='Synthetic archive unused definition');submit(hq,form)
    with app.app_context():
        sku=db.session.scalar(select(SKU).where(SKU.code=='SYN-TRANSFORM-SKU'))
        assert not sku.active and sku.name=='Synthetic reviewed product'
    hq.goto(origin+'/purchasing')
    form=open_form(hq,'Create supplier')
    fill(form,code='SYN-TRANSFORM-SUPPLIER',display_name='Synthetic review supplier',reason='Synthetic supplier identity')
    submit(hq,form)
    form=open_form(hq,'Edit supplier SYN-TRANSFORM-SUPPLIER')
    fill(form,display_name='Synthetic reviewed supplier',active='false',reason='Synthetic deactivate unused supplier');submit(hq,form)
    results.append('catalog_category_SKU_create_edit_archive_and_supplier_create_edit')

    # Receipt, FEFO priced sale, partial inspected refund and financial reversal.
    manager.goto(origin+'/receiving')
    form=open_form(manager,'Receive new lot')
    fill(form,sku_id=121,lot_code='SYN-TRANSFORM-RECEIPT',supplier_lot_code='SYN-TRANSFORM-RECEIPT',
         expiry_date='2031-02-28',quantity=10,external_reference='SYN-TRANSFORM-RECEIPT',
         receipt_kind='normal',reason='Synthetic connected receiving',evidence='Synthetic sealed delivery label')
    submit(manager,form)
    assert 'receipt' in parse_qs(urlparse(manager.url).query)
    clerk.goto(origin+'/branch-sales')
    form=open_form(clerk,'Create priced sale')
    fill(form,sku_id=121,quantity=2,business_reference='<script>window.__unexpected=1</script>',reason='Synthetic connected sale')
    submit(clerk,form,'Sale #')
    assert clerk.evaluate('window.__unexpected===undefined')
    sale_id=int(parse_qs(urlparse(clerk.url).query)['sale_id'][0])
    manager.goto(origin+f'/branch-sales?sale_id={sale_id}')
    refund=manager.locator('form[aria-label^="Refund allocation "]').first
    refund.evaluate("e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true}")
    fill(refund,quantity=1,disposition='saleable',inspection_evidence='Synthetic sealed goods inspected',
         business_reference='SYN-TRANSFORM-REFUND',reason='Synthetic partial refund')
    refund.locator('[name="saleable_verified"]').check()
    submit(manager,refund)
    form=open_form(manager,'Reverse remaining sale')
    fill(form,business_reference='SYN-TRANSFORM-REVERSAL',reason='Synthetic financial reversal without restock')
    submit(manager,form)
    with app.app_context():
        sale=db.session.get(PricedSale,sale_id)
        refunds=list(db.session.scalars(select(SaleRefund).where(SaleRefund.sale_id==sale_id)))
        assert len(refunds)==2 and sum(r.total_minor for r in refunds)==sale.total_minor
    results.append('receipt_FEFO_sale_partial_refund_and_remaining_reversal')

    # Native purchasing maker-checker, partial delivery, cancellation of remainder.
    select_branch(hq,2);hq.goto(origin+'/purchasing')
    form=open_form(hq,'Create purchase order')
    fill(form,supplier_id=supplier,expected_date='2031-01-05',sku_id=122,quantity=3,unit_cost='5.00',
         business_reference='SYN-TRANSFORM-PO',reason='Synthetic connected purchasing')
    submit(hq,form,'Draft')
    order_id=int(parse_qs(urlparse(hq.url).query)['order_id'][0])
    form=open_form(hq,f'Submit PO {order_id}');fill(form,reason='Synthetic submit');submit(hq,form,'Submitted')
    assert hq.get_by_role('form',name=f'Approve PO {order_id}',exact=True).count()==0
    select_branch(approver,2);approver.goto(origin+f'/purchasing?order_id={order_id}')
    form=open_form(approver,f'Approve PO {order_id}');fill(form,reason='Synthetic independent approval');submit(approver,form,'Approved')
    manager.goto(origin+f'/purchasing?order_id={order_id}')
    form=manager.locator('form[aria-label^="Receive PO "]').first
    form.evaluate("e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true}")
    fill(form,mode='new_lot',lot_code='SYN-TRANSFORM-PO-LOT',supplier_lot_code='SYN-TRANSFORM-PO-LOT',
         quantity=1,external_reference='SYN-TRANSFORM-PO-DELIVERY',receipt_kind='normal',
         evidence='Synthetic inspected supplier delivery',reason='Synthetic partial delivery')
    submit(manager,form,'Partially received')
    hq.goto(origin+f'/purchasing?order_id={order_id}')
    form=open_form(hq,f'Cancel PO {order_id}');fill(form,reason='Synthetic cancel remaining two units');submit(hq,form)
    with app.app_context():
        line=db.session.scalar(select(PurchaseOrderLine).where(PurchaseOrderLine.order_id==order_id))
        assert (line.ordered,line.received,line.cancelled)==(3,1,2)
    results.append('purchase_draft_submit_independent_approval_partial_receipt_cancel_remaining')

    # Native transfer reservation/dispatch, actual partial arrival, discrepancy/loss.
    manager.goto(origin+'/transfers')
    form=open_form(manager,'Create transfer draft')
    fill(form,destination_branch_id=1,planned_arrival_utc='2031-01-05T08:00:00+00:00',
         sku_id=122,quantity=2,business_reference='SYN-TRANSFORM-TRANSFER',reason='Synthetic connected transfer')
    submit(manager,form,'Draft')
    transfer_id=int(parse_qs(urlparse(manager.url).query)['transfer_id'][0])
    form=open_form(manager,f'Submit transfer {transfer_id}');fill(form,reason='Synthetic submit');submit(manager,form,'Submitted')
    approver.goto(origin+f'/transfers?transfer_id={transfer_id}')
    form=open_form(approver,f'Approve transfer {transfer_id}');fill(form,reason='Synthetic independent approval');submit(approver,form,'Approved')
    manager.goto(origin+f'/transfers?transfer_id={transfer_id}')
    dispatch_name=manager.locator(f'form[aria-label^="Dispatch shipment {transfer_id}"]').first.get_attribute('aria-label')
    form=open_form(manager,dispatch_name);fill(form,reason='Synthetic departure inspected');submit(manager,form)
    select_branch(hq,1);hq.goto(origin+f'/transfers?transfer_id={transfer_id}')
    form=hq.locator('form[aria-label^="Receive shipment "]').first
    form.evaluate("e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true}")
    fill(form,quantity=1,disposition='saleable',reason='Synthetic one unit physically arrived');submit(hq,form)
    form=hq.locator('form[aria-label^="Discrepancy shipment "]').first
    form.evaluate("e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true}")
    fill(form,quantity=1,reason='Synthetic one unit not located');submit(hq,form)
    select_branch(approver,1);approver.goto(origin+f'/transfers?transfer_id={transfer_id}')
    form=approver.locator('form[aria-label^="Resolve shipment "]').first
    form.evaluate("e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true}")
    fill(form,decision='loss',reason='Synthetic evidenced authorized loss');submit(approver,form)
    with app.app_context():
        assert db.session.get(TransferExecution,transfer_id).state=='Received'
        rows=list(db.session.scalars(select(TransferTransit).join(
            db.metadata.tables['stock_transfer_allocations'],
            db.metadata.tables['stock_transfer_allocations'].c.allocation_id==TransferTransit.allocation_id)
            .where(db.metadata.tables['stock_transfer_allocations'].c.transfer_id==transfer_id)))
        assert sum(t.received for t in rows)==1 and sum(t.lost for t in rows)==1 and sum(t.remaining for t in rows)==0
    results.append('transfer_draft_submit_approve_reserve_dispatch_partial_receive_discrepancy_authorized_loss')

    # T27 creation and GET review are inert; explicit confirmation is once only.
    with app.app_context():
        lot=db.session.scalar(select(StockLot.lot_id).where(StockLot.lot_code=='SYN-TRANSFORM-RECEIPT'))
    def counts():
        with app.app_context():
            return (db.session.scalar(select(func.count()).select_from(InventoryOperation)),
                    db.session.scalar(select(func.count()).select_from(StockTransfer)),
                    db.session.get(BranchStock,(2,lot)).on_hand)
    for cancel in (True,False):
        before=counts();manager.goto(origin+'/assistant/proposals')
        manager.get_by_label('Exact local proposal question').fill(f'propose write-off SKU 121 lot {lot} quantity 1')
        manager.get_by_role('button',name='Create proposal for review',exact=True).click()
        manager.wait_for_url('**/assistant/proposals/*');assert counts()==before
        manager.get_by_role('button',name='Confirm exact proposal',exact=True).click()
        assert counts()==before and not manager.get_by_role('checkbox').is_checked()
        if cancel:
            manager.get_by_role('button',name='Reject / cancel proposal',exact=True).click()
            manager.get_by_text('Cancelled. No business action was initiated.',exact=True).wait_for()
            assert counts()==before
        else:
            manager.set_viewport_size(dict(width=320,height=1000))
            assert manager.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            manager.screenshot(path=str(output/'proposal-review-320.png'),full_page=True)
            manager.get_by_role('checkbox').check()
            manager.get_by_role('button',name='Confirm exact proposal',exact=True).click()
            manager.get_by_text('Confirmed once.',exact=False).wait_for()
            after=counts();assert after[0]==before[0]+1 and after[2]==before[2]-1
            manager.reload();manager.reload();assert counts()==after
    manager.set_viewport_size(dict(width=1440,height=1000))
    manager.goto(origin+'/assistant/proposals');before=counts()
    manager.get_by_label('Exact local proposal question').fill('propose transfer SKU 122 quantity 1 to branch 1 arriving 2031-01-05T08:00:00+00:00')
    manager.get_by_role('button',name='Create proposal for review',exact=True).click()
    manager.wait_for_url('**/assistant/proposals/*');assert counts()==before
    manager.get_by_role('checkbox').check();manager.get_by_role('button',name='Confirm exact proposal',exact=True).click()
    manager.get_by_text('Confirmed once.',exact=False).wait_for()
    after=counts();assert after[1]==before[1]+1
    with app.app_context():
        draft=db.session.scalar(select(StockTransfer).order_by(StockTransfer.transfer_id.desc()).limit(1))
        assert draft.state=='Draft' and draft.approver_id is None
        current=dict(db.session.execute(select(PricedSale.sale_id,PricedSale.total_minor)).all())
        assert all(current[k]==v for k,v in original_prices.items())
    manager.reload();assert counts()==after
    results.append('proposal_review_cancel_writeoff_confirmation_once_transfer_draft_only')
    hq.goto(origin+'/admin-settings')
    form=open_form(hq,'Update profile')
    original_name=form.locator('[name="name"]').input_value()
    fill(form,name='Synthetic reviewed HQ profile',email='synthetic-901@demo.invalid')
    submit(hq,form,'Profile settings saved and updated successfully!')
    assert hq.locator('.profile-summary strong').inner_text()=='Synthetic reviewed HQ profile'
    assert not hq.locator('#global-message').is_visible()
    form=open_form(hq,'Update profile');fill(form,name=original_name,email='synthetic-901@demo.invalid')
    submit(hq,form,'Profile settings saved and updated successfully!')
    results.append('profile_name_save_and_visible_native_feedback')
    hq.goto(origin+'/branch-administration')
    form=open_form(hq,'Edit branch DEMO-03')
    assert form.evaluate('e=>e.action instanceof HTMLInputElement'), 'Exercise the real named-control collision'
    fill(form,display_name='Demo West reviewed');submit(hq,form)
    form=open_form(hq,'Edit branch DEMO-03');fill(form,display_name='Demo West');submit(hq,form)
    form=open_form(hq,'Save branch assignment')
    fill(form,user_id=921,branch_id=3,branch_role='clerk',active='true');submit(hq,form)
    form=open_form(hq,'Save branch assignment')
    fill(form,user_id=921,branch_id=3,branch_role='clerk',active='false');submit(hq,form)
    results.append('branch_display_edit_and_scoped_assignment_grant_revoke')
    return dict(passed=True,workflows=results,original_sale_headers_unchanged=True,escaped_reference_no_execution=True)
