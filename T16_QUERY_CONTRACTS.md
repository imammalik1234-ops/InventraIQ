# T16 scoped query contracts

All application readers resolve the current actor, account/session stamp, active
assignments and branch capability from the database. Query-builder helpers accept
only the resulting server-owned scope; they are not public tool/service entry
points. A client/assistant branch ID is a requested selection, never authority.
No result cache or materialized aggregate table exists.

## Paging and consistency

* `page`: integer 1–10,000; `page_size`: integer 1–100, default 25.
* `sort`: `asc` or `desc`; inventory order is source (native before legacy),
  branch/SKU/lot/status, with every tie resolved. Sales/observations order by
  immutable ID. Observation groups order by their minimum immutable fact ID.
* `start`/`end`: inclusive original branch business-date labels, maximum 366 days.
  Operational reports default to the latest included branch's business date and
  the preceding 29 days. Historical queries do not move old dates to today.
* Inventory `search` is an escaped literal substring of code/name/lot; `%` and
  `_` are literal. Case conversion uses SQL `lower` (SQLite ASCII case conversion;
  non-ASCII case behavior requires engine-specific verification). Status filters:
  all, healthy, warning (early/high/critical), expired, review, not_tracked,
  low_stock. Summary totals intentionally ignore list search/status.
* Unknown/repeated parameters fail. Server limits apply to direct services too.
  Native sales form detail and SKU-choice parameters are explicit adapter fields.

Inventory/alert pages use SQL OFFSET/LIMIT, never load all rows then slice.
`pagination.snapshot` is a signed, actor/scope/filter-bound optimistic revision.
Reuse it on continuation/previous pages, as the Jinja links do. It pins the
classification as-of instant and detects balance/lot versions, operation changes,
legacy source changes, branch timezone and catalog/policy audit changes. A changed
revision, authorization context, query or expired token returns 409; restart from
page one. Global catalog audit changes conservatively invalidate navigation; only
an HMAC of the revision is exposed. No cross-request DB transaction is retained.
Without this token, legacy numeric page callers explicitly receive a fresh live
view each time; no stability under intervening writes is promised.

Sales/observation `next_cursor` tokens additionally bind a high-watermark ID,
last ID, filters, page size/order, actor/effective scope and source revision.
Use the returned token with the original filters. Subsequent pages use keyset
predicates, not OFFSET; appended higher IDs are excluded. Immutable facts cannot
be deleted or edited through supported services. Observation source replacement
invalidates continuation with 409. Tokens expire after 15 real minutes. First
numeric deep-page requests remain compatibility OFFSET adapters. Report sales
links combine the sales cursor with the report revision. These tokens authorize
nothing; permission is checked again before any query on every request.

## Endpoints and services

| Interface | Capability | Result/contract |
|---|---|---|
| Existing dashboard/inventory/alerts/reports/analytics/sales Jinja adapters | Existing corresponding T05 read grant | Same shell and facts; query only the detail sections displayed |
| `/api/operations`, `operational_services.report` | `reports.read` (or explicit existing adapter grant) | Bounded lot/alert/sale lists, branch aggregates, signed revision |
| Existing inventory/alert JSON adapters | `inventory.read` / `alerts.read` | Only the requested bounded section |
| `/api/priced-sales`, `sales_services.sales_list` | `sales.read` | `sales`, `historical_unknown`, total/filters/next_cursor; maximum 100 each |
| `/branch-sales` | `sales.read`; existing write capabilities unchanged | Bounded history; SQL SKU choices, 100/page, `choice_search`/`choice_page` navigation |
| `/api/observations`, `historical_queries.read` | `reports.read` | Synthetic published daily facts, missing/zero distinctions, source versions and observed-unit totals |
| `/api/observations/summary` | `reports.read` | `group_by=branch/sku/day` (default day), always branch-scoped; bounded groups and reconciled live totals |
| `/operations/export.csv`, existing export adapter | `export.read` | Streamed authorized branch summary, MYR/units/date/as-of/source labels |
| `/operations/observations.csv` | `export.read` | Streamed historical facts; original dates, coverage, synthetic provenance, optional source USD metadata |
| `/api/predictions`, `/ai-predictions` | `predictions.read` | Normal requests are gated pending T18–T20; API preserves array shape `[]` plus explicit feature-gate header; UI explains unavailability |

