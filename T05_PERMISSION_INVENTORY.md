# T05 endpoint and service permission inventory

All business resources require an enabled, current server-authenticated user and
active database grants. Client branch selectors request a subset; they never grant
access. Every query includes an explicit branch predicate, including HQ consolidated
queries. All unsafe HTTP methods retain CSRF protection. Unknown endpoints deny.

## Role capabilities

| Capability | HQ admin | HQ analyst | Branch manager | Branch clerk |
| --- | --- | --- | --- | --- |
| Inventory, sales, alerts, reports, predictions, export reads | All active branches or selected branch | All active branches or selected branch | Selected assigned branch | Selected assigned branch |
| Consolidated read | Yes, explicit capability | Yes, explicit capability | No | No |
| Sales creation | Selected branch | No | Selected assigned branch | Selected assigned branch |
| Existing mapped-batch receipt service | Selected branch | No | Selected assigned branch | No |
| Stock adjustment (reason required) | Selected branch | No | Selected assigned branch | No |
| Catalog edit/archive; batch correction capability | Selected mapped legacy resource | No | No | No |
| Branch / assignment / HQ-scope administration | Explicit HQ scope | No | No | No |
| Expiry policy management capability | Reserved for T06 | No | No | No |
| Profile update | Own account | No | No | No |

Expiry-policy and batch-correction capabilities are separate grants in the matrix.
T05 adds no policy resolver, new SKU/lot receiving, FEFO, ledger or transfer logic.

## HTTP inventory

HEAD follows GET. OPTIONS does not perform a mutation. `selected` means one active
authorized branch; `read` permits explicit HQ consolidation. `/scope` and
`/api/scope` are authenticated self-service selection, including for accounts with
no grants so they can see the empty assignment state.

| Route | Method | Capability / boundary |
| --- | --- | --- |
| `/` | GET | `Public navigation` |
| `/add-product` | GET | `catalog.manage; read` |
| `/add-product` | POST | `catalog.manage; selected branch` |
| `/admin-add-product` | GET | `catalog.manage; read` |
| `/admin-add-product` | POST | `catalog.manage; selected branch` |
| `/admin-alerts` | GET | `alerts.read; read` |
| `/admin-dashboard` | GET | `inventory.read; read` |
| `/admin-delete-product/<int:product_id>` | POST | `catalog.manage; selected branch` |
| `/admin-edit-product/<int:product_id>` | GET | `catalog.manage; read` |
| `/admin-edit-product/<int:product_id>` | POST | `catalog.manage; selected branch` |
| `/admin-inventory` | GET | `inventory.read; read` |
| `/admin-record-sale` | GET | `sales.create; read` |
| `/admin-settings` | GET | `profile.manage; own account` |
| `/admin-settings` | POST | `profile.manage; own account` |
| `/admin-settings/export-data` | GET | `export.read; read` |
| `/ai-predictions` | GET | `predictions.read; read` |
| `/alerts` | GET | `alerts.read; read` |
| `/alerts/expiring-soon` | GET | `alerts.read; read` |
| `/analytics` | GET | `reports.read; read` |
| `/api/admin/assignments` | GET | `assignments.manage; explicit HQ admin` |
| `/api/admin/assignments` | PUT | `assignments.manage; explicit HQ admin` |
| `/api/admin/branches` | GET | `branches.manage; explicit HQ admin` |
| `/api/admin/branches` | POST | `branches.manage; explicit HQ admin` |
| `/api/admin/branches/<int:branch_id>` | PATCH | `branches.manage; explicit HQ admin` |
| `/api/admin/hq-scopes` | PUT | `assignments.manage; explicit HQ admin` |
| `/api/branch-stock` | GET | `inventory.read; scoped holdings / non-disclosing lot lookup` |
| `/api/predictions` | GET | `predictions.read; read` |
| `/api/scope` | GET | `Authenticated; validate database grants (CSRF for POST)` |
| `/api/scope` | POST | `Authenticated; validate database grants (CSRF for POST)` |
| `/api/stock-lots/<int:lot_id>` | GET | `inventory.read; scoped holdings / non-disclosing lot lookup` |
| `/archived-products` | GET | `inventory.read; read` |
| `/branch-administration` | GET | `assignments.manage; explicit HQ admin` |
| `/branch-administration` | POST | `assignments.manage; explicit HQ admin` |
| `/check-alerts` | POST | `alerts.evaluate; selected branch` |
| `/dashboard` | GET | `inventory.read; read` |
| `/delete-product/<int:product_id>` | POST | `catalog.manage; selected branch` |
| `/edit-product/<int:product_id>` | GET | `catalog.manage; read` |
| `/edit-product/<int:product_id>` | POST | `catalog.manage; selected branch` |
| `/expiry-alerts` | GET | `alerts.read; read` |
| `/inventory` | GET | `inventory.read; read` |
| `/inventory-page` | GET | `inventory.read; read` |
| `/inventory/all-products` | GET | `inventory.read; read` |
| `/inventory/low-stock` | GET | `inventory.read; read` |
| `/inventory/reorder-needed` | GET | `inventory.read; read` |
| `/login/admin` | GET | `Public authentication + login CSRF + throttle` |
| `/login/admin` | POST | `Public authentication + login CSRF + throttle` |
| `/login/staff` | GET | `Public authentication + login CSRF + throttle` |
| `/login/staff` | POST | `Public authentication + login CSRF + throttle` |
| `/logout` | POST | `Authenticated + CSRF; session removal` |
| `/notify-manager` | POST | `unavailable; selected branch` |
| `/products` | POST | `catalog.manage; selected branch` |
| `/products` | GET | `inventory.read; read` |
| `/products/<int:product_id>` | DELETE | `catalog.manage; selected branch` |
| `/products/<int:product_id>` | PUT | `catalog.manage; selected branch` |
| `/reassign-tasks` | POST | `unavailable; selected branch` |
| `/record-sale` | GET | `sales.create; read` |
| `/remove-stock/<int:product_id>` | POST | `stock.adjust; selected branch` |
| `/reports` | GET | `reports.read; read` |
| `/request-replacement` | POST | `unavailable; selected branch` |
| `/roles` | GET | `Public navigation` |
| `/sales` | POST | `sales.create; selected branch` |
| `/sales` | GET | `sales.read; read` |
| `/scope` | GET | `Authenticated; validate database grants (CSRF for POST)` |
| `/scope` | POST | `Authenticated; validate database grants (CSRF for POST)` |
| `/staff` | GET | `inventory.read; read` |
| `/static/<path:filename>` | GET | `Public local assets` |

