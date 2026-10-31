# Development transformation: engineering and feature inventory

This document records the implementation inventory and historical development findings for the current Flask/Jinja application. Public setup uses disposable SQLite demos; private checkpoints, credentials and development databases are omitted. Historical browser measurements below are separate from fresh release verification.

## Architecture

| Layer | Implementation and responsibility |
| --- | --- |
| HTTP and sessions | `app.py`, `authentication.py`, `security.py`, `authz.py`: revocable login, CSRF, active database grants, branch selection, capability and transaction boundaries |
| Operational adapters | `catalog_routes.py`, `sales_routes.py`, `purchasing_routes.py`, `transfer_routes.py`, `operational_routes.py`: ordinary forms and JSON adapters over the same services; compatibility routes are mapped explicitly |
| Inventory facts | `models.py`, `inventory_ledger.py`, `receiving_services.py`, `expiry.py`: lot balances, canonical expiry, FEFO, reservations, revisions, movement/audit reconciliation |
| Business workflows | `sales_services.py`, `purchasing_services.py`, `transfer_services.py`, `transfer_execution.py`: immutable priced transactions/refunds, maker-checker orders/transfers, dispatch, partial arrival, quarantine and discrepancy resolution |
| Read models | `operational_services.py`, `query_paging.py`, historical queries and `services/workspace_views.py`: permission-scoped reports, bounded chart series and open-document queues; no writes or model fitting |
| Forecast and advice | `forecasting/`, `intelligence.py`, `services/recommendations.py`: offline publication and stored evidence; synthetic provisional planning remains separate from operational facts and execution |
| Assistant | `assistant/tools.py`, `assistant/orchestrator.py`, `assistant/evidence.py`, conversation/artifact/provider adapters: validated read-only plans, bounded scoped evidence, citations and disclosed deterministic local explanations |
| Reviewed actions | `assistant/proposals.py` and proposal adapters: sealed review, explicit confirmation, cancellation/expiry, normal business services and once-only operation mapping; feature gated |
| Presentation | Jinja workspaces, `templates/components/`, `static/css/product.css`, `static/js/workspace.js`, `static/js/assistant.js`: shared responsive product system, accessible evidence and progressively enhanced real forms |
| Development evidence | SYN-T15-V1 and separate SYN-T21-V1 fixtures, browser verifiers, focused and full regression runners; all databases are newly created and disposable |

The complete source inventory is [TRANSFORMATION_ROUTES.md](TRANSFORMATION_ROUTES.md):
124 Flask rules, including 25 rendered workspace rules, compatibility surfaces,
APIs, exports, action endpoints and static transport. Source accounting does not
mean every endpoint has a separate browser scenario.

## Roles and connected behavior

| Actor | Development workflow and boundary |
| --- | --- |
| HQ administrator | Consolidated review, branch selection, catalog/supplier/access administration, order requests and scoped stock actions. Cannot approve their own request. |
| Independent HQ approver | Separate HQ administrator identity; reviews another requester's PO/transfer through ordinary approval services. This is separation of people, not a new permission role. |
| HQ analyst | Read-only authorized company/branch evidence; no sale, refund, stock, proposal creation or administrative forms. |
| Branch manager | Assigned branch inventory, receiving, adjustments, sales/refunds, transfer requests/dispatch/arrival and approved PO receipt; no independent HQ approval. |
| Branch clerk | Assigned branch overview, inventory, sales and authorized reports/evidence; no receiving, adjustments, purchasing, transfers or administrative writes. |

The browser scenarios connect actual forms and final database checks for category
and SKU creation/edit/archive, supplier create/edit, receipt, FEFO priced sale,
inspected partial refund and financial reversal, PO draft/submit/independent
approval/partial receipt/cancel remainder, transfer reservation/dispatch/partial
receipt/discrepancy/authorized loss, branch edits and assignment grant/revocation.
They also exercise T27 cancellation, inert review, unchecked confirmation,
write-off confirmation/reload safety and transfer-draft-only confirmation.

## Confirmed findings and corrections

