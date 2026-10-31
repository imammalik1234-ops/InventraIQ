"""Final candidate regression; memory/disposable DBs and loopback networking only."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def hashes():
    extensions={'.py','.html','.css','.js','.cjs','.svg','.sql'}
    excluded={'.git','.venv','node_modules','data','.codex','.agents','.aws','__pycache__'}
    result={}
    for directory,children,files in os.walk(ROOT):
        children[:]=[n for n in children if n not in excluded]
        for name in files:
            path=Path(directory)/name
            if path.suffix in extensions:
                result[path.relative_to(ROOT).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-name',choices=('regression','regression-final'),default='regression',
                        help='Keep the final candidate separate from an earlier running integration check')
    args=parser.parse_args()
    os.environ.pop('GEMINI_API_KEY',None)
    os.environ.update(DATABASE_URL='sqlite:///:memory:',ASSISTANT_PROVIDER='fake',GEMINI_ENABLED='false',
                      ASSISTANT_PROPOSALS_ENABLED='false',PYTHON_DOTENV_DISABLED='1')
    def audit(event,values):
        if event=='socket.connect':
            address=values[1]
            if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1','localhost'):
                raise RuntimeError('External networking forbidden during regression')
    sys.addaudithook(audit)
    output=ROOT/'data/private/transformation';output.mkdir(parents=True,exist_ok=True)
    before=hashes();started=time.monotonic()
    suite=unittest.TestLoader().discover(str(ROOT/'tests'))
    with (output/(args.evidence_name+'.log')).open('w',encoding='utf-8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    unchanged=before==hashes()
    summary=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                 skipped=len(result.skipped),elapsed_seconds=round(time.monotonic()-started,3),
                 external_networking='blocked',candidate_source_unchanged=unchanged,
                 passed=result.wasSuccessful() and unchanged,source_sha256=before)
    (output/(args.evidence_name+'.json')).write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='source_sha256'}),flush=True)
    raise SystemExit(not summary['passed'])


if __name__=='__main__':main()
