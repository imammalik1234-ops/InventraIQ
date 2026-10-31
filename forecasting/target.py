"""Opt-in preparation for a NEW synthetic T20 disposable target only."""
from unittest.mock import patch
from sqlalchemy import create_engine,select
from flask import current_app
from database import db
from simulation import runtime
from ingestion.target import ACK
from forecasting.schema import metadata,triggers,ForecastTarget,ForecastPublication
from ingestion.synthetic import SOURCE

PURPOSE='inventraiq-t20-new-synthetic-only'


def prepare(parent,profile='medium'):
    if profile not in ('small','medium'):raise ValueError('Approved generated profiles only')
    original=runtime.generate_fixture
    with patch.object(runtime,'generate_fixture',side_effect=lambda root,**kw:original(root,profile=profile)):
        root=runtime.initialize(parent,acknowledgment=ACK)
    engine=create_engine('sqlite:///'+(root/'disposable.sqlite').as_posix())
    try:
        metadata.create_all(engine)
        with engine.begin() as conn:
            for sql in triggers('sqlite'):conn.exec_driver_sql(sql)
            conn.execute(ForecastTarget.insert().values(singleton=1,purpose=PURPOSE))
            conn.execute(ForecastPublication.insert().values(source_id=SOURCE,version=0))
    finally:engine.dispose()
    app=runtime.create_app(root);app._t20_target=root
    from simulation.runner import run
    run(app)
    return root,app


def enabled():return getattr(current_app,'_t20_target',None) is not None


def require_target():
    from security import MutationError
    if not enabled() or current_app._t20_target!=getattr(current_app,'_t15_target',None):
        raise MutationError('Forecast writes require a newly prepared T20 synthetic target.',503)
    # Check capability, attestation and exact engine path before any forecast write.
    root,_=runtime.attest(current_app._t20_target)
    if db.engine.dialect.name!='sqlite' or db.engine.url.database!=str(root/'disposable.sqlite').replace('\\','/'):
        # SQLAlchemy preserves forward-slash database URLs in the T15 app.
        from pathlib import Path
        if db.engine.dialect.name!='sqlite' or Path(db.engine.url.database).resolve()!=root/'disposable.sqlite':
            raise MutationError('Unverified forecast database target.',503)
    if db.session.scalar(select(ForecastTarget.c.purpose).where(ForecastTarget.c.singleton==1))!=PURPOSE:
        raise MutationError('Forecast target marker mismatch.',503)
