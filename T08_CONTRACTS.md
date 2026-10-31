# T08 — priced sales, refunds and reversals

Approved session policy: **MYR only**, integer units, two decimal places, no
tax/discounts, no currency conversion. HQ admins and branch managers may refund;
managers only within their authorized branches. Analysts and clerks cannot refund.
Returned goods require explicit saleability inspection before available restock.

## Sale and line contracts

The original `Sale` / `sales` table remains an unchanged legacy quantity source.
New `PricedSale` / `priced_sales` headers contain authorized branch/cashier,
real UTC transaction timestamp, branch business date, currency, original total,
T07 operation FK, immutable financial audit FK and approved policy snapshot.
Their presence represents a completed native sale. Original completion facts
never change; partial/full refund state is derived from linked compensations.

`SaleLine` records stable SKU, integer quantity, final authorized unit price,
extended amount, definition snapshot and price-source/revision snapshot. Each SKU
appears once per sale. `SaleAllocation` links each line to original lot quantity
and immutable T07 movement. A single SKU can span many FEFO lots. Sale input accepts
1–50 unique SKU lines; order is normalized for idempotency and lock ordering.

Approved branch override takes precedence over HQ default, including a legitimate
zero price. Missing approved price rejects the sale. Clients cannot submit prices,
currency, taxes, discounts, actor, date or lot choices. Later price changes do not
change original receipts or refund unit amounts.

Calculations use Decimal and integer quantities. Monetary database columns use
exact **integer sen** (`*_minor`, BIGINT) on both dialects; Decimal model properties
and JSON strings expose MYR to two decimals. This deliberately avoids SQLite
NUMERIC affinity converting large decimal amounts through binary float. There is
no silent rounding of higher-precision prices. Maximum sale/refund is
9,000,000,000,000,000 sen; unit prices retain the existing catalog bound of
MYR 99,999,999.99. An amount exceeding the ceiling fails before deductions.
Line amount = original unit price × quantity; original total = sum of lines.
There are no implied tax-inclusive/exclusive calculations: these are item prices
and item totals under the explicitly tax-free demo policy. Receipt `net_total`
means original item total minus refunds, not a tax/accounting metric.

## One transaction and existing ledger

Services re-resolve actor/branch/capability, claim the T07 actor/request key, lock
all affected balances in `(branch, SKU, lot)` order and validate every line before
deducting. T07's shared `fefo_plan` supplies expiry/FIFO/lot-ID order; its guarded
`apply` produces every delta. No parallel stock engine was introduced.
Balances, original allocations, sale/refund headers/lines, immutable financial
audit, T07 InventoryDocument and operation audit seal in one caller-owned commit.
Services never commit. Direct callers must roll back on every exception.

Expiry/branch-date/authorization checks run again immediately before commit.
Current authorization is required on replay. Same normalized key and payload
returns the stored original result; changed facts conflict. Header price snapshots
are not recomputed on replay. Lost-response retry cannot create a second receipt.
Database triggers reject edits/deletes of all five financial tables and referenced
financial audit entries, and reject new lines appended after the parent operation
is sealed. Correct mistakes through compensating operations only.

## Refunds and reversals

Refunds target **original SaleAllocation IDs**, never arbitrary current lot IDs.
Every refund requires reason, business reference, key and fresh `sales.refund`.
The original sale header is exclusively locked. Remaining quantities derive from
prior immutable refund lines. A locking current read of those lines avoids stale
MySQL REPEATABLE READ aggregates after waiting on a competing refund. Repeated or
competing requests cannot refund more than originally sold, even at zero price.

`SaleRefund` links original sale and new compensating T07 operation, actor,
timestamp/date, currency, amount, reason and financial audit. `RefundLine` links
original allocation, quantity, original-priced amount, disposition, inspection
evidence and optional restock movement. Original sale/line/movement facts survive.

- `no_restock`: financial refund only. No physical balance or movement changes.
- `quarantine`: explicit physical return increases on_hand and quarantined by the
  same quantity; available units do not increase.
