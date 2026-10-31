# T10 transfer proposal and reservation contracts

T10 adds only pre-dispatch transfers. T05 identity/session/assignment resolution,
T06 expiry policy and T07 ledger/caller-owned transaction rules remain authoritative.
Direct service callers must commit on success and roll back on every exception.
No existing database migration or backfill runs automatically.

## State and permission matrix

| Transition | Source manager | HQ admin, selected source | HQ analyst | Clerk |
|---|---|---|---|---|
| Create Draft | assigned active source | yes | deny | deny |
| Draft ? Submitted | assigned active source | yes | deny | deny |
| Submitted ? Approved | deny | independent of requester | deny | deny |
| Submitted ? Rejected | deny | yes | deny | deny |
| Draft/Submitted ? Cancelled | assigned active source | yes | deny | deny |
| Approved ? Cancelled | deny | yes | deny | deny |
| Read proposal/history | assigned source only | explicit selected/all branches | explicit selected/all branches | deny |
| Dispatch/destination receipt | unavailable | unavailable | unavailable | unavailable |

Source managers may administer proposals from their authorized source, not just
those they personally created. Only the original requester is excluded from
approval; no emergency override exists. HQ-created proposals require a second HQ
admin. Destination staff visibility and responsibilities belong to T11; T10 does
not grant source stock access to destination users. Active destination names/codes
are available as routing choices without destination quantities, prices or users.
HQ analysts have read-only oversight; no additional stock permissions are granted.

Every transition re-resolves current actor and source permission, holds shared
authorization/metadata locks and rechecks authority before commit. Approval also
requires the original requester still be enabled and have active source manager
or HQ-admin authority. Revocation never blocks HQ rejection/cancellation recovery.
Consolidated mode is read-only: select a source branch before any mutation.

## Document and request contract

StockTransfer stores source/destination, requester, independent approver, exact
state/version, planned UTC arrival, created/updated timestamps and creation,
approval and cancellation operation IDs. StockTransferLine stores 1?50 distinct
SKUs and strictly positive bounded integer quantities. Lines are immutable even
in Draft: cancel/recreate a mistaken proposal, rather than edit approved evidence.
All branches/SKUs/offers must be active/approved at proposal, submission and
approval. Draft/submission do not promise current stock or reserve anything;
availability and lot eligibility are authoritatively checked during approval.

Creation accepts only destination_branch_id, planned_arrival_utc, lines,
request_key, business_reference and reason. Source is the server-authorized active
branch. Arrival must be an explicit future ISO timestamp with offset and whole
second precision. No arrival duration, manufacturer expiry or stock is inferred.
Line input accepts only sku_id and quantity. SKU lines are sorted before hashing.

Transitions accept only expected_version, request_key, business_reference and
reason. Client state, actors, authority, balances, lots and prices are rejected.
Wrong-scope IDs return generic 403/404; stale versions/invalid states/shortages
return 409; unsupported actions or malformed inputs return 400. Reasons are
required, 1?255 characters. Singular form/query fields cannot be repeated.

All mutations use T07 InventoryOperation unique(actor,request_key) with canonical
branch/type/payload hash, InventoryDocument and CatalogAudit. Same actor/key and
normalized payload replays the exact original result, including original state,
version, allocation and balances, even after a later transition. It is marked
replayed=true; GET detail returns the current state. Changed payload/type/branch
conflicts. New keys cannot repeat an invalid state transition. Permission is
rechecked before replay. A different actor cannot replay another actor's key;
state/version guards prevent a second approval/release across actors as well.

## Atomic stock and expiry

Approval locks all source balances by (branch, SKU, lot), then uses T07 FEFO and
eligibility to select lots. Source eligibility excludes reserved/quarantined,
expired/review/invalid undated perishable stock and unapproved legacy openings.
Candidate lots must additionally meet effective policy minimum remaining days
at the explicit planned arrival in the destination business timezone, with the
T06 canonical expiry instant and inclusive minimum boundary. Unresolvable/past
arrival requires cancel/reproposal. Unknown dates are never invented; properly
configured nonperishables can use eligible undated lots.

Every line is planned before any balance changes. Approval changes reserved only,
not on_hand, destination stock, prices or any sale/refund. T07 guarded updates
prevent negative availability. Canonical source expiry, projected destination
life, assignments, catalog/offer state and business date are rechecked at commit.
Failures roll back state/version, operation, movements, allocations and audits.
SQLite busy/snapshot conflicts and MySQL deadlocks/timeouts fail safely with 409;
there is no unbounded or automatic retry. Retry the original key and parameters.

StockTransferAllocation is relational immutable evidence linking transfer line,
source branch/SKU/lot, positive quantity and its original reservation movement.
It is deliberately separate from generic InventoryReservation: generic release
and sale-fulfil endpoints cannot steal transfer holds. Cancellation uses new
T07 compensating reserved deltas, each referencing the allocation and original
movement. Approved cancellation is HQ-only and accepts expired/archived lots or
inactive destinations so holds can be recovered. Source actor scope must remain
active. State/version CAS ensures release exactly once. Rejected/cancelled states
are terminal. Original allocation quantity is preserved; DTO remaining is zero
after cancellation. No transport or destination holding exists in T10.

StockTransferEvent records every transition (including creation), actor, UTC
instant, reason, version and unique operation ID. Database triggers prevent
update/delete of lines/allocations/events and additions to sealed facts. Allocation
composite FKs enforce source and line/SKU/lot consistency. Movement identity is
validated/assigned by the service; no movement/allocation IDs are client input.
Historical user IDs are retained as evidence even if an account is later removed.

## Interface inventory

| Endpoint | Service | Capability/scope |
|---|---|---|
| GET /transfers | listing/detail/choices | transfer.read; source scope; choices only proposers |
| GET /api/transfers?page=N | listing | transfer.read; explicit source/all HQ |
| GET /api/transfers/<id> | detail | transfer.read; explicit source/all HQ |
| POST /api/transfers | create | transfer.propose; selected source |
| POST /transfers/create | create | same + CSRF form adapter |
| POST /api/transfers/<id>/<action> | transition | service resolves exact action capability |
| POST /transfers/<id>/<action> | transition | same + CSRF form adapter |

Every registered endpoint participates in the central deny-by-default inventory.
Generic action routes first require transfer.read in a selected write scope, then
services require transfer.propose or transfer.approve; read permission alone cannot
mutate anything. All writes retain CSRF/revocable-session protection. Lists use
25 rows, page 1?10000, one lookahead row; detail is at most 50 SKU lines with their
actual allocations and at most four state events. Lots per SKU/allocations and
catalog choices retain the existing T07 full-read scalability limitation for T16.
No cross-request caches are introduced. T09 totals already subtract reserved
balances and never count them again as physical or destination inventory.

T11 must consume these holds, preserve original lot/expiry identity, revalidate
source/destination life at dispatch/receipt and add in-transit, partial receipt,
discrepancy/return accounting with approved migration/contracts. T10 rejects all
such actions and supplies no automatic dispatch or destination receipt.
