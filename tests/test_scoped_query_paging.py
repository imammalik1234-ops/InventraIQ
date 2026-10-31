"""T16 SQL/page/export invariants on new synthetic disposable targets only."""
import csv
import io
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from sqlalchemy import select, update, func, event
from werkzeug.exceptions import Forbidden
from scripts.measure_t16 import fixture, client
from simulation.runtime import actor
from database import db
from models import UserBranch, BranchStock, StockLot
from security import MutationError
import operational_services as reports
import historical_queries as history


class ScopedQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory()
        cls.root,cls.app=fixture(cls.temp.name,'small',indexes=True)
    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():db.session.remove();db.engine.dispose()
        cls.temp.cleanup()

    def history(self, **kw):
        return history.read(dict(start='2001-01-01',end='2001-01-14',**kw))

    def test_invalid_and_repeated_page_parameters(self):
        user=client(self.app)
        for value in ['page=0','page_size=101','page_size=-1','page_size=1.5','page=10001','sort=random',
                      'page=1&page=2','cursor=bad','actor_id=901']:
            response=user.get('/api/observations?start=2001-01-01&end=2001-01-14&'+value)
            self.assertIn(response.status_code,(403,) if value.startswith('actor_id=') else (400,409),value)
        for value in ('choice_page=0','choice_page=bad','page_size=101','choice_search='+('x'*81)):
            self.assertEqual(user.get('/branch-sales?branch_id=1&'+value).status_code,400,value)

    def test_deep_observation_keysets_no_duplicates_or_missing_cells(self):
        with actor(self.app,901):
            query=dict(page_size=13,sort='asc');ids=[]
            while True:
                data=self.history(**query)
                self.assertLessEqual(len(data['items']),13)
                ids += [r['observation_id'] for r in data['items']]
                if not data['next_cursor']:break
                query['cursor']=data['next_cursor']
            self.assertEqual(len(ids),252);self.assertEqual(len(set(ids)),252)
            self.assertEqual(ids,sorted(ids))

    def test_descending_filter_parity_and_out_of_range_page(self):
        with actor(self.app,912,2):
            asc=self.history(page_size=100,sort='asc');desc=self.history(page_size=100,sort='desc')
            self.assertEqual([r['observation_id'] for r in asc['items']],list(reversed([r['observation_id'] for r in desc['items']])))
            empty=self.history(page=10000)
            self.assertEqual(empty['items'],[]);self.assertEqual(empty['total'],84)
            short=history.read(dict(start='2001-01-01',end='2001-01-01',page_size=100))
            self.assertEqual(short['totals']['cells'],6)

    def test_role_scope_matrix_and_forged_branch(self):
        for uid,branch,expected in [(901,None,{1,2,3}),(903,None,{1,2,3}),(911,1,{1}),(921,1,{1})]:
            with self.subTest(uid=uid),actor(self.app,uid,branch):
                data=self.history(page_size=100)
                self.assertTrue({r['branch_id'] for r in data['items']} <= expected)
                self.assertEqual(set(data['scope']['branch_ids']),expected)
                if branch:
                    with self.assertRaises(Forbidden):self.history(branch_id=2)

    def test_cursor_binds_actor_scope_filters_and_publication_version(self):
        with actor(self.app,911,1):token=self.history(page_size=1)['next_cursor']
        with actor(self.app,921,1),self.assertRaises(MutationError):self.history(page_size=1,cursor=token)
        with actor(self.app,911,1):
            for changes in [dict(sort='desc'),dict(page_size=2),dict(end='2001-01-13')]:
                values=dict(start='2001-01-01',end='2001-01-14',page_size=1,cursor=token);values.update(changes)
                with self.assertRaises(MutationError):history.read(values)
            from query_paging import decode,serializer
            value=serializer().loads(token);context=value['context'];context['revision'][0][1]+=1
            with self.assertRaises(MutationError):decode(token,context)

    def test_same_request_revocation_is_rechecked_without_cache(self):
        with actor(self.app,911,1):
            data=self.history(page_size=1)
            db.session.execute(update(UserBranch).where(UserBranch.user_id==911).values(active=False))
            with self.assertRaises(Forbidden):self.history(page_size=1,cursor=data['next_cursor'])
            with self.assertRaises(Forbidden):reports.report({})
            # actor() rolls back; no committed fixture changes.

    def test_missing_zero_units_and_original_dates_reconcile(self):
        with actor(self.app,901):
            data=self.history(page_size=100)
            self.assertEqual(data['totals'],dict(units=732,cells=252,missing=10,observed_zero=36))
            self.assertEqual(data['provenance'],'synthetic_fixture')
            self.assertTrue(all(r['business_date'].startswith('2001-') for r in data['items']))
            self.assertTrue(any(r['units'] is None and r['coverage']=='missing' for r in data['items']))
            self.assertTrue(any(r['units']==0 and r['coverage']=='observed' for r in data['items']))
            branches=[self.history(branch_id=b)['totals']['units'] for b in (1,2,3)]
            self.assertEqual(sum(branches),732)

    def test_sql_status_and_totals_match_t09_pure_classifier(self):
        from authz import resolve_scope
        from expiry import utcnow
        with actor(self.app,901):
            old=list(reports._lots(resolve_scope(),utcnow(),'asc'))
            result=reports.report(dict(page_size=100))
            key=lambda r:(r['source'],r['branch_id'],r['sku_id'],r['lot_id'])
            actual={key(r):r for r in result['inventory']['items']}
            for row in old:
                self.assertEqual(row,actual[key(row)])
            self.assertEqual(sum(r['on_hand'] for r in old),result['total']['on_hand'])
            self.assertEqual(sum(r['available'] for r in old),result['total']['available'])

    def test_sql_expiry_boundaries_timezones_and_review_policy_parity(self):
        from datetime import timedelta
        from expiry import canonical_expiry
        from models import Branch,CategoryExpiryPolicy
        from authz import resolve_scope
        boundary=canonical_expiry('2031-01-04','Asia/Singapore')
        with actor(self.app,901):
            for timezone in ('Asia/Singapore','America/New_York','Pacific/Auckland'):
                db.session.get(Branch,1).timezone=timezone;db.session.flush()
                for instant in (boundary-timedelta(days=3),boundary-timedelta(seconds=1),boundary,boundary+timedelta(seconds=1)):
                    with patch.object(reports,'utcnow',return_value=instant):
                        old=list(reports._lots(resolve_scope(),instant,'asc'))
                        result=reports.report(dict(page_size=100))
                        expected={(r['branch_id'],r['lot_id']):(r['status'],r['days_remaining'],r['available']) for r in old}
                        self.assertEqual(expected,{(r['branch_id'],r['lot_id']):(r['status'],r['days_remaining'],r['available']) for r in result['inventory']['items']})
            # A missing policy on an identity-managed SKU remains review with
            # unknown days, not an optimistic expiry/availability classification.
            for policy in db.session.scalars(select(CategoryExpiryPolicy)):db.session.delete(policy)
            db.session.flush()
            data=reports.report(dict(page_size=100))
            self.assertTrue(all(r['status']=='review' and r['days_remaining'] is None for r in data['inventory']['items']))

    def test_inventory_deep_pages_and_snapshot_conflict(self):
        with actor(self.app,901):
            first=reports.report(dict(page_size=1));token=first['pagination']['snapshot']
            ids=[]
            for n in range(1,first['inventory']['total']+1):
                data=reports.report(dict(page_size=1,page=n,snapshot=token))
                r=data['inventory']['items'][0];ids.append((r['source'],r['branch_id'],r['lot_id']))
            self.assertEqual(len(set(ids)),first['inventory']['total'])
            db.session.execute(update(BranchStock).where(BranchStock.branch_id==1).values(version=BranchStock.version+1))
            with self.assertRaises(MutationError):reports.report(dict(page_size=1,page=2,snapshot=token))

    def test_sale_cursor_high_watermark_and_money_reconcile(self):
        with actor(self.app,901):
            data=reports.report(dict(page_size=2));ids=[]
            while True:
                ids += [r['sale_id'] for r in data['sales']['items']]
                token=data['sales']['next_cursor']
                if not token:break
                data=reports.report(dict(page_size=2,cursor=token))
            self.assertEqual(ids,list(range(1,10)))
            self.assertEqual(data['total']['gross_minor']-data['total']['refund_minor'],31540)
            self.assertEqual(data['total']['recorded_net'],'315.40')

    def test_native_sale_cursor_excludes_concurrent_append_and_choices_are_bounded(self):
        from sales_services import sales_list,create_sale
        from receiving_services import sales_choices
        with actor(self.app,901,1):
            original=sales_list(dict(page_size=1));cursor=original['next_cursor']
            create_sale(dict(lines=[dict(sku_id=121,quantity=1)],request_key='SYN-T16-APPEND',business_reference='SYN-T16',reason='Generated append'))
            result=sales_list(dict(page_size=1,cursor=cursor))
            self.assertEqual(result['total'],original['total'])
            choices=sales_choices(dict(choice_search='DRINK'))
            self.assertEqual(len(choices['items']),1)
            self.assertEqual(choices['items'][0]['sku_id'],121)

    def test_live_grouped_summaries_reconcile_and_are_scoped(self):
        with actor(self.app,912,2):
            for group in ('branch','sku','day'):
                result=history.read(dict(start='2001-01-01',end='2001-01-14',group_by=group,page_size=100),grouping=True)
                self.assertTrue(all(r['branch_id']==2 for r in result['items']))
                self.assertEqual(sum(r['units'] or 0 for r in result['items']),result['totals']['units'])
                self.assertEqual(sum(r['missing'] for r in result['items']),result['totals']['missing'])

    def test_low_stock_sku_count_does_not_duplicate_legacy_and_native_sources(self):
        from models import Product,Inventory,LegacyProductMapping
        with actor(self.app,901):
            db.session.add(Product(product_id=991,name='Synthetic legacy source',reorder_level=1000,version=1))
            lot=StockLot(sku_id=121,lot_code='SYN-T16-LEGACY-UNOPENED',validation_state='review',provenance={'source':'synthetic_fixture'})
            db.session.add(lot);db.session.flush()
            inventory=Inventory(product_id=991,quantity=1);db.session.add(inventory);db.session.flush()
            db.session.add(LegacyProductMapping(legacy_product_id=991,legacy_inventory_id=inventory.inventory_id,
                branch_id=2,sku_id=121,lot_id=lot.lot_id,evidence={'source':'synthetic_fixture','quantity':1}))
            db.session.flush()
            result=reports.report(dict(branch_id=2))
            self.assertEqual(result['total']['low_stock_skus'],3)
            inventory.quantity=0;db.session.get(Product,991).version=2;db.session.flush()
            with self.assertRaises(MutationError):reports.report(dict(branch_id=2,snapshot=result['pagination']['snapshot']))

    def test_scoped_streamed_export_and_formula_escaping(self):
        user=client(self.app,912,2)
        response=user.get('/operations/observations.csv?start=2001-01-01&end=2001-01-14',buffered=False)
        self.assertTrue(response.is_streamed)
        chunks=list(response.response);response.close()
        self.assertLess(max(map(len,chunks)),1024)
        rows=list(csv.reader(io.StringIO(b''.join(chunks).decode())))
        header=next(i for i,r in enumerate(rows) if r[0]=='observation_id')
        self.assertEqual(len(rows[header+1:-1]),84)
        self.assertTrue(all(r[5]=='2' and r[11]=='synthetic_fixture' for r in rows[header+1:-1]))
        self.assertEqual(rows[-1],['export_complete','84','synthetic_fixture'])
        from operational_routes import csv_row
        escaped=next(csv.reader(io.StringIO(csv_row(['=1+1',' @SUM(A1)','-1','\tcmd','safe']))))
        self.assertTrue(all(r.startswith("'") for r in escaped[:4]));self.assertEqual(escaped[-1],'safe')
        self.assertEqual(user.get('/operations/observations.csv?branch_id=1').status_code,403)

    def test_query_and_payload_budgets_and_no_orm_lot_loading(self):
        with self.app.app_context():engine=db.engine
        statements=[]
        def count(conn,cursor,statement,parameters,context,many):statements.append(statement)
        event.listen(engine,'before_cursor_execute',count)
        try:
            with patch.object(reports,'_lots',side_effect=AssertionError('Unbounded lot reader used')):
                response=client(self.app).get('/api/operations?page_size=1')
            self.assertEqual(response.status_code,200)
            self.assertLessEqual(len(statements),35);self.assertLess(len(response.data),16000)
            self.assertEqual(len(response.json['inventory']['items']),1)
            statements.clear()
            response=client(self.app,912,2).get('/api/observations?start=2001-01-01&end=2001-01-14&page_size=1')
            self.assertEqual(response.status_code,200);self.assertLessEqual(len(statements),22)
            self.assertLess(len(response.data),6000)
            self.assertTrue(any('LIMIT' in sql for sql in statements))
        finally:event.remove(engine,'before_cursor_execute',count)

    def test_normal_prediction_requests_do_not_fit_models(self):
        user=client(self.app,912,2)
        with patch('app.inventory_intelligence',side_effect=AssertionError('Request attempted model fitting')):
            response=user.get('/api/predictions')
            self.assertEqual(response.status_code,200);self.assertEqual(response.json,[])
            self.assertIn('pending-T20',response.headers['X-InventraIQ-Feature-Gate'])
            self.assertIn('No model is fitted',user.get('/ai-predictions').get_data(as_text=True))

    def test_index_plans_and_migration_artifacts(self):
        with self.app.app_context():
            for sql,index in [("SELECT sale_id,branch_id,business_date,total_minor FROM priced_sales WHERE branch_id=1 ORDER BY sale_id LIMIT 25",'ix_t16_sale_branch_id_date'),
                ("SELECT observation_id FROM sales_observations WHERE run_id=1 AND branch_id=1 ORDER BY observation_id LIMIT 25",'ix_t16_observation_run_branch_id_date')]:
                plan=str(db.session.connection().exec_driver_sql('EXPLAIN QUERY PLAN '+sql).all())
                self.assertIn(index,plan);self.assertNotIn('TEMP B-TREE',plan)
            db.session.rollback()


class MediumQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();cls.root,cls.app=fixture(cls.temp.name,'medium',indexes=True)
    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():db.session.remove();db.engine.dispose()
        cls.temp.cleanup()

    def test_medium_export_bounded_memory_and_reconciliation(self):
        import tracemalloc
        user=client(self.app)
        tracemalloc.start()
        response=user.get('/operations/observations.csv?start=2001-01-01&end=2001-03-31',buffered=False)
        count=units=missing=zeros=0;complete=False;max_chunk=0
        try:
            for chunk in response.response:
                max_chunk=max(max_chunk,len(chunk))
                row=next(csv.reader([chunk.decode()]))
                if row[0]=='export_complete':complete=row[1]=='32400'
                elif row[0].isdigit():
                    count+=1
                    if row[8]=='':missing+=1
                    else:
                        number=int(row[8]);units+=number;zeros+=number==0
            _,peak=tracemalloc.get_traced_memory()
        finally:response.close();tracemalloc.stop()
        self.assertTrue(complete);self.assertEqual((count,units,missing,zeros),(32400,91486,1906,4355))
        self.assertLess(max_chunk,2048);self.assertLess(peak,12*1024*1024)
        # Evidence is intentionally small and carries no credentials/paths.
        print(json.dumps(dict(t16_export_rows=count,units=units,missing=missing,zeros=zeros,peak_traced_bytes=peak,max_chunk_bytes=max_chunk)))

    def test_medium_page_and_response_budgets(self):
        user=client(self.app)
        first=user.get('/api/operations?page_size=1').json
        last=user.get('/api/operations?page_size=1&page=134&snapshot='+first['pagination']['snapshot'])
        self.assertEqual(last.status_code,200);self.assertEqual(len(last.json['inventory']['items']),1)
        self.assertEqual(last.json['inventory']['total'],134)
        self.assertEqual(last.json['total'],first['total']);self.assertLess(len(last.data),16000)
        with actor(self.app,901):
            data=history.read(dict(start='2001-01-01',end='2001-03-31',page_size=1))
            self.assertEqual(data['totals'],dict(cells=32400,units=91486,missing=1906,observed_zero=4355))
        self.assertEqual(user.get('/api/priced-sales?page_size=101').status_code,400)
        self.assertEqual(user.get('/api/priced-sales?page_size=1&page_size=2').status_code,400)
        self.assertEqual(len(user.get('/api/priced-sales?page_size=1').json['sales']),1)

    def test_export_rechecks_revocation_between_chunks(self):
        user=client(self.app,912,2)
        # Simulate the current database resolver changing during the stream. The
        # real assignment resolver is separately exercised with database revocation.
        import historical_queries
        original=historical_queries.resolve_scope;calls=[]
        def revoke(*args,**kw):
            calls.append(1)
            if len(calls)>1:raise Forbidden('Access denied.')
            return original(*args,**kw)
        with patch.object(historical_queries,'resolve_scope',side_effect=revoke):
            response=user.get('/operations/observations.csv?start=2001-01-01&end=2001-03-31',buffered=False)
            chunks=[]
            try:
                with self.assertRaises(Forbidden):
                    for chunk in response.response:chunks.append(chunk)
            finally:response.close()
        self.assertNotIn(b'export_complete',b''.join(chunks))
        self.assertEqual(sum(chunk[:1].isdigit() for chunk in chunks),100)


