# T07 v001 — reviewed expand and opening procedure

These artifacts are **not executed by application startup**. No existing database
was migrated during implementation. Never run a demo/rehearsal against a user DB.

1. Obtain owner approval, stop legacy/native stock writers, take and verify a
   restorable backup, and rehearse on a disposable restore. Preserve schema,
   identifiers, source row counts, sales/audit counts, mapping manifests and hashes.
2. Review `t07_v001_expand_mysql.sql` (MySQL client DELIMITER syntax) or the SQLite
   equivalent. Apply only the four new tables, constraints, index and eight
   immutability/sealing triggers. Existing table definitions/data remain unchanged.
   Verify InnoDB, foreign keys, binary operation keys, trigger installation,
   database/application UTC timestamps and privileges. The ordinary app principal
   must not be able to drop/disable triggers. Record artifact SHA-256 and operator
   approval in the manually maintained SchemaMigration registry.
3. Freeze source quantities for a reviewed opening manifest, **per branch/lot**.
   T04 copies are snapshots, not proof of today's balance. Resolve any mismatch
   between source Inventory and BranchStock with owner evidence before opening;
   the service refuses to repair or overwrite either source automatically.
   Each mapped legacy product stays in its one explicitly approved branch. Do not
   copy a source balance into the other demo branches. HQ is never a stock branch.
4. For each manifest record include `request_key`, `business_reference`, `reason`,
   `lot_id`, `expected_revision`, `on_hand`, `reserved`, `quarantined_quantity`,
   sorted exact `source_receipt_ids` and `approval_evidence`. Use authenticated
   HQ catalog authority and an explicitly selected authorized branch. Invoke
   `inventory_ledger.opening` in one operator-controlled transaction and commit
   only after its audit/business/ledger evidence and reconciliation are reviewed.
   There is deliberately no public HTTP opening endpoint or bulk auto-migrator.
5. Legacy openings verify the original Inventory ID/count and retain Product ID,
   Inventory ID and mapping evidence in the movement. They permanently adopt the
   SKU, disabling old writers. Missing source identity/count agreement is a gate.
   Unknown reservation allocations are rejected, not reconstructed. Review all
   mapped lots before enabling the SKU's native engine.
6. Existing T06 receipts are **included once in the opening**, not replayed as new
   receipts. The exact receipt IDs and their original references, actors, dates,
   policy and label evidence remain unchanged. Their physical/quarantine totals
   must equal the opening. The opening starts ledger accounting from zero to the
   already-existing balance without changing its quantity, stock revision or
   first-receipt timestamp. Record the cutover instant; do not fabricate historical
   receipt times, expiry dates, lot identifiers or sale prices.
7. Require opening plus committed deltas to equal all current counters, verify
   movement before/after/version chains, and inspect unresolved rows. Review-held
   lots remain held after opening. Expiry/policy/correction approval is separate.
   Verify exact same-key replay after a simulated lost response before cutover.
8. Enable the reviewed native workflows only after reconciliation and MySQL
   race/expiry/restore checks pass. Old legacy stock writes are unavailable in
   normal runtime. The explicit `TESTING && LEGACY_STOCK_REHEARSAL` exception
   exists only to exercise pre-cutover disposable compatibility regressions.

## Rollback and repair

Before any native operation, keep the expansion inert and return to the verified
pre-cutover application/backup through an approved maintenance procedure. Preserve
all new evidence; this migration provides no destructive down script. After any
native movement, do **not** return to stale legacy quantities or delete/edit ledger
facts. Freeze writes, preserve the full database and reconcile a reviewed recovery.
Corrections use new evidenced adjustment/write-off operations, never rewritten
movements. Automatic reversal/refund/transfer workflows are not part of T07.

## Reproducible local command

`python scripts/reconcile_t07.py` accepts no DB path or URL and always creates a
new temporary synthetic SQLite DB. It prints the unreconciled opening state and
the reconciled result, then disposes the fixture. It does not load `.env` or
contact configured databases. `scripts/render_t07_schema.py` only renders SQL.

MySQL DDL compilation is not live MySQL validation. Disposable MySQL execution,
locking/deadlock tests, trigger/collation verification and restore sign-off remain
mandatory deployment gates, as does owner approval of actual legacy data.
