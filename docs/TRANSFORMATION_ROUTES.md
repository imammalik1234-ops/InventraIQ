# Transformation route inventory

Generated from the actual Flask url_map on a newly initialized disposable SYN-T15 target. No existing database was opened or migrated. HEAD/OPTIONS are omitted from the method column. Static asset expansion is represented by its Flask rule.

This is source and render coverage, not a browser-verification claim. Final browser evidence is recorded separately by the integration verifier. All rendered routes use the shared Aperture shell/design layer, including compatibility templates. Runtime permission checks remain authoritative.

## Workspace and template map

| Workspace | Entry and composition | Role-specific controls |
| --- | --- | --- |
| Access | /roles, /login/admin, /login/staff â†’ auth-base + login | Portal is presentation; grants are checked at sign-in. |
| Overview / attention | /admin-dashboard, /dashboard, /admin-alerts, /expiry-alerts â†’ pages/operations | HQ consolidated review; branch users see assigned scope. No clerk purchasing exposure. |
| Inventory | /admin-inventory, /inventory-page, /sku-inventory and inventory aliases â†’ operations components | Scoped lots and risks; receipt/adjustment controls only HQ admin/manager. |
| Sales | /sales read overview; /branch-sales priced transactions | HQ analyst read-only; clerk/manager/HQ admin sale creation; manager/HQ admin refunds. |
| Purchasing / transfers | purchasing.html, transfers.html | HQ analyst review; stage and role controls follow server services. Maker-checker preserved. |
| Catalog / access | sku_catalog.html, branch_administration.html, admin-settings.html | Shared reads; HQ admin catalog and access edits. |
| Intelligence | pages/forecast_unavailable.html + forecast/recommendation components | Scoped snapshot/previews; unavailable/withheld states remain explicit. |
| Assistant / proposals | shared assistant drawer; assistant_proposals.html | Authorized T25 evidence; feature-gated sealed proposals with explicit human confirmation. |
| Errors / scope | error.html, service_unavailable.html, scope.html | Status, support reference, active assignment boundaries; no authority from client UI. |

## Render and transport coverage

Rendered workspaces receive product.css, full shared control/table/form/state treatment and task-specific layouts. Compatibility routes keep their existing behavior and the same shell. JSON APIs and exports are intentionally not visual pages; they are mapped below to their protected service entry points. Unavailable messaging/task actions remain truthful 501 responses. T27 activation is limited to approved fresh disposable schemas; existing-database and MySQL acceptance remain separate.

Total Flask rules: 124.

## Rendered workspaces (25)

| Rule | Methods | Endpoint | Effective template / surface | Authorization contract | Notes |
| --- | --- | --- | --- | --- | --- |
| /admin-alerts | GET | admin_alerts | pages/operations.html (Alerts) | read: alerts.read |  |
| /admin-dashboard | GET | admin_dashboard | pages/operations.html (Dashboard) | read: inventory.read | HQ overview; consolidated or selected branch report. |
| /admin-inventory | GET | admin_inventory | pages/operations.html (Inventory) | read: inventory.read |  |
| /admin-settings | GET,POST | admin_settings | admin-settings.html | read: profile.manage; write: profile.manage | HQ admin profile, scoped exports and organization-access entry. |
| /ai-predictions | GET | ai_predictions | pages/forecast_unavailable.html | read: predictions.read | Published forecast snapshots/replenishment previews; explicit unavailable state. |
| /analytics | GET | analytics | pages/operations.html (Analytics) | read: reports.read |  |
| /assistant/proposals | GET | assistant_proposals.page | assistant_proposals.html | read: inventory.read | Feature gated. Deterministic local proposal intake; no external AI write access. |
| /assistant/proposals/<key> | GET | assistant_proposals.review | assistant_proposals.html | read: inventory.read | Feature gated. Sealed human review; server rechecks capability/revision/expiry. |
| /branch-administration | GET,POST | branch_administration | branch_administration.html | admin: assignments.manage | HQ admin-only branches and grants; affected sessions revoked. |
| /branch-sales | GET | sales_native.sales_page | branch_sales.html | read: sales.read | Priced transactions, local creation/refunds only where capability permits. |
| /catalog | GET | catalog.catalog_page | sku_catalog.html | read: inventory.read | Shared SKU/category/assortment directory; edit controls HQ admin only. |
| /dashboard | GET | home | pages/operations.html (Dashboard) | read: inventory.read | Branch overview; scoped operational report. |
| /expiry-alerts | GET | expiry_alerts_page | pages/operations.html (Alerts) | read: alerts.read |  |
| /inventory-page | GET | inventory_page | pages/operations.html (Inventory) | read: inventory.read |  |
| /login/admin | GET,POST | admin_login | admin_login.html â†’ pages/login.html | public transport; login/logout retain CSRF/session checks | HQ sign-in; assigned HQ grants checked server-side. |
| /login/staff | GET,POST | staff_login | staff_login.html â†’ pages/login.html | public transport; login/logout retain CSRF/session checks | Branch sign-in; assigned branch role checked server-side. |
| /purchasing | GET | purchasing.page | purchasing.html | read: purchasing.read | Detail before queue; supplier/order actions by capability. |
| /receiving | GET,POST | catalog.receiving_page | receiving.html | read: stock.receive; write: stock.receive | Manager/HQ receipt and physical adjustment forms; stock services remain authoritative. |
| /reports | GET | reports | pages/operations.html (Reports) | read: reports.read |  |
| /roles | GET | role_selection | role_selection.html | public transport; login/logout retain CSRF/session checks | Portal selector; no role grant. |
| /sales | GET | sales | pages/operations.html (Sales) | read: sales.read |  |
| /scope | GET,POST | scope_page | scope.html | authenticated active assignments | Authorized scope selector; POST changes session context only. |
| /sku-inventory | GET,POST | catalog.stock_page | pages/operations.html (Inventory) | read: inventory.read; write: sales.create | Lot inventory alias intercepted by operational DTO reader. |
| /staff | GET | staff | pages/operations.html (Staff) | read: inventory.read | My workspace; current authorized scope and operational roster. |
| /transfers | GET | transfers.page | transfers.html | read: transfer.read | Detail before queue; maker-checker and stage-specific action controls. |

