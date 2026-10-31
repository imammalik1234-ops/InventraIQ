# T07 — inventory operation contracts and permission inventory

All enabled native quantity mutations use `inventory_ledger` and the existing
single commit/rollback transaction decorator. Services never commit themselves.
Callers of direct services own rollback on **every** exception. SQL, exceptions,
actors and client scope objects are not accepted as operation inputs.

## Persistent facts

InventoryOperation uniquely claims `(actor_id, request_key)`. Its SHA-256 covers
canonical sorted JSON of normalized parameters, operation type and selected branch.
The actor, assignments and active branch are freshly resolved on the server.
Keys use binary MySQL collation. Same key/facts returns the original committed
result, including original allocations and balances even after later changes;
changed facts/type/branch returns 409. Revocation is rechecked before replay.
StockReceipt retains its additional branch/delivery-reference uniqueness across
different actors. Existing T06 forms use `receipt:<branch>:<external_reference>`
as their stable key; JSON receipts may supply an explicit `request_key`.

InventoryMovement records branch/SKU/lot, physical/reserved/quarantine deltas,
before/after counters and revisions, with policy/lot decision evidence. A line is
unique per operation/lot. InventoryDocument stores quantity-only business facts
and links native receipts to their original receipt IDs. CatalogAudit stores the
operation outcome in the same transaction. No legacy Sale or financial price is
invented for native quantity sales; priced sale documents are T08.

The operation stages, flushes its balances, movements, document and audit, then
seals as committed. Any failure rolls everything back. Database triggers forbid
update/delete of movement/document facts, modification/deletion of a committed
operation, and insertion of new facts into a sealed operation. Correct mistakes
with new evidenced operations, retaining the old facts. Trigger removal requires
separate database administration, outside application capabilities.

## Locking, expiry and reservations

Authorization rows, branches, SKU/policy/assortment/lot metadata use shared locks
in native services. Catalog/security edits retain exclusive locks. Balance rows
are exclusively locked in `(branch_id, sku_id, lot_id)` order; FEFO planning is a
separate order. Guarded updates compare counters and revision and enforce
`on_hand >= reserved + quarantined >= 0`, bounded quantities and no lost deltas.
Absent balances have composite unique keys. SQLite competing-writer conflicts
and InnoDB deadlocks/timeouts return a safe 409; there is no automatic retry.
Retry an uncertain submission using its original key and identical parameters.
Shared global metadata locks permit independent branch quantities to proceed
without a global SKU **exclusive** lock; live InnoDB behavior remains unverified.

Sale/reservation allocation excludes archived/unoffered SKUs, review/quarantine
lots, invalid/missing required expiry, expired instants and unavailable counters.
FEFO sorts dated canonical UTC expiry first, then immutable branch first-receipt
time and lot ID. Eligible undated nonperishables come last. Unknown opening
receipt times stay unknown and use stable lot-ID ordering after known times.
There is no manufacturer date inference or client-selected-lot sale bypass.

T06 pinned timezone/calendar policy remains authoritative. Time is captured after
allocation locks and revalidated immediately before commit, including branch
business-date rollover. Inclusive warnings do not prohibit unexpired sales.
Receipts also recheck minimum remaining life before commit. A reservation never
extends expiry: fulfilment revalidates all its lots, or returns a conflict. The
manager/HQ may release that hold, including after expiry, through an audited key.
Unknown pre-ledger reservations cannot be adopted without reviewed allocation.

Adjustments require reason and current stock `expected_revision`; they describe
an increase/decrease delta, never a client absolute balance. Positive adjustments
require an existing eligible lot. Write-offs decrease physical stock, consume
quarantined units first, and cannot consume reservations. Quarantine release,
transfers, purchasing, refunds and approval workflows are not exposed here.

## Endpoint/service permission inventory

Every write re-resolves and locks authorization in the service and rechecks it
before commit. Every operation selects one active authorized branch; HQ cannot
write a consolidated scope. Cross-branch resource IDs return uniform 404/403.

| Interface | Service | HQ admin | HQ analyst | Branch manager | Branch clerk |
|---|---|---|---|---|---|
| POST `/api/receipts`, POST `/receiving` | `receiving_services.receive` | selected branch | deny | own branch | deny |
| POST `/api/sku-sales` | `inventory_ledger.allocate` | selected branch | deny | own branch | own branch |
| POST `/api/stock-adjustments` | `inventory_ledger.adjust` | selected branch | deny | own branch | deny |
| POST `/api/stock-reservations` | `allocate(reserve=True)` | selected branch | deny | own branch | deny |
| POST `/api/stock-reservations/release` | `settle_reservation` | selected branch | deny | own branch | deny |
| POST `/api/stock-reservations/fulfil` | `settle_reservation(sell=True)` | selected branch | deny | own branch | deny |
| GET `/sku-inventory`, `/api/sku-stock` | scoped holdings | explicit HQ scope | read | own branches | own branches |
| POST `/sku-inventory` | native sale or adjustment service | selected branch | deny | own branch | sale only |
| offline approved opening, no HTTP adapter | `opening` | selected branch | deny | deny | deny |
| scoped read-only reconciliation, no HTTP adapter | `reconcile` | explicit HQ scope | read | own branches | own branches |

Reservation settlement is whole remaining quantity, with reservation operation ID
and matching SKU/quantity; branch stock managers may administer their branch's
holds. Clerks' capabilities are unchanged. Mixed/bulk client operations and
actor/scope injection are rejected. The existing CSRF, revocable sessions,
throttling, private response caching and non-disclosing errors remain in force.
No business cache is introduced.

Native sale JSON example:

```json
{"sku_id":2,"quantity":3,"request_key":"sale-device-42","business_reference":"COUNTER-42","reason":"Recorded quantity sale"}
```

Adjustment additionally supplies `lot_id`, `expected_revision`, optional
`kind` (`adjustment`/`write_off`) and `direction` (`decrease`/`increase`).
Reservation creation uses the sale shape; settlement adds `reservation_id`.

## Legacy boundary and reconciliation

Unopened balances cannot enter the native engine. Mapped SKUs require approved
opening evidence for all their mapped lots. The opening service preserves source
identifiers and includes existing T06 receipts exactly once without reapplying
quantities. Review state, original sale history and audit evidence survive.
Normal legacy quantity writers fail closed pending cutover. Only explicitly
enabled disposable `TESTING && LEGACY_STOCK_REHEARSAL` fixtures retain the former
safe compatibility implementation for regression/rehearsal; they are not a
production alternate inventory engine.

`reconcile()` compares opening plus committed deltas against all three counters,
checks the full before/after/version chain and current stock revision, and marks
unopened rows unresolved even if their physical count is zero. It is branch-scoped.
See `migrations/t07_v001_migration.md` for cutover, rollback and the reproducible
temporary-data command. No existing database is automatically altered.
