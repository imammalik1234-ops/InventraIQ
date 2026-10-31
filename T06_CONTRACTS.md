# T06 catalog, policy and receipt contracts

All business entry points re-resolve the enabled actor and active grants through
T05 authz; request actor IDs, clocks and policy overrides are not authority.
No migration, policy default, company timezone or opening balance is applied at
startup. No duplicate user accounts or fourth HQ inventory branch is introduced.

## Identity and categories

- `SKU` remains T04's stable sellable identity; old `Product` is the preserved legacy
  batch contract. SKU and legacy Product IDs are distinct namespaces.
- New SKU codes are canonical uppercase ASCII business keys and immutable after
  creation. Name, category, unit, flat text variant attributes, current HQ default
  price and active/archive state are editable only by HQ admin with reason and
  expected SKU version. Archive retains lots, holdings, receipts and history.
- `SKUIdentity` holds a unique SHA-256 canonical name/unit/variant-attribute key.
  Normalization uses Unicode NFKC/case folding/whitespace normalization. This blocks
  an exact sellable variant being recreated under another code, including archived
  identities. No fuzzy deduplication, unit conversion or guessed brand mapping.
- `Category` has stable ID/code/name. Category policy selection uses the FK, never
  free-text/fuzzy category matching. Existing mappings, category text, supplier text
  and historic source rows are retained unchanged.
- HQ price is Decimal NUMERIC(10,2), nonnegative, bounded, at most two decimal places.
  `BranchProduct` controls approval, optional current price/reorder overrides and a
  separate expected version. Null override inherits the current HQ default. A preserved unknown legacy default may remain unknown during metadata edits or
  archive; no guessed price is required. None of these operations changes Sale, transaction-time prices or old receipt facts.
- New catalog and receiving choices are database-driven. The hard-coded product
  dictionary is removed. Legacy edit selectors also use scoped mapped database
  definitions, with the stored family attribute or exact name as fallback.

## Expiry policy and date rules

CategoryExpiryPolicy is the default; ProductExpiryPolicyOverride is an optional
**complete replacement**, not per-field null inheritance. Missing/invalid policy
means configuration review; it never implies non-perishable. HQ explicitly approves
require_expiry, all thresholds/minima, optional expected-shelf-life metadata,
reason and expected policy revision. No business defaults are silently seeded.

Require `0 <= critical_days < high_days < early_days <= 3650`; minima are integer
0..3650, optional expected shelf life is integer 1..3650. Non-perishable means the
policy explicitly permits an undated lot. Voluntary actual dates still expire.
Shelf-life metadata is never used to generate or overwrite actual manufacturer dates.
Policy revisions and removal events have immutable service-written CatalogAudit
snapshots; remove/recreate never resets a SKU override's revision sequence.
Receipts store the resolved source/key/revision/full policy and trusted capture time.

CompanyExpirySettings records an HQ-approved label interpretation timezone and
revision. New dated receipts use that zone. A different supplier interpretation
requires HQ batch.correct capability and supporting zone_reason. Managers cannot
supply a different timezone or relax policy. Existing lots always retain their
accepted canonical instant, even after company/branch timezone or tzdata changes.

An actual date E is valid through that label date. Expiry is the beginning of E+1
in the recorded label zone, stored as UTC. At `as_of >= expires_at_utc`, it is
expired. Ambiguous midnight chooses the earlier valid UTC instant; a midnight gap
uses the first valid second on that next calendar day. A completely skipped day
or uninterpretable boundary rejects ordinary acceptance and requires review.
Conversion uses the explicitly pinned tzdata package, recording its actual version.
Both DB artifacts retain the existing DATETIME precision: **one second**, so warning
calendar calculations subtract one second before deriving the local final valid date.

One trusted UTC instant is captured after receipt locks. For branch warnings use
`date((expires_at_utc - 1 second) in branch zone) - date(as_of in branch zone)`.
Statuses are exclusive: review, not_tracked, expired, critical, high, early, healthy.
Threshold comparisons are inclusive; zero critical days is supported. Ordinary
receipt requires days_remaining >= the independent receipt minimum and a non-expired,
validated lot. No browser/server-local date arithmetic decides eligibility.

Enabling required dates identifies affected branches/lots and holds undated lots
in review without changing physical quantities. Disabling it never releases holds
or erases recorded dates. Reserved affected lots reject the policy transition until
T07 reconciliation; no implicit reservation release is implemented. HQ date corrections
require expected lot version, reason and evidence; affected holdings are disclosed,
reserved lots are gated, old facts are audited, and corrected lots remain review-held.
There is no release/disposal path or automatic extension into saleable stock.

