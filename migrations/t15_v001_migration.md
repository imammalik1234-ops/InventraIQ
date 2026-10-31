# T15 v001 — new disposable simulation journal only

No migration is authorized against an existing target. T15 initialization first
creates a **new** T14-attested disposable SQLite database, imports original generated
T14 small observations, and then installs this two-table journal before operational
users/records exist. It cannot adopt a T14-only or operational database. There is
no startup schema change or automatic migration in the normal Flask app.

Artifacts add simulation_runs and simulation_steps, FK/CHECK/unique constraints and
two immutable step triggers. Existing T01–T14 tables, identifiers, sales, balances
and imported observations are untouched by the artifact. No historical data is
backfilled. Run/step metadata are separate from stock/financial facts and do not
form a second inventory ledger.

simulation_runs binds the exact manifest, source-code/dependency fingerprint and
original T14 evidence hash. One versioned simulation per target is intentional.
simulation_steps binds each stable index/key to authorized actor, branch, fixed UTC
instant, input metadata and returned IDs/results. A service step and journal entry
commit atomically in the same DB session/transaction; update/delete of steps is
prohibited. Quantity effects and immutable business facts stay in T07–T12 tables.

SQLite is the only runnable backend. MySQL artifact is offline InnoDB/binary-
collation DDL, not evidence of live DDL, CHECK/FK/trigger/locking or restore behavior.
No MySQL instance was contacted. Any future target expansion requires its own
approved disposable rehearsal, backup/restore, code/schema review and registry
record of actual execution; these artifacts are not real-data cutover approval.

## Recovery / rollback

Resume the same manifest/application version on the same private target. Completed
steps are loaded from the immutable journal; incomplete transactions roll back.
Lost acknowledgment after commit is safe because the journal committed with the
business effects. Stable native operation/delivery keys remain intact as additional
service-level safeguards. Never delete a step, alter a committed quantity or reset
an expected revision to force progress.

A changed code/dependency/manifest fingerprint refuses resume and serving. Restore
the matching version for investigation, or initialize a separately named new demo.
Existing users/operational records cannot be adopted before bootstrap. There is no
down migration, delete/reset command, automatic repair, rollback-by-overwrite or
cleanup against any configured/user/production DB. Preserve failed evidence.
