# T15 deterministic synthetic operational demo

**Generated demo only. Real M5 rights/ingestion remain BLOCKED.** No Walmart
receipts, prices, brands, physical counts or profit are asserted. This is not a
forecasting model, procurement automation or production deployment.

## Create, run, resume and view

Use the project's working Python interpreter (`python` below). Select an existing
private parent directory or temporary directory. No configured DATABASE_URL/.env
is consumed as a simulation target. Initialization always makes a new uniquely
named `t14-demo-<uuid>` child with both T14 and T15 capabilities; it never adopts or
overwrites an existing database.

```powershell
python scripts/seed_operations.py init --parent C:\path\to\private-parent --acknowledgment "CREATE DISPOSABLE SYNTHETIC DATABASE"
python scripts/seed_operations.py run --target C:\path\to\t14-demo-<uuid> --max-new-steps 25
python scripts/seed_operations.py run --target C:\path\to\t14-demo-<uuid>
python scripts/seed_operations.py verify --target C:\path\to\t14-demo-<uuid>
python scripts/seed_operations.py serve --target C:\path\to\t14-demo-<uuid> --port 5055
```

Replace placeholders with the printed absolute path. Omitting `--max-new-steps`
runs to completion. Optional `--manifest` must exactly match
[t15_simulation.v1.json](t15_simulation.v1.json); changed or unknown specifications
are rejected. `serve` requires a completed, reconciled demo and binds **127.0.0.1
only**, no debug/reloader. Open the printed local address yourself. Do not point
the normal app at this target or expose this server publicly.

Nine distinct synthetic people are provisioned once with generated private passwords:

| Account | Portal | Authority |
|---|---|---|
| synthetic-901@demo.invalid | admin | HQ requester/catalog administrator |
| synthetic-902@demo.invalid | admin | Independent HQ approver/resolver |
| synthetic-903@demo.invalid | admin | HQ analyst, read only |
| synthetic-911/912/913@demo.invalid | staff | Managers of DEMO-01/02/03 respectively |
| synthetic-921/922/923@demo.invalid | staff | Clerks of DEMO-01/02/03 respectively |

Each slash-separated address means three different people/addresses, not aliases
for one account. HQ is an administrative scope, not another inventory branch.
Passwords and the demo signing key are in `simulation-private.json` inside the
private target; retrieve them locally, never paste them into Git/reports. CLI output
does not print credentials. Keep the target directory private; Git ignores all
`t14-demo-*` directories. This is an offline local-owner capability, not protection
against an OS administrator rewriting both the capability and database.

## Reproducibility, dates and opening quantities

Version v1 pins simulation ID SYN-T15-V1, PRNG seed 42, stable user/SKU/supplier/lot/
operation references, explicit branch demand/counts and dates. The seed generates
stable business-reference suffixes; demand quantities are explicit manifest inputs,
not fitted to future T14 outcomes. No source observations determine stock/orders.
The code fingerprint additionally binds Python and Flask/SQLAlchemy/Werkzeug/
Flask-Login/tzdata versions. A different application version refuses to resume or
serve an existing run; initialize a new target instead.

Opening is **2031-01-01 08:00 UTC**, simulation ends/frozen display as-of is
**2031-01-03 12:00 UTC**. T14 synthetic history stays 2001-01-01–2001-01-14 and is
hashed unchanged before/after operations, including its run metadata. The normal
app's clock is unchanged. A context-local server-only clock works only with the
attested demo app and matching target, with no request/body/query clock override.
Authentication expiry/throttling continue to use their real security clocks.

Initial counts by branch: 240, 20 and 90 units (350 total), split across explicit
early/late drink, undated non-perishable rice and snack lots. Lot dates are declared
synthetic labels; shelf-life metadata is not used to invent dates. There is no raw
positive balance assignment: a zero-only lot/holding scaffold, an authorized T07
zero opening and guarded positive initial-count adjustment are one atomic step.
The journal preserves the opening and count operation IDs. Unknown real opening
receipt times remain unknown; no historical physical receipt is fabricated.

## Real authorized workflows and resulting cases

69 chronological journal steps drive existing services with fresh DB actor/grants,
reasons, expected revisions and stable idempotency keys. There is no `force` flag,
role bypass, direct positive stock overwrite or second sales/transfer ledger.

- HQ category/policy/timezone/SKU/assortment and supplier management. Three shared
  SKU variants; explicit non-perishable rice and a DEMO-02 drink price override.
