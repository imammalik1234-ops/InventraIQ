# T20 synthetic forecast snapshots and operations runbook

T19's two-week seasonal mean is a **provisional synthetic baseline**, not a
qualified champion. Production/real-data publication is disabled in the schema,
artifact and services. The T19 56/56 evaluation gate, category/sparse evidence,
real-data rights and MySQL validation remain outstanding. T20 does not select or
retrain models, generate recommendations or enable a scheduler.

## Safe local execution

```powershell
$env:APP_ENV='testing'
$env:PYTHON_DOTENV_DISABLED='1'
$env:DATABASE_URL='sqlite:///:memory:'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
python scripts/publish_forecasts.py --profile medium --skus 12
python scripts/measure_t20.py --profile medium --samples 5 --output data/private/t20/new-measurement.json
python scripts/verify_t20.py
```

Each command creates a **new** generated, attested disposable SQLite target. No
command accepts a production/database URL or adopts an existing demo database.
The publishing demonstration prints health/status and deletes its temporary
target on exit; it is not a deployment command. Browser verification creates its
own target. Measurement output must be a new JSON file in ignored
`data/private/t20/`. There is no model upload, pickle or joblib deserializer.

Service integration for a newly created synthetic target:

```python
from tempfile import TemporaryDirectory
from forecasting.target import prepare
from forecasting.jobs import offline, publish, rollback, recover
from forecasting.monitoring import evaluate, health

with TemporaryDirectory() as parent:
    root, app = prepare(parent, "medium")
    with offline(app):  # server-owned synthetic HQ actor 901, rechecked in DB
        run = publish(request_key="SYN-DAILY-2001-03-31-V1",
                      as_of="2001-03-31", skus=12)
        print(health())
    # Dispose app sessions/engine before removing its target on Windows.
```

Services require the server-owned offline context, current HQ-admin
`forecasts.publish`, T14/T15 attestation, exact engine path and a T20 target marker.
HQ analysts, managers and clerks cannot publish, recover or roll back. No HTTP job
endpoint exists. Reads resolve `predictions.read` and current branch assignments
every time; HQ consolidated reads require `consolidated.read`. An actor/scope
supplied by an AI tool, URL or request body grants no authority.

## Publication and integrity

One job selects the first bounded manifest SKU ordinals in every active authorized
source branch; no outcome ranking or operational sales are used. Maximum 120
SKUs × the three source branches = 360 series, seven-day horizon, T18 history cap
1,096 days. The source profile remains unchanged (small 14 days; medium 90).
Eligibility is at least 56 calendar and 28 observed training days. Genuine zero
is observed; missing targets remain unknown. Same-weekday values from two prior
weeks implement the T19 baseline. Missing slots use T19's observed mean28 fallback
and are explicitly labeled. No observed history means unavailable, not zero.

Runs persist model/configuration, qualification, source revision/manifest/run,
original historical origin, real generation timestamp, actor, expected/eligible
counts and artifact/batch hashes. A durable `running` row and immutable start
event precede generation. No snapshots are visible at that stage. A single
transaction inserts **all** snapshot rows, marks the run published, switches the
publication pointer with an expected-version compare-and-swap, and records the
publication event. Failure rolls everything back and records a classified failure;
the previous batch remains active. Competing publishers cannot overwrite a newer
pointer. A successful run remains immutable even after another batch supersedes it.

The same request key/configuration returns the original successful result, without
another publication. Different parameters with that key conflict. A new key is
an intentional new run, not an automatic retry. Journal admission stops at 365
attempts per synthetic target pending reviewed archival; no automatic deletion
or history rewriting. This is a local guard, not distributed retention capacity.

Trusted JSON configuration artifacts live inside the private database, outside
public static assets. They contain no executable serialized weights. Exact
artifact/data/feature/model versions, source implementation hashes and the T19
limited-evaluation identity are pinned. Readers check artifact and run agreement,
per-snapshot canonical hashes, series identity and finite nonnegative quantities.
Changing code/configuration requires a reviewed compatible version; tampering or
source correction withholds results. Hashes detect corruption and incompatibility,
not an attacker who controls the application source and database together.

## Read contracts and clocks

- `/api/forecast-snapshots`: envelope with batch/version, qualification, original
  `as_of`, generated UTC, source/manifest/artifact identity, freshness, scoped rows
  and page coverage. Filters: `branch_id`, `sku_id`, `page`, `page_size`, `run_id`.
  Default 25, maximum 100, page 1–10,000; stable branch/SKU ordering. SQL includes
  branch predicates before retrieval. Pin `run_id` across pages; a new active batch
  gives 409 so navigation can restart instead of silently mixing versions.
- `/api/predictions`: compatible list shape using the same snapshot rows; headers
  expose run/state and the synthetic-only feature gate. Missing/unqualified global
  publication returns an empty list, never fits a model.
