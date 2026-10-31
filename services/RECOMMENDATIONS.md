# T21 synthetic recommendation contracts

Only synthetic review previews are approved (`synthetic-review-2026-10-23`).
Production numeric output and automatic actions are always disabled. The fixture
numbers below are not business defaults. Existing T15 demos keep their original
2031 operational clock; a historical forecast for 2001 cannot support their
current-stock recommendations.

## Read interface and authorization

`services.recommendations.report(values)` and `intelligence.branch_recommendations`
resolve the current server actor and database assignments with `predictions.read`.
`GET /api/recommendations` accepts only `branch_id`, `sku_id`, `page`, `page_size`;
repeated or unknown parameters fail. Page size defaults to 5 SKUs, maximum 10;
page is 1..10000, ordered by stable SKU ID, then branch ID. Pagination is a live
view: repeat requests can observe new facts. Lot/inbound bounds fail explicitly
instead of silently truncating. There is no client actor, policy, approval,
balance, clock or write parameter and no recommendation POST endpoint.

All four established roles can read their scoped stock/forecast explanations.
Only explicit HQ consolidated scope supports company transfer previews and
complete inbound planning. A branch manager's current transfer visibility does
not include another source's undispatched reservation; clerks cannot read PO or
transfer facts at all. Consequently branch-only calculations withhold quantities
with `INBOUND_VISIBILITY_INCOMPLETE`, rather than claiming unseen inbound is zero
or widening any existing permission. Supplier policy and supplier records are
also withheld from clerks (`POLICY_VISIBILITY_RESTRICTED`); no supplier SQL runs
without purchasing-read capability. Forbidden branch selection returns 403.
No candidate contains other branches' identities in a branch-only response.

Each response uses one database read snapshot, captured trusted UTC instant and
branch timezone. SQLite explicitly begins the read transaction; MySQL readers
require repeatable-read/serializable isolation (live MySQL remains unverified).
Queries include authorized branch predicates before retrieval. Bounded limits:
2,000 lots, 500 PO lines, 500 transfer allocations per selected SKU page. A
snapshot lookup is bounded to the three-demo-branch/10-SKU scope; unsupported
or missing forecast rows withhold quantities. No unbounded history reads, model
fits, stock locks, global cache, business writes or decision persistence occur.
The API returns `Cache-Control: no-store`. Future actions must reauthorize and
recheck every version/eligibility through T07/T10–T12, never execute a preview.

## Versioned server configuration

`RECOMMENDATION_POLICIES[(branch_id, sku_id)]` must explicitly contain:

```python
{
    'version': 1, 'approval_reference': 'synthetic-review-2026-10-23',
    'supplier_id': 1, 'lead_days': 2, 'review_days': 5,
    'safety_units': 10, 'excess_buffer_units': 5,
    'minimum_order_units': 12, 'pack_units': 6,
}
```

This example is synthetic test configuration only. Every field is required,
nonnegative integer/non-boolean; version/supplier/minimum/pack must be positive;
lead+review is 1..7. The supplier must currently be active. Route configuration
is separately keyed `(source_branch_id, destination_branch_id)` and contains
version, approval reference, transit_days (0..7). There are no application numeric
defaults and no HTTP policy editor. Missing/invalid configuration withholds output.
Change the reviewed configuration version for every intended policy change;
complete policies, route values and canonical hashes are included in explanations.
Policy metadata lives in the reviewed server configuration, so no schema/migration
is needed. Future durable decision storage is outside this read-only task.

## Projection and formulas

Use the eligible T20 snapshot's seven actual daily predictions, never an invented
daily split. The forecast is additional unreserved demand, an explicit approved
synthetic assumption. Free supply excludes reservations, quarantine, expired,
unverified or missing-required-date lots. Unknown stock is null, not zero. Each
future day consumes hypothetical decimal demand by FEFO; expiry is conservatively
evaluated at that branch's day end. This is planning arithmetic and never writes
an operational allocation. Existing T07 remains the only movement implementation.

Approved PO remaining is ordered minus received minus cancelled. Approved transfer
allocations count before dispatch; after dispatch only remaining transit counts.
Already received units are physical stock, not additional inbound. Source reserved
units are already excluded from donor free supply. Draft/rejected/cancelled PO
quantities are excluded. Unresolved transit discrepancies are ineligible.

