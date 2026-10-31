# T08 v001 — expand only; no automatic data migration

1. Approve the target, take a verified backup and restore it to a disposable
   rehearsal. Preserve counts/checksums for legacy sales, products, inventory,
   mappings, audits, T07 operations, documents and movements. The implementation
   has not connected to or modified a configured user database.
2. Review `t08_v001_expand_mysql.sql` or SQLite equivalent. Apply the five new
   tables, foreign keys, uniqueness/check constraints, read index and 17 financial
   immutability/sealing/audit triggers **only after explicit approval**. MySQL
   artifact uses client DELIMITER syntax. Existing tables are not ALTERed/DROPped.
   Record the reviewed artifact SHA-256 in SchemaMigration using the established
   manual operator procedure. Application startup never executes these artifacts.
3. Verify exact BIGINT monetary storage (sen), MYR constraints, InnoDB foreign
   keys, trigger installation/privileges and UTF-8 operation key compatibility.
   Ordinary app credentials must not disable/drop immutable-fact triggers.
4. Keep original legacy Sale rows and committed T07 quantity operations unchanged.
   Do not copy current prices into history, infer currency for unknown amounts,
   invent old revenue or replay deductions while creating financial rows. No
   automatic historical financial backfill is supplied. Unknown records cannot
   be refunded through the new priced-sale namespace.
5. Existing T07 opening/cutover gates remain in force. Every mapped SKU needs the
   approved one-branch source opening; nothing is copied to the other branches.
   Existing request keys survive the code transition: old quantity-operation
   retries return the original result; new single-SKU requests and reservation
   fulfilments use priced adapters. There is no unapproved balance dual-write.
6. Rehearse all sale/refund permission combinations, unchanged-key retries after
   lost responses, price changes, multi-line insufficient-stock rollback,
   inspected/expired/damaged returns and concurrent final-unit/refund requests.
   Run both `inventory_ledger.reconcile()` and `sales_services.reconcile_sales()`
   through an authorized operator session, checking every affected branch.
7. Require disposable live MySQL DDL, trigger, lock ordering/current-read,
   deadlock/retry, exact-value and restore evidence before enabling a real-data
   cutover. Offline compilation and SQLite execution do not establish parity.

## Rollback

Before native financial writes, keep the additive schema inert and use an
approved application rollback/restore plan. Retain all evidence; no destructive
down migration is provided. After priced sales/refunds exist, freeze writes and
preserve the full database before recovery. Do not roll back to a version that
can create unpriced sales or ignore compensations. Original sale and movement
facts are never deleted/edited. Correct financial outcomes with linked remaining
refund/reversal operations and explicitly inspected stock dispositions.

If a return is not physically verified saleable, use no-restock or quarantine.
Financial correction does not authorize available inventory. This task does not
implement payment settlement, tax/discount policy or generic ledger reversals.
