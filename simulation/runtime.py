"""Attested new demo target and isolated copy of the existing Flask interfaces."""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3

from ingestion.target import initialize_target,connect,ACK,checked_root
from ingestion.pipeline import generate_fixture,load_manifest,ingest
from simulation.manifest import MANIFEST,validate,canonical


def history_hash(root):
    with connect(root) as conn:
        rows={name:[tuple(r) for r in conn.execute('SELECT * FROM '+name+' ORDER BY 1,2')]
              for name in ['import_runs','sales_observations','import_quarantine','historical_datasets']}
    return hashlib.sha256(canonical(rows).encode()).hexdigest()


def initialize(parent,*,acknowledgment):
    if acknowledgment!=ACK:raise ValueError('Explicit new disposable database acknowledgment required')
    root=initialize_target(parent,acknowledgment=acknowledgment)
    fixture=generate_fixture(root,profile='small')
    ingest(root,load_manifest(root,fixture))
    from sqlalchemy import create_engine
    from simulation.schema import metadata,triggers
    engine=create_engine('sqlite:///'+(root/'disposable.sqlite').as_posix())
    try:
        metadata.create_all(engine)
        with engine.begin() as conn:
            for sql in triggers('sqlite'):conn.exec_driver_sql(sql)
    finally:engine.dispose()
    credentials={str(uid):secrets.token_urlsafe(24) for uid in [901,902,903,911,912,913,921,922,923]}
    # Private local capability only; never printed or included in reports/manifest.
    with (root/'simulation-private.json').open('x',encoding='utf-8') as handle:
        json.dump(dict(purpose='inventraiq-t15-synthetic-only',manifest_hash=validate(MANIFEST),
            session_secret=secrets.token_hex(32),credentials=credentials,history_hash=history_hash(root)),handle)
    return root


def attest(root):
    root,_,_=checked_root(root)
    with connect(root) as conn:
        for name in ['simulation_runs','simulation_steps']:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone():
                raise ValueError('T15 requires its own newly initialized target')
    path=root/'simulation-private.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size>8192:raise ValueError('T15 private capability missing')
    info=json.loads(path.read_text(encoding='utf-8'))
    if info.get('purpose')!='inventraiq-t15-synthetic-only' or info.get('manifest_hash')!=validate(MANIFEST):
        raise ValueError('T15 target purpose/manifest mismatch')
    from simulation.manifest import code_hash
    with connect(root) as conn:
        run=conn.execute('SELECT code_hash FROM simulation_runs WHERE run_id=1').fetchone()
        if run and run[0]!=code_hash():raise ValueError('Application version changed; create a new demo target')
    return root,info


@contextmanager
def safe_import_environment():
    values=dict(APP_ENV='testing',DATABASE_URL='sqlite:///:memory:',PYTHON_DOTENV_DISABLED='1',
                SECRET_KEY=secrets.token_hex(32),OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
    old={k:os.environ.get(k) for k in values};os.environ.update(values)
    try:yield
    finally:
        for key,value in old.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def create_app(root):
    root,info=attest(root)  # Before app import/configuration or writable engine use.
    with safe_import_environment():
        from app import app as original,login_manager
    from flask import Flask,g,current_app
    from configuration import application_config
    from database import db
    from simulation_clock import at_time
    from sqlalchemy import event
    from simulation.manifest import ROOT
    app=Flask('inventraiq_t15_demo',template_folder=str(ROOT/'templates'),static_folder=str(ROOT/'static'))
    app.config.update(application_config(dict(APP_ENV='testing',DATABASE_URL='sqlite:///'+(root/'disposable.sqlite').as_posix(),
        SECRET_KEY=info['session_secret'])))
    app.config.update(TESTING=False,LEGACY_STOCK_REHEARSAL=False,MAX_CONTENT_LENGTH=1024*1024)
    app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax')
    from authentication import init_authentication
    init_authentication(app)  # Preserve idle lifetime and app-specific permanent-login signal.
    db.init_app(app);login_manager.init_app(app)
    for rule in original.url_map.iter_rules():
        if rule.endpoint=='static':continue
        app.add_url_rule(rule.rule,endpoint=rule.endpoint,view_func=original.view_functions[rule.endpoint],
            defaults=rule.defaults,methods=rule.methods,strict_slashes=rule.strict_slashes)
    app.before_request_funcs={key:[f for f in values if f.__name__!='require_configured_https'] for key,values in original.before_request_funcs.items()}
    app.after_request_funcs={key:list(values) for key,values in original.after_request_funcs.items()}
    app.teardown_request_funcs={key:list(values) for key,values in original.teardown_request_funcs.items()}
    app.template_context_processors={key:list(values) for key,values in original.template_context_processors.items()}
    app.error_handler_spec=copy.deepcopy(original.error_handler_spec)
    app.jinja_env.globals.update(original.jinja_env.globals)
    app.jinja_env.filters.update(original.jinja_env.filters)
    app._t15_target=root
    app.before_request_funcs.setdefault(None,[]).insert(0,lambda:start_clock())
    def start_clock():
        clock=at_time(app,MANIFEST['as_of']);clock.__enter__();g._t15_clock=clock
    @app.teardown_request
    def stop_clock(error):
        clock=g.pop('_t15_clock',None)
        if clock:clock.__exit__(None,None,None)
    @app.context_processor
    def provenance():return dict(synthetic_demo=dict(id=MANIFEST['simulation_id'],as_of=MANIFEST['as_of']))
    @app.after_request
    def label_demo(response):
        response.headers['X-InventraIQ-Provenance']='synthetic_demo; SYN-T15-V1'
        if response.is_json:
            value=response.get_json(silent=True)
            if isinstance(value,dict):
                value['demo_provenance']=dict(source='synthetic_demo',simulation_id=MANIFEST['simulation_id'],
                    as_of=MANIFEST['as_of'],financial='Simulated MYR, not real-world revenue')
                response.set_data(app.json.dumps(value));response.mimetype='application/json'
        return response
    with app.app_context():
        def protect_history(connection,_):
            forbidden={'import_runs','sales_observations','import_quarantine','historical_datasets','historical_demo_target'}
            def authorize(action,first,second,database,trigger):
                if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE) and first in forbidden:
                    return sqlite3.SQLITE_DENY
                if action in (sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH):return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            connection.set_authorizer(authorize)
        event.listen(db.engine,'connect',protect_history)
    return app


@contextmanager
def actor(app,uid,branch=None,instant=None):
    """Local authenticated service call; normal DB-resolved authorization still runs."""
    from flask import session
    from flask_login import login_user
    from database import db
    from models import User
    from simulation_clock import at_time
    with app.test_request_context('/'):
        with at_time(app,instant or MANIFEST['as_of']):
            user=db.session.get(User,uid)
            if user is None:raise ValueError('Synthetic actor not provisioned')
            login_user(user)
            if branch is not None:session['active_branch_id']=branch
            try:yield
            finally:db.session.rollback();db.session.remove()
