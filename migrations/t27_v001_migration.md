# T27 minimum additive migration plan — approval required

No existing database has been migrated. Proposal endpoints default to disabled
(`ASSISTANT_PROPOSALS_ENABLED=false`), and application startup never runs DDL.

Scope is two tables, `operation_proposals` and `proposal_audit`, plus five
immutability/state-transition/append-only triggers. No existing table, business
row, user permission or historical observation is changed or backfilled.
The proposal references existing branches and immutable inventory operations.

Before enabling on an existing target, obtain owner approval identifying the
database, downtime/backup and recovery plan, and the two permitted action types.
Use the established backup and restore procedure; inspect readiness and current
schema read-only. Apply exactly one reviewed dialect artifact using the normal
database operator account, then verify tables, constraints, triggers, foreign
keys and readiness. Do not call `db.create_all()` to upgrade an existing target.

SQLite: `t27_v001_expand_sqlite.sql`, tested on a new in-memory previous-schema
fixture with preserved rows and a clean foreign-key check. MySQL:
`t27_v001_expand_mysql.sql` targets existing supported MySQL >=8.0.16/InnoDB,
uses the client `DELIMITER` directive for triggers, and is rendered/tested for
DDL consistency only. Live MySQL migration/locking verification remains an
operator acceptance step on a separately authorized disposable MySQL target.
No new dependency or live provider call is required.

The action policy is a conservative subset of the original T27 roadmap:

* `propose_transfer`: one SKU, positive integer quantity, active destination,
  timezone-aware future planned arrival and reason. HQ administrator or source
  branch manager with existing `transfer.propose`; one source selected through
  the ordinary authenticated scope selector. Confirmation calls
  `transfer_services.create`, creating **Draft only**. Source eligibility/FEFO,
  destination assortment/arrival suitability are checked, but no donor-surplus,
  forecast quality or cost-benefit claim is made. Normal submission, independent
  eligible HQ approval, reservation, dispatch and receipt remain separate.
* `propose_adjustment`: one SKU/lot and positive integer physical disposal
  quantity with reason. HQ administrator or branch manager with existing
  `stock.adjust`; `inventory_ledger.adjust` executes a **decrease/write_off**.
  Quarantined/expired physical stock may be disposed of through the ordinary
  ledger rules; reserved stock cannot be consumed. No increases or receipts,
  batch/date/policy edits, refunds, PO actions or transfer transitions are allowed.

HQ analysts and clerks receive no new mutation capability. Only the original
authenticated proposer can confirm/cancel; independent operational approval is
not replaced by assistant review. A changed role, scope, tenant, security stamp,
authorization audit revision, stock/lot/catalog/policy fact, expiry or integrity
check requires a new proposal. Normal transfer arrival/state rules still apply.

After migration verification and approval of this policy, enable the feature
explicitly in the backend configuration, with a stable private SECRET_KEY. Key
rotation invalidates existing seals and review tokens rather than weakening
verification. Proposals expire at most ten minutes after creation; creation
timestamps are sealed at database-supported second precision. Failed or stale
proposals remain non-executable and are not silently refreshed. There is no
automatic retry of failed confirmations.

Rollback: disable the feature first and retain both tables/audit evidence.
Do not drop a consumed proposal, delete its audit or undo a business effect by
SQL. Any business compensation must use the ordinary authorized workflow.
Review retention/archival separately; no destructive down migration is supplied.

Durability is per configured business database and tenant marker, matching the
existing single-company architecture. It does not implement a new multi-tenant
storage system. Capacity, cross-process state and operation keys live in SQL,
not the T26 in-memory conversation store.
