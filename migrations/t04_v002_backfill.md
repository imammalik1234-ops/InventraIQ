# T04 v002: reviewed legacy backfill and reconciliation contract

This is a manual, separately reviewed data-migration stage, not executable startup
SQL. No existing database has been inspected or modified by T04. v001 expands the
schema; this stage is blocked until the owner designates ONE legacy branch and
approves the exact SKU/category/date interpretation mapping. Demo Central is NOT
an implicit legacy destination.

1. Stop writers and take a consistent backup, including sales, inventory, audits
   and schema. Record file/database snapshot checksum, counts, per-product stock,
   sales quantities by product/date, source IDs and audit snapshot checksums.
   Restore it into an explicitly disposable database and verify the restore.
2. Apply the dialect-specific v001 artifact to that disposable copy only. It adds
   tables and leaves all legacy tables unchanged. It is intentionally not blindly
   rerunnable: inspect schema_migrations and actual schema first. Compute SHA-256
   of the reviewed SQL bytes, record `t04_v001_expand`, checksum and actual UTC
   applied_at in schema_migrations after schema validation. MySQL DDL implicitly
   commits: do not claim transaction rollback protects expansion.
3. Seed branches explicitly using branch_foundation.seed_demo_branches(session).
   It inserts DEMO-01/02/03 only; conflicts fail without overwriting. There are no
   automatic assignments, HQ inventory branch, credentials or inventory copies.
   Existing extra branches are retained. Caller commits or rolls back.
4. Author an approved manifest covering EVERY old Product ID exactly once. Create
   explicit Category and SKU definitions first, with reviewed stable unique codes.
   Several old Product batches may map to the same SKU; names, supplier text and
   expiry are not automatic dedup keys. Different sellable variants use different
   SKUs. Category text (including null/unknown) needs an explicit reviewed category
   mapping; do not fuzzy-match or seed policies/default prices to resolve ambiguity.
5. Pass `legacy_branch_id`, an approval reference and a mapping keyed by integer
   legacy Product ID to `branch_foundation.backfill_legacy`. Each entry contains
   sku_id and, for dated rows only, expiry_timezone and expires_at_utc. These are
   reviewed interpretation inputs, not a calculation performed by this foundation.
   Original legacy DATEs are unchanged. Confirm label semantics/timezone and exact
   UTC boundary under section 22 before accepting those inputs. No date means all
   three fields absent. Missing actual dates are never synthesized.
6. Execute within a caller-owned transaction on the disposable copy. Any error
   requires rollback. The helper validates manifest coverage first, rejects replay,
   creates one lot per legacy Product and one holding at the ONE explicit branch,
   disables newly created branch assortment and quarantines all migrated units.
   A missing Inventory row is represented by zero quantity and null source ID;
   negative quantities, duplicate Inventory rows and orphan source records must be
   resolved from evidence before migration. Source rows are never updated/deleted.
7. Reconcile source and target for every Product/Inventory ID and quantity, not
   merely grand totals. Assert mapping count equals Product count, every inventory
   row maps once, all mapping branch IDs equal the approved destination, each lot
   preserves the source date and supplier/category text evidence, and BranchStock
   on_hand and quarantined totals equal legacy quantity. Missing receipt times stay
   NULL; do not infer them from last_updated. New operational receipts in T06/T07
   must supply immutable first_received_at_utc for deterministic FIFO ties.
8. Compare all source identifiers/counts, sales-by-date totals and audit checksums
   before/after. Sale remains linked to its original Product, with SKU/branch
   traceability through LegacyProductMapping. Historical transaction prices remain
   unknown. Current legacy prices are evidence only, never historical sale prices
   or an automatic SKU default. Check foreign keys, uniqueness, policy bounds,
   lot null tuples and disposition conservation. Record reviewed manifest checksum
   under `t04_v002_backfill` with UTC applied_at only after reconciliation succeeds.

No default policy is seeded: business approval and policy resolver/receipt
validation/revision history belong to T06. No supplier FK exists until T12. T05
must implement scoped services before any new operational reads/writes are wired.
T07 supplies allocations/ledger; this foundation has no dual-write or FEFO path.

## Rollback and cutover

Before cutover, disable foundation paths, discard uncommitted backfill data or
restore the disposable rehearsal snapshot; keep expansion tables. No DROP/down
migration is supplied. Rehearse restore and repeat all reconciliation checks.
The unit suite exercises rollback of staged backfill and source preservation;
that is not a production backup-restore certification.

Production cutover requires separately authorized schema inspection, approved
manifest and destination, tested snapshot restore, MySQL parity, T05/T06/T07
scoped readers/writers and a maintenance window. Legacy routes continue using
legacy tables in this release; do not start new stock writes beside them. Once
lot-aware writes start, reverting code/schema alone loses information: use forward
repair or cutover snapshot plus a complete operation log/replay. Retain legacy
IDs/tables/audits until retention approval; never drop evidence for rollback.
