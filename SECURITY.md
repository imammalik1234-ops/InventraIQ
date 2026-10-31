# Security and operational boundaries

InventraIQ is a local portfolio candidate, without production security certification. Use loopback demos and synthetic inputs. Production ingress, trusted-host policy, capacity and operational recovery require separate review.

## Authentication and authorization

Flask-Login sessions use current account state and revocation checks. Login rotates session/CSRF state; disabled accounts and replayed revoked sessions are rejected. Shared database-backed login throttling is implemented. Cookies use HttpOnly and SameSite=Lax. Local HTTP demo cookies intentionally omit Secure; production requires HTTPS, secure cookies and a stable secret of at least 32 characters. Private responses are not cacheable. The application does not configure a trusted-host allowlist.

Explicit HQ grants and active branch assignments determine business capabilities; legacy admin/staff portal roles alone grant no business scope. HQ analysts are read-only. Branch managers and clerks are constrained by assignments and action capabilities. Transfers retain independent approval. See [permissions](T05_PERMISSION_INVENTORY.md).

Unsafe requests require session-bound CSRF, including login/logout and JSON writes. Inputs enforce bounded quantities, decimal prices, dates and revision semantics. Current domain services and the inventory ledger enforce authorization, immutable committed facts and transactional rollback. Audit records are application evidence, not external tamper-proof storage.

## Legacy Step 4 contract

`inventory_transactions.py` retains the product-stock compatibility workflow: lock product before inventory, use conditional revision updates, reject stale absolute edits and prevent over-deduction. Stock, sale and audit mutations commit together or roll back. This earlier Step 4 contract does not enumerate every current domain service or grant.

## Configuration and migrations

Real environment files, credentials, databases and evidence are excluded from the public copy. `.env.example` has safe development defaults and a blank secret. Keep persistent secrets private. Production configuration additionally requires MySQL with trusted CA/hostname verification and debug disabled; these guards do not certify a deployed service.

Startup does not migrate existing data. Historical `step4_mysql.sql` is not a current universal upgrade. Protected migration artifacts must retain raw bytes. Prior development evidence records fresh MySQL migration and recovery rehearsals; those results are historical and are not rerun against retained targets for this release. The public SQLite checks do not establish MySQL isolation or arbitrary-host installation. See [setup and operator limitations](docs/LOCAL_SETUP.md) and [migration compatibility](migrations/PHASE1_SINGLETON_COMPATIBILITY.md).

## Assistant boundary and testing

The default provider is fake and proposals are disabled. Optional Gemini is synthetic-only bounded planning with local evidence rendering, strict scope/schema checks and no retry or paid fallback. See [controls](T26_STAGE_B_GEMINI.md). No external Gemini request is needed for verification.

Focused public tests cover configuration, authentication, session/CSRF, scope, transaction rollback, concurrency and mocked provider failures. Use the disposable SQLite commands in [local setup](docs/LOCAL_SETUP.md). No production penetration test, MFA, external audit storage or real-data security acceptance is claimed. The owner identifies historical SmartShelf credentials as local development/demo credentials; no independent provider-side rotation or revocation was verified. See [attribution and credential provenance](ATTRIBUTION.md).
