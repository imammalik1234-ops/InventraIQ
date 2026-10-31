# T18 observed-unit data and clocks

T18 prepares data; it does not fit, compare or select a model, publish snapshots,
cache forecasts or calculate replenishment. Real M5 use stays **BLOCKED** by
`data/RIGHTS_REVIEW.md`. Only T14's original generated source on an attested
disposable T15 app is supported. No new schema or migration is required.

## Authorized interface

`forecasting.data.load_series(branch_id=..., sku_id=..., as_of=..., start=None,
training_end=None, source_revision=None, manifest_hash=None)` re-resolves the
server actor and `predictions.read` permission. Jobs need the same authenticated
application context; no caller-supplied actor/scope object is accepted. Each call
selects **one branch and one SKU**, including for HQ. Consolidated permission does
not merge training series. Missing/unmapped resources use a non-disclosing 404.

The HTTP equivalent is:

```text
GET /api/forecast-data?branch_id=1&sku_id=1&as_of=2001-01-14&training_end=2001-01-07
GET /ai-predictions?branch_id=1&sku_id=1&as_of=2001-01-14&training_end=2001-01-07
```

Use normal authenticated sessions. All roles with `predictions.read` can read
their assigned branches; HQ admin/analyst can explicitly choose any authorized
active branch. Assignments are read from the database on every service call.
Unknown/repeated parameters and actor, scope, exposure, source-type or feature
injection are rejected. Source selection is server-controlled. Optional revision
and manifest pins must match the active published revision; mismatches return 409,
never silent fallback to a newer or superseded source. Responses are not cached.

Without a SKU, the existing forecast page shows source and operational clocks.
Its explicit `as_of` is validated; with a SKU it uses the **same** data service and
history identity as JSON/jobs. The optional preview shows bounded coverage, not
forecasts. `/api/predictions` remains `[]` with its existing T20 gate header.

## Dates and source identity

- Dates use strict `YYYY-MM-DD`. `start <= training_end <= as_of`; `training_end`
  defaults to `as_of`, and `start` to the source coverage start.
- `as_of` is the inclusive label/evaluation cutoff; `forecast_origin` is the
  inclusive training cutoff. Training ends there. Evaluation labels cover
  `(training_end, as_of]` and are separate from training values. No row beyond
  `as_of` is loaded. This is a partition contract, not T19 backtesting/model scoring.
- Source date labels remain original calendar dates. They are not converted to
  UTC or shifted into the 2031 operational period. Branch timezone is explicit
  metadata and controls the separate branch-local operational business date.
- Read a frozen active publication in the existing read snapshot. Validate the
  canonical manifest hash, source ID, revision, provenance and source window.
  Business cutoff semantics are **retrospective frozen-revision** semantics;
  T14 does not record historical per-cell reporting/availability instants. This
  does not claim that data imported in 2026 was available to a user in 2001.
- Current T14 synthetic manifests pin the SKU/store selection. No outcome-based
  selection, scaling, encoding or normalization is fitted here. No operational
  Sale/SaleLine, refund, stock, current price/category or promotion features are
  read. Historical SKU/branch codes must match their pinned source mappings.

Each returned contract records version, branch/SKU IDs and codes, timezone,
source/revision/manifest identity, provenance, measure, dates and exposure evidence.
`input_identity` hashes this semantic contract; `history_identity` also binds its
daily coverage/units. `training_identity` binds training values/evidence separately
from held-out values. Runtime IDs, private paths and live timestamps are excluded.
Changing evaluation labels cannot change training values or their identity for
the same pinned contract. A changed source manifest intentionally changes identity.

## Coverage and exposure

Every calendar day has a record mask, observed mask, raw units, coverage state,
recorded-zero mask, eligible observed-zero mask, exposure mask, unknown stock
availability and training/evaluation eligibility masks. Values in training and
evaluation arrays are **null** when ineligible; no zero fill or interpolation.

| State | Meaning / eligibility |
|---|---|
| observed | Explicit recorded units, including zero; eligible in its date partition |
| missing | Explicit missing coverage, null units |
| missing_record | No record inside declared source coverage; unknown |
| outside_source_coverage | Requested date outside source coverage; unknown |
| not_yet_launched | Recorded server evidence says before exposure; excluded |
| outside_exposure | After recorded exposure end; excluded |

Zero observed sales are not proof of unconstrained zero demand. Stockout censoring,
store closures and physical availability remain unknown. No first-sale date is
treated as a launch date; no current assortment is treated as historical exposure.
Pre-launch zero records can retain `recorded_zero=true` but are excluded and have
`observed_zero=false`. Nonzero units outside a known exposure window conflict (409).

T14 has no launch evidence. Default exposure and `stock_available` remain null.
An optional **server configuration**, never HTTP input, can supply reviewed
synthetic exposure evidence:

```python
app.config['FORECAST_EXPOSURES'] = {
    (source_manifest_hash, branch_id, sku_id): {
        'start': '2001-01-03', 'end': None,
        'known_on': '2001-01-01', 'reference': 'Reviewed synthetic launch evidence'
    }
}
```

Dates are source business dates. `known_on` must be at/before the training cutoff
to apply; later evidence is ignored and cannot change the training set. The exact
manifest/branch/SKU key prevents evidence from leaking to another source or branch.
Applied dates/reference/knowledge date are included in the contract identity.
This interface does not authorize invented evidence or add an administration UI.

## Bounds and compatibility

At most 1,096 calendar days and 1,096 daily facts per series. The SQL query selects
only needed columns and uses branch, SKU, source revision, run, source identifiers
and inclusive date predicates plus a 1,097-row sentinel limit. Duplicate dates or
conflicting identities fail closed. No all-SKU batch or global history load.
Manifest metadata is bounded to 64KB; metadata source queries are bounded too.
Windows outside recorded coverage remain unknown. Larger source windows require
explicit bounded calls and future reviewed evaluation orchestration.

`prepare_history` is a pure, bounded adapter for already-authorized facts. It has
no database capability. `withheld_forecast` keeps the legacy nested keys `status`,
`reason`, `predicted_demand`, `horizon_days`, `mae`, `history_days`, `selling_days`,
`method`; predictions and MAE remain null. It never calls the regression function.
`ai_model.forecast_demand` is unchanged, including its historical positive-sales,
zero-fill and holdout behavior. It is not the T18 observation adapter.

Legacy `inventory_intelligence` keeps server-resolved branch scope and product
mappings; its default dates now come from one trusted instant converted per branch.
The original regression and recommendation calculations remain separate. Native
forecast routes keep the T20 gate, so ordinary requests do not train models.

`clock.clocks` labels real UTC separately from the trusted T15 operational instant
and branch-local date. Historical `as_of` parameters never set that clock, stock
expiry time, audit timestamps or transaction dates. DST conversion uses pinned
timezone rules; no naive datetime instant is accepted.

T17's failed concurrent-read budgets, exact scale-tier gaps and MySQL limitations
remain. This focused data layer does not claim capacity improvements, stockout
recovery, point-in-time feature reconstruction or forecasting accuracy.
