# InventraIQ product design system

The transformation keeps the Inventory Aperture mark and the Flask/Jinja/vanilla JavaScript stack. The shared presentation is `static/css/product.css`, loaded after the existing contract stylesheet in both `base.html` and `auth-base.html`. Existing component signatures, form names, server routes, CSRF fields, progressive-enhancement attributes and dialog/navigation hooks are preserved. No remote font, stock imagery or frontend dependency was added.

## Product direction

An operational evidence desk: warm limestone foundations, crisp white data surfaces, quiet olive context and a restrained emerald action color. Company numbers read as a ledger; exceptions read as a queue. Screens use their actual purpose rather than repeating identical cards. Authentication explains the observation → evidence → decision workflow without fabricated metrics. The sidebar Aperture identity remains unchanged.

## Tokens

| Token | Value / purpose |
| --- | --- |
| `--canvas` | `#f5f4ef`, warm neutral workspace |
| `--surface` | `#ffffff`, readable data/form surfaces |
| `--surface-subtle` | `#eeede6`, supporting context |
| `--ink` / `--muted` | `#242d29` / `#59645d`, primary and secondary text |
| `--line` / `--line-strong` | `#dedfd5` / `#7c8678`, structure and control boundaries |
| `--emerald` / `--emerald-dark` | `#08725c` / `#075844`, primary actions and focus |
| `--emerald-soft` | `#e7f1e9`, positive context |
| `--danger` / `--danger-soft` | `#a0332a` / `#fbefeb`, consequential/error states |
| `--amber` / `--amber-soft` | `#815719` / `#fbf2df`, review/expiry risk |
| Typography | Local Segoe UI Variable/Segoe UI/system sans; no network font fetch |
| Scale | 9–11px metadata, 12–13px operational text, 15–21px sections, 27–38px page title, 29–43px ledger values |
| Spacing | Existing 4/8/12/16/24/32/48px tokens; 18–30px workspace gaps |
| Shape | 3px small states; 6px controls; 8–10px data surfaces; shadows reserved for overlays |
| Controls | 40–46px normal primary controls; existing pointer/coarse 44px minima retained where applicable |

The smaller metadata is supplementary; operational field text, exact figures, source details and touch targets remain available at zoom and narrow sizes. Actual rendered contrast and reflow need the integration browser checks; token choices alone are not an accessibility certification.

## Information architecture

| Group | Workspaces | Role behavior |
| --- | --- | --- |
| Monitor | Overview, Attention | HQ company overview; branch operational overview; scoped risk queues |
| Operate | Inventory, Sales, Receive stock, Transfers, Purchasing | Receiving shown only to manager/HQ admin. Transfers/purchasing shown only to manager/HQ. HQ analyst review only. Clerk keeps inventory/sales. |
| Intelligence | AI intelligence, Analytics, HQ Reports | Snapshots, evidence, recorded data and limitations remain explicit |
| Manage | Shared catalog, Settings / My workspace | Catalog reads are shared; settings/access maintenance is HQ admin only |

The top bar shows company/branch context, a persistent authorized scope selector, assistant access and the actual database-resolved role. HQ analysts see explicit “Read only” context. Choosing a portal or changing the displayed scope never grants authority. Server-side authorization still resolves every operation. Access administration remains discoverable from Settings. The T27 proposal entry remains capability/configuration gated in the assistant workflow.

## Component conventions

| Component | Contract / use |
| --- | --- |
| `page_header`, `section_header` | One page `h1`; editorial eyebrow; concise operational description; actions grouped separately |
| `.metrics-strip` | Three headline facts with thin separators. At 320px facts become rows with right-aligned values; no omitted metrics |
| `.summary-ledger` | Responsive columns for record/catalog totals; inside `.workspace-aside`, compact stacked label/value rows |
| `.workspace-grid`, `.workspace-main`, `.workspace-aside` | Operational workspace plus supporting policy/context; collapses without losing controls |
| `.work-section`, `.section-heading` | Plain work surfaces with purposeful headings; tables/queues provide their own contained surfaces |
| `.attention-list`, `.attention-row` | Actionable inventory exceptions; label, evidence and inspect link; stacked on narrow viewports |
| `.table-scroll`, `.table-container` | Bounded horizontal/vertical scrolling. Existing named, keyboard-focusable regions and captions remain authoritative |
| `.form-grid`, `.form-stack`, `.form-actions` | Aligned field groups; explicit required labels; native validation and associated hints/errors retained |
| `.workflow-steps` | Existing workflow explained as numbered stages with helper text; no state implied without service data |
| `.workflow-action`, `.workflow-create` | Progressive disclosure of consequence-specific forms, preserving revisions/idempotency and submission recovery |
| `.scope-banner`, `.workflow-note` | Compact scope/policy context, subordinate to operational work |
| `.chart-grid`, `.chart-panel`, `.chart-plot` | Server-backed SVG charts, exact accessible source tables. Named horizontal plot region preserves label size at 320px |
| `.assistant-answer`, `.assistant-findings`, `.assistant-source` | Human-readable local evidence, provenance/scope/timestamps; technical JSON remains secondary |
| `.notice`, `.badge`, `.status-label` | Text plus semantic state; color is not the sole indicator |
| `.demo-provenance` | Persistent synthetic scenario and pinned date disclosure; simulated money is never called real revenue |
| `.error-page`, `.service-state` | Honest restricted/missing/unavailable state, next action and sanitized support reference |

Data tables retain exact values and drill-down forms. No chart replaces source access, and no decorative chart receives invented data. The assistant shell and human proposal UI continue to distinguish evidence, suggestions and confirmed business actions.

## Responsive and interaction design

Desktop uses a 224px fixed rail; at 1000px and below the existing modal navigation handles focus containment, inert background, Escape and focus return. The visual system does not replace that behavior. At tablet widths the supporting workspace becomes a section below the main work. At 480px and below forms become one column, risk items stack, the metric ledger becomes rows, and the assistant uses the full viewport width. Navigation, forms and precise tables remain reachable at 320px.

Focus uses a visible 3px outline. Reduced-motion preferences disable decorative transitions and repeated animation. Forced-colors receives explicit boundaries and selection outlines. Print removes navigation and preserves data. No motion is required to understand state. The loading, validation, native dialogs, disclosure actions and recoverable server feedback use the existing enhancement layer.

## Focused verification

Executed with the repository Windows virtual environment on new disposable SYN-T15 fixtures:

- 5 new `test_transformation_shell.py` tests: analyst/clerk/manager/admin navigation, read-only context, branch-selector isolation, current-page markers, CSRF, password-manager and auth landmark contracts.
- 9 existing `test_design_accessibility.py` tests: unique IDs/landmarks, labels, captioned tables, preserved guard fields, safe errors/flashes and field associations.
- 9 existing `test_t24_presentation.py` tests: scope/privacy, real service figures, disclosure ordering and submission contracts.
- 8 existing `test_workspace_accessibility.py` JavaScript behavior checks: navigation/dialog focus, resize, validation and safe disclosure dismissal.

These 31 checks passed. They are semantic/security and mocked interaction evidence. They do not replace screenshots, real browser reflow, actual assistive-technology checks or end-to-end business workflow verification. Final integrated browser evidence, regression results and remaining limitations are recorded in the transformation integration report. See `TRANSFORMATION_ROUTES.md` for every registered Flask rule and workspace mapping.
