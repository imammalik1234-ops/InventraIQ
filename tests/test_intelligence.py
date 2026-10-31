import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
import unittest
from datetime import date, timedelta
from types import SimpleNamespace as NS
from unittest.mock import patch
from ai_model import forecast_demand
from intelligence import recommend
from flask import g
from app import app
from database import db
from models import User, Product, Inventory, Sale
from sqlalchemy.exc import IntegrityError

TODAY = date.today()
def history(n=14, quantity=10):
    return [NS(sale_date=TODAY-timedelta(days=i), quantity=quantity) for i in range(n)]

class ForecastTests(unittest.TestCase):
    def test_empty_and_sparse(self):
        for sales in ([], history(1), history(6)):
            self.assertEqual(forecast_demand(sales,TODAY)['status'], 'insufficient_data')
    def test_constant_and_daily_aggregation(self):
        result = forecast_demand(history()+history(), TODAY)
        self.assertEqual(result['predicted_demand'],140)
        self.assertEqual(result['mae'],0)
    def test_invalid_and_stale(self):
        for sale in [NS(sale_date=None,quantity=2), NS(sale_date=TODAY,quantity=-1), NS(sale_date=TODAY+timedelta(days=1),quantity=1)]:
            self.assertEqual(forecast_demand([sale],TODAY)['status'],'invalid_data')
        self.assertEqual(forecast_demand(history(),TODAY+timedelta(days=9))['status'],'stale_data')
    def test_unreliable(self):
        sales=history()
        for s in sales[:3]: s.quantity=1
        self.assertEqual(forecast_demand(sales,TODAY)['status'],'unreliable')
    def test_recommendations(self):
        p=NS(product_id=1,name='Milk',expiry_date=None)
        r=recommend(p,[NS(quantity=20)],history(),TODAY)
        self.assertEqual((r['projected_shortfall'],r['recommended_reorder_qty']),(50,64))
        self.assertEqual(recommend(p,[NS(quantity=100)],history(),TODAY)['recommended_reorder_qty'],0)
        self.assertIsNone(recommend(p,[],history(),TODAY)['recommended_reorder_qty'])
        self.assertIsNone(recommend(p,[NS(quantity=20)],[],TODAY)['recommended_reorder_qty'])
        p.expiry_date=TODAY-timedelta(days=1)
        self.assertEqual(recommend(p,[NS(quantity=100)],history(),TODAY)['available_stock'],0)

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True,SECRET_KEY='test-only')
        self.ctx=app.app_context(); self.ctx.push()
        self.assertEqual(str(db.engine.url), 'sqlite:///:memory:')
        db.create_all()
        db.session.add_all([User(user_id=1,name='Reviewer',email='test@example.com',password_hash='unused',role='staff'),
                            Product(product_id=1,name='Milk',reorder_level=10)])
        db.session.flush()  # Persist the FK parent before raw-ID child fixtures.
        db.session.add(Inventory(product_id=1,quantity=5))
        from auth_fixtures import grant_fixture_scopes
        grant_fixture_scopes()
        db.session.commit()
        self.client=app.test_client()
        with self.client.session_transaction() as token_session: token_session['_csrf_token']='test-token'
        self.client.environ_base['HTTP_X_CSRF_TOKEN']='test-token'
    def tearDown(self):
        db.session.remove(); db.drop_all(); db.engine.dispose(); self.ctx.pop()
    def login(self,role='staff'):
        from auth_fixtures import grant_fixture_scopes
        g.pop('_login_user', None)
        db.session.get(User,1).role=role; grant_fixture_scopes(); db.session.commit()
        with self.client.session_transaction() as s: s['_user_id']=db.session.get(User,1).get_id(); s['_fresh']=True; s['active_branch_id']=1
    def test_permissions_and_read_only(self):
        for path in ['/ai-predictions','/api/predictions']:
            self.assertEqual(self.client.get(path).status_code,302)
        for role in ['staff','admin']:
            self.login(role)
            self.assertEqual(self.client.get('/ai-predictions').status_code,200)
            self.assertEqual(self.client.get('/api/predictions').status_code,200)
        self.assertEqual(Inventory.query.one().quantity,5)
        self.assertEqual(Sale.query.count(),0)
        self.assertEqual(self.client.post('/ai-predictions').status_code,405)
    def test_sale_validation_and_stock(self):
        self.assertEqual(self.client.post('/sales',json={'product_id':1,'quantity':2}).status_code,302)
        self.login()
        for qty in [0,-1,True,1.5,'2',6]:
            self.assertEqual(self.client.post('/sales',json={'product_id':1,'quantity':qty}).status_code,400)
        self.assertEqual(self.client.post('/sales',json={'product_id':1,'quantity':3}).status_code,200)
        self.assertEqual(self.client.post('/sales',json={'product_id':1,'quantity':3}).status_code,400)
        self.assertEqual(Inventory.query.one().quantity,2)
        self.assertEqual(Sale.query.one().quantity,3)
    def test_product_stock_validation(self):
        self.login('admin')
        payload = dict(name='Milk', quantity=-1, reorder_level=10, unit_price=2, expiry_date='')
        for method, path in [('post','/products'),('put','/products/1')]:
            self.assertEqual(getattr(self.client,method)(path,json=payload).status_code,400)
        self.assertEqual(Inventory.query.one().quantity,5)

    def test_expired_and_duplicate_inventory(self):
        self.login()
        product=db.session.get(Product,1)
        product.expiry_date=TODAY-timedelta(days=1)
        db.session.commit()
        self.assertEqual(self.client.post('/sales',json={'product_id':1,'quantity':1}).status_code,400)
        product.expiry_date=None
        db.session.add(Inventory(product_id=1,quantity=5))
        with self.assertRaises(IntegrityError): db.session.commit()
        db.session.rollback()
        Inventory.query.delete(); db.session.commit()
        self.assertEqual(self.client.post('/sales',json={'product_id':1,'quantity':1}).status_code,409)
        row=self.client.get('/api/predictions').get_json()[0]
        self.assertIsNone(row['recommended_reorder_qty'])

    def test_rollback(self):
        self.login()
        with self.assertLogs('app', level='ERROR'), patch.object(db.session,'commit',side_effect=RuntimeError('injected')):
            self.assertEqual(self.client.post('/sales',json={'product_id':1,'quantity':2}).status_code,500)
        self.assertEqual(Inventory.query.one().quantity,5)
        self.assertEqual(Sale.query.count(),0)

if __name__=='__main__': unittest.main()
