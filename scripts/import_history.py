"""T14 synthetic-only CLI. No default DB, network, archive extraction or M5 input."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ingestion.target import initialize_target
from ingestion.pipeline import generate_fixture,load_manifest,ingest,reconcile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    init=commands.add_parser('init');init.add_argument('--parent',required=True);init.add_argument('--acknowledgment',required=True)
    fixture=commands.add_parser('generate');fixture.add_argument('--target',required=True)
    fixture.add_argument('--profile',choices=['small','medium'],required=True);fixture.add_argument('--revision',default='synthetic-r1')
    imp=commands.add_parser('run');imp.add_argument('--target',required=True);imp.add_argument('--manifest',required=True)
    imp.add_argument('--chunk-size',type=int,default=250);imp.add_argument('--max-chunks',type=int)
    imp.add_argument('--stage-only',action='store_true')
    check=commands.add_parser('reconcile');check.add_argument('--target',required=True);check.add_argument('--run-id',type=int,required=True)
    args=parser.parse_args()
    if args.command=='init':result={'target':str(initialize_target(args.parent,acknowledgment=args.acknowledgment))}
    elif args.command=='generate':result={'manifest':str(generate_fixture(args.target,profile=args.profile,revision=args.revision))}
    elif args.command=='run':result=ingest(args.target,load_manifest(args.target,args.manifest),chunk_size=args.chunk_size,
        max_chunks=args.max_chunks,publish_result=not args.stage_only)
    else:result=reconcile(args.target,args.run_id)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