Known expected arrivals become conditionally available on the next business day,
not guaranteed physical stock. Outside-horizon arrivals are excluded; overdue or
unknown arrivals, unknown quantities or unverified shelf life withhold calculations.
POs requiring expiry cannot imply future lot dates, so their eligibility remains
unknown. An explicit non-perishable policy permits undated expected PO supply.
Transferred lots retain actual expiry and destination minimum-life checks.

For protection horizon H=lead+review, project supply and demand for H daily steps:

```text
raw need = ceil(unmet demand + max(0, safety - projected ending stock))
purchase = pack * ceil(max(raw need, minimum order) / pack), if raw need > 0
purchase = 0, otherwise
physical excess = floor(max(0, ending stock without future inbound - safety - excess buffer))
transfer = min(receiver remaining raw need, donor remaining physical excess, eligible lot units)
```

Whole-unit suggestions round only at final rules; Decimal arithmetic retains daily
fractions. Out-of-range quantities withhold. Early unmet demand is flagged: a new
order/transfer cannot cure a shortage before arrival. Transfer lots must survive
the receiver's entire protection horizon and its inclusive arrival minimum life.
Deterministic virtual donor, receiver and lot budgets prevent reuse across previews.
The historical legacy 20% buffer remains labeled, never used as approved policy.

The purchase and transfer previews are alternative review options; do not execute
both blindly. There are no action buttons or calls to purchase/transfer mutations.

## Explanation and provenance

Every row has branch/SKU, business date/timezone, live lot balances/revisions,
allowed inbound references/versions, expected availability assumptions, daily
forecast, run/snapshot/model/origin/qualification/freshness/coverage/training identity,
expiry policy revision, complete business policy/hash, formulas, reason codes and
withheld reasons. Conditional inbound, forecast and physical stock are separate.
No confidence percentages, revenue or financial-benefit estimates are generated.

`decision_id` hashes the semantic inputs/calculation; the set identity also covers
transfer candidates. Evaluated wall time is outside the hash. Responses are not a
historical decision archive: save the complete authorized response privately to
reproduce an old explanation, since live balances and configuration can change.
No client-supplied response is an authorization token.

Numeric output additionally requires a new T21 target marker, exact approved source
manifest hash, provisional synthetic qualification, eligible/fresh snapshot and
exact forecast-origin/current-branch-date agreement. Nonapproved environments,
unqualified/production artifacts and mismatched dates always withhold numbers.

## Reproducible verification

Use an installed project-compatible Python interpreter with testing environment,
`PYTHON_DOTENV_DISABLED=1`, `DATABASE_URL=sqlite:///:memory:` and bytecode disabled:

```powershell
python -W error -m unittest discover -s tests -p test_recommendations.py -q
python scripts/verify_t21.py
python -W error -m unittest discover -s tests -q
git diff --check
```

On this managed Windows host, the existing T17 deadline test can fail cleanup
through the venv launcher (`WinError 32`). Run the underlying `sys._base_executable`
with the installed venv `Lib/site-packages` on `PYTHONPATH` for that regression;
the exact tested command is in the verification report. This changes no installed
packages or databases and does not suppress the test. The benchmark launcher
limitation remains a separate follow-up.

The browser command creates and removes its own NEW temporary target and credentials;
it has no configured-database or existing-target argument. `recommendation_demo.prepare`
generates the unchanged T14 medium history, adds new explicit operations on
2001-03-31 and publishes the existing T19 provisional baseline offline. No historical
dates are shifted and no POS sales are inferred from observations. The original
2031 T15 scenario is not rewritten or rebranded as a completed T21 simulation.
The isolated T21 business clock permits only 2001-03-31..2001-04-01 UTC, with exact
private marker and existing target/path attestation. HTTP cannot set this clock.

See verification (historical development record; omitted from this package) for the example totals and evidence.
Real M5 rights/fingerprints, production policy/model qualification, existing-data
cutover and disposable MySQL checks remain blocked/pending. No automatic migration
or schema rollback is involved: disable the read surface/configuration to roll back
T21; inventory and original sale/transfer/PO facts remain unchanged.
