# T12 suppliers and purchase orders

Owner-approved policy (2026-10-22): only HQ admins create/submit, approve/reject,
cancel remaining PO quantities and maintain suppliers. Approval actor differs
from requester. Destination managers/HQ admins may receive approved deliveries.
Managers cannot create/submit/approve/reject/cancel. Analysts read in authorized
scope; clerks have no purchasing access. This supersedes the earlier proposal.

## Permission and endpoint inventory

| Interface / service | Capability | HQ admin | HQ analyst | Manager | Clerk |
|---|---|---|---|---|---|
| GET /api/suppliers, suppliers() | purchasing.read | read | read | routing identity read | deny |
| POST /api/suppliers; PATCH /api/suppliers/<id>; save_supplier() | supplier.manage | global HQ identity | deny | deny | deny |
| GET /purchasing, /api/purchase-orders, /api/purchase-orders/<id>; listing/detail | purchasing.read | explicit selected/consolidated | same, read only | assigned selected destination | deny |
| POST /api/purchase-orders, create() | purchasing.manage | selected destination | deny | deny | deny |
| POST /api/purchase-orders/<id>/submit/approve/reject/cancel; transition() | purchasing.manage | selected destination, independent approve | deny | deny | deny |
| POST /api/purchase-orders/<id>/receive; receive() | purchasing.receive | selected destination | deny | assigned active destination | deny |
| GET /api/purchase-orders/reconciliation; reconcile() | purchasing.read | explicit scope | explicit scope | assigned destination | deny |
| POST /purchasing/supplier, /purchasing/create, /purchasing/<id>/<action> | exact service capability | corresponding form adapter | deny writes | receipt only | deny |

Central routing is deny by default. Generic transition adapters require scoped
read first; services resolve exact write authority independently. Grants/users/
branches are freshly resolved from DB and rechecked before commit. Consolidated
selection is read-only. Payload actors, branch assignments, prices/counters,
supplier authority and operation IDs are not trusted. Wrong-resource/scope errors
are generic 403/404. CSRF, revocable sessions and throttling remain unchanged.

## Supplier and financial facts

Supplier has normalized stable unique code, name, active/archive and revision.
HQ may edit name/status with expected_version/reason, not code. Identity changes
are catalog-audited and reauthorized at commit. There is no delete endpoint;
PO/lot FK references prevent deleting a used supplier. Archive blocks new orders/
submission/approval, but does not erase history or prevent already-approved actual
fulfillment. Order snapshots retain original supplier name/code after later edits.

New identified PO lots carry StockLot.supplier_id. Existing lots and Product
supplier text remain untouched/null until reviewed mapping; no automatic identity
inference. A PO cannot receive against an existing lot whose supplier is unknown
or different. Global T06 manufacturer lot uniqueness is retained; ambiguous
same-batch/date aliases across suppliers require review, not automatic merging.

PO records supplier, destination, requester/independent approver, MYR currency,
optional recorded expected_date, status/version/timestamps and operation references.
Each of up to 50 distinct SKU lines records positive bounded quantity and agreed
unit_cost. Decimal validation requires exact two decimal precision; storage uses
integer sen (BIGINT), never SQLite floating money. Price/ordered quantity/SKU/PO
identity is immutable; draft corrections use cancel/recreate. Total is derived
from line quantities/costs and bounded to the T08 exact amount ceiling.
No payment, invoice liability, tax, discount, conversion or accounting is implied.
Purchasing costs never change HQ/branch customer prices or old sale/refund values.
Unknown expected dates, old supplier identity and historical costs stay unknown.

## State machine

Draft -> Submitted -> Approved -> PartiallyReceived -> Received.
Submitted may become Rejected. HQ may cancel Draft/Submitted/Approved or the
unreceived remainder of PartiallyReceived; terminal state Cancelled retains all
physical receipt facts. Received/Rejected/Cancelled are terminal. Approval must
be by another HQ admin. All transitions require expected_version, reason,
request_key and business_reference. Supplier/SKU/assortment must be active/approved
at create/submission/approval; an archived supplier's existing approval can still
be physically fulfilled. There is no order amendment or over-receipt override.

