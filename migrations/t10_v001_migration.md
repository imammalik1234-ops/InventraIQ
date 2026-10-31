# T10 v001 ? manual expand-only transfer foundation

No configured/existing database was inspected, opened or modified. No automatic
migration runner, startup DDL, data backfill, dispatch or receipt is supplied.

1. Obtain separate target approval, freeze affected writers, take a verified backup
   and restore to a disposable rehearsal. Preserve legacy identifiers, mappings,
   sale/refund/audit checksums and T07 opening/movement/balance reconciliation.
2. Review `t10_v001_expand_sqlite.sql` or `t10_v001_expand_mysql.sql`. Add only
   StockTransfer, StockTransferLine, StockTransferAllocation and StockTransferEvent,
   their keys/checks/indexes and nine immutable/sealed-fact triggers. Existing
   tables are not ALTERed or DROPped; no historical rows are changed or seeded.
   The allocation/event tables are required to link actual lot movements to lines
   and preserve transition evidence rather than rely on an unvalidated JSON list.
3. MySQL requires supported >=8.0.16 InnoDB, CHECK/FK enforcement, trigger creation
   privileges and client DELIMITER processing. DDL can implicitly commit. Verify
   actual lock ordering, current reads, deadlocks/timeouts, binary request-key
   matching, timestamp precision, FK constraints and restore on the approved
   disposable server. Offline SQL compilation is not live MySQL validation.
4. Record exact reviewed SQL SHA-256, operator approval and actual UTC application
   time in SchemaMigration after validation. No real-data transfer is synthesized.
   Existing one-branch legacy opening and mapping sign-off gates still apply.
5. Rehearse independent HQ approval, request-key retries after lost responses,
   failed multi-line approvals, source revocation, destination life policy and
   cancellation after expiry. Compare opening plus movements to all balances;
   physical stock is unchanged throughout T10, reserved quantity changes only on
   approval/cancellation. No destination holding or in-transit stock is created.
6. Enable interfaces only after separate deployment/cutover authorization and the
   common acceptance gate. App credentials must not disable immutable-fact triggers.

## Rollback

Before any transfer operations, leave the expansion inert and disable interfaces;
retain new tables. No destructive down migration exists. After approvals, freeze
writers and preserve all operations, allocation movement IDs and state events.
Release approved holds only through reviewed HQ cancellation (before dispatch).
Never delete transfer history, reset reserved balances, replay an opening, drop
allocation tables, or roll back to code that cannot account for transfer holds.
Use an approved snapshot plus complete replay/reconciliation or forward repair.
T11 must preserve these holds and original expiry/lot identities, explicitly model
full dispatch and destination partial/discrepant receipts, and forbid a post-dispatch
cancellation shortcut. Those transitions are deliberately rejected in T10.
