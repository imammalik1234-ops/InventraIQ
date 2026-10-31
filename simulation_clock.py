"""Context-local clock for attested T15 demo apps; never accepts HTTP input."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime,timezone

_clock=ContextVar('inventraiq_disposable_simulation_clock',default=None)


def current_instant():
    value=_clock.get()
    if value is None:return None
    from flask import current_app,has_app_context
    if not has_app_context() or current_app._get_current_object() is not value[0]:return None
    return value[1]


@contextmanager
def at_time(app,instant):
    from pathlib import Path
    from ingestion.target import connect
    from database import db
    root=getattr(app,'_t15_target',None)
    if root is None:raise ValueError('Simulation clock requires an attested demo app')
    with connect(root):pass
    from flask import current_app
    if current_app._get_current_object() is not app or Path(db.engine.url.database).resolve()!=Path(root)/'disposable.sqlite':
        raise ValueError('Simulation app/target mismatch')
    if isinstance(instant,str):instant=datetime.fromisoformat(instant)
    allowed=instant.tzinfo is not None and datetime(2031,1,1,tzinfo=timezone.utc)<=instant<=datetime(2031,1,5,tzinfo=timezone.utc)
    if not allowed and instant.tzinfo is not None and getattr(app,'_t21_target',None) is not None:
        from services.recommendation_demo import clock_allowed
        allowed=clock_allowed(app,instant)
    if not allowed:
        raise ValueError('Simulation instant outside approved fixture window')
    token=_clock.set((app,instant.astimezone(timezone.utc)))
    try:yield
    finally:_clock.reset(token)