## Compatibility pages and redirects (12)

| Rule | Methods | Endpoint | Effective template / surface | Authorization contract | Notes |
| --- | --- | --- | --- | --- | --- |
| / | GET | index | redirect â†’ authorized overview / portal | public transport; login/logout retain CSRF/session checks |  |
| /add-product | GET,POST | add_product | add-product.html | read: catalog.manage; write: catalog.manage |  |
| /admin-add-product | GET,POST | admin_add_product | admin-add-product.html | read: catalog.manage; write: catalog.manage |  |
| /admin-edit-product/<int:product_id> | GET,POST | admin_edit_product | admin-edit-product.html | read: catalog.manage; write: catalog.manage |  |
| /admin-record-sale | GET | admin_record_sale | redirect â†’ /branch-sales | read: sales.create | Redirect to priced sales workflow. |
| /alerts/expiring-soon | GET | expiring_soon_page | pages/operations.html (Alerts) | read: alerts.read |  |
| /archived-products | GET | archived_products | archived_products.html | read: inventory.read |  |
| /edit-product/<int:product_id> | GET,POST | edit_product | edit-product.html | read: catalog.manage; write: catalog.manage |  |
| /inventory/all-products | GET | all_products_page | pages/operations.html (Inventory) | read: inventory.read |  |
| /inventory/low-stock | GET | low_stock_page | pages/operations.html (Inventory) | read: inventory.read |  |
| /inventory/reorder-needed | GET | reorder_needed_page | pages/operations.html (Inventory) | read: inventory.read |  |
| /record-sale | GET | record_sale_page | redirect â†’ /branch-sales | read: sales.create | Redirect to priced sales workflow. |

## Read APIs and exports (30)

