# Owner decision ? approved T12 authority (2026-10-22)

The owner approved **HQ-only PO creation/submission**, independent HQ approval/
rejection, HQ-only remaining cancellation and supplier maintenance. Assigned
branch managers may only view relevant orders and receive approved deliveries;
HQ analysts read only; clerks have no purchasing access. The requester must not
approve their own order. This decision supersedes the historical proposal below.
`T12_CONTRACTS.md` records the implemented policy. The earlier proposal is retained
as decision evidence, not the current permission matrix.

---

# T12 purchasing policy proposal ? awaiting owner approval

## Why approval is required

The T12 request explicitly says: "If the existing specification does not define
who may submit orders or resolve other purchasing authority, document the proposed
policy and obtain approval rather than silently broadening permissions."

The master audit defines HQ supplier identity/approval and branch-scoped receiving,
plus the PO lifecycle, but does not assign PO creation/submission or remaining
cancellation authority. T05?T11 capabilities currently grant no purchasing writes.
No purchasing permissions, routes, schema or stock mutations are enabled by this
document. It is a reviewable proposal, not an approved business policy.

## Proposed permission matrix

| Action | HQ admin | HQ analyst | Branch manager | Clerk |
|---|---|---|---|---|
| Supplier create/edit/archive | yes | deny | deny | deny |
| Read supplier routing identity | yes | read only | active suppliers needed for own orders | deny |
| PO create and submit | selected authorized destination | deny | assigned active destination only | deny |
| PO approve | selected destination; never requester | deny | deny | deny |
| PO reject | selected destination | deny | deny | deny |
| Cancel remaining quantities, before or after partial receipt | selected destination | deny | deny | deny |
| Receive approved PO | selected destination | deny | assigned active destination only | deny |
| Read POs, quantities, agreed costs and receipt evidence | explicit selected/consolidated | explicit selected/consolidated, read only | assigned destination only | deny |

Alternative for owner selection: restrict PO creation/submission to HQ admins;
keep every other row unchanged. No one may approve their own order under either
option. HQ-created requests need another HQ admin for approval. Supplier archive
retains referenced records and evidence; physical deletion is unavailable.

Reasons, active assignments, expected revisions, CSRF and idempotency are required
for transitions. Consolidated selection is read only. No client-supplied actor,
price authority, balance or branch assignment is trusted. Existing clerk grants
are unchanged. Draft mistakes can be cancelled/recreated; approved lines/costs
must not be silently rewritten.

## Proposed implementation contract once authority is approved

- Supplier: stable unique code/name, active flag and revisioned HQ audit history.
  Keep legacy supplier text untouched and unmapped unless explicitly reviewed.
- PurchaseOrder: supplier, destination, requester/independent approver, MYR currency,
  nullable actual expected delivery date, status/revision/timestamps and operation
  references. Do not infer arrival or lead time.
- PurchaseOrderLine: SKU, ordered quantity, immutable agreed cost in exact integer
  sen with Decimal calculations, and received/cancelled/outstanding conservation.
  Purchasing cost is separate from HQ/branch customer sale prices.
- Lifecycle: Draft -> Submitted -> Approved -> PartiallyReceived -> Received;
  rejection before receipt and explicit cancellation of only unreceived quantities.
  Partial receipt plus cancellation retains the original receipt history and
  distinguishes fulfilled quantities from cancelled remainder.
- Supplier/PO/line/receipt/event relations and immutable receipt references use
  additive, versioned SQLite/MySQL schema artifacts. Introduce nullable real lot
  supplier association without fabricating legacy identities. Older versioned
  migration artifacts must stay frozen; no migration runs automatically.
- The PO service locks/checks destination, PO version and outstanding quantity,
  then delegates lot validation and every stock effect to receiving_services and
  the T07 ledger in the same caller-owned transaction. Relational links retain
  receipt, lot, inventory movement and operation IDs. No second stock engine.
- Operation-key and delivery-reference replay must never attach an old receipt
  to another PO line or double-count its quantities. Different payloads conflict.
  Entire PO/counters/lot/stock/operation/audit changes roll back on any failure.
- T06 normal receipt rejects invalid/missing required expiry or expired lots;
  explicit quarantine receives actual unsuitable goods with zero available units.
  PO physical received totals must label quarantine separately rather than imply
  those units are saleable or supplier payment is approved. No invoicing/payment,
  tax, discount, conversion or accounting subsystem is added.
- Approved outstanding inbound is reported separately from on-hand, available
  stock and transfer transit. Unapproved/cancelled quantity supplies neither.
  Financial reconciliation uses actual agreed line costs; unknown legacy costs,
  supplier mappings and order history remain unknown.
- Existing non-PO T06 receipt compatibility must not be converted into a fake PO
  or retroactively assigned a supplier. New PO-authorized receipts cannot bypass
  PO approval/outstanding bounds through either direct services or HTTP forms.

## Verification and gates

Use fresh disposable SQLite with FK enforcement for role/state matrices, exact
costs, partial/final/cancelled quantities, idempotency/reference collisions,
competing receivers, expiry/quarantine, rollback and ledger/PO reconciliation.
Run full regression and browser workflows after implementation. No tests have
been added or rerun for this documentation-only policy checkpoint.

Live MySQL validation still requires a separately approved disposable target.
No existing database is opened or migrated, no prior work is discarded, and no
Git commit/push/deployment is authorized or performed by this proposal.

Historical proposal status: superseded by the owner decision above; see T12_VERIFICATION.md for implementation acceptance. The reported 309-test T11 baseline is not a T12 test result.
