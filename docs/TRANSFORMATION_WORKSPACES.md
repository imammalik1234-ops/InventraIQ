# Operational workspace transformation

Implemented locally in the canonical Windows repository. All reads and checks use new disposable SYN-T15-V1 fixtures. No external AI calls, existing-database changes or policy changes are part of this work.

## Route and template coverage

| Workspace / routes | Implemented presentation | Preserved behavior |
| --- | --- | --- |
| `/admin-dashboard`, `/dashboard` | Distinct company and branch introductions; risk-first review queue; stock / recorded-money charts; role-safe open order and transfer queues; branch shortcuts | Scoped current balances, pinned demonstration clock, business-date money totals, provenance and data definitions |
| `/admin-inventory`, `/inventory-page`, `/inventory/all-products`, `/sku-inventory` | Compact lot table, stock summary, condition badges, lot evidence drilldown and scoped action links | Server pagination, status/search filters, stock revision checks, request keys, eligible lot adjustments |
| `/inventory/low-stock`, `/inventory/reorder-needed`, `/expiry-alerts`, `/admin-alerts`, `/alerts/expiring-soon` | Shared condition review queue with exact SKU / lot drilldown | Existing low-stock and expiry policy; no forecast-based alert thresholds |
| `/sales`, `/reports`, `/analytics` | Scoped charts and precise branch comparison tables; recorded transaction drilldown; explicit separation of stock and business-date finance | Recorded MYR values and separate refunds; unknown historical revenue excluded; authorized CSV export |
| `/staff` | Scoped assignment table in the normal operational view | Clerks see their own assignments. The retained legacy workspace also hides sale controls from read-only roles and consolidated scope |
| `/catalog` | Searchable, paged SKU table; product status and effective expiry-policy evidence; record-bound editing; disclosed administration and branch offers | Existing create/edit/archive forms, reason fields, revisions, policy replacement, stable identity, assortment and price rules |
| `/receiving` | Three-step receiving workbench, inspection checklist, new-lot form, existing-lot disclosures, recent receipt table | Observed label evidence, quarantine choice, supplier lot facts, idempotent delivery reference, original labels and branch query context |
| `/branch-sales` | New-sale workbench, checklist, selected-receipt financial summary, allocation-specific refund disclosures, history table | Original three sale lines, recorded price, FEFO allocation, idempotency, explicit saleable-return inspection, no-restock reversal and refund authorization |
| `/purchasing` | Order lifecycle, visible-page summary, selected record before queue, cost reconciliation, receipt forms and disclosed evidence / supplier maintenance | Independent approval, state-dependent actions, versions, request keys, bounded supplier / SKU lot suggestions, receipt evidence and cancellation rules |
| `/transfers` | Transfer lifecycle, selected record before queue, permitted shipment actions, progress table and reconciliation evidence | Source / destination scope, independent approval, reservation / dispatch / receipt separation, partial receipt, discrepancy and reverse-transfer contracts |
| `/ai-predictions` | Planning workspace, operational / historical clock context, compact recommendation outcomes, inspectable calculation tables and snapshot series table | Synthetic / provisional provenance, withheld quantities and reasons, versioned snapshots, no in-request fitting or automatic actions |
| `/assistant/proposals`, `/assistant/proposals/<key>` | Bounded-action guide, human review lifecycle, structured proposal facts, exact-effect callout and integrity disclosure | Native required confirmation checkbox, sealed review token, request key, expiration, cancellation and existing confirmation service; no model-generated proposal claim |
| `/branch-administration` | Branch directory, editable branch disclosures, named account / branch assignments and separate HQ permission review | Existing POST action names and server authorization, CSRF and session revocation |
| `/admin-settings` | Profile editing with export / access administration alongside it | Current-account profile update, email session revocation and scoped CSV export |
| `/add-product`, `/admin-add-product`, `/edit-product/<id>`, `/admin-edit-product/<id>` | Shared record form and accountable-change guide in the new workbench layout | Original selectors, validation, stock revision and form JS contracts |

Normal application inventory routes use `pages/operations.html` through the existing T09 route adapter. Retained pre-cutover `sku_inventory.html` and `ai_predictions.html` templates also received the coherent presentation; the ordinary forecasting route still uses `pages/forecast_unavailable.html` and its synthetic evidence contracts.

Shared navigation, typography, spacing, responsive surfaces, controls and error states live in the base/authentication shells and `static/css/product.css`. Scoped chart data lives in `services/workspace_views.py`; its accessible SVG/table presentation is `templates/components/workspace_charts.html`. This document records workspace implementation alongside those shared components.

## Components and interaction contracts

- `work-section`, `section-heading` and named `table-scroll` regions provide compact data surfaces with captions and scoped table headers.
- `workspace-grid`, `workspace-main`, `workspace-aside` organize a working form beside the relevant inspection checklist; the grid collapses on narrow screens.
- `summary-ledger`, `scope-banner`, `workflow-steps` and `workbench-toolbar` surface exact measures, context and existing next actions.
- Existing `workflow-action`, `workflow-tools`, `workflow-create`, `evidence-disclosure`, `data-enhance-form`, catalog-search and lot-mode hooks remain in use.
- Consequential actions remain original server forms. No action was replaced with a visual placeholder, and no role receives an additional mutation capability.

## Focused verification

| Verification | Result |
| --- | --- |
| `test_t24_presentation.py` | 9 passed; report / export values, branch isolation, optional lines, role-safe actions, selected record order, revision and request fields |
| `test_design_accessibility.py` | 9 passed; one main / h1, unique IDs, labeled controls, named scroll regions, table headers, CSRF and scope boundaries |
| `test_transformation_workspaces.py` | 4 passed; SKU row / edit binding, directory and planning semantics, receipt branch context, allocation refund controls and read-only rendering |
| Jinja parser | All 18 changed workspace templates parsed successfully |

Each fixture's final business facts matched its pre-render baseline. These focused tests verify structure and integration, not browser appearance or WCAG certification. Final browser coverage, screenshots, performance and regression results are recorded by the integrated Gate C verification; do not infer them from HTTP success or this document.

## Deliberate limitations

- The proposal feature remains controlled by its existing development feature gate; this transformation does not activate or migrate it on existing databases.
- Planning remains synthetic and provisional. Natural-language model generation and any external transmission of tool results require separate authorization and evidence.
- No urgency ranking, confidence score, sales amount, trend or forecast-quality claim was invented to fill the interface.
- No T28 deployment or production-readiness work is included.
