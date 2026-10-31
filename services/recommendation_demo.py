"""A separate, explicit T21 scenario, never an upgrade of an existing demo."""
from pathlib import Path
import json
from datetime import datetime,timezone
from unittest.mock import patch
from sqlalchemy import create_engine
from flask import g
from database import db
from simulation import runtime
from ingestion.target import ACK
from forecasting.schema import metadata,triggers,ForecastTarget,ForecastPublication
from forecasting.target import PURPOSE
from ingestion.synthetic import SOURCE
from services.recommendation_policy import APPROVAL

SCENARIO=dict(version='SYN-T21-V1',purpose='new-t21-synthetic-review-only',approval=APPROVAL,
    manifest_hash='150629c5f23ceb1764e18d920ca1007cf15c3847adf13908f238b8216cba482b',
    as_of='2001-03-31T04:00:00+00:00',clock_start='2001-03-31T00:00:00+00:00',clock_end='2001-04-01T00:00:00+00:00')


def environment(app):
    root=getattr(app,'_t21_target',None)
    if root is None or root!=getattr(app,'_t15_target',None):return False
    marker=Path(root)/'t21-scenario.json'
    try:
        return not marker.is_symlink() and marker.is_file() and marker.stat().st_size<4096 and json.loads(marker.read_text())==SCENARIO
    except (OSError,ValueError):return False


def clock_allowed(app,instant):
    return environment(app) and datetime.fromisoformat(SCENARIO['clock_start'])<=instant<=datetime.fromisoformat(SCENARIO['clock_end'])


def actor(app,uid=901,branch=None):return runtime.actor(app,uid,branch,SCENARIO['as_of'])


def policy(supplier_id,**updates):
    # These are explicitly approved synthetic test inputs, NEVER application defaults.
    return dict(dict(version=1,approval_reference=APPROVAL,supplier_id=supplier_id,lead_days=2,review_days=5,
        safety_units=10,excess_buffer_units=5,minimum_order_units=12,pack_units=6),**updates)


def prepare(parent):
    original=runtime.generate_fixture
    with patch.object(runtime,'generate_fixture',side_effect=lambda root,**kw:original(root,profile='medium')):
        root=runtime.initialize(parent,acknowledgment=ACK)
    engine=create_engine('sqlite:///'+(root/'disposable.sqlite').as_posix())
    try:
        metadata.create_all(engine)
        with engine.begin() as conn:
            for statement in triggers('sqlite'):conn.exec_driver_sql(statement)
            conn.execute(ForecastTarget.insert().values(singleton=1,purpose=PURPOSE))
            conn.execute(ForecastPublication.insert().values(source_id=SOURCE,version=0))
    finally:engine.dispose()
    with (root/'t21-scenario.json').open('x',encoding='utf-8') as stream:json.dump(SCENARIO,stream)
    app=runtime.create_app(root);app._t20_target=root;app._t21_target=root
    # Only provision fresh identities, not the T15 2031 operational scenario.
    from simulation.runner import bootstrap
    from simulation.manifest import MANIFEST
    bootstrap(app,MANIFEST)
    def start():
        from simulation_clock import at_time
        clock=at_time(app,SCENARIO['as_of']);clock.__enter__();g._t15_clock=clock
    app.before_request_funcs[None][0]=start
    @app.context_processor
    def label():return dict(synthetic_demo=dict(id='SYN-T21-V1',as_of=SCENARIO['as_of']))
    def provenance(response):
        response.headers['X-InventraIQ-Provenance']='synthetic_provisional_advisory; SYN-T21-V1'
        if response.is_json:
            data=response.get_json(silent=True)
            if isinstance(data,dict):
                data['demo_provenance']=dict(source='synthetic_provisional_advisory',simulation_id='SYN-T21-V1',
                    as_of=SCENARIO['as_of'],financial='No real-world financial claims')
                response.set_data(app.json.dumps(data))
        return response
    app.after_request_funcs[None].insert(0,provenance)
    from models import UserHQScope,UserBranch
    import catalog_services as catalog
    import purchasing_services as purchasing
    import receiving_services as receiving
    with actor(app):
        db.session.add(UserHQScope(user_id=903,hq_role='analyst',active=True))
        for branch in (1,2,3):
            for uid,role in ((910+branch,'manager'),(920+branch,'clerk')):
                db.session.add(UserBranch(user_id=uid,branch_id=branch,branch_role=role,active=True))
        catalog.save_policy('category',1,dict(require_expiry=False,early_days=90,high_days=60,critical_days=30,
            expected_shelf_life_days=None,min_remaining_days_on_receipt=0,min_remaining_days_on_transfer_receipt=0),0,'Synthetic T21 nonperishable fixture policy')
        supplier=purchasing.save_supplier(dict(code='SYN-T21',display_name='Synthetic fixture supplier',active=True,
            expected_version=0,reason='Explicit generated T21 fixture'))
        for branch in (1,2,3):
            catalog.save_assortment(branch,1,dict(enabled=True,expected_version=0,reason='Explicit synthetic source SKU offer'))
        db.session.commit()
    for branch,quantity in ((1,80),(2,20),(3,45)):
        with actor(app,901,branch):
            receiving.receive(dict(mode='new_lot',sku_id=1,lot_code='SYN-T21-'+str(branch),supplier_lot_code='SYN-T21-'+str(branch),
                quantity=quantity,expiry_date=None,request_key='SYN-T21-OPEN-'+str(branch),external_reference='SYN-T21-OPEN-'+str(branch),
                reason='Generated T21 aligned-date fixture',evidence='Synthetic nonperishable units; no manufacturer evidence inferred'))
            db.session.commit()
    app.config['RECOMMENDATION_POLICIES']={(branch,1):policy(supplier['supplier_id']) for branch in (1,2,3)}
    app.config['RECOMMENDATION_ROUTES']={(a,b):dict(version=1,transit_days=1,approval_reference=APPROVAL) for a in (1,2,3) for b in (1,2,3) if a!=b}
    from forecasting.jobs import offline,publish
    with offline(app):publish(request_key='SYN-T21-BASELINE',as_of='2001-03-31',skus=1)
    return root,app