- `saleable`: requires `saleable_verified: true` and inspection evidence. The
  original lot must still pass T06/T07 expiry, disposition, SKU and assortment
  checks. A failed saleability check routes that physical return to quarantine,
  with requested/effective disposition recorded. It never becomes available.
  If expiry is crossed immediately before commit, the transaction rolls back;
  retry explicitly as quarantine/no-restock. Inspection cannot extend expiry.

Full financial refund (`mode: full`) refunds all remaining allocations without
automatic inventory return. For physical returns use explicit allocation lines.
A reversal compensates **all remaining** sale quantities/value; it can follow a
partial refund, but cannot be an additional partial compensation. Explicit
inspected lines are permitted only when they cover the full remaining sale.
No payment gateway, tax credit, customer accounting, purchase or transfer workflow
is implied. Damage never increases available inventory; no restock date is invented.

## Endpoint/service permission inventory

All writes require one selected active authorized branch. HQ consolidated access
is read-only and explicit. Guessed/cross-branch sale/allocation IDs return 404/403.

| Interface | Service | HQ admin | Analyst | Branch manager | Clerk |
|---|---|---|---|---|---|
| GET `/branch-sales`, GET `/api/priced-sales` | `sales_list`, `sale_detail` | explicit HQ scope | read | own scope | own scope |
| POST `/api/priced-sales`, `/branch-sales/create` | `create_sale` | selected branch | deny | own branch | own branch |
| POST `/api/priced-sales/<sale_id>/refunds`, `/branch-sales/<sale_id>/refund` | `refund_sale` | selected branch | deny | own branch | deny |
| POST `/api/priced-sales/<sale_id>/reversal` | `refund_sale(reversal=True)` | selected branch | deny | own branch | deny |
| Existing POST `/api/sku-sales`, single-SKU stock-page form | `single_sale_adapter` → `create_sale` | selected branch | deny | own branch | own branch |
| Existing POST `/api/stock-reservations/fulfil` | `reserved_sale_adapter` → `create_sale` | selected branch | deny | own branch | deny |
| Direct read-only operator reconciliation | `reconcile_sales` | explicit HQ scope | read | own scope | own scope |

`GET /api/priced-sales?sale_id=<id>` returns an authorized detailed receipt,
original allocations, remaining refundable quantities and compensations. List
responses cap each source at 100 recent rows; no dashboard/report integration is
claimed. The page supports three product rows; the JSON service supports 50.
HTML/API services share the same policy, CSRF, sessions and rollback boundary.

Sale:

```json
{"lines":[{"sku_id":2,"quantity":3},{"sku_id":3,"quantity":1}],"request_key":"cashier-42","business_reference":"COUNTER-42","reason":"Recorded sale"}
```

Inspected partial return:

```json
{"lines":[{"allocation_id":7,"quantity":1,"disposition":"saleable","saleable_verified":true,"inspection_evidence":"Original sealed batch inspected"}],"request_key":"return-42","business_reference":"RETURN-42","reason":"Customer return"}
```

Full financial refund/reversal:

```json
{"mode":"full","request_key":"reverse-42","business_reference":"VOID-42","reason":"Authorized cancellation"}
```

## Legacy, compatibility and reconciliation

No legacy rows or committed T07 operations are repriced. Their monetary fields
remain unknown (`null`, not zero). Original legacy IDs and source audit evidence
remain available. T07 single-SKU routes keep their request shape and old top-level
allocation result fields; **new** requests create priced headers. Replays of
pre-T08 quantity operation keys return their original unpriced result unchanged.
New reservation fulfilments consume/revalidate the reserved original lots and
capture the current approved price at actual sale time; reservation itself is
not a historical price promise. T07 low-level ledger primitives remain available
to trusted services; cashier HTTP writes use financial adapters.

`reconcile_sales` checks line arithmetic, sale/allocation quantities, movement
origin and lot identity, original-priced refund amounts and bounds, header totals
and return movements. T07 reconciliation independently checks all balance counters
and movement chains. Neither runs automatically on a configured database.
Old global dashboards/reporting still show their historical quantity contracts;
native financial dashboard integration remains T09. See the manual migration
guide for reviewed expansion and cutover gates.
