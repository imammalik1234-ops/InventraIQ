"""Render measured T17 JSON as Markdown; never connects to a database."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.t17 import summary


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join('---' for _ in headers)+'|']+
        ['| '+' | '.join(str(c) for c in row)+' |' for row in rows])


def physical_ram(environment):
    total=(environment.get('physical_memory') or {}).get('total_bytes')
    return 'unknown physical RAM' if total is None else f'{total/1073741824:.2f} GiB physical RAM'


def render(value):
    lines=['## Recorded measurements','',
        'All durations below are measured milliseconds unless labeled otherwise. '
        'Warm samples are pooled across independent rounds; raw first requests, '
        'individual rounds and SQL fingerprints remain in the JSON.','']
    for p in value['profiles']:
        lines.extend([f"### {p['profile']}: {p['status']}",''])
        if p['status']!='COMPLETED':
            lines.extend([f"Stop: `{p['resource_stop']}`, exit {p['exit_code']}; "
                f"{p['wall_seconds']} seconds. Available partial facts are in JSON.",'']);continue
        result=p['result'];rounds=result['rounds'];environment=result['environment']
        lines.extend([f"Profile wall: {p['wall_seconds']}s; conservative process-tree peak RSS: "
            f"{p['peak_rss_bytes']/1048576:.2f} MiB; sampled temporary-disk peak: "
            f"{p['sampled_peak_temporary_bytes']/1048576:.2f} MiB. "
            f"Pre-race lots/sales: {rounds[0]['facts']}. No resource stop.",'',
            f"Environment: {environment['os']}; {environment['cpu']}; "
            f"{environment['logical_cpus']} logical CPUs; "
            f"{physical_ram(environment)}; "
            f"Python {environment['python']}; SQLite {environment['sqlite']}; "
            f"libraries `{environment['dependencies']}`. "
            f"SQLite settings `{rounds[0]['sqlite']}`.",''])
        rows=[]
        for name in rounds[0]['reads']:
            samples=[s for r in rounds for s in r['reads'][name]['warm']['samples']];v=summary(samples)
            cold=[r['reads'][name]['first']['ms'] for r in rounds]
            rows.append([name,v['n'],f"{v['p50_ms']:.2f}",f"{v['p95_ms']:.2f}",f"{v['p99_ms']:.2f}",
                f"{v['throughput_per_second']:.2f}",v['max_queries'],v['max_body_bytes'],
                f"{min(cold):.2f}–{max(cold):.2f}",f"{v['errors']}/{v['timeouts']}"])
        lines.extend([table(['Warm read','n','p50','p95','p99','req/s','max SQL','max bytes','first range','errors/timeouts'],rows),''])
        mixed=[]
        for workers in rounds[0]['mixed']:
            samples=[s for r in rounds for s in r['mixed'][workers]['samples']];v=summary(samples)
            elapsed=sum(r['mixed'][workers]['n']/r['mixed'][workers]['throughput_per_second'] for r in rounds)
            mixed.append([workers,v['n'],f"{v['p50_ms']:.2f}",f"{v['p95_ms']:.2f}",f"{v['p99_ms']:.2f}",
                f"{len(samples)/elapsed:.2f}",v['statuses'],f"{v['errors']}/{v['timeouts']}"])
        lines.extend([table(['Mixed reader threads','n','p50','p95','p99','req/s','statuses','errors/timeouts'],mixed),''])
        tx=[]
        for name in sorted(set().union(*(r['transactions'] for r in rounds))):
            v=summary([s for r in rounds for s in r['transactions'].get(name,{}).get('samples',[])])
            tx.append([name,v['n'],f"{v['p50_ms']:.2f}",f"{v['p95_ms']:.2f}",f"{v['p99_ms']:.2f}",
                f"{v['throughput_per_second']:.2f}",v['max_queries'],v['statuses']])
        lines.extend([table(['Service operation','n','p50','p95','p99','ops/s sequential','max SQL','statuses'],tx),'',
            'Service samples include their enclosing synthetic step/commit. Small n limits tail interpretation.',''])
        races=[]
        for name,v in rounds[0]['concurrency'].items():
            if 'samples' not in v:continue
            samples=[s for r in rounds for s in r['concurrency'][name]['samples']];v=summary(samples)
            elapsed=sum(r['concurrency'][name]['n']/r['concurrency'][name]['throughput_per_second'] for r in rounds)
            races.append([name,v['n'],f"{v['p50_ms']:.2f}",f"{v['p95_ms']:.2f}",f"{v['p99_ms']:.2f}",
                f"{len(samples)/elapsed:.2f}",v['statuses'],f"{100*v['statuses'].get('200',0)/v['n']:.1f}%",
                f"{100*v['conflicts']/v['n']:.1f}%",v['server_errors'],v['timeouts']])
        lines.extend([table(['Competing service group','n','p50','p95','p99','ops/s wall','statuses','200 incl replay','409 conflicts','5xx/599','timeouts'],races),'',
            'Successful HTTP-style service results include idempotent replays; they do not imply another committed effect.',''])
        imports=[x for r in rounds for x in r['imports']]
        lines.extend([f"Import duration range: {min(i['ms'] for i in imports):.2f}–{max(i['ms'] for i in imports):.2f}ms; "
            f"each accepted {imports[0]['counters']['accepted_rows']} records, rejected "
            f"{imports[0]['counters']['rejected_rows']}, preserved {imports[0]['counters']['units']} units. "
            f"Manifest SHA-256 `{imports[0]['manifest_hash']}`.",'',
            f"Logical database bytes before races: {[r['database_bytes'] for r in rounds]}; "
            f"post-workload WAL bytes: {[r['wal_bytes'] for r in rounds]}; "
            f"post-workload temporary bytes: {[r['temporary_bytes'] for r in rounds]}.",''])
        for r in rounds:
            samples=[s for entry in r['reads'].values() for s in [entry['first'],*entry['warm']['samples']]]
            samples += [s for entry in r['mixed'].values() for s in entry['samples']]
            slow=[q for s in samples for q in s.get('slow_queries',[])]
            # Independent fixed-manifest bookkeeping, in addition to service
            # reconcilers. Scheduling can choose a sale or reservation winner.
            final=r['concurrency']['final_reconciliation']
            levels=sum(k.startswith('final_unit_approval_sale_') for k in r['concurrency'])
            sale_winners=final['groups']['sales']['rows']-r['facts']['sales']-levels
            extra_receipts=0 if p['profile']=='small' else 40+result['profile_contract']['extra_receipts_per_branch']
            extra_sales=0 if p['profile']=='small' else 60+result['profile_contract']['extra_sales_per_branch']
            expected_owned=335+2*levels+3*(10*extra_receipts-extra_sales)-sale_winners
            expected_gross=32440+1380*extra_sales+945*levels+125*sale_winners
            expected_refunds=900+125*levels
            if not (0<=sale_winners<=levels and final['stock']+final['transit']==expected_owned and final['loss']==1
                    and final['gross_minor']==expected_gross and final['refund_minor']==expected_refunds):
                raise AssertionError('Independent synthetic quantity/money conservation failed')
            lines.extend([f"Round {r['round']}: read status counts {dict(Counter(s['status'] for s in samples))}; "
                f"slow executions >=25ms retained: {len(slow)}; "
                f"maximum SQL execution {max(s.get('slow_sql_ms',0) for s in samples):.2f}ms. "
                f"Final reconciliation `{r['concurrency']['final_reconciliation']}`. "
                f"Rollback unchanged: {r['concurrency']['injected_rollback']['unchanged']}; "
                f"revoked old session status {r['revoked_assignment']['status']}.",''])
            lines.extend([f"Independent manifest equation: sale winners {sale_winners}/{levels}; "
                f"physical + transit = {final['stock']} + {final['transit']} = {expected_owned}; "
                f"authorized loss = 1; MYR gross/refund/net minor units = "
                f"{expected_gross}/{expected_refunds}/{expected_gross-expected_refunds}. PASS.",''])
    lines.extend(['All raw samples, limits, source/configuration identities and incomplete '
        'outcomes are retained in the input results JSON. These tables do not extrapolate '
        'unexecuted audit tiers or MySQL behavior.',''])
    return '\n'.join(lines)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('results');args=parser.parse_args()
    print(render(json.loads(Path(args.results).read_text(encoding='utf-8'))))
