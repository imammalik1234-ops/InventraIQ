# T11 execution contracts

T11 extends T10 documents and the T07 transaction/operation engine. Services never
commit themselves. Direct callers must roll back every exception; HTTP adapters
retain the existing single commit/rollback boundary, CSRF and revocable sessions.

## State and authority

Approval state remains in the unchanged T10 header; after dispatch the additive
TransferExecution.state is authoritative. API/UI state is composed, never inferred
from header Approved alone. Shared StockTransfer.version guards every mutation,
including parent return claims. Execution states are Dispatched, PartiallyReceived,
Exception and Received (all quantities accounted for, including explicit loss).

| Action | Capability | Branch | Actors |
|---|---|---|---|
| dispatch | transfer.dispatch | source | assigned manager / HQ admin |
| receive | transfer.receive | destination | assigned manager / HQ admin |
| discrepancy | transfer.receive | destination | assigned manager / HQ admin |
| resolve | transfer.resolve | destination | HQ admin only |
| return (reverse Draft) | transfer.propose | original destination | assigned manager / HQ admin |
| read post-dispatch shipment | transfer.read | source or destination | assigned manager / HQ admin; HQ analyst read-only |
| reconciliation | transfer.read | explicit authorized scope | same read actors |

Clerks gain no transfer capabilities. Destination detail is unavailable before
actual dispatch and contains shipment facts, not source stock balances, prices
or unrelated activity. Movement before/after balances appear only for authorized
branches. Source authority cannot receive at destination merely by guessing IDs.
HQ must select the relevant branch for writes; consolidated mode remains read-only.
Every service and operation commit rechecks active grants/branches. Source/Dest
routing, actor, operation identity, balances and physical custody are server-resolved.

POST `/api/transfers/<id>/<action>` and POST `/transfers/<id>/<action>` reuse
T10's registered adapters with exact action authority in services. GET transfer
list/detail now includes dispatched inbound shipments as well as source proposals.
GET `/api/transfers/reconciliation` returns scoped transit-chain reconciliation.
All mutations require expected_version, request_key, business_reference and reason.
The actor/key hash covers normalized payload, action and selected branch. Same
key/payload replays original committed state/balances; different payload conflicts.
Replays happen before current version/state checks but after fresh authorization.
New keys never repeat dispatch or over-credit receipts. Lock/uniqueness conflicts
are safe 409s with no automatic retry; reuse the original key after uncertainty.

## Dispatch

Extra `lines`: every approved allocation exactly once, each with allocation_id and
source stock expected_revision. Full approved shipment only; partial dispatch is
not implemented. Actual lot allocations cannot be changed by request. Acquire
source balances in branch/SKU/lot order, validate active catalog/offers, openings,
canonical expiry, reserved quantities and destination projected life at recorded
planned arrival. Passed arrival/invalid lot requires cancel/reproposal before
shipment. T07 apply atomically decreases on_hand and reserved by each allocation.
An execution row, transit counters and immutable transit facts reference the same
operation and source movements. Metadata/expiry/permissions are rechecked at commit.

## Receipt and discrepancies

Extra `lines`: allocation_id, positive quantity, expected_revision of destination
holding (0 only for absent holding), disposition `saleable` or `quarantine`.
Up to 100 allocation lines per event. Quantities cannot exceed dispatched remaining
less unresolved transit disputes. Multiple partial events use updated document
and holding revisions. Existing lot identity, label timezone, expiry and source
provenance stay unchanged. A new branch holding records its real first receipt
instant, not a fabricated manufacturer date or new lot.

Only actual arrivals increase destination on_hand. Damaged arrivals, invalid/
expired/unapproved lots, disabled SKU/offers or insufficient actual remaining life
increase quarantine equally, so available stock never increases. Saleable arrivals
are revalidated at commit; a boundary crossing rolls back for a fresh quarantine
review. Transit decreases only by physically received quantity. Each immutable
receipt fact links the destination stock movement and quarantine/accepted counts.

Quarantine arrivals create immutable discrepancies. `discrepancy` accepts one
allocation_id and positive quantity to record missing/disputed transit; it creates
no stock effect and does not remove company-owned transit. Duplicate disputes
cannot exceed remaining undisputed units. Open issues make state Exception.

HQ `resolve` accepts discrepancy_id and decision:

- `found`: closes a transit dispute; quantities remain in transit until actual receipt.
- `loss`: explicitly removes evidenced missing transit into the durable loss counter
  and immutable authorized_loss fact; no fictional receipt or second branch deduction.
- `quarantine_acknowledged`: closes a physical arrival issue but leaves all held
  quantities quarantined. It does not release, destroy or sell damaged goods.

All are reasoned, versioned, idempotent HQ operations. Location/decision mismatch,
re-resolution, forged issue/transfer IDs and excessive quantities fail atomically.
There is no silent write-off, post-dispatch cancellation or balance reset.

## Linked returns

`return` accepts planned_arrival_utc plus allocation_id/quantity lines. It creates
a reverse T10 Draft and immutable TransferReturnLink to original received lots;
it does not immediately move stock. Received quantities less existing noncancelled
linked returns bound claims. Original parent version serializes competing claims.
The reverse transfer follows ordinary submission, independent HQ approval,
reservation, dispatch and verified receipt. Approval is restricted to the linked
original lots with per-lot return caps, rather than arbitrary FEFO replacement lots.
Reject/cancel frees a claim; dispatched/completed return claims remain historical.
No financial sale/refund is synthesized, and original transfer facts remain intact.

Only eligible saleable received stock can use ordinary returns. Quarantined,
expired or otherwise unavailable goods remain held; their exceptional transport
path is explicitly gated pending an approved custody policy. No negative-number
receipt, expiry relaxation or quarantine-to-available shortcut is supplied.

## Conservation, reporting and evidence

For each allocation: dispatched = remaining transit + physical receipts + explicit
loss. TransitFact rows carry before/delta/after, original allocation, operation,
stock movement where applicable and disposition evidence. Immutable triggers
protect facts, discrepancies, resolutions and return links. Header locks/version
CAS and guarded T07 counters serialize writers and absent-holding creation.
No global exclusive SKU locks or parallel branch stock engine are added.

T11 reconciliation checks transit fact chains, component sums, allocation quantity
and linked branch/lot/SKU/operation/stock deltas. Use T07 reconcile as well to check
opening plus all branch deltas. Reports/CSV add outgoing_transit, incoming_transit,
company_owned_physical (on_hand + outgoing_transit), authorized_transfer_loss.
Transit is attributed once to source. Inbound is informational, not added again.
Quarantine remains a subset of on_hand; reserved stock is not added again either.
Normal sales/refunds/revenue contracts remain unchanged, including unknown history.

UI uses current state, transit progress, operation IDs, discrepancies and latest
50 quantity effects. Relevant before/after stock is scoped per branch. Forms use
server-rendered IDs/revisions, but all hidden data is treated as untrusted. Lists
retain T10 pagination. Large document allocation/issue/history scans and full
reconciliation optimization remain T16 work; no global response cache is introduced.