## Service inventory

| Entry point | Permission and resource enforcement |
| --- | --- |
| `authz.resolve_scope` / `authorize` | Refresh enabled user/token/security stamp; resolve HQ/active assignment and active branch from DB; deny unknown capabilities; no accepted client Scope object |
| `authz.scoped_query` / `legacy_predicate` | SQL IN subquery over LegacyProductMapping and explicit authorized branch IDs; never a missing-filter fallback |
| `authz.legacy_resource` | Permission plus mapped branch ownership before loading/mutating legacy facts; absent and cross-branch IDs both 404 |
| `inventory_intelligence` | predictions.read, then scoped Product/Inventory/Sale queries before forecasts |
| App summaries, clusters, top-selling, dashboards, alert views | Scoped model queries and scoped aggregate predicates; no business-data cache |
| `create_product` | catalog.manage + selected scope, then unavailable (409); new identity/lot creation deferred to T06 |
| `receive_stock` | stock.receive + selected mapped legacy resource; no new lot or SKU creation |
| `edit_product` | catalog.manage + batch.correct + selected mapped resource; existing revision/transaction/audit safety |
| `deduct_stock(..., sale=True)` | sales.create + selected mapped resource; guarded quantity update and existing transaction/audit |
| `deduct_stock(..., sale=False)` | stock.adjust + selected mapped resource + nonempty bounded reason retained in audit |
| `delete_product` | catalog.manage + selected mapped resource; archive only when zero; mapped permanent deletion refused to retain evidence |
| Private stock lock/inventory/audit helpers | Recheck selected resource and writer permission; no commits |
| `check_alerts` | alerts.evaluate + selected scope, scoped Product/Inventory/Alert queries |
| Automatic low-stock/expiry alert helpers | Scoped mapped resource + writer capability; existing authorized transaction side effects |
| `manage_branch` | branches.manage; HQ-only; validate stable code/IANA timezone/status; audit in same transaction |
| `manage_assignment` / `manage_hq_scope` | assignments.manage; HQ-only; existing user IDs, role/status validation; target session revision increment + audit |
| `assignment_directory` | assignments.manage; no credential/hash fields returned |
| `select_scope` / `available_scopes` | Server identity and current grants; branch active check; explicit HQ consolidated.read; selection changes no business data |
| `stock_rows` / `lot_detail` | inventory.read; SQL scoped holdings; no unscoped receipt/provenance JSON |

## Compatibility boundaries and remaining global paths

- Old admin/staff route names are compatibility URLs, not grants. Authorized roles
  can use read aliases; mutations use capabilities rather than the old portal role.
- Legacy operational rows are visible only through explicit LegacyProductMapping.
  Unmapped rows are withheld even from HQ operational totals, never assigned to an
  implicit branch. Staff catalog forms/JSON are denied; HQ may edit/archive mapped
  legacy rows and receive an existing batch. New identity creation is unavailable.
- Current legacy Inventory and Sale remain the compatibility operational contract.
  T04 BranchStock/lot balances are foundation/review data, exposed separately at
  `/api/branch-stock`; no dual writes or claim of lot-aware operational cutover.
  New native SKU/lot sales and receiving stay disabled pending T06/T07. The receipt
  service has manager capability checks, but mixed catalog/receipt HTTP forms are
  HQ-only until a dedicated T06 receiving interface exists.
- HQ branch/user directories intentionally span metadata under explicit HQ-only
  capabilities. Identity lookup/throttling is company-wide authentication, not a
  business-data query. Offline migration/render/readiness helpers remain trusted
  operator tooling and are not exposed by HTTP. Pure forecast/rule functions accept
  already supplied data and perform no database reads.
- There are no remaining unfiltered application business-data reads. Internal stock
  updates use raw ORM predicates only after mapped-resource authorization and locks.
- No business-data cache exists. Every authorization call refreshes user/security
  state and database grants. Session branch selection is revalidated, and private
  responses retain Cache-Control: no-store. Future caches/AI tools must use these
  services and must not treat serialized Scope objects as grants.
