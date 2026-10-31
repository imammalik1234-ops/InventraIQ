# T11 v001 ? manual additive transport expansion

No migration runs at startup. No configured/user database was contacted. All
executed DDL in verification targets fresh disposable SQLite only.

## Rehearsal and cutover

1. Obtain separate owner/target approval; freeze transfer and stock writers. Back
   up and verify a disposable restore, preserving all T01?T10 source IDs, legacy
   mappings, opening/stock/audit/sale/refund facts and existing approvals/holds.
2. Review exact SQLite/MySQL SQL and SHA-256. Expansion adds six tables only:
   transfer_executions, transfer_transit, transfer_transit_facts,
   transfer_discrepancies, transfer_resolutions and transfer_return_links, plus
   constraints and twelve immutable/sealed-fact triggers. No existing table is
   altered/rebuilt/dropped and no data is copied, seeded or retrospectively valued.
3. T10 StockTransfer.state retains its approval-state contract. The T11 companion
   execution row becomes authoritative transport state after dispatch; the service,
   API/UI and transition guards compose these explicitly. Do not query approval
   state alone to infer transport state, or run old T10 writers after T11 cutover.
   This avoids a destructive SQLite table rebuild merely to widen an old CHECK.
4. MySQL requires approved supported >=8.0.16 InnoDB, actual CHECK/FK enforcement,
   trigger privileges, binary operation keys, timestamp/isolation checks and client
   DELIMITER handling. DDL may implicitly commit. Verify actual restore, locks,
   deadlock responses and concurrent absent destination balances on the disposable
   server. Offline dialect compilation is not live server evidence.
5. Rehearse dispatch, partial/duplicate receipts, missing/damaged/expired goods,
   loss/found/quarantine resolutions, reverse transfers and uncertain-response
   retries. Run both T07 branch-stock and T11 transit reconciliation in authorized
   contexts. Verify stock + source-attributed transit + explicitly authorized loss
   conservation, with reservations and informational inbound excluded from sums.
6. Record reviewed checksum/operator/UTC applied_at in SchemaMigration after
   validation. T04 one-branch mapping/opening sign-off is still required. Do not
   fabricate dispatch/receipt history for old balances or duplicate legacy stock.

## Rollback and recovery

Before any T11 operation, disable execution interfaces and retain inert expansion.
After dispatch, freeze affected writers and preserve every transit fact, ledger
movement, resolution and return link. Never reset approval state, remove execution
rows, cancel post-dispatch, delete an unmatched shipment or restore source stock
without verified return custody. Old T10 binaries must remain disabled because
approval-state-only logic does not understand dispatched holds. Use reviewed
forward repair or an approved backup plus complete replay/reconciliation. No
DROP/down migration or unapproved real-data restore is supplied.

Quarantined/expired goods cannot enter ordinary return transport. They remain
held pending a separately approved exceptional custody/return policy; do not
relax canonical expiry or reclassify unavailable stock to bypass that gate.
