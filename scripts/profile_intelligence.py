"""Disposable-only request benchmark. No configured database is opened."""
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import json
from datetime import date, timedelta
from statistics import median
from time import perf_counter
from unittest.mock import patch
from sqlalchemy import event
from sklearn.linear_model import LinearRegression
from app import app
from database import db
from models import Product, Inventory, Sale, User


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--products', type=int, default=100)
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--samples', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.products <= 1000 or not 14 <= args.days <= 365 or not 1 <= args.samples <= 10:
        parser.error('Use 1..1000 products, 14..365 days, 1..10 samples')
    app.config.update(TESTING=True, SECRET_KEY='disposable-profile-only')
    with app.app_context():
        assert str(db.engine.url) == 'sqlite:///:memory:', 'Refusing non-disposable database'
        db.create_all()
        db.session.add(User(user_id=1, email='benchmark@example.invalid', password_hash='unused', role='admin'))
        db.session.add_all([Product(product_id=i, name=f'Benchmark {i}', category='Test', supplier='Test',
                                   unit_price=2, reorder_level=5) for i in range(1, args.products+1)])
        db.session.flush()
        db.session.add_all([Inventory(product_id=i, quantity=50) for i in range(1, args.products+1)])
        db.session.add_all([Sale(product_id=i, quantity=5+i%7, sale_date=date.today()-timedelta(days=d))
                            for i in range(1, args.products+1) for d in range(args.days)])
        db.session.commit()
        auth_token = db.session.get(User, 1).get_id()
        engine = db.engine
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = auth_token; session['_fresh'] = True
    queries = []
    def count_query(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)
    event.listen(engine, 'before_cursor_execute', count_query)
    original_fit = LinearRegression.fit
    fits = [0]
    def count_fit(self, *a, **kw):
        fits[0] += 1
        return original_fit(self, *a, **kw)
    results = []
    try:
        with patch.object(LinearRegression, 'fit', new=count_fit):
            for path in ('/api/predictions', '/admin-dashboard'):
                assert client.get(path).status_code == 200
                samples = []
                for _ in range(args.samples):
                    queries.clear(); fits[0] = 0
                    start = perf_counter()
                    response = client.get(path)
                    elapsed = (perf_counter()-start)*1000
                    assert response.status_code == 200
                    samples.append((elapsed, len(queries), fits[0], len(response.data)))
                results.append(dict(path=path, median_ms=round(median(s[0] for s in samples), 2),
                                    queries=samples[-1][1], regression_fits=samples[-1][2], response_bytes=samples[-1][3]))
        print(json.dumps(dict(products=args.products, days=args.days, samples=args.samples,
                              sales_rows=args.products*args.days, results=results)))
    finally:
        event.remove(engine, 'before_cursor_execute', count_query)
        with app.app_context():
            db.session.remove(); db.engine.dispose()

if __name__ == '__main__':
    main()
