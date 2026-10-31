"""T09 role/scope/reconciliation contracts on disposable synthetic data only."""
import unittest
import csv
import re
import io
from contextlib import contextmanager
from unittest.mock import patch
from sqlalchemy import select
from werkzeug.exceptions import Forbidden
import test_catalog_receiving as fixtures
from app import app
from database import db
from models import Branch,Sale,UserBranch,User,BranchProduct,StockLot,SKU
import operational_services as queries

NOW=fixtures.NOW


class OperationalReportingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.CatalogReceivingTests();self.fixture.setUp();self.client=self.fixture.client
        app.config['LEGACY_STOCK_REHEARSAL']=False
        self.clocks=[patch('inventory_ledger.utcnow',return_value=NOW),patch('operational_services.utcnow',return_value=NOW)]
        for clock in self.clocks:clock.start()
        self.receipts={};self.sales={}
        for branch in (1,2,3):
            self.fixture.fixture.login(1,branch=branch)
            self.receipts[branch]=self.fixture.receive(self.fixture.delivery(lot_code=f'LOT-BR{branch}',supplier_lot_code=f'LABEL-BR{branch}',
                external_reference=f'BR{branch}-PRIVATE-RECEIPT',quantity=branch*10))
            self.sales[branch]=self.fixture.post('/api/priced-sales',dict(lines=[dict(sku_id=self.fixture.sku,quantity=branch)],request_key=f'SALE-BR{branch}',business_reference=f'BR{branch}-PRIVATE-SALE',reason='Synthetic branch sale'))
        self.fixture.fixture.login(1,branch=2)
        self.fixture.post(f"/api/priced-sales/{self.sales[2]['sale_id']}/refunds",dict(mode='partial',
            lines=[dict(allocation_id=self.sales[2]['lines'][0]['allocations'][0]['allocation_id'],quantity=1,disposition='no_restock')],
            request_key='REFUND-BR2',business_reference='BR2-PRIVATE-REFUND',reason='Synthetic refund'))
        for row in db.session.scalars(select(Sale)):row.sale_date=NOW.date()
        db.session.commit();self.fixture.fixture.login(1,branch=None)
    def tearDown(self):
        for clock in self.clocks:clock.stop()
        self.fixture.tearDown()
    @contextmanager
    def context(self,uid=1,branch=None):
        ctx=self.fixture.fixture.context(uid,branch)
        try:yield
        finally:ctx.pop()
    def api(self,query=''):
        response=self.client.get('/api/operations'+query);self.assertEqual(response.status_code,200,response.get_data(as_text=True));return response.get_json()

    def test_three_distinct_branches_hq_totals_reconcile(self):
        report=self.api();self.assertEqual([r['on_hand'] for r in report['branches']],[20,40,60])
        self.assertEqual([r['available'] for r in report['branches']],[9,18,27])
        self.assertEqual((report['total']['recorded_gross'],report['total']['recorded_refunds'],report['total']['recorded_net']),('15.00','2.50','12.50'))
        for field in ('on_hand','available','known_sold_units','unknown_revenue_units','gross_minor','refund_minor'):
            self.assertEqual(report['total'][field],sum(row[field] for row in report['branches']))
        self.assertEqual(report['total']['legacy_unverified'],66)
        self.assertGreater(report['total']['unknown_revenue_units'],0)

    def test_role_matrix_and_scope_isolation_on_pages_api_exports(self):
        for uid,branch,allowed in ((1,None,[1,2,3]),(2,None,[1,2,3]),(3,1,[1]),(4,1,[1]),(6,2,[2])):
            self.fixture.fixture.login(uid,branch=branch)
            with self.subTest(uid=uid):
                data=self.api();self.assertEqual(data['scope']['branch_ids'],allowed)
                self.assertEqual({row['branch_id'] for row in data['branches']},set(allowed))
                for path in ('/dashboard','/admin-dashboard','/inventory-page','/sales','/reports','/analytics','/expiry-alerts','/staff'):
                    response=self.client.get(path);self.assertEqual(response.status_code,200,(uid,path,response.get_data(as_text=True)))
                    text=response.get_data(as_text=True)
                    for other in set((1,2,3))-set(allowed):self.assertNotIn(f'BR{other}-PRIVATE-',text)
                exported=self.client.get('/operations/export.csv');self.assertEqual(exported.status_code,200)
                rows=list(csv.reader(io.StringIO(exported.get_data(as_text=True))))
                index=next(i for i,row in enumerate(rows) if row[:2]==['branch_id','code'])
                self.assertEqual([int(row[0]) for row in rows[index+1:]],allowed)

    def test_branch_selector_only_authorized_options_and_selection_persists(self):
        self.fixture.fixture.login(6,branch=1)
        text=self.client.get('/dashboard').get_data(as_text=True)
        self.assertIn('aria-label="Switch branch"',text)
        options=re.search(r'<select[^>]*id="shell-branch"[^>]*>(.*?)</select>',text,re.S).group(1)
        self.assertNotIn('value="3"',options);self.assertNotIn('Company-wide (HQ)',options)
        response=self.client.post('/scope',data={'branch_id':'2','next':'/analytics'})
        self.assertEqual((response.status_code,response.location),(302,'/analytics'))
        self.assertEqual(self.api()['scope']['branch_ids'],[2])
        self.assertEqual(self.client.post('/scope',data={'branch_id':'3'}).status_code,403)
        self.assertEqual(self.api()['scope']['branch_ids'],[2])

    def test_hq_analyst_can_explicitly_switch_selected_and_company_wide(self):
        self.fixture.fixture.login(2,branch=None)
        self.assertIn('Company-wide (HQ)',self.client.get('/dashboard').get_data(as_text=True))
        self.assertEqual(self.client.post('/scope',data={'branch_id':'3','next':'/reports'}).status_code,302)
        self.assertEqual(self.api()['scope']['branch_ids'],[3])
        self.client.post('/scope',data={'branch_id':'all','next':'/reports'})
        self.assertTrue(self.api()['scope']['consolidated'])
        self.assertEqual(self.client.post('/api/priced-sales',json={}).status_code,403)

    def test_revoked_selected_assignment_clears_selection_without_broadening(self):
        self.fixture.fixture.login(6,branch=2)
        db.session.get(UserBranch,(6,2)).active=False;db.session.commit()
        self.assertEqual(self.client.get('/dashboard').status_code,403)
        with self.client.session_transaction() as session:self.assertNotIn('active_branch_id',session)
        self.assertEqual(self.api()['scope']['branch_ids'],[1])

    def test_forged_branch_ids_wrong_branch_lots_and_direct_services(self):
        self.fixture.fixture.login(3)
        for path in ('/api/operations?branch_id=2','/reports?branch_id=2','/operations/export.csv?branch_id=2',
                     f"/api/stock-lots/{self.receipts[2]['lot_id']}"):
            self.assertIn(self.client.get(path).status_code,(403,404),path)
        with self.context(3,1):
            with self.assertRaises(Forbidden):queries.report({'branch_id':2})
        self.assertEqual(self.client.get('/api/operations?branch_ids=1,2').status_code,403)

    def test_dashboard_reports_analytics_api_agree(self):
        self.fixture.fixture.login(3)
        data=self.api()
        for path in ('/dashboard','/reports','/analytics','/inventory-page','/sales','/expiry-alerts'):
            text=self.client.get(path).get_data(as_text=True)
            for attr,field in (('gross','recorded_gross'),('refunds','recorded_refunds'),('net','recorded_net')):
                self.assertIn(f'data-{attr}="{data["total"][field]}"',text)
        self.assertEqual(self.client.get('/inventory').get_json(),data['inventory'])
        self.assertEqual(self.client.get('/alerts').get_json(),data['alerts'])
        self.assertEqual(self.client.get('/api/sku-stock').get_json(),data['inventory'])

    def test_csv_formula_escape_scope_and_provenance(self):
        branch=db.session.get(Branch,1);branch.display_name=' =HYPERLINK("evil")';db.session.commit()
        self.fixture.fixture.login(3)
        response=self.client.get('/operations/export.csv')
        rows=list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertTrue(any(row and any(cell.startswith("' =HYPERLINK") for cell in row) for row in rows))
        self.assertIn('MYR',response.get_data(as_text=True));self.assertIn('inventory_as_of_utc',response.get_data(as_text=True))
        self.assertIn('no-store',response.headers['Cache-Control'])
        old=self.client.get('/admin-settings/export-data');self.assertEqual(old.get_data(),response.get_data())

    def test_dates_empty_range_and_refund_period_are_independent_of_sale_period(self):
        data=self.api('?start=2031-01-01&end=2031-01-02')
        self.assertEqual((data['total']['recorded_gross'],data['total']['recorded_refunds'],data['total']['unknown_revenue_units']),('0.00','0.00',0))
        self.assertEqual(data['total']['on_hand'],120)
        with patch('inventory_ledger.utcnow',return_value=NOW.replace(day=2)):
            self.fixture.fixture.login(1,branch=1)
            self.fixture.post(f"/api/priced-sales/{self.sales[1]['sale_id']}/refunds",dict(mode='full',request_key='NEXT-DAY',business_reference='NEXT-DAY',reason='Next day return'))
        data=self.api('?start=2030-01-02&end=2030-01-02')
        self.assertEqual((data['total']['recorded_gross'],data['total']['recorded_refunds'],data['total']['recorded_net']),('0.00','2.50','-2.50'))

    def test_empty_branch_remains_honest(self):
        db.session.add(Branch(branch_id=4,code='EMPTY',display_name='Empty branch',timezone='UTC'));db.session.commit()
        self.fixture.fixture.login(1,branch=4);data=self.api()
        self.assertEqual((data['total']['on_hand'],data['total']['sold_units'],data['total']['recorded_net']),(0,0,'0.00'))
        self.assertEqual(data['inventory']['items'],[]);self.assertEqual(data['activity'],[])
        self.assertIn('No inventory matches',self.client.get('/dashboard').get_data(as_text=True))

    def test_pagination_filter_sort_and_validation(self):
        first=self.api('?page=1&page_size=1');second=self.api('?page=2&page_size=1')
        self.assertEqual(len(first['inventory']['items']),1)
        self.assertNotEqual(first['inventory']['items'][0]['lot_id'],second['inventory']['items'][0]['lot_id'])
        self.assertEqual(first['inventory']['total'],6);self.assertEqual(first['total'],second['total'])
        search=self.api('?search=LOT-BR3');self.assertEqual(search['inventory']['total'],1)
        self.assertEqual(search['total']['on_hand'],120)
        for args in ('page=0','page_size=101','sort=money','status=fake','start=2030-02-30','start=2030-01-02&end=2030-01-01','start=2020-01-01&end=2030-01-01','page=1&page=2','actor=1'):
            self.assertEqual(self.client.get('/api/operations?'+args).status_code,400,args)

    def test_staff_roster_respects_scope_and_clerk_self_visibility(self):
        self.fixture.fixture.login(3)
        text=self.client.get('/staff').get_data(as_text=True);self.assertIn('Fixture 4',text);self.assertNotIn('Branch 2:',text)
        self.fixture.fixture.login(4);text=self.client.get('/staff').get_data(as_text=True)
        self.assertIn('Fixture 4',text);self.assertNotIn('Fixture 3',text)

    def test_request_cache_rechecks_authorization_and_scope(self):
        with self.context(6,1):
            first=queries.report({})
            second=queries.report({'branch_id':2})
            self.assertEqual(first['scope']['branch_ids'],[1]);self.assertEqual(second['scope']['branch_ids'],[2])
            db.session.get(UserBranch,(6,2)).active=False;db.session.flush()
            with self.assertRaises(Forbidden):queries.report({'branch_id':2})
            db.session.rollback()

    def test_csrf_and_local_redirect_only(self):
        self.fixture.fixture.login(6)
        self.assertEqual(self.client.post('/scope',data={'branch_id':'2'},headers={'X-CSRF-Token':''}).status_code,400)
        response=self.client.post('/scope',data={'branch_id':'2','next':'https://example.invalid'})
        self.assertEqual(response.location,'/dashboard')

    def test_reservations_quarantine_and_expiry_boundaries_match_shared_classifier(self):
        self.fixture.fixture.login(3)
        self.fixture.post('/api/stock-reservations',dict(sku_id=self.fixture.sku,quantity=2,request_key='HOLD',business_reference='HOLD',reason='Verified hold'))
        data=self.api();self.assertEqual((data['total']['on_hand'],data['total']['available'],data['total']['reserved']),(20,7,2))
        self.fixture.receive(dict(mode='existing_lot',sku_id=self.fixture.sku,lot_id=self.receipts[1]['lot_id'],expiry_date='2030-01-08',quantity=3,
            receipt_kind='quarantine',external_reference='HOLD-ARRIVAL',reason='Inspected damage',evidence='Damaged carton'))
        data=self.api();self.assertEqual((data['total']['on_hand'],data['total']['available'],data['total']['quarantined']),(23,7,3))
        from expiry import canonical_expiry
        with patch('operational_services.utcnow',return_value=canonical_expiry('2030-01-08','Asia/Singapore')):
            data=self.api();self.assertEqual(data['total']['available'],0);self.assertEqual(data['total']['expired_lots'],1)

    def test_native_unopened_balances_are_review_held(self):
        from models import BranchStock
        lot=StockLot(sku_id=self.fixture.sku,lot_code='NO-OPENING',validation_state='validated',provenance={'synthetic':True})
        db.session.add(lot);db.session.flush()
        db.session.add(BranchStock(branch_id=1,sku_id=self.fixture.sku,lot_id=lot.lot_id,on_hand=5,reserved=0,quarantined_quantity=0,version=1));db.session.commit()
        self.fixture.fixture.login(3)
        row=next(r for r in self.api()['inventory']['items'] if r['lot_id']==lot.lot_id)
        self.assertEqual((row['status'],row['available'],row['availability_verified']),('review',0,False))

    def test_query_count_does_not_grow_per_lot_and_response_is_bounded(self):
        from sqlalchemy import event
        from models import BranchStock
        self.fixture.fixture.login(3)
        def count():
            statements=[]
            def capture(*args):statements.append(args[2])
            event.listen(db.engine,'before_cursor_execute',capture)
            try:data=self.api('?page_size=1')
            finally:event.remove(db.engine,'before_cursor_execute',capture)
            return len(statements),data
        before,_=count()
        for index in range(60):
            lot=StockLot(sku_id=self.fixture.sku,lot_code=f'SYNTHETIC-{index}',validation_state='review',provenance={'synthetic':True})
            db.session.add(lot);db.session.flush()
            db.session.add(BranchStock(branch_id=1,sku_id=self.fixture.sku,lot_id=lot.lot_id,on_hand=1,reserved=0,quarantined_quantity=0,version=1))
        db.session.commit();after,data=count()
        self.assertEqual(before,after);self.assertEqual(len(data['inventory']['items']),1)
        self.assertEqual(data['inventory']['total'],62)

    def test_adopted_legacy_without_opening_uses_actual_source_not_stale_copy(self):
        from models import Inventory
        import catalog_services
        catalog_services._adopt_identity(db.session.get(SKU,1))
        source=db.session.scalar(select(Inventory).where(Inventory.product_id==11));source.quantity=15;db.session.commit()
        self.fixture.fixture.login(3)
        data=self.api();legacy=next(row for row in data['inventory']['items'] if row['lot_id']==11)
        self.assertEqual((legacy['source'],legacy['on_hand'],data['total']['on_hand']),('legacy_inventory',15,24))
        self.assertEqual(sum(row['lot_id']==11 for row in data['inventory']['items']),1)

    def test_multiline_headers_are_not_duplicated_by_financial_joins(self):
        self.fixture.fixture.login(1,branch=None)
        other=self.fixture.post('/api/skus',self.fixture.sku_payload(code='OTHER-REPORT',name='Report second SKU',variant_attributes={'volume':'other'},default_price='1.00'))['sku_id']
        self.fixture.put(f'/api/assortments/1/{other}',dict(enabled=True,expected_version=0,reason='Approved assortment'))
        self.fixture.fixture.login(1,branch=1)
        self.fixture.receive(self.fixture.delivery(sku_id=other,lot_code='OTHER',supplier_lot_code='OTHER',external_reference='OTHER',quantity=3))
        self.fixture.post('/api/priced-sales',dict(lines=[dict(sku_id=self.fixture.sku,quantity=1),dict(sku_id=other,quantity=1)],
            request_key='MULTI-REPORT',business_reference='MULTI-REPORT',reason='Two recorded product lines'))
        data=self.api();self.assertEqual((data['total']['recorded_gross'],data['total']['known_sold_units']),('6.00',3))

    def test_report_uses_one_read_transaction_without_data_mutations(self):
        with self.context(1,None):
            queries.report({})
            self.assertTrue(db.session.connection().connection.driver_connection.in_transaction)
            self.assertFalse(db.session.new);self.assertFalse(db.session.dirty);self.assertFalse(db.session.deleted)
            db.session.rollback()

    def test_normal_lot_operations_keep_authorized_request_scope_on_post_and_redirect(self):
        self.fixture.fixture.login(1,branch=1)
        report=self.api('?branch_id=2')
        row=next(r for r in report['inventory']['items'] if r['source']=='native_lot')
        page=self.client.get('/sku-inventory?branch_id=2').get_data(as_text=True)
        self.assertIn('aria-label="Adjust native lot',page)
        self.assertIn('/sku-inventory?branch_id=2',page)
        result=self.client.post('/sku-inventory?branch_id=2',data=dict(stock_action='adjustment',sku_id=row['sku_id'],lot_id=row['lot_id'],
            expected_revision=row['stock_revision'],quantity=1,kind='adjustment',direction='decrease',
            request_key='SCOPED-FORM',business_reference='BR2-COUNT',reason='Verified branch 2 count'))
        self.assertEqual((result.status_code,result.location),(302,'/sku-inventory?branch_id=2'))
        self.assertEqual(self.api('?branch_id=2')['total']['on_hand'],39)
        self.assertEqual(self.api()['scope']['branch_ids'],[1])

    def test_temporary_branch_preview_does_not_expand_role_or_misdirect_cashier_form(self):
        self.fixture.fixture.login(6,branch=1)
        page=self.client.get('/branch-sales?branch_id=2').get_data(as_text=True)
        self.assertIn('/branch-sales/create?branch_id=2',page)
        page=self.client.get('/sku-inventory?branch_id=2').get_data(as_text=True)
        self.assertNotIn('aria-label="Adjust native lot',page)
        sale=self.client.post('/api/priced-sales?branch_id=2',json=dict(lines=[dict(sku_id=self.fixture.sku,quantity=1)],
            request_key='BR2-CLERK',business_reference='BR2-CLERK',reason='Authorized branch 2 clerk sale'))
        self.assertEqual(sale.status_code,200);self.assertEqual(sale.get_json()['branch_id'],2)
        self.assertEqual(self.api()['scope']['branch_ids'],[1])

    def test_filtered_aliases_and_archived_skus_do_not_fabricate_reorder_actions(self):
        offer=db.session.get(BranchProduct,(1,self.fixture.sku));offer.reorder_level_override=20;db.session.commit()
        self.fixture.fixture.login(3)
        for path in ('/inventory/low-stock','/inventory/reorder-needed','/inventory-page?status=low_stock'):
            response=self.client.get(path);self.assertEqual(response.status_code,200)
            from test_design_accessibility import Document
            document=Document(response.get_data(as_text=True))
            conditions=[node for node in document.nodes if node.attrs.get('data-condition')=='low_stock']
            self.assertTrue(conditions)
            self.assertTrue(all(node.text.strip()=='Low stock' for node in conditions))
        sku=db.session.get(SKU,self.fixture.sku);sku.active=False;db.session.commit()
        data=self.api();self.assertEqual(data['total']['low_stock_skus'],0)
        self.assertEqual(data['total']['available'],0)
        self.assertFalse(any(row['status']=='low_stock' for row in data['alerts']['items']))
