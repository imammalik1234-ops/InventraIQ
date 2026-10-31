# T12 v001 ? manual additive purchasing expansion

No migration is executed by startup. No existing/user database was contacted or
modified during implementation. Verification uses new disposable SQLite only.

1. Obtain separate target/owner approval, freeze affected writers, verify backup
   and disposable restore. Preserve Product supplier text, legacy mappings/IDs,
   all actual lot dates/provenance, balances, sales/refunds, audits and T07/T11 facts.
2. Review exact SQLite/MySQL SQL and checksum. Expansion creates Supplier,
   PurchaseOrder, PurchaseOrderLine, PurchaseReceiptLink and PurchasingEvent tables,
   keys/checks/indexes and nine immutable/sealed/line-identity triggers. It adds one
   nullable StockLot.supplier_id FK column. Existing rows remain NULL; no supplier,
   PO, agreed cost, manufacturer date or historical receipt is inferred/backfilled.
   No table is dropped, rebuilt or destructively migrated. ALTER ADD COLUMN is
   additive, not automatically approved for a real database.
3. T04 rendering explicitly preserves its pre-T12 lot columns/constraint order.
   Existing SQL artifacts remain unchanged. Apply expansions in reviewed version
   order; do not regenerate/reapply T04 to obtain the new lot column.
4. MySQL requires approved supported >=8.0.16 InnoDB, actual CHECK/FK enforcement,
   binary operation/delivery keys, trigger privilege/installation, UTC precision,
   transaction isolation, lock/deadlock and restore verification. DDL may implicitly
   commit. SQL uses client DELIMITER processing. Offline rendering is not live
   MySQL evidence; no MySQL target was contacted.
5. Rehearse independent HQ approval, real partial/duplicate/concurrent receipts,
   old delivery-reference collisions, quarantine, failure rollback and remaining
   cancellation. Run T07 branch-stock and T12 PO reconciliation. Verify ordered =
   received + cancelled + outstanding and exact MYR costs, while approved inbound
   remains separate from physical/on-hand/available and transfer transit.
6. After validation, record the exact SQL SHA-256, approval and actual UTC applied_at
   in SchemaMigration. Enable only through separately approved deployment/cutover.
   Preserve the existing one-branch legacy ownership/opening approval gate.

## Rollback and cutover

Before purchasing writes, leave additive schema inert and disable new interfaces.
After PO/supplier/receipt evidence exists, freeze affected writers and preserve
all facts. Do not delete suppliers referenced by POs/lots, erase supplier links,
reset received/cancelled counters, reattach old receipts or recreate stock effects
from snapshots. Cancel unreceived remainder through the HQ service; already
received stock and immutable operation facts are retained. Use reviewed forward
repair or approved snapshot plus complete replay/reconciliation. No destructive
DROP/down script or automatic restore is provided. Old application versions that
ignore PO/supplier receipt guards must not write during/after purchasing cutover.

Unknown legacy supplier text/cost/history stays unknown. A PO cannot retroactively
prove an old lot's supplier; uncertain existing lots require separate reviewed
mapping before being used as identified PO supplier lots.
