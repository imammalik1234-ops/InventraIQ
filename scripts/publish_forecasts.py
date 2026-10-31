"""Create a NEW disposable synthetic target and publish one provisional batch."""
import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=('small','medium'),default='medium')
    parser.add_argument('--skus',type=int,default=12);parser.add_argument('--as-of',default=None)
    args=parser.parse_args()
    if not 1<=args.skus<=120:parser.error('Use 1..120 manifest SKUs')
    os.environ.update(APP_ENV='testing',DATABASE_URL='sqlite:///:memory:',PYTHON_DOTENV_DISABLED='1',
        PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
    from forecasting.target import prepare
    from forecasting.jobs import offline,publish
    from forecasting.data import source_contract
    from forecasting.reader import page
    from forecasting.monitoring import health
    from database import db
    with TemporaryDirectory(prefix='inventraiq-t20-job-') as temp:
        root,app=prepare(temp,args.profile)
        try:
            with offline(app):
                _,manifest=source_contract();origin=args.as_of or manifest['coverage']['last_date']
                result=publish(request_key='SYN-T20-CLI-V1',as_of=origin,skus=args.skus)
                snapshot=page()
                print(json.dumps(dict(run=result,state=snapshot['state'],coverage=snapshot['coverage'],
                    publication_health=health(),production_publication=False,provenance='synthetic_fixture')))
        finally:
            with app.app_context():db.session.remove();db.engine.dispose()


if __name__=='__main__':main()
