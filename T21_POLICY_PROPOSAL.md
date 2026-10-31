# T21 recommendation policy — synthetic preview approval

The user approved these formulas for **synthetic review previews only** on
2026-10-23, reference `synthetic-review-2026-10-23`. These are **not production
defaults**. Numerical fixture values remain test data. Required configuration,
scope, provenance and matching-date gates still apply. Unknown values remain
null. The legacy 20% buffer is historical only and is not adopted here.

Proposed version: `horizon-fefo-net-need-v1`.

1. Each branch/SKU has a server-reviewed, versioned supplier policy with explicit
   lead days, review days, safety units, excess buffer units, minimum purchase
   units and purchase pack size. No field has a business default. The protection
   horizon is lead + review days, between one and the seven days actually
   forecast. Longer horizons are unavailable, with no extrapolation.
2. Forecast origin must match the branch's current operational business date.
   Production recommendations require a qualified forecast, which T19/T20 do not
   supply. Provisional synthetic forecasts may support **synthetic review
   previews only**, if separately approved; the historical and operational dates
   must still match. Old observations are never shifted to satisfy this rule.
3. Current supply is verified eligible on-hand less reservations and quarantine.
   Forecast demand is treated as additional unreserved demand; reservations are
   excluded from supply rather than silently matched to forecast demand. This
   conservative assumption is explicit and requires approval.
4. Pure daily FEFO projection excludes each lot once it becomes ineligible at the
   conservative end-of-business-day evaluation instant. It never writes stock.
   Daily forecast quantities stay decimal; only final whole-unit suggestions are
   rounded. Missing expiry/coverage/opening evidence causes review, not invented
   supply. Projected unused expiry is informational risk, not an automatic action.
5. Count approved open PO quantities once, net of actual receipts/cancellations,
   only with a recorded expected date and sufficient eligibility evidence.
   Date-only non-perishable PO arrivals are conditionally available from the next
   business day; this is an assumption, not a fabricated actual delivery time.
   Unknown perishable lot expiry or overdue/unknown arrival dates withhold a
   numerical recommendation. Supply outside the horizon is shown but not counted.
6. Approved transfer allocations are expected inbound once; after dispatch use
   remaining transit only, never both allocation and transit. Actual receipts are
   already in destination stock and are not added again. Disputed, overdue or
   expiry-ineligible inbound is explicit review. Source reservations are already
   excluded from available source stock. Planned arrival is not guaranteed receipt.
7. Raw replenishment need is the whole-unit ceiling of projected unmet demand
   plus the end-of-horizon safety-stock gap. For a positive need only, purchase
   suggestion = pack × ceil(max(raw need, minimum order) / pack). Explain earlier
   shortages that a new order cannot cure before its configured lead time.
8. Potential excess is verified current stock left after projected demand,
   without relying on future inbound, above safety + the explicit excess buffer.
   It is not a slow-sales label. Expiring, reserved, quarantined and unverified
   stock cannot become an ordinary donor quantity.
9. HQ consolidated transfer previews require both branches' compatible forecasts
   and policies, an explicit route transit duration, destination assortment and
   existing receipt-life policy. Quantity is limited by verified receiver need,
   donor physical surplus and eligible donor lots through the receiver horizon.
   Deterministic branch/SKU/FEFO ordering and virtual quantity budgets prevent
   duplicate donor/receiver use across suggestions. No real reservations occur.
   Branch-only users receive no other branch's identities, balances or candidates.

Test-only worked example (not production defaults): lead 2 days, review 5 days,
safety 10 units, minimum purchase 12, pack 6, excess buffer 5, route transit 1 day.
Seven-day demand 35, eligible free stock 20, no inbound => raw need 25, purchase
preview 30. A donor with 80 eligible free units and the same demand/safety/buffer
has 30 units of potential surplus; at most 25 can cover that receiver's raw need.
These quantities are valid only when daily expiry/arrival projections and all
other gates agree. The example numbers are synthetic test inputs, never defaults.

Approval recorded: the user approved the formulas and assumptions above for
**synthetic review previews only**, reference `synthetic-review-2026-10-23`.
Production publication/qualification remains gated. Every real
branch/SKU and route would still need explicit approved configuration values.
