"""Reproducible T07 demonstration, ALWAYS on a newly created disposable DB.

No database URL/path/configuration argument is accepted. No .env is loaded.
Use the permission-scoped inventory_ledger.reconcile service for an approved
operator integration; this executable cannot inspect or migrate user databases.
"""
import os
import sys
import json
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if sys.argv[1:]:
        raise SystemExit('No arguments accepted: this command uses disposable synthetic data only.')
    os.environ.update(PYTHON_DOTENV_DISABLED='1', APP_ENV='testing', PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1')
    with tempfile.TemporaryDirectory(prefix='inventraiq-t07-reconcile-') as directory:
        target = Path(directory).resolve()/'disposable.db'
        os.environ['DATABASE_URL']='sqlite:///'+target.as_posix()
        os.environ['SECRET_KEY']='disposable-t07-command-only'
        from app import app
        from database import db
        from models import User, UserHQScope, Branch, Category, SKU, BranchProduct, StockLot, BranchStock
        from flask_login import login_user
        from flask import session
        import inventory_ledger as ledger
        with app.app_context():
            assert Path(db.engine.url.database).resolve()==target
            db.create_all()
            user=User(user_id=1,email='reconcile@test.invalid',password_hash='unused',role='admin')
            db.session.add_all([user,Branch(branch_id=1,code='DISPOSABLE',display_name='Disposable',timezone='UTC'),
                                Category(category_id=1,code='DEMO',display_name='Demo')]);db.session.flush()
            db.session.add_all([UserHQScope(user_id=1,hq_role='admin'),SKU(sku_id=1,code='DEMO',name='Demo',category_id=1)])
            db.session.flush();db.session.add(BranchProduct(branch_id=1,sku_id=1));db.session.add(StockLot(lot_id=1,sku_id=1,lot_code='OBSERVED',provenance={'synthetic':True}))
            db.session.flush();db.session.add(BranchStock(branch_id=1,sku_id=1,lot_id=1,on_hand=7,reserved=0,quarantined_quantity=7,version=1));db.session.commit()
            with app.test_request_context():
                login_user(user);session['active_branch_id']=1
                before=ledger.reconcile()
                opening=ledger.opening(dict(request_key='opening-demo-1',business_reference='SYNTHETIC-COUNT',reason='Disposable reviewed count',
                    lot_id=1,expected_revision=1,on_hand=7,reserved=0,quarantined_quantity=7,source_receipt_ids=[],approval_evidence='Synthetic review, no historical facts claimed'))
                db.session.commit()
                after=ledger.reconcile()
                print(json.dumps(dict(disposable=True,before=before,opening=opening,after=after),sort_keys=True))
                assert all(row['balanced'] for row in after)
            db.session.remove();db.engine.dispose()


if __name__=='__main__': main()
