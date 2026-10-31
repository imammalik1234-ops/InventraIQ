# T17 disposable synthetic benchmarks

Run from the repository root with the project's installed Python dependencies:

```powershell
python scripts/benchmark_t17.py --profiles small medium large-operational-proxy --samples 20 --rounds 2 --workers 25 --seconds 300 --memory-mb 1536 --disk-mb 512 --output data/t17_results.json
python scripts/summarize_t17.py data/t17_results.json
```

Generated `data/t16_*.json` and `data/t17_*.json` captures are Git-ignored local
outputs; verification reports preserve reviewed measurements and limitations.

The output must be a new JSON file in an existing directory. No database URL,
existing fixture path, remote server, credentials or real-source manifest is
accepted. Every profile runs in its own subprocess and creates new attested T15
targets inside a temporary directory. Targets and private worker logs are removed
after execution. Existing demo databases are never benchmark targets.

## Profiles and reproducibility

`small` and `medium` reuse the approved T14 generated fixtures: respectively six
SKUs × three stores × 14 days (252 coverage cells), and 120 × three × 90 (32,400
cells). Source revision is `synthetic-r1`, seed 13. Original 2001 observation dates
remain unchanged. Operational time is fixed to 2031-01-03. T15 services create
operational facts separately from historical observations.

`large-operational-proxy` adds 80 ten-unit receipts and 120 one-unit sales per
branch to the medium operational fixture. It still has 32,400 historical cells.
It is **not** the audit's 17.75-million-observation large tier. The proposed audit
tiers and T14's approved generator cap differ; this harness does not expand that
ingestion contract. See the verification report for unexecuted scale gates.

Independent rounds rebuild targets. The operation plan, manifest hashes and
pre-race counts are deterministic. Thread scheduling and race winners are not;
compare invariants, workload counts and latency distributions rather than final
business hashes. Application, simulation manifest, harness and index hashes are
recorded with results.

## Measurement contract

Requests use in-process Flask test clients with valid, separate revocable
sessions. This measures application/SQLite work, not network or production WSGI
capacity. Each named read has a first request and repeated warm samples. The OS
page cache is not flushed. Observation exports have only two warm samples per
round to bound execution. CSV bytes are consumed incrementally and hashed.

Latencies use `perf_counter`, including complete streamed body consumption.
Quantiles use empirical nearest rank; small samples do not establish reliable
p99 capacity. Sequential throughput is completed samples divided by summed
durations; concurrent throughput uses group wall time. SQL counters are local to
each task; slow executions (>=25ms) retain fingerprints, never SQL or bind values.
Cursor timings cover completed SQL executions; failed lock waits remain in
operation latency rather than the successful-cursor timing list.
OS peak working set and sampled temporary-directory bytes are measured.
Disk sampling covers the controlled target tree (fixtures, databases, WAL, logs,
checkpoints). OS pagefile and any SQLite scratch files outside that tree are not
included; this is not a system-wide disk high-water measurement.

The controller samples every 200ms and terminates its own process tree on wall,
RSS or disk limits. On Windows it monitors both the virtual-environment launcher
and actual worker; their summed individual RSS peaks are conservative, not
necessarily simultaneous. Limits can overshoot between samples; these are not
kernel quotas. BLAS/OpenMP threads are one; no OS CPU quota is claimed. Samples,
rounds, workers, memory, disk and wall duration have hard configuration maxima.

Non-success statuses remain in the evidence. Expected permission denials are
separate. A 409 from a competing writer is a conflict, not a successful mutation;
`errors` includes unanticipated non-success HTTP statuses, including those 409s.
Report conflict counts separately from 5xx/599 failures. Injected rollback has an
intentional classified 599 and must leave reconciliation facts unchanged.

## Correctness and recovery

Barriers overlap final-unit checkout versus transfer reservation, identical-key
receipts, refunds, PO partial receiving and transfer receiving. Services resolve
actors and assignments afresh. Checks require one effect, nonnegative available
stock, bounded refunds/receipts, and balanced ledger, sales, purchasing and
transfer reconcilers. Assignment revocation invalidates the old session; a login
redirect or denial is expected. Wrong-branch and clerk purchasing reads must deny.

An incomplete run records its resource stop or exception class and safe source
frame locations. Atomic checkpoints preserve completed measurements without
publishing private logs. Retry with a new output filename; never reuse a partial
database. A failed budget is evidence, not permission to increase approved limits
or optimize unrelated application code.

MySQL needs separately approved disposable infrastructure. No MySQL capacity,
locking parity, real M5 permission, or production acceptance follows from this
SQLite harness. Existing historical forecast-fitting evidence is recorded
separately; benchmark requests do not train models or bypass the T20 feature gate.
