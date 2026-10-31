"""Canonical Windows focused/full regression. No external requests or installs."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full',action='store_true')
    args=parser.parse_args()
    # Before importing application/test modules. Never adopt a configured DB/key.
    os.environ.pop('GEMINI_API_KEY',None)
    os.environ.update(DATABASE_URL='sqlite:///:memory:',ASSISTANT_PROVIDER='fake',
        GEMINI_ENABLED='false',ASSISTANT_PROPOSALS_ENABLED='false')
    def audit(event,values):
        if event=='socket.connect':
            address=values[1]
            if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1','localhost'):
                raise RuntimeError('External networking forbidden during T27 regression')
    sys.addaudithook(audit)
    root=Path(__file__).resolve().parents[1]
    output=root/'data/private/t27';output.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(root/'tests'))
    loader=unittest.TestLoader()
    suite=loader.discover(str(root/'tests')) if args.full else loader.loadTestsFromNames([
        'test_assistant_proposals','test_t27_migration','test_assistant_conversations',
        'test_gemini_provider','test_assistant_tools','test_branch_authorization',
        'test_inventory_ledger','test_transfers'])
    name='regression' if args.full else 'focused'
    started=time.monotonic()
    with (output/(name+'.log')).open('w',encoding='utf-8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    summary=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
        skipped=len(result.skipped),elapsed_seconds=round(time.monotonic()-started,3),
        external_networking='blocked',passed=result.wasSuccessful())
    (output/(name+'.json')).write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary),flush=True)
    raise SystemExit(not result.wasSuccessful())


if __name__=='__main__':main()
