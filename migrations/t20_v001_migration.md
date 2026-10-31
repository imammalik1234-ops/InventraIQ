# T20 v001 additive expansion and cutover requirements

Artifacts: `t20_v001_expand_sqlite.sql`, `t20_v001_expand_mysql.sql`, reproducibly
rendered by `scripts/render_t20_schema.py`. SQLAlchemy definitions are separate
from operational metadata; application import/startup never applies them.

Tables: `forecast_demo_target`, `forecast_runs`, `forecast_publications`,
`forecast_snapshots`, `forecast_events`, `forecast_monitors`, `forecast_metrics`.
Foreign keys preserve actor, branch, SKU and import-run identity. Unique job keys,
run/branch/SKU identities and monitoring run/source/cutoff identities prevent
duplicates. Checks bound horizons/counts and prohibit qualified production status.
Triggers protect published runs, snapshots, events and monitoring facts. Indexes
support active-run branch/SKU pagination and run-time ordering.

Only the **newly generated disposable** target factory installs this version while
creating a target. Existing user, production and T15 private demo databases must
not be adopted or altered. No backfill of legacy sales, inventory, prices or
historical forecasting facts exists. All operational tables/data remain untouched.

Future cutover requires explicit approval, reviewed backups, dependency schema
verification (T04–T15), MySQL 8 constraint/trigger/FK and transactional tests,
repeatable-read isolation, least-privilege job/database credentials, source rights
and fingerprint checks, full T19 qualification and common release gates. No live
MySQL execution is claimed by offline compilation or SQLite tests.

Rollback for a failed job is transaction rollback plus a failure event; the last
active pointer stays intact. Operational rollback disables publication/read
integration or switches to a compatible previously published batch through the
authorized service with expected version/reason. Leave additive schema and audit
facts in place. There is no DROP, destructive down-migration or automatic cleanup.
Database archival/retention is a separate approved maintenance action; this
version stops new journal admission after its local 365-attempt guard.

See [scheduling/recovery/promotion runbook](../forecasting/SNAPSHOTS.md). Production
and real-data gates remain closed. Never apply this artifact to an existing target
merely to make a demonstration command run.