| Rule | Methods | Endpoint | Effective template / surface | Authorization contract | Notes |
| --- | --- | --- | --- | --- | --- |
| /admin-settings/export-data | GET | export_data | â€” | read: export.read | Authorized branch-summary CSV; no legacy archive dump. |
| /alerts | GET | get_alerts | â€” | read: alerts.read | Operational expiry/stock alert DTO. |
| /api/assistant/conversations/<key> | GET | assistant_conversations.history | â€” | read: inventory.read | Local conversation service; scoped tool dispatch, budgets, cancel/forget. Provider disabled/fake unless configured. |
| /api/assistant/tools | GET | assistant_tools.catalog_route | â€” | read: inventory.read | T25 allowlist, schema validation, scoped authorization, bounded evidence/download. |
| /api/branch-stock | GET | branch_stock_api | â€” | source-specific guard; see handler | Operational lot DTO; branch scope enforced. |
| /api/categories | GET | catalog.category_list_api | â€” | read: inventory.read |  |
| /api/forecast-data | GET | operations.forecast_data | â€” | read: predictions.read |  |
| /api/forecast-monitoring | GET | operations.forecast_monitoring | â€” | read: predictions.read |  |
| /api/forecast-snapshots | GET | operations.forecast_snapshots | â€” | read: predictions.read |  |
| /api/observations | GET | operations.history_api | â€” | read: reports.read |  |
| /api/observations/summary | GET | operations.history_summary | â€” | read: reports.read |  |
| /api/operations | GET | operations.api | â€” | read: reports.read |  |
| /api/predictions | GET | api_predictions | â€” | read: predictions.read | Published snapshot read; no HTTP model fitting. |
| /api/priced-sales | GET | sales_native.list_api | â€” | read: sales.read |  |
| /api/purchase-orders | GET | purchasing.list_api | â€” | read: purchasing.read |  |
| /api/purchase-orders/<int:order_id> | GET | purchasing.detail_api | â€” | read: purchasing.read |  |
| /api/purchase-orders/reconciliation | GET | purchasing.reconciliation_api | â€” | read: purchasing.read |  |
| /api/recommendations | GET | operations.recommendations_api | â€” | read: predictions.read |  |
| /api/sku-stock | GET | catalog.stock_api | â€” | read: inventory.read | Operational lot DTO; branch scope enforced. |
| /api/skus | GET | catalog.sku_list_api | â€” | read: inventory.read |  |
| /api/stock-lots/<int:lot_id> | GET | lot_api | â€” | source-specific guard; see handler |  |
| /api/suppliers | GET | purchasing.suppliers_api | â€” | read: purchasing.read |  |
| /api/transfers | GET | transfers.list_api | â€” | read: transfer.read |  |
| /api/transfers/<int:transfer_id> | GET | transfers.detail_api | â€” | read: transfer.read |  |
| /api/transfers/reconciliation | GET | transfers.reconciliation_api | â€” | read: transfer.read |  |
| /assistant/artifacts/<artifact_id> | GET | assistant_tools.download | â€” | read: export.read | T25 allowlist, schema validation, scoped authorization, bounded evidence/download. |
| /inventory | GET | get_inventory | â€” | read: inventory.read | Operational lot DTO; legacy endpoint contract retained. |
| /operations/export.csv | GET | operations.csv_export | â€” | read: export.read |  |
| /operations/observations.csv | GET | operations.history_export | â€” | read: export.read |  |
| /products | GET | get_products | â€” | read: inventory.read |  |

## Mutations and authenticated orchestration (56)

