"""Dry-run by default. Explicit authorization required for ONE synthetic request.

No existing-target option, credentials printed, dependency installs or retries.
Use only after the owner approves the documented payload and checks AI Studio.
"""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def hq_evidence_report(conversation):
    """Inspect local evidence, emitting only fixed classifications and counts."""
    from assistant.tools import CATALOG
    expected=dict(branch_ids=[1,2,3],role='hq_analyst',consolidated=True)
    binding_ok=conversation.get('scope')==expected
    turn=(conversation.get('messages') or [{}])[-1]
    evidence=turn.get('evidence',[])
    rows=[];diagnostics=[]
    for item in evidence:
        result=item.get('result',{})
        name=result.get('tool')
        name=name if type(name) is str and name in CATALOG else 'unknown'
        scope=result.get('scope') or {}
        branches=scope.get('branch_ids',[])
        scope_ok=(binding_ok and scope.get('role')=='hq_analyst' and bool(branches) and
            set(branches)<=set(expected['branch_ids']) and
            (scope.get('consolidated') is True or len(branches)==1))
        data_rows=(result.get('data') or {}).get('rows',[])
        reason=('tool_error' if result.get('status')=='error' else
            'tool_unavailable' if result.get('status')!='ok' else
            'scope_mismatch' if not scope_ok or any(r.get('branch_id') not in branches
                for r in data_rows if name in ('inventory_risks','inventory_summary')) else
            'missing_source_metadata' if not (item.get('citation') and result.get('as_of') and result.get('provenance')) else
            'truncated' if result.get('completeness',{}).get('truncated',True) else
            'irrelevant_tool' if name not in ('inventory_risks','inventory_summary') else 'accepted')
        diagnostics.append(dict(tool=name,evidence_status=reason,row_count=len(data_rows),
            scope_verified=scope_ok))
        if reason=='accepted':rows.extend(data_rows)
    east=any(r['branch_id']==2 and r['status']=='low_stock' for r in rows)
    central=any(r['branch_id']==1 and r['status']=='critical' and r['days_remaining']==1 for r in rows)
    return dict(hq_authorized_evidence_verified=bool(east and central),
        east_low_stock_evidence=east,central_near_expiry_evidence=central,
        generated_ranking_verified=False,evidence_count=len(evidence),
        accepted_evidence_count=sum(d['evidence_status']=='accepted' for d in diagnostics),
        evidence_diagnostics=diagnostics)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorize-synthetic-egress',action='store_true')
    parser.add_argument('--verified-project-free-tier',action='store_true')
    parser.add_argument('--hq-inventory-urgency',action='store_true',
        help='Prepare the fixed HQ analyst inventory-urgency evidence workflow.')
    args=parser.parse_args()
    from assistant.gemini import MODEL, Settings, payload_for, COUNTER, INVENTORY_URGENCY_QUESTION
    from assistant.conversations import encoded
    settings=Settings(calls=1,rpm=1,output_tokens=256,session_units=16640)
    question=INVENTORY_URGENCY_QUESTION if args.hq_inventory_urgency else 'inventory'
    payload=payload_for(question,settings)
    body=encoded(payload)
    print(json.dumps(dict(model=MODEL,classification='fixed SYN-T15-V1 demonstration prompt and static schemas only',
        requests=1,input_bytes=len(body),max_output_tokens=256,max_session_units=16640,
        payload_sha256=hashlib.sha256(body).hexdigest(),payload=payload),indent=2))
    if not args.authorize_synthetic_egress:
        print('DRY RUN: no network, fixture or credential access.');return
    if not args.verified_project_free_tier:
        raise SystemExit('Verify the actual project Free tier, model access and quota in AI Studio first.')
    if not os.environ.get('GEMINI_API_KEY'):
        raise SystemExit('Set GEMINI_API_KEY in the backend process environment first.')
    if COUNTER.snapshot()['attempts']:
        raise SystemExit('Use a fresh single-process smoke session.')
    from scripts.measure_t16 import fixture,client
    from simulation.verify import verify
    from database import db
    with TemporaryDirectory(prefix='inventraiq-gemini-smoke-') as parent:
        with patch.dict(os.environ,{'ASSISTANT_PROVIDER':'fake','GEMINI_ENABLED':'false'}):
            root,app=fixture(parent,'small')
        diagnostic_loggers=[logging.getLogger(name) for name in ('assistant.gemini','assistant.orchestrator')]
        handler=logging.StreamHandler()
        previous_levels=[logger.level for logger in diagnostic_loggers]
        for logger in diagnostic_loggers:
            logger.addHandler(handler);logger.setLevel(logging.INFO)
        try:
            before=verify(app)
            caller=client(app,903) if args.hq_inventory_urgency else client(app,912,2)
            with caller.session_transaction() as state:state['_csrf_token']='local-gemini-smoke'
            config=dict(ASSISTANT_PROVIDER='gemini',GEMINI_ENABLED='true',GEMINI_FREE_TIER_VERIFIED='true',
                GEMINI_MODEL=MODEL,GEMINI_MAX_CALLS='1',GEMINI_MAX_RPM='1',GEMINI_MAX_INPUT_BYTES='16384',
                GEMINI_MAX_OUTPUT_TOKENS='256',GEMINI_MAX_SESSION_UNITS='16640')
            with patch.dict(os.environ,config):
                headers={'X-CSRF-Token':'local-gemini-smoke'}
                created=caller.post('/api/assistant/conversations',json={},headers=headers)
                if created.status_code!=201:raise SystemExit('Smoke setup failed; no retry.')
                key=created.get_json()['id']
                response=caller.post('/api/assistant/conversations/'+key+'/messages',
                    json=dict(text=question),headers=headers)
                result=response.get_json()
                if verify(app)!=before:raise SystemExit('Disposable business facts changed.')
                evidence_passed=True
                if args.hq_inventory_urgency:
                    report=hq_evidence_report(result)
                    evidence_passed=report['hq_authorized_evidence_verified']
                    print(json.dumps(report))
                print(json.dumps(dict(http_status=response.status_code,
                    passed=response.status_code==200 and evidence_passed,
                    error_code=result.get('error',{}).get('code'),
                    local_counter=COUNTER.snapshot(),business_facts_unchanged=True)))
                caller.post('/api/assistant/conversations/'+key+'/forget',json={},headers=headers)
                if response.status_code!=200:raise SystemExit('Smoke failed safely; do not retry automatically.')
                if not evidence_passed:raise SystemExit('Expected HQ evidence missing; no retry.')
        finally:
            for logger,level in zip(diagnostic_loggers,previous_levels):
                logger.removeHandler(handler);logger.setLevel(level)
            with app.app_context():db.session.remove();db.engine.dispose()


if __name__=='__main__':main()