- `/ai-predictions` and dashboard: the same reader and visible batch identity.
  Pages do not load observation histories. They display generation/origin,
  qualification, coverage, quantities and synthetic provenance in existing cards.
- `/api/forecast-data`: explicit bounded historical exploration remains available.
  Its nested forecast uses a snapshot only when branch/SKU, origin and manifest
  match; otherwise it stays withheld. History loading is explicit on this endpoint,
  not a hidden dependency of prediction/dashboard reads.
- `/api/forecast-monitoring?run_id=...`: current scoped metric rows, no on-request
  evaluation or fit. Metrics for another branch/run are denied without disclosure.

All reads are `no-store` where JSON is returned; no global result cache exists.
SQLite explicitly starts a read transaction; MySQL reads require repeatable-read
or serializable isolation. API/page requests share version semantics, not a
transaction across HTTP requests: publication between requests may change the
version. Consumers compare/pin run IDs. Snapshots are immutable within each batch.

States: missing (no active batch), unqualified (normal production gate),
provisional (fresh eligible synthetic demonstration), stale (old generation or
historical lag), unavailable (ineligible series or incompatible input/artifact).
Stale/ineligible results expose null numerical forecasts. Valid means usable for
this synthetic demonstration only; `qualification=provisional_synthetic` and
`production_publication=false` always remain explicit.

Freshness checks **real UTC generation age ≤24 hours** and historical origin no
more than one day behind the current source's original end date. These clocks do
not move 2001 observations into live/demo time. User-supplied dates cannot refresh
a saved batch or change expiry/stock/business clocks. The T15 2031 operational
clock is separately labeled. No production forecast is current merely because a
historical synthetic batch was regenerated today.

## Monitoring, scheduling and recovery

`health()` reports the current pointer/source and last 20 HQ-only attempts,
including success/failure classification and eligible counts. Never publish raw
exception messages, credentials or source rows in job logs.

`evaluate(run_id, as_of)` is an explicit offline monitor. It scores only subsequent
observed dates in `(forecast_origin, origin+7]`, up to the requested historical
cutoff and current source end. The observation revision must already have been
published at the real monitoring instant. There is no use of future observations
relative to that evaluation cutoff. Missing labels are excluded; genuine zeros
remain. Metrics record observation run/revision identity and evaluation timestamp.
Revision corrections invalidate old metrics; mismatched revisions are recorded
as `source_changed`, not silently rescored. This is **frozen-revision retrospective
synthetic monitoring**, not evidence of historically available real-time data.

Daily MAE, WAPE and forecast-minus-actual bias use scored observed labels, with
null MAE for no labels and null WAPE/percentage bias for zero actual-unit sum.
Per-branch and training-derived regular/sparse segments remain separate; absent
historical category labels are explicitly unavailable. Metrics and monitor rows
are immutable and repeat evaluation of the same run/source/cutoff is idempotent.

Proposed schedule, **not enabled**: after an authorized source publication and
reconciliation, a daily job per dataset clock publishes a deterministic keyed
batch, then monitors matured seven-day horizons. Job credentials should have
only approved synthetic HQ scope in this version. Freshness health should alert
after missed publication. For a future qualified live system, branch-local
business dates and cutoff readiness must be approved before scheduling.
Review model benchmarks periodically (for example monthly or after significant
new coverage/corrections); retraining/promotion requires T19 evidence and explicit
review. The seasonal baseline itself has no fitted state to retrain.

Failure recovery: stop/confirm the worker is no longer running, inspect immutable
events and `health()`, and retain the last valid pointer. A leftover `running` run
is not resumable partial output: HQ may `recover(run_id, reason)` to mark it failed,
then use a **new** request key. Failed/running keys do not silently restart. Never
edit snapshots or remove history to make a retry succeed.

Batch rollback: HQ calls `rollback(old_run_id, expected_publication_version,
reason)` for a previously published batch with compatible current artifact and
source. It atomically switches only the pointer and appends an event; original
facts remain. Older origins may immediately be stale. Corrected-source or
incompatible-model batches cannot be reactivated. Champion promotion remains
gated: obtain full T19 qualification, rights, MySQL and release sign-off before
adding a reviewed trusted artifact/version and an approved publication policy.
Do not relabel this provisional baseline as qualified. A true champion-version
rollback must restore a reviewed compatible application/artifact together; this
task provides no production promotion switch.

Job generation is bounded by series/history caps and a cooperative 120-second
deadline checked between series. Measurement commands add 240-second/1GiB/128MiB
checks between stages. These are not OS-enforced quotas or database statement
timeouts. Keep supervision external for any future deployed worker; no scheduler
or unapproved background process is installed here.