| Impact | Finding | Implemented correction / evidence |
| --- | --- | --- |
| High: incomplete assistant answer | The T26 HQ example could select valid tools while missing a required branch/condition. A successful provider request did not establish evidence completeness. | Original whole-plan validation and authorization precede server-defined unfiltered inventory-risk retrieval for the exact approved question. Signed snapshot paging stays within four tools/time/byte limits. Denial, changed snapshots and exhausted budgets stay incomplete. Focused evidence tests and real local assistant browser interaction. |
| High: misleading stock feedback | Enhanced POST followed a redirect in `fetch`, consuming one-use Flask success messages before a second browser GET. A committed adjustment appeared without its confirmation. | An explicitly enhanced, CSRF-checked POST receives a same-origin JSON redirect destination after the original transaction decision. JS navigates once. Native HTTP redirects and uncertain-outcome locking remain. Four Python transport tests, seven JS transport tests and browser rejection/correction/commit confirmation. |
| Medium: profile feedback | Settings returns a successful HTML 200 instead of a redirect; generic fetch enhancement treated a saved name as an unknown outcome. | Use native POST only for the profile form, preserving its success/validation page, CSRF and email-session revocation semantics. Connected browser save/revert checks actual profile text and visible success. |
| Medium: report regression | New charts imposed 90 days while reports and exports allow 366 inclusive days. | Chart reader matches the existing 366-day contract. Full-window dates and money totals, all four charted workspaces, invalid overlong range and browser annual report checked. |
| Medium: filter recovery | Invalid report filters returned a raw JSON page to browser users. | Render a scoped, escaped filter error with a reset link for ordinary page requests. APIs and explicit JSON callers retain JSON/status contracts. Integration and browser rejection/reset checks. |
| Medium: administrative form transport | A named `action` input masked the native `form.action` property, giving fetch the control rather than its intended URL. | Transport reads literal action/method attributes. JS checks cover masked properties; browser edits a branch and grants/revokes an assignment. |
| Medium: dishonest open-work queue | Transfer request state remains Approved after physical receipt; using it alone kept completed shipments in the open queue. | Join execution state and exclude fully received shipments. Regression compares queue entries with durable execution records. |
| Medium: receipt feedback scope | A receipt query parameter could suggest success without establishing an authorized actual receipt. | Feedback matches a receipt in the returned authorized list. Regression checks own/foreign receipt IDs; connected receipt browser flow verifies successful feedback. |
| Medium: proposal shell context | Proposal pages lacked the normal resolved role/context fallback, risking incorrect presentation of action entry points. | Presentation resolves existing permissions, preserves HQ analyst context and keeps controls gated. Server authorization remains decisive. Role and proposal lifecycle tests. |
| Product usability | Dense old workspaces, hidden context, repetitive surfaces, clipped catalog disclosures and encoded status labels weakened daily operation. | Coherent shell/navigation, distinct HQ/branch overviews, scoped chart/source pairs, selected-document-first workspaces, wrapping action labels and readable partial-receipt states. See design/workspace docs and browser captures. |
| Integration | The standalone brand renderer has no Flask config; a new optional action link assumed it existed. Two reworded headings/provenance messages also violated retained text contracts. | Restore the missing-context guard, exact proposal effect heading and explicit simulated-money disclaimer. Standalone brand, proposal review and six-page provenance checks pass. |
| Verification reliability | A terminated Windows benchmark worker could keep SQLite shared-memory files open after the venv redirector exited, breaking temporary cleanup. | Wait on a native synchronization handle for the actual owned worker before cleanup; preserve failure on wait timeout. Real deadline/cleanup, repeated-round reconciliation and two handle-lifecycle checks pass among 15 focused harness tests. No application business rule changed. |

## Integrity and security evidence

The final regression includes existing authorization, FK/migration, expiry/FEFO,
overselling/concurrency, idempotency, revision, rollback, immutable money, transfer,
assistant prompt/plan validation and proposal security suites. Browser flows add
actual form transport, CSRF rejection for all five actors, branch isolation,
read-only/clerk restrictions, inert proposal review, reload safety and literal
untrusted sale references. No external security assessment is claimed.

Read walkthroughs assert business facts unchanged. Mutation walkthroughs check all
four reconciliation services, movement deltas against physical stock, preserved
original sale headers and the original historical-data hash. This does not prove
MySQL locking or performance under production concurrency.

## Functional limits and owner decisions

- T26 locally explains observed risks with citations. Only the exact documented
  inventory-urgency question receives the new deterministic coverage policy.
  Other questions retain bounded provider-independent orchestration. No approved
  rule ranks stock shortage against expiry, so no winning branch is invented.
- Local deterministic explanations and fake-provider demonstrations are labeled.
  Another live Gemini request needs specific approval. Sending synthetic tool
  evidence for model-authored synthesis needs a separate privacy/data/budget
  contract; real business-data transmission is not enabled.
- T27 works on supported newly created disposable SQLite schemas. Existing-target
  activation and MySQL migration/locking/concurrency acceptance remain blocked on
  explicit approval and a separately authorized disposable MySQL target.
- Legacy notify-manager, reassign-tasks and request-replacement endpoints retain
  truthful 501 responses. No messaging/task service or sending authorization was
  introduced. Self-service password recovery remains unavailable and is disclosed
  at sign-in. A full audit-log exploration product is not implemented; existing
  scoped recent operations and document histories remain available.
- Forecast numeric/advisory output is synthetic/provisional. No source-data rights,
  real-world model accuracy, calibrated confidence or automatic replenishment is
  inferred. No external dataset was downloaded.

These are boundaries of the approved development product, not hidden completed
features. The final verification/handoff records release and accessibility limits.