## Receiving and duplicate handling

A manager or HQ admin must select one authorized active branch. Clerks and HQ
analysts cannot receive. SKU and branch assortment must be active/approved. Receipt
payloads cannot edit identity, price, policy, actor, reservations or evaluation time.

- `mode=new_lot`: sku_id, new internal lot_code, actual supplier_lot_code and observed
  expiry_date (or explicit absence when allowed). Different expiry labels require
  distinct lot IDs/internal codes, even when supplier lot text is reused.
- `mode=existing_lot`: sku_id, known lot_id and the observed expiry_date, which must
  match. Receiving cannot change its code/date/zone. Managers can select only lots
  already held by their branch. HQ has explicit cross-branch metadata authority for
  a supplier delivery of an existing global lot into another approved branch; this
  does not deduct or inspect another branch's quantities and is not a transfer.
- Internal `(SKU, lot_code)` uniqueness plus LotIdentity's unique signature over
  SKU/actual supplier identifier/observed expiry label prevent duplicate lot aliases.
  The semantic key excludes zone so changing the company timezone cannot create a
  second interpretation of the same known batch/date. Genuine HQ correction updates
  that key transactionally; collisions fail instead of silently merging quantities.
- Quantity is a bounded positive whole number. Every delivery line requires an
  exact-text `external_reference`, reason and observed-label/delivery evidence.
  Use the actual delivery-line reference again on retries. References are unique
  per branch; changing the same reference's payload conflicts. Replay returns the
  existing receipt after rechecking current authority and does not add stock.
  Separate real deliveries to one lot need distinct references.
- StockReceipt is receiving evidence, not a generic InventoryOperation/Movement
  ledger. It stores the actor/branch/lot/SKU, quantities, payload hash, receipt time,
  immutable policy and label facts, branch clock, SKU/assortment revisions and evidence.
  MySQL delivery keys use utf8mb4_bin for exact-text comparisons, matching SQLite.
- Actor/grant/branch/SKU/lot locks, guarded balance versions and DB unique keys protect
  concurrent requests. One transaction includes the lot, identity key, balance,
  receipt and audit. Any validation/uniqueness/audit/commit failure rolls back all.
  First-received time is captured only when a new branch holding is created; it is
  not overwritten by subsequent receipts. No legacy receipt time is invented.

`receipt_kind=normal` rejects missing required/invalid/expired/insufficient-life
labels. Explicit `quarantine` receipt records the physical arrival, reason and
conserved quantity, all held. Unreadable/unresolvable labels are preserved verbatim
in provenance, with canonical date fields absent and validation held; they are not
presented as valid undated non-perishables. Missing policy can enter this explicit
hold path with an unresolved-policy snapshot, never ordinary stock.

Holdings DTOs expose branch/as_of/zone, original and local expiry, exclusive status,
policy source/revision, on_hand, reserved, quarantine, eligible and unusable quantities.
Expired/review/archived/disabled holdings have zero eligible units. Unusable totals
count physical units once even when expiry and quarantine overlap. These are evidence
for future allocation, not a claim that new lot-based sales are enabled.

## HTTP and service permission inventory

Unsafe methods retain CSRF and the shared transaction boundary. Unknown endpoints
continue to deny. Services enforce the same permissions on direct invocation.

| Route | Method | Capability / contract |
| --- | --- | --- |
| `/catalog` | GET | inventory.read; company SKU/category/policy definitions; branch prices scoped |
| `/catalog/actions` | POST | HQ catalog.manage; action-specific services additionally require expiry_policy.manage |
| `/api/skus` | GET | inventory.read; active company definitions |
| `/api/skus` | POST | HQ catalog.manage; create definition only |
| `/api/skus/<sku_id>` | PATCH | HQ catalog.manage; expected_version + reason; archive via active=false |
| `/api/categories` | GET / POST | inventory.read / HQ catalog.manage |
| `/api/expiry-settings` | PUT | HQ expiry_policy.manage; label_timezone, expected_version, reason |
| `/api/expiry-policies/category/<id>` | PUT | HQ expiry_policy.manage; full policy, expected_revision, reason |
| `/api/expiry-policies/sku/<id>` | PUT / DELETE | HQ expiry_policy.manage; full replacement / audited removal |
| `/api/assortments/<branch_id>/<sku_id>` | PUT | HQ catalog.manage; enabled/overrides, expected_version, reason |
| `/api/lot-corrections/<lot_id>` | PATCH | HQ batch.correct; expected_version, actual date/zone, reason, evidence |
| `/receiving` | GET / POST | stock.receive; one authorized active branch; explicit new/existing lot |
| `/api/receipts` | POST | stock.receive; same receiving service as form |
| `/sku-inventory` / `/api/sku-stock` | GET | inventory.read; SQL-filtered managed branch holdings |
| `/api/sku-sales` | POST | sales.create + selected scope, then 409 unavailable; no deduction |

