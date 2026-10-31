"""T15 synthetic-only demo. New attested SQLite targets; never a configured DB."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--parent',required=True);init.add_argument('--acknowledgment',required=True)
    for name in ('run','verify','serve'):
        command=sub.add_parser(name);command.add_argument('--target',required=True)
        if name=='run':
            command.add_argument('--max-new-steps',type=int)
            command.add_argument('--manifest',default=str(Path(__file__).resolve().parents[1]/'data/t15_simulation.v1.json'))
        if name=='serve':command.add_argument('--port',type=int,default=5055)
    args=parser.parse_args()
    from simulation.runtime import initialize,create_app
    if args.command=='init':
        print(json.dumps(dict(target=str(initialize(args.parent,acknowledgment=args.acknowledgment)),credentials='Private simulation-private.json inside target; do not publish')))
        return
    app=create_app(args.target)
    if args.command=='run':
        from simulation.runner import run
        path=Path(args.manifest)
        if not path.is_file() or path.stat().st_size>16384:raise ValueError('Small versioned T15 manifest required')
        result=run(app,json.loads(path.read_text(encoding='utf-8')),max_new_steps=args.max_new_steps)
    elif args.command=='verify':
        from simulation.verify import verify
        result=verify(app)
    else:
        if not 1024<=args.port<=65535:parser.error('Use a non-privileged local port')
        from simulation.verify import verify
        if not verify(app)['ok']:raise ValueError('Only a complete reconciled demo may be served')
        app.run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False);return
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