| Rule | Methods | Endpoint | Effective template / surface | Authorization contract | Notes |
| --- | --- | --- | --- | --- | --- |
| /admin-delete-product/<int:product_id> | POST | admin_delete_product | â€” | write: catalog.manage |  |
| /api/admin/assignments | GET,PUT | assignment_admin | â€” | admin: assignments.manage |  |
| /api/admin/branches | GET,POST | branch_admin | â€” | admin: branches.manage |  |
| /api/admin/branches/<int:branch_id> | PATCH | branch_admin | â€” | admin: branches.manage |  |
| /api/admin/hq-scopes | PUT | hq_scope_admin | â€” | admin: assignments.manage |  |
| /api/assistant/conversations | POST | assistant_conversations.create | â€” | write: inventory.read | Local conversation service; scoped tool dispatch, budgets, cancel/forget. Provider disabled/fake unless configured. |
| /api/assistant/conversations/<key>/cancel | POST | assistant_conversations.cancel | â€” | write: inventory.read | Local conversation service; scoped tool dispatch, budgets, cancel/forget. Provider disabled/fake unless configured. |
| /api/assistant/conversations/<key>/forget | POST | assistant_conversations.forget | â€” | write: inventory.read | Local conversation service; scoped tool dispatch, budgets, cancel/forget. Provider disabled/fake unless configured. |
| /api/assistant/conversations/<key>/messages | POST | assistant_conversations.send | â€” | write: inventory.read | Local conversation service; scoped tool dispatch, budgets, cancel/forget. Provider disabled/fake unless configured. |
| /api/assistant/proposals | POST | assistant_proposals.create | â€” | write: inventory.read | Feature gated; human review/ownership and action capability enforced. |
| /api/assistant/tools/<name> | POST | assistant_tools.invoke_route | â€” | write: inventory.read | T25 allowlist, schema validation, scoped authorization, bounded evidence/download. |
| /api/assortments/<int:branch_id>/<int:sku_id> | PUT | catalog.assortment_api | â€” | admin: catalog.manage |  |
| /api/categories | POST | catalog.category_create_api | â€” | admin: catalog.manage |  |
| /api/expiry-policies/<kind>/<int:key> | DELETE,PUT | catalog.policy_api | â€” | admin: expiry_policy.manage |  |
| /api/expiry-settings | PUT | catalog.settings_api | â€” | admin: expiry_policy.manage |  |
| /api/lot-corrections/<int:lot_id> | PATCH | catalog.lot_correction_api | â€” | admin: batch.correct |  |
| /api/priced-sales | POST | sales_native.create_api | â€” | write: sales.create |  |
| /api/priced-sales/<int:sale_id>/refunds | POST | sales_native.refund_api | â€” | write: sales.refund |  |
| /api/priced-sales/<int:sale_id>/reversal | POST | sales_native.reversal_api | â€” | write: sales.refund |  |
| /api/purchase-orders | POST | purchasing.create_api | â€” | write: purchasing.manage |  |
| /api/purchase-orders/<int:order_id>/<action> | POST | purchasing.action_api | â€” | write: purchasing.read |  |
| /api/receipts | POST | catalog.receipt_api | â€” | write: stock.receive |  |
| /api/scope | GET,POST | scope_selection | â€” | authenticated active assignments |  |
| /api/sku-sales | POST | catalog.native_sale_gate | â€” | write: sales.create |  |
| /api/skus | POST | catalog.sku_create_api | â€” | admin: catalog.manage |  |
| /api/skus/<int:sku_id> | PATCH | catalog.sku_update_api | â€” | admin: catalog.manage |  |
| /api/stock-adjustments | POST | catalog.adjustment_api | â€” | write: stock.adjust |  |
| /api/stock-reservations | POST | catalog.reservation_api | â€” | write: stock.adjust |  |
| /api/stock-reservations/fulfil | POST | catalog.reservation_sale_api | â€” | write: stock.adjust |  |
| /api/stock-reservations/release | POST | catalog.release_api | â€” | write: stock.adjust |  |
| /api/suppliers | POST | purchasing.supplier_create_api | â€” | admin: supplier.manage |  |
| /api/suppliers/<int:supplier_id> | PATCH | purchasing.supplier_update_api | â€” | admin: supplier.manage |  |
| /api/transfers | POST | transfers.create_api | â€” | write: transfer.propose |  |
| /api/transfers/<int:transfer_id>/<action> | POST | transfers.action_api | â€” | write: transfer.read |  |
| /assistant/proposals | POST | assistant_proposals.create | â€” | write: inventory.read | Feature gated; human review/ownership and action capability enforced. |
| /assistant/proposals/<key>/cancel | POST | assistant_proposals.cancel | â€” | write: inventory.read | Feature gated; human review/ownership and action capability enforced. |
| /assistant/proposals/<key>/confirm | POST | assistant_proposals.confirm | â€” | write: inventory.read | Feature gated; human review/ownership and action capability enforced. |
| /branch-sales/<int:sale_id>/refund | POST | sales_native.refund_form | â€” | write: sales.refund |  |
| /branch-sales/create | POST | sales_native.create_form | â€” | write: sales.create |  |
| /catalog/actions | POST | catalog.catalog_action | â€” | admin: catalog.manage |  |
| /check-alerts | POST | run_alert_check | â€” | write: alerts.evaluate |  |
| /delete-product/<int:product_id> | POST | delete_product | â€” | write: catalog.manage |  |
| /logout | POST | logout | â€” | public transport; login/logout retain CSRF/session checks |  |
| /notify-manager | POST | notify_manager | â€” | write: unavailable | Explicitly unavailable (501); no message sent. |
| /products | POST | add_product_api | â€” | write: catalog.manage |  |
| /products/<int:product_id> | DELETE | delete_product_api | â€” | write: catalog.manage |  |
| /products/<int:product_id> | PUT | update_product_api | â€” | write: catalog.manage |  |
| /purchasing/<int:order_id>/<action> | POST | purchasing.action_form | â€” | write: purchasing.read |  |
| /purchasing/create | POST | purchasing.create_form | â€” | write: purchasing.manage |  |
| /purchasing/supplier | POST | purchasing.supplier_form | â€” | admin: supplier.manage |  |
| /reassign-tasks | POST | reassign_tasks | â€” | write: unavailable | Explicitly unavailable (501); no task changed. |
| /remove-stock/<int:product_id> | POST | remove_stock | â€” | write: stock.adjust |  |
| /request-replacement | POST | request_replacement | â€” | write: unavailable | Explicitly unavailable (501); no request sent. |
| /sales | POST | record_sale | â€” | write: sales.create |  |
| /transfers/<int:transfer_id>/<action> | POST | transfers.action_form | â€” | write: transfer.read |  |
| /transfers/create | POST | transfers.create_form | â€” | write: transfer.propose |  |

## Infrastructure and assets (1)

| Rule | Methods | Endpoint | Effective template / surface | Authorization contract | Notes |
| --- | --- | --- | --- | --- | --- |
| /static/<path:filename> | GET | static | â€” | public transport; login/logout retain CSRF/session checks |  |

## Boundaries and pending verification

No route enables live external AI transmission by itself. No route authorizes migration, deployment or paid provider fallback. Desktop/320px, representative tablet, keyboard/dialog, populated/empty and connected business browser results are recorded in the integration report (historical development record; omitted from this package). All route rows are source-accounted; a 200 response alone is not treated as end-to-end verification. Not every API or compatibility rule has its own browser scenario; focused/full service and HTTP suites supply their additional evidence.
