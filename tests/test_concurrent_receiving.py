"""T06 real competing receipt requests on a temporary file database."""
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from flask import Flask
from flask_login import LoginManager
from sqlalchemy import select, func
from database import db
from models import User, UserHQScope, UserBranch, Branch, StockReceipt, BranchStock, StockLot
from configuration import application_config
from security import init_security
from authz import init_authorization
from authentication import load_authenticated_user
from catalog_routes import bp
from test_catalog_receiving import DEFAULT


class ConcurrentReceivingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='inventraiq-t06-receipts-')
        self.path=Path(self.temp.name).resolve()/'receipts.db'
        self.app=Flask('t06-concurrency')
        self.app.config.update(application_config(dict(APP_ENV='testing',DATABASE_URL='sqlite:///'+self.path.as_posix(),SECRET_KEY='t06-disposable-concurrency-secret')))
        self.app.config.update(TESTING=True,SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'timeout':5}})
        db.init_app(self.app)
        manager=LoginManager(self.app);manager.user_loader(load_authenticated_user)
        init_security(self.app,manager);init_authorization(self.app);self.app.register_blueprint(bp)
        for extra in getattr(self,'extra_blueprints',()):self.app.register_blueprint(extra)
        with self.app.app_context():
            self.assertEqual(Path(db.engine.url.database).resolve(),self.path)
            db.create_all()
            db.session.add(Branch(branch_id=1,code='TEST',display_name='Test',timezone='UTC'))
            db.session.add_all([User(user_id=i,email=f'fixture-{i}@test.invalid',password_hash='unused',role='admin' if i==1 else 'staff') for i in (1,2,3)])
            db.session.flush()
            db.session.add(UserHQScope(user_id=1,hq_role='admin'))
            db.session.add_all([UserBranch(user_id=i,branch_id=1,branch_role='manager') for i in (2,3)])
            db.session.commit();self.admin_token=db.session.get(User,1).get_id()
        admin=self.client(self.admin_token)
        def write(method,path,data):
            result=getattr(admin,method)(path,json=data,headers={'X-CSRF-Token':'token'})
            self.assertEqual(result.status_code,200,result.get_data(as_text=True));return result.get_json()
        category=write('post','/api/categories',dict(code='TEST',display_name='Test',reason='Fixture'))['category_id']
        write('put',f'/api/expiry-policies/category/{category}',dict(policy=DEFAULT,expected_revision=0,reason='Fixture'))
        write('put','/api/expiry-settings',dict(label_timezone='UTC',expected_version=0,reason='Fixture'))
        self.sku=write('post','/api/skus',dict(code='ITEM',name='Item',category_id=category,default_price='2.50',reason='Fixture'))['sku_id']
        write('put',f'/api/assortments/1/{self.sku}',dict(enabled=True,expected_version=0,reason='Fixture'))
        with self.app.app_context():self.tokens=[db.session.get(User,i).get_id() for i in (2,3)]
    def tearDown(self):
        with self.app.app_context():db.session.remove();db.engine.dispose()
        self.temp.cleanup()
    def client(self,token):
        client=self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id']=token;session['_fresh']=True;session['_csrf_token']='token';session['active_branch_id']=1
        return client
    def payload(self,**changes):
        return dict(dict(mode='new_lot',sku_id=self.sku,lot_code='CONCURRENT',supplier_lot_code='ACTUAL-LABEL',quantity=4,
                         expiry_date='2090-01-01',external_reference='DELIVERY-1',reason='Fixture receipt',evidence='Actual label fixture'),**changes)
    def compete(self,payloads):
        barrier=Barrier(len(payloads))
        def run(pair):
            index,payload=pair;client=self.client(self.tokens[index]);barrier.wait(timeout=10)
            return client.post('/api/receipts',json=payload,headers={'X-CSRF-Token':'token'}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,enumerate(payloads)))
    def test_same_reference_concurrent_retry_has_one_receipt_and_one_balance_effect(self):
        statuses=self.compete([self.payload(),self.payload()])
        self.assertTrue(all(code in (200,409) for code in statuses),statuses);self.assertIn(200,statuses)
        client=self.client(self.tokens[0]);response=client.post('/api/receipts',json=self.payload(),headers={'X-CSRF-Token':'token'})
        self.assertEqual(response.status_code,200);self.assertTrue(response.get_json()['replayed'])
        with self.app.app_context():
            self.assertEqual(db.session.scalar(select(func.count()).select_from(StockReceipt)),1)
            self.assertEqual(db.session.scalar(select(BranchStock.on_hand)),4)
            self.assertEqual(db.session.scalar(select(func.count()).select_from(StockLot)),1)
    def test_distinct_receipts_to_existing_lot_never_lose_successful_deltas(self):
        client=self.client(self.tokens[0]);response=client.post('/api/receipts',json=self.payload(),headers={'X-CSRF-Token':'token'})
        self.assertEqual(response.status_code,200);lot_id=response.get_json()['lot_id']
        payloads=[dict(mode='existing_lot',sku_id=self.sku,lot_id=lot_id,quantity=q,expiry_date='2090-01-01',external_reference=f'NEXT-{q}',reason='Additional delivery',evidence='Same actual lot') for q in (3,5)]
        statuses=self.compete(payloads)
        self.assertTrue(all(code in (200,409) for code in statuses),statuses)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(select(BranchStock.on_hand)),4+sum(q for q,status in zip((3,5),statuses) if status==200))
            self.assertEqual(db.session.scalar(select(func.count()).select_from(StockReceipt)),1+statuses.count(200))

    def test_supplier_batch_alias_race_does_not_create_two_lot_identities(self):
        payloads=[self.payload(lot_code='ALIAS-A',external_reference='ALIAS-A'),self.payload(lot_code='ALIAS-B',external_reference='ALIAS-B')]
        statuses=self.compete(payloads)
        self.assertEqual(sorted(statuses),[200,409])
        with self.app.app_context():
            self.assertEqual(db.session.scalar(select(func.count()).select_from(StockLot)),1)
            self.assertEqual(db.session.scalar(select(BranchStock.on_hand)),4)