class PublicationCorrectionTests(unittest.TestCase):
    def test_atomic_source_replacement_invalidates_cursor_without_pos_changes(self):
        from ingestion.pipeline import generate_fixture,load_manifest,ingest
        from models import PricedSale,InventoryOperation
        with TemporaryDirectory() as temp:
            root,app=fixture(temp,'small')
            try:
                with actor(app,901):
                    before=history.read(dict(start='2001-01-01',end='2001-01-14',page_size=1))
                    stock=[tuple(r) for r in db.session.execute(select(BranchStock.branch_id,BranchStock.lot_id,BranchStock.on_hand,BranchStock.version))]
                    counts=[db.session.scalar(select(func.count()).select_from(t)) for t in (PricedSale,InventoryOperation)]
                path=generate_fixture(root,profile='small',revision='synthetic-correction',seed=14)
                ingest(root,load_manifest(root,path))
                with actor(app,901):
                    with self.assertRaises(MutationError):history.read(dict(start='2001-01-01',end='2001-01-14',page_size=1,cursor=before['next_cursor']))
                    after=history.read(dict(start='2001-01-01',end='2001-01-14',page_size=100))
                    self.assertEqual(after['total'],252)
                    self.assertTrue(all(r['source_revision']=='synthetic-correction' for r in after['items']))
                    self.assertEqual(stock,[tuple(r) for r in db.session.execute(select(BranchStock.branch_id,BranchStock.lot_id,BranchStock.on_hand,BranchStock.version))])
                    self.assertEqual(counts,[db.session.scalar(select(func.count()).select_from(t)) for t in (PricedSale,InventoryOperation)])
            finally:
                with app.app_context():db.session.remove();db.engine.dispose()
