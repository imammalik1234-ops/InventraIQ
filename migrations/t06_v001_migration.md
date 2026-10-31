# T06 v001 expansion, migration rehearsal and rollback

No existing database was opened or modified during implementation. The SQL files
are manual expand-only artifacts, rendered without connecting to any database:

- t06_v001_expand_sqlite.sql
- t06_v001_expand_mysql.sql

They create SKUIdentity, LotIdentity, CatalogAudit, CompanyExpirySettings and
StockReceipt tables only. Existing T04/T05 tables/columns and source identifiers
are unchanged. No category defaults, dates, prices, branch assignments, quantities
or settings are seeded automatically. Old migration artifacts remain separate.

## Required rehearsal and cutover approval

1. Take and restore a consistent approved snapshot. Record schema/version/checksum,
   Product/Inventory/Sale/audit/mapping IDs and counts, stock per Product and branch,
   sales-by-date units and audit checksums. Preserve the single approved legacy
   branch designation; never replicate historical stock to all three demo branches.
2. Apply reviewed T04/T05 prerequisites and this expansion only to a disposable
   restored copy first. MySQL requires selected supported >=8.0.16 InnoDB; DDL may
   implicitly commit. Review keys, exact delivery-reference collation, constraints,
   timestamp precision, privileges and actual server behavior. Run schema readiness,
   full regression, competing receipts and restore/reconciliation checks there.
3. Compute SHA-256 of the exact reviewed artifact and record t06_v001_expand,
   checksum and actual UTC applied_at in SchemaMigration after successful schema
   validation. No automatic runner/DDL execution or destructive down script exists.
4. Review existing SKU/category mappings and normalized identity duplicates before
   adoption. Do not fuzzy-match category strings, merge uncertain variants, invent
   actual manufacturer lot IDs or infer dates from expected shelf life. Preserve
   old IDs and all reconciliation evidence. Genuine duplicate definitions require
   an explicit reviewed mapping; uniqueness conflicts fail rather than delete data.
5. HQ explicitly configures company label interpretation zone and category policies,
   with reason/revision. Per-SKU exceptions are full overrides. Proposed audit demo
   defaults are not automatically business-approved. Pin/record tzdata and second
   timestamp precision; review any old higher-precision interpretation separately.
6. New native SKUs can be offered and received. Validate each branch/lot balance
   against StockReceipt quantities and quarantine subsets, external reference
   uniqueness, receipt hashes/policy/label snapshots and audit records. No sale price
   or historic receipt time is fabricated. Historical sales remain quantities with
   old prices unknown; current catalog/default/override edits do not revalue them.
7. Mapped legacy SKU receiving remains explicitly gated. T04 balance copies may be
   stale after legacy activity: T07 must freeze/reconcile source operations, establish
   approved opening balances, retain dated/undated/review evidence and connect future
   ledger operations to existing receipt references without replaying their deltas.
   Adoption disables legacy operational readers/writers for that SKU, while keeping
   mapped history. Do not enable a mixture of obsolete Product expiry checks and
   canonical lot operations, or use an old global query as a fallback.
8. Before enabling deductions/reservation release, T07 must consume the shared
   policy/classifier, lock/revalidate actual lot expiry and current policy at commit,
   allocate FEFO and persist the complete operation/movement/allocation evidence.
   Policy/date changes affecting reserved units remain blocked until that exists.

## Rehearsal evidence supplied here

Disposable SQLite tests execute the exact new SQL against preserved legacy fixtures
and compare all source rows/FKs. T04 mapping/rollback fixtures continue to pass.
Tests prove receipt/audit failure rolls back new lots and balances, duplicate/reference
and alias races conserve successful deltas, and old mapping/sales history survives
catalog, price and policy changes. This is not an existing-data backfill execution
or a production backup-restore certification. Live MySQL was not contacted.

## Rollback

Before receiving/cutover, keep expansion tables and disable new interfaces; restore
an approved rehearsal snapshot if needed. Do not revert to pre-scope controllers
or erase adoption markers to regain legacy writes. No DROP/down migration is provided.
After receipts exist, retain receipt hashes, actual lot evidence, quarantine units,
policy history and audits. A single legacy Product date cannot reconstruct multiple
lots: use reviewed forward repair or an approved snapshot plus complete receipt/
operation replay and reconciliation. Never discard arrivals or invent old prices to
make rollback appear successful. No deployment or real-data cutover is authorized
by these artifacts.
