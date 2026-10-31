-- REVIEW ONLY. Never run automatically. Requires completed T01/T02 schema upgrades.
-- MySQL 8.0.16+ / InnoDB. Verify backup/restore and disposable parity before approval.
-- DDL implicitly commits in MySQL; this is not an atomic or rerunnable migration.
ALTER TABLE products ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE products ADD COLUMN archived_at DATETIME NULL;

-- SQLite additive equivalent (review and execute separately only after approval):
-- ALTER TABLE products ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0;
-- ALTER TABLE products ADD COLUMN archived_at DATETIME NULL;
-- No row deletion, history rewriting, stock changes or automatic archive backfill.
-- New disposable SQLite schemas use AUTOINCREMENT to prevent deleted IDs being reused.
-- Legacy SQLite without AUTOINCREMENT: archive works after these additions, but
-- permanent deletion fails closed. Enabling AUTOINCREMENT needs a separately reviewed
-- table-copy migration with FK/reconciliation checks; it is NOT performed here.
-- Rollback: roll application back and retain these additive columns and archive data.
-- Do not drop the marker or unarchive products automatically; older application
-- versions lack archive restrictions and must not resume writes to archived records.