Historical readers/exports require the prepared disposable synthetic T15 app. They
fail closed (503) on ordinary/unprepared targets; no schema discovery, migration
or real-data enablement is attempted. T14's offline target-capability reader is
not an HTTP authorization service. Real M5 stays blocked.

Native sale history preserves its earlier all-dates default when no date filter
is supplied, but its rows are now paged. Explicit date windows follow the validated
366-day contract. Date application is bound into its cursor. Unknown historical
quantities are SQL-paged separately and retain `total=None,currency=None`; no
current catalog price is used to value them. Native sale details/refunds retain
the T08 service contract. SKU choice search is separate from history filters.

## Aggregate and expiry rules

Live SQL sums only authorized branches and requested financial windows. Sale and
refund headers are summed separately from line units to avoid multiplied revenue.
Refunds use their own business dates. MYR amounts stay integer sen until exact
Decimal formatting; unknown legacy quantities never enter gross/refund/net MYR.
Current physical stock, reservations/quarantine and source-attributed transit stay
separate. HQ totals sum included branch facts exactly once.

T06 policy precedence/validation is expressed in SQL. UTC cutoff constants use
the pinned tzdata package and canonical inclusive label-date boundaries for each
branch/effective threshold. Invalid policies, unknown labels/zones, unopened stock
and unavailable SKUs remain held. No manufacturer expiry is inferred. Only the
selected lot page receives Python DTO/day formatting. Risk/reorder grouping and
page counts happen in SQL. The SQL JSON arrays are bounded transport, not stored
business facts; bounded pages are sorted again after JSON decoding because JSON
aggregate element order varies by engine.

Historical facts are already unique daily branch/SKU/measure observations. Direct
SQL totals avoid grouping every cell then summing it again; optional branch/SKU/day
grouping is computed live for requested dates. The active T14 publication pointer
and committed run status exclude superseded/staged revisions. Missing remains
unknown; observed zero remains a recorded zero. Optional USD prices are source
metadata, never operational MYR revenue. Corrections become visible atomically
with publication, with no refresh job or stale-result invalidation queue.

## Streaming and security

CSV writes one escaped row at a time. Historical SQL pages fetch at most 101 rows
(100 plus lookahead); generators retain only bounded batches. No full CSV string,
observation ORM collection, downloadable artifact or background job is created.
Summary data scales with authorized branch count, not lot/history row count.
Permission is rechecked before summary streaming and every further 100 branches.
Historical streaming releases its read snapshot and re-resolves permissions/source
publication before each further 100-row chunk. Already-sent bytes cannot be
revoked. Loss of permission/source revision aborts the stream; no completion footer
is written. Consumers must require historical `export_complete,<rows>,synthetic_fixture`
and reconcile the row count. Summary consumers check `included_branch_rows`.
No artifact URL exists to bypass current permissions later.

Every field is CSV-escaped; formula-leading `= + - @`, leading whitespace formulas
and tab/CR/LF are prefixed with an apostrophe. Responses use `Cache-Control: no-store`.
Existing CSRF/session/transaction write safeguards are unchanged.

## Reproduction and limits

```
python scripts/measure_t16.py --reference --output data/private/t16-reference.json
python scripts/measure_t16.py --indexes --output data/private/t16-measurements.json
python -W error -m unittest discover -s tests -p test_scoped_query_paging.py -v
python scripts/verify_t16.py
```

The measurement command always creates new temporary databases; it has no database
URL/existing-target option. Small is the T15 operational scenario plus T14's
6-SKU/14-day fixture. Medium adds 40 generated 10-unit receipts and 60 one-unit
sales per branch through real authorized ledger services, plus T14's 120-SKU/
90-day fixture. These are **synthetic pipeline profiles**, smaller than the audit's
proposed production scale tiers; no 657,000-row or real-M5 result is claimed.
One reader, first application read then three warm reads; OS cache is not flushed.
Detailed T17 concurrent load/SLO work and T20 forecast snapshots are deferred.

Counts/live risk aggregates still scan relevant authorized rows in SQL; they are
not O(1). SQL CASE complexity depends on distinct thresholds and branch count.
Scope-sized branch comparisons/authorization metadata and category/admin/receiving
catalog maintenance are not a general virtualized large-catalog UI. Index write
cost, large threshold/branch cardinality, large-tier workloads, independent/manual
accessibility and second-browser checks need further evidence. No response-result
cache was justified. See migration notes and verification for MySQL gates.
