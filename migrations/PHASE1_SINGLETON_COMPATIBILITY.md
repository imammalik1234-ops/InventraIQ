# Phase 1 singleton compatibility: immutable history, new fresh-install versions

MySQL 8 rejects CHECK expressions that reference AUTO_INCREMENT columns (error
3818). A singleton key must be explicitly assigned 1, primary-key unique, NOT NULL,
and CHECK-constrained to 1. Removing AUTO_INCREMENT preserves that invariant;
removing the CHECK or weakening enforcement does not.

T06's current application metadata already uses `autoincrement=False` for
`company_expiry_settings.settings_id`. T14 now does the same for
`historical_demo_target.singleton`. SQLite's INTEGER primary-key storage and CHECK
semantics remain intact. Neither change imports data or alters target capabilities.

## Historical compatibility

All verified v001 SQL working files remain byte-for-byte unchanged. The relevant historical MySQL
artifacts and T06/T14 SQLite SHA-256 values are pinned in
`phase1_singleton_compatibility.json` and checked before rendering or executing
new artifacts. Historical `render(..., version='v001')` explicitly reproduces
the old DDL on copied singleton metadata; it does not mutate current metadata or
claim that old MySQL DDL is executable. Existing SQLite v001 artifacts remain valid.

The checkpoint's explicit `.gitattributes` rules preserve all 12 manifest-pinned
SQL files with `-text`, plus the generated JSON manifest whose bytes the renderer
also verifies. This overrides platform line-ending conversion without changing
global Git settings or checksum calculations. Seven historical files had CRLF
working bytes that earlier Git blobs normalized to LF; their checkpoint blobs
now store the same verified CRLF bytes. This representation correction has no SQL
content changes, working-file edits, applied-version/checksum updates or history
rewriting. Fresh isolated Git checkouts with autocrlf true/false and WSL integrity
checks passed; see the verification report for exact evidence.

New `t06_v002_expand_mysql.sql`, `t14_v002_expand_mysql.sql`,
`t15_v002_expand_mysql.sql` and `t20_v002_expand_mysql.sql` are fresh-target
replacements for their v001 MySQL expansions, not ALTERs to run after them. Changes
are the version label and removal of AUTO_INCREMENT on the singleton key only.
Other columns, CHECKs, primary/foreign keys, indexes, views and triggers are retained.
No historical migration row or checksum may be relabeled as v002.

Use the existing `schema_migrations` registry. The fresh-target rehearsal records
`t06_v002_expand`, `t14_v002_expand`, `t15_v002_expand`, `t20_v002_expand`,
and unchanged T16/T27 identities, exact artifact SHA-256 and actual UTC time
only after successful execution/validation. It must reject an existing version,
changed bytes, v001/v002 combinations, unknown registry rows or a nonempty target
instead of skipping, repairing, overwriting or changing a recorded checksum.
The manifest identifies expected bytes; it is not a second migration registry.

An already migrated SQLite database keeps its v001 identity/checksum and needs no
automatic schema change. A previously migrated MySQL database must retain its
recorded identity and data. Do not apply these CREATE artifacts there: inspect
version/checksum, actual SHOW CREATE TABLE and singleton rows read-only, then obtain
an explicit forward-migration/backup/recovery plan. If an existing v001 record has
different bytes or a manually altered schema, stop as divergent; do not bless it
by editing history. The fresh-only guard makes no automatic adoption decision.

## Scope and dependencies

T06 depends on the operational/user/catalog prerequisites; downstream operational
tables are initialized through existing metadata only after its tables exist.
T14 requires operational branches/SKUs. The planned live sequence then requires
T15, T16 indexes, T20, and T27, with no silent skips. T27's existing operator,
backup/recovery and action-policy approvals remain; proposals stay disabled.

The owner explicitly approved the same versioned DDL-only correction for confirmed
downstream failures. Live MySQL 8.4.11 execution confirmed T15 error 3818 on
`ck_simulation_run` and then T20 error 3818 on `forecast_demo_target_chk_1`.
Their v002 artifacts preserve original CRLF endings and differ only in version
comment and removal of the singleton AUTO_INCREMENT attribute. Simulation and
forecasting runtime modules, v001 renderers, AI behavior and datasets are unchanged.
This approval does not authorize changes to existing targets or feature activation.

The final new-target sequence passed: T06 v002 -> T14 v002 -> T15 v002 ->
T16 indexes v001 -> T20 v002 -> T27 v001. All four singleton keys were tested
using the restricted application account. Evidence and exact commands are in
`docs/PHASE1_MYSQL_VERIFICATION.md`.

Render/check new artifacts using the existing WSL interpreter against canonical
Windows source:

```text
python -B scripts/render_phase1_singletons.py
python -B scripts/render_phase1_singletons.py --check
```

No application startup executes migrations. No destructive down migration or
automatic cutover is introduced.
