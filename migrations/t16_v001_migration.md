# T16 v001: optional ordered-read indexes

Additive indexes only. No application import/startup executes this artifact.
Apply only after review/backup and a disposable rehearsal against the exact engine.
The observation index requires T14's separate historical schema. It does not
authorize real-data ingestion. Existing databases were not migrated for T16.

SQLite EXPLAIN evidence and index write/space tradeoffs are recorded in
`T16_VERIFICATION.md`. Retain the existing branch/date indexes: narrow date
aggregates and ordered-ID continuation have different access patterns.

MySQL requires an approved disposable MySQL 8/InnoDB target, repeatable-read
report isolation, EXPLAIN ANALYZE, JSON aggregation/order/size verification,
online-DDL/lock and restore rehearsal. Offline SQL compilation is insufficient.
Do not enable production cutover based on SQLite timings.

Rollback: roll back application code first; with separately approved maintenance,
drop only `ix_t16_sale_branch_id_date` and
`ix_t16_observation_run_branch_id_date` (MySQL `DROP INDEX name ON table`,
SQLite `DROP INDEX name`). No business rows or original indexes are removed.
Rollback needs no reconstruction of financial, stock or historical facts.

No materialized aggregates or result caches were introduced. Live SQL grouping
uses current committed operational facts and the T14 active publication pointer.
T14 corrections atomically replace that pointer; continuation tokens bind its
version and fail on replacement. No aggregate refresh job or invalidation queue
exists or is needed. Financial refunds retain their own business dates.