Global catalog management uses explicit HQ scope; request branch IDs are targets,
not grants. Stock reads/receipts always use server-resolved branch IDs. No whole
provenance/evidence JSON from another branch is exposed through a shared lot.
Existing T05 routes remain protected as listed in its historical inventory, with
these T06 changes: managed SKU operational legacy reads are withheld, managed SKU
legacy writes are gated, and historical Sale totals remain linked and scoped.

Service entry points: catalog_list/category_list/settings/assortment_list require
inventory.read; create_category/save_sku/save_assortment require HQ catalog.manage;
save_policy/save_settings require HQ expiry_policy.manage; correct_lot requires HQ
batch.correct; receive/receiving_choices require stock.receive in a selected branch;
holdings/receipts require scoped inventory.read. Internal policy/normalization/audit
helpers are not external tool entry points; pure expiry functions have no DB access.

### JSON examples

Create SKU (no stock or expiry field):

```json
{"code":"COLA-500","name":"Coca-Cola 500ml","category_id":2,"unit":"unit","variant_attributes":{"volume":"500ml"},"default_price":"2.50","reason":"Approved variant"}
```

Create/replace a category policy (expected_revision=0 means absent):

```json
{"expected_revision":0,"reason":"HQ-approved rules","policy":{"require_expiry":true,"early_days":7,"high_days":3,"critical_days":1,"expected_shelf_life_days":30,"min_remaining_days_on_receipt":0,"min_remaining_days_on_transfer_receipt":1}}
```

New lot receipt (dates below are illustrative actual-label inputs, not seeded facts):

```json
{"mode":"new_lot","sku_id":2,"lot_code":"BATCH-A-DATE1","supplier_lot_code":"LABEL-BATCH-A","expiry_date":"2030-01-08","quantity":8,"external_reference":"DELIVERY-123-LINE-1","receipt_kind":"normal","reason":"Verified arrival","evidence":"Observed manufacturer label and delivery note"}
```

Existing lot receipt:

```json
{"mode":"existing_lot","sku_id":2,"lot_id":7,"expiry_date":"2030-01-08","quantity":3,"external_reference":"DELIVERY-124-LINE-1","reason":"Additional arrival of same batch","evidence":"Matching actual label"}
```

## Legacy and T07 cutover boundaries

No legacy source row or mapping is automatically rewritten. SKUIdentity also acts
as a persistent adoption marker. Explicit HQ SKU/policy/correction actions adopt
reviewed definitions; removing an override never re-enables obsolete legacy writers.
Operational legacy Product/Inventory/Alert/Prediction queries exclude adopted SKUs,
preventing old Product.expiry_date/server-date logic from becoming a fallback. Scoped
historical Sale reads remain intact and show original source Product identities.
Unadopted legacy compatibility operations retain T05 behavior until separately reviewed.

T04 BranchStock copies may be stale after intervening legacy operations. **Any SKU
linked to LegacyProductMapping is blocked from native receiving**, including HQ,
until the reviewed opening-balance cutover is supplied by T07. Never treat a copied
balance as reconciled or dual-write it. New native SKUs have usable receiving now.
Unadopted foundation snapshots remain in the T05 foundation API; the new lot inventory
shows managed holdings only, with review evidence and without invented opening times.

T07 must supply the complete operation/movement ledger, opening reconciliation,
FEFO deductions, reservations, release/disposal and commit-time expiry revalidation.
It must adopt existing StockReceipt IDs/references exactly once, not reapply their
quantities. For new native SKUs in this release, physical on_hand reconciles to the
sum of accepted receipt quantities per branch/lot; no outgoing engine exists.
T09/T16/T21 later integrate operational alert lifecycle, reports and expiry-sensitive
forecast supply. This slice creates no second deduction engine or simulated revenue.