For each line, ordered = physically_received + cancelled + outstanding.
Cancellation closes all unreceived remainder, not a negative receipt or stock
correction. It does not undo prior deliveries or remove physical/quarantine stock.

## Receipt atomicity and idempotency

Creation accepts supplier_id, expected_date (nullable) and lines containing sku_id,
quantity and unit_cost, plus operation metadata. Source/destination is the selected
server-authorized destination, not a body-supplied branch. Ordinary state actions
add only expected_version. Receipt adds line_id and delivery object with T06
new/existing lot fields, actual quantity/expiry, delivery-line reference, explicit
normal/quarantine disposition and observed evidence. SKU, reason and native key
are derived by the PO service. Clients cannot supply actor, supplier, branch,
SKU override, price, balance, PO authority or native operation identity.

Receive locks/checks PO/version/line/outstanding before invoking T06 receive and
T07 guarded updates. One line/delivery per PO receipt event; repeated events can
fulfill all SKUs and multiple lots. Parent PO operation and native receipt operation
share one DB transaction. Relational PurchaseReceiptLink retains PO line,
StockReceipt, movement, quantity/quarantine and parent operation. Both operation
outcomes, counters, lot, stock, documents and audits commit together or roll back.
A final link/counter guard prevents standalone use of internal PO receiving
context from committing an unlinked receipt. Context is validated against an
actual staging server operation and current PO/branch/user authority.

Same actor/key/normalized payload replays the original committed outcome before
new state/version checks, after fresh authorization. Changed facts/type/branch
conflict. GET returns current state. A used delivery-line reference with a new PO
key conflicts: retry the original request key rather than attach an existing
manual/other-PO receipt. Unique delivery/receipt/movement constraints and shared
PO CAS prevent double credit, over-receipt and receipt-vs-cancel races. Busy/
deadlock/uniqueness conflicts return safe 409; no automatic retry is introduced.

T06 expiry/actual lot rules are unchanged. Normal receipt rejects missing required,
invalid, expired or insufficient-life labels. Explicit quarantine records actual
unsuitable goods with equal physical/quarantine increases and zero availability.
No manufacturer date is inferred. Physical quarantined deliveries count in received
quantity/cost, but are separately labelled as held; they are not saleable or
supplier-payment approval. Accepted replacements need a new approved order, not
an automatic rewrite of received history.

Known supplier lots and PO delivery references cannot bypass purchasing via generic
T06 receiving. Existing unidentified/manual T06 receipt compatibility remains;
it does not fabricate an order/supplier or consume PO outstanding. Historic
unidentified lot supplier attribution is blocked pending reviewed mapping.
Transfers retain their separate T11 identity/stock/transit contracts.

## Reporting and reconciliation

Detail exposes original/current state, revisions, MYR costs, quantities, approved
outstanding and up to 100 recent receipts/events with exact operation/movement
references. Orders paginate 25 rows (page 1?10000); active supplier/catalog choices
are bounded to 1000, with large-directory/history optimization deferred to T16.
Supplier choice caps are not a database limit. No global cache is introduced.
Read methods use T09 coherent snapshots; live MySQL requires RR/SERIALIZABLE.

Approved/PartiallyReceived outstanding quantities appear as approved_po_inbound,
separate from physical/available and source-attributed transfer transit. Draft,
Submitted, Rejected, Cancelled supply neither. Managers/analysts/HQ can read these
purchasing aggregates; clerks do not receive PO aggregates or costs (CSV value
blank). Existing inventory quantity/money totals are unchanged by merely placing
or approving an order. No ML result, historical PO, lead time or arrival is inferred.

Reconciliation checks ordered/received/cancelled/outstanding, latest immutable
purchasing audit snapshot and each linked native receipt, movement, lot supplier,
branch/SKU/quantity/quarantine and committed operation. Exact received cost is
quantity times immutable agreed sen. T07 reconciliation separately proves stock
opening plus all movement deltas; PO fulfillment does not assume receipts remain
unsold or untransferred. Cancellation counter tampering is detected as well as
receipt mismatches. Full reconciliation scans remain operator/T16 scale work.

Migrations are manual/additive, with nullable lot supplier column, no data backfill
or startup DDL. Live MySQL, approved real-data mappings/opening and deployment
review remain external release gates. No payment/procurement automation or dataset
import is implemented.
