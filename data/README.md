# InventraIQ data provenance and selection contract (T13 v1)

**Real M5 ingestion is BLOCKED.** See the [rights evidence/use matrix](RIGHTS_REVIEW.md).
No dataset is included, downloaded or licensed by this repository. The T13 helper
does not import Flask, open a database, read M5 files, train models or make requests.
The Phase 4 parent checkpoint is `38f8be2f0016c3f8a1f1a9171efbfc11f290c7f3`.

## Source and version

Authoritative source: [M5 Forecasting — Accuracy on Kaggle](https://www.kaggle.com/competitions/m5-forecasting-accuracy),
hosted by University of Nicosia using Walmart observations. The logical input
contract is **accuracy-validation-d1-d1913**, not the evaluation file and not a
third-party mirror. It requests `sales_train_validation.csv`, `calendar.csv` and
`sell_prices.csv`. No `sample_submission.csv` is needed. The later evaluation
release must not be substituted or unioned with validation history.

This names an exact *logical slice*, **not a verified byte release**. No Kaggle
numeric revision, accepted terms version, hash or byte count can currently be
attested. The three checked-in templates deliberately use null fingerprints and
`byte_identity: unverified`; they are planning contracts, not import-ready manifests.
After separate rights/download approval, pin every source file's SHA-256 and byte
size from original bytes before parsing, and preserve the provider revision/access
receipt. Reject mixed releases, unexpected fields and checksum mismatches. Never
generate plausible placeholder hashes. Reconciliation belongs to T14.

## Deterministic stores and shared SKU selection

`data_manifest.py` implements `first-per-state-v1`: validate unique source store IDs,
sort within CA, TX and WI using ordinal string order, choose the first in each,
then freeze **CA_1, TX_1, WI_1**. No sales, model score or future outcome influences
store choice. T14 must check actual source metadata produces these IDs and fail on
drift, rather than quietly choosing substitutes. Source bytes were not inspected
here, so the proposed IDs remain subject to that validation.

| Real source store code | Existing demo branch code | Meaning |
|---|---|---|
| CA_1 | DEMO-01 | Source observations owned by this one branch only |
| TX_1 | DEMO-02 | Source observations owned by this one branch only |
| WI_1 | DEMO-03 | Source observations owned by this one branch only |

These are demonstration mappings, not a claim that a Malaysian/Singapore-timezone
demo branch is a real Walmart location. HQ remains an administrative scope. Source
calendar dates do not undergo branch-timezone conversion. No observation is copied
to other branches. Shared source item IDs represent shared anonymized catalog
identities, not duplicated users or invented brands. T14 must reconcile actual
category/unit mappings to the T06 catalog before writing its separate facts.

`shared-training-strata-v1` accepts only bounded training summaries with exactly
store_id, item_id, category, training_end_day, observed_days, nonzero_days and
total_units. Use **d_1–d_1857 only** for every profile. Each candidate needs complete
1857-day coverage and one metadata row in each selected store; incomplete shared
IDs are excluded, malformed/duplicate rows fail. Coverage means supplied source
cells, not an inferred start of product availability. T14 must derive summaries
strictly from those columns, verify their hash and reject false coverage claims.

For shared candidates, aggregate the three training nonzero-day counts and units.
Stratify by category, activity (`zero`; otherwise `intermittent` if nonzero count
is less than half 5571 cells, otherwise `dense`) and volume (`low` if total units
<5571, otherwise `high`). Sort strata by ordinal tuple order. Within each, rank
by SHA-256 of canonical JSON `["inventraiq-t13-v1", item_id]`, item_id tie-break.
Round-robin one ID per nonempty stratum until cap/exhaustion. No random runtime
state, sales ranking or held-out values are consulted. Shared IDs have consistent
category prefixes. All-zero candidates remain eligible in their own stratum.
Small/medium/large are prefixes of the same ranking. Fewer eligible IDs yield an
explicit smaller set, never fabricated replacements or replication.

`freeze_selection(template, rows)` freezes selected IDs and the canonical sorted
metadata SHA-256. It cannot approve rights or verify that a supplied summary is
truthful; it does not read source files. Templates have empty lists because no
real selection metadata exists yet. Tests construct original synthetic summaries
in memory and explicitly set `synthetic_fixture` / `synthetic-metadata-v1`.

## Profiles, original dates and scale ceilings

All windows end at d_1913 / 2016-04-24. Internal validation is d_1858–1885;
internal test is d_1886–1913; neither influences selection. d_1914 onward is unused.
These are our chronological splits, not a claim to reproduce the official scoring
protocol. Metadata selection uses the same pre-holdout history for nesting, even
when a smaller profile publishes a shorter observation window.

| Profile | Shared SKU cap | Stores | Observation days | Maximum observation rows |
|---|---:|---:|---|---:|
| small | 30 | 3 | d_1549–1913 (365) | 32,850 |
| medium | 300 | 3 | d_819–1913 (1095) | 985,500 |
| large | 3049 | 3 | d_1–1913 (1913) | 17,498,211 |

Ceilings are `3 × selected IDs × days`, not measured counts or performance claims.
Metadata extraction may require scanning more source history than published rows.
No benchmark or real import has run. Insufficient common coverage must be reported.
`demo_as_of` stays 2016-04-24; never shift dates to today. d_1 maps to 2011-01-29,
with consecutive day/date validation required against calendar.csv in T14.

## Schema, labels and transformations

[manifest.schema.json](manifest.schema.json) is JSON Schema draft 2020-12, with
closed objects. The stdlib validator supports only the explicit keyword subset
used in this schema; cross-field checks additionally enforce mappings, profiles,
dates, file column/type identities, paths and selection completeness. No runtime
dependency is added. `validate_manifest` returns a canonical JSON SHA-256; canonical
JSON uses sorted keys, ASCII escaping, compact separators and no non-finite values.
File hashes always concern *original bytes*, not canonical JSON. Semantic hashes
and byte hashes must not be confused.

Column contracts: `wide-units-v1` has six string dimension columns plus integer,
nonnegative d_1…d_1913 cells; `calendar-v1` has unique consecutive d/date (ISO DATE),
integer week/day/month/year/SNAP fields and nullable string event labels;
`weekly-price-v1` has string store/item IDs, integer week and nonnegative decimal
USD price, unique(store_id,item_id,wm_yr_wk). The schema's d_1..d_1913 token expands
to exactly 1913 column names, not a literal CSV column. Required headers and order
are pinned; T14 must validate actual headers/types/cardinality, preserve missing
as unknown and fail rather than silently coerce. Calendar can include future rows;
future sales/prices cannot influence selection. Weekly prices are observations,
never immutable POS unit prices. No backward fill or USD→MYR conversion.

Imported facts are `m5_historical_observation` / `imported_daily_units` with natural
key (source_revision, store_id, item_id, date, measure_kind), separate from T08
operational Sale/SaleLine. They never deduct current inventory, create lots/expiry
dates or enter recorded MYR revenue. Price-derived value, if later approved, must
be labeled estimated USD, not operational revenue. Synthetic metadata is labeled
`synthetic_fixture`; no metric or observation is claimed to be Walmart evidence.

## Private storage and approval boundary

Only reviewed schemas, templates, original synthetic fixture logic and redacted
documentation belong in Git. Put any later approved bytes in `data/private/raw/`,
selection summaries in `data/private/metadata/`, restricted rights evidence in
`data/private/rights/`, reconciliation in `data/private/reconciliation/` and local
fully fingerprinted manifests in `data/private/manifests/`. All are ignored.
Raw data must remain immutable. Archive extraction/sizing checks are T14 work;
T13 has no extraction, downloader, CLI import job or database migration.

Paths must be root-relative portable paths within data/private, with no absolute
paths, traversal, backslashes, URL/drive/ADS syntax, Windows devices or symlink
escape. Validation does not create directories or files. It is not a substitute
for T14's extraction and time-of-use containment checks.

Never commit Kaggle credentials, local manifests, raw/normalized subsets, trained
artifacts or restricted screenshots. Gitignore is a guard, not rights approval;
force-add can bypass it. Do not include secrets even in evidence references.
`require_real_ingestion` **always rejects** in T13, including synthetic, hashed or
client-edited manifests. A future reviewed version must explicitly implement the
rights gate; no `approved: true` field can unlock this one.

## Rights follow-up and synthetic T14 preparation

The [Zenodo follow-up](RIGHTS_REVIEW.md#zenodo-rights-follow-up--checked-2026-10-22)
records one CC BY 4.0 metadata assertion and one record without a license field;
neither proves uploader authority. [source_evidence.v1.json](source_evidence.v1.json)
keeps published MD5/byte counts separate from null local verification fields.
It is an evidence register, **not an alternate approved manifest**. Source hashes,
selection and rights gates are unchanged. See the [synthetic-only T14 work plan](T14_SYNTHETIC_PREPARATION.md)
for safe future parsing/join/restart/reconciliation tests without real data or
operational database changes.

## T14 synthetic-only implementation

The owner subsequently authorized a **separate generated-source contract** and new
disposable SQLite importer. See [usage, schema, restart and safety contracts](T14_IMPORTER.md)
and verification (historical development record; omitted from this package). This adds no M5 execution path and does
not relax any T13 manifest/rights decision. Its small/medium fixtures are original
formula-generated JSONL, not M5 files; operational stock/sales remain unchanged.

## T15 separate operational demonstration

The subsequently authorized [T15 demo](T15_DEMO.md) creates a **new** disposable
target and uses real business services for explicitly synthetic 2031 operations.
Its original T14 2001 observations are preserved unchanged, not converted into POS
or stock effects. The new local demo app labels all simulated monetary outputs.
This does not relax real M5 source rights, fingerprint or ingestion gates.