- Two HQ-created/submitted POs, independently approved by 902. Full snack PO: 12
  received. Rice PO: 20 ordered, 3 then 2 received into the same identified supplier
  lot, 15 still outstanding. All native receipts use T06/T07/T12 transaction rules.
- Nine multi-line clerk sales over three days, FEFO against actual lot dates,
  immutable MYR sale-time prices. Manager 911 refunds two inspected saleable drink
  units from the first sale, preserving the original sale/allocations/amounts.
- Two source-manager proposals, independent HQ approval and 15 units of reservation
  effects, then full dispatch. One has 4/10 received and **6 still in transit**.
  The other has 4/5 received, an explicit missing-unit discrepancy and HQ-authorized
  loss of 1, fully accounted as Received. Current reserved is correctly zero after
  dispatch; approval/dispatch movements preserve earlier holds.
- Manager 913 writes off two damaged snack units with reason/current stock revision.
- DEMO-01 retains excess stock and a critical near-expiry drink lot; DEMO-02 has
  real low-stock conditions for all three SKUs. Reports derive these from actual
  generated balances/policies at the pinned display instant, never placeholder cards.

| Branch | On hand / available | Simulated gross MYR | Refund MYR | Net MYR | Case |
|---|---:|---:|---:|---:|---|
| DEMO-01 | 215 | 133.90 | 9.00 | 124.90 | Excess, 5 near-expiry drink units, 6 source-attributed transit |
| DEMO-02 | 17 | 74.60 | 0.00 | 74.60 | Three low-stock SKUs, partial PO, incoming transit |
| DEMO-03 | 72 | 115.90 | 0.00 | 115.90 | Resolved shortage and write-off |
| HQ total | 304 | 324.40 | 9.00 | 315.40 | 6 transit counted once, 1 explicit authorized loss |

These are simulated recorded transactions, not real-world revenue or profit.
Company physical plus transit is 310 units. Conservation:
`350 opening counts + 17 PO receipts - 56 sold + 2 saleable returns - 2 write-offs
- 1 authorized loss = 304 on hand + 6 transit`.

## Provenance and interface scope

Names/codes, SKU attributes, lot provenance, reasons, immutable operation business
references and the journal link records to SYN-T15-V1. Existing tables without a
source column retain provenance through those references/relationships rather than
a schema rewrite. T14's imported daily observations never become POS sales, revenue
or inventory deductions, and the demo engine denies writes to historical tables.

The demo copy of the existing Flask/Jinja interfaces retains the normal CSRF,
revocable sessions, login throttling and T05 service/route permissions. It reuses
the existing light UI/components, with only a conditional synthetic/as-of notice.
Demo JSON responses and headers carry provenance; scoped operational CSV includes
the synthetic financial statement. Ordinary app responses are unchanged. There
is no global environment switch enabling a simulated clock or authority.

## Interrupted execution, replay and rollback

The service effects and immutable step journal commit in one BEGIN IMMEDIATE
transaction. A crash before commit rolls both back; a lost acknowledgment after
commit causes restart to load the original step result. Completed catalog changes
are skipped instead of creating duplicate identities. Business services still use
their own actor/key/payload, separation-of-duties and revision checks.

Re-running a complete manifest adds **zero** new steps/operations/stock/sales.
No passwords are regenerated and no assignments overwritten. Bootstrap refuses
preexisting users or operational records, wrong/missing capability or code/manifest
drift. There is no reset, delete, truncate, auto-repair or destructive rollback.
Preserve failed evidence and restore the matching version for resume, or create a
separately named fresh target. Never edit journal/revisions to force success.

## Verification and limits

```powershell
python -W error -m unittest discover -s tests -p test_operational_demo.py -v
python scripts/verify_t15.py
```

The browser verifier uses a fresh temporary demo, real login and the existing
desktop/mobile pages; it does not download datasets or contact providers. See
T15_VERIFICATION.md (historical development record; omitted from this package) for exact evidence and test results.

Runtime is disposable SQLite only. MySQL artifacts are offline, not live validation.
Real M5 rights/fingerprints and prior production/cutover gates remain blocked.
No T16 query/performance work, T15 real-world claims, model training, deployment,
second-browser/manual screen-reader certification or production credentials are
included. Existing UI mutation forms retain prior T06–T12 browser coverage; T15
focused browser checks navigate the generated outcomes and test role/scope denials.
