# InventraIQ UI/UX redesign

## Copy and navigation simplification

Analytics and Reports have been consolidated into **Analytics**. The combined
workspace contains the stock and sales charts, branch sales/refund totals, and CSV
export. `/reports` remains a compatibility alias with the same Analytics title,
content, and active navigation item. It is no longer a separate menu destination.

Decorative section labels, page taglines, marketing footers, backend definitions,
repeated disclaimers, and redundant instructions have been removed. Generated
retry tokens remain hidden form fields. HQ now has a small, open planning review
on the overview, with the full evidence in Planning. Essential expiry, quarantine, pricing, and demo-data
information remains concise and visible where needed.

The redesign treats InventraIQ as a working inventory application: understand the
current position, spot exceptions, then open the appropriate workflow. It replaces
the previous warm-neutral presentation and its oversized stack of equally weighted
sections with a blue-and-slate product system and a more deliberate hierarchy.

## What changed and why

| Decision | Reason |
| --- | --- |
| White navigation and data surfaces on a cool slate canvas | Separates persistent navigation, content, and individual work areas without heavy borders or decoration. |
| Blue for navigation and primary actions | Makes the next action recognizable and leaves green, amber, and red available for stock conditions. |
| Consistent typography, spacing, radii, and controls | Gives unrelated operational screens the same rhythm and makes the app feel like one product. Body text and data labels are more readable than the previous small presentation. |
| Four overview metrics | Provides a quick orientation: available stock, recorded net sales, low-stock SKUs, and incoming transfers. Current inventory and dated sales retain explicit labels. |
| A compact, linked risk summary | Keeps expiry and stock exposure visible without making every exception a full-width card above the rest of the dashboard. |
| Actual stock and sales charts with exact-data disclosures | Supports scanning while preserving accessible tables and precise reconciliation. No invented trends, percentages, or financial estimates are introduced. |
| A four-record priority queue with links to the full list | Gives the overview a bounded length. Each item opens the relevant lot or SKU in its authorized branch. |
| A separate work-in-motion panel | Makes transfers and orders visible beside the priority queue instead of burying them below long exception lists. Clerks get appropriate daily links. |
| Open HQ planning and supply availability; supporting evidence and activity in disclosures | Makes decision inputs prominent while keeping supporting detail close to its context. Synthetic-data and assistant limitations remain explicit. |
| Workspace search with Ctrl+K / Command+K | Reduces navigation effort for frequent users. Destinations come from the authorized sidebar, so the search respects the same role visibility. |
| Navy sign-in panel and clearer access choices | Establishes a recognizable product identity before entry while keeping the form itself simple and familiar. |
| Responsive layouts and contained table scrolling | Keeps navigation and forms usable on phones; dense tables scroll inside named regions rather than widening the page. |

## Interaction and accessibility

### Surface boundaries and numeric hierarchy

White cards, work areas, charts, and table containers use a stronger slate border.
Summary strips now contain individual tinted, bordered metric tiles, with bold
navy figures and quieter labels. Planning and assistant metrics share that
treatment. Numeric displays use lining, tabular digits; table values align to the
right, and important totals receive stronger emphasis. Units and currency prefixes
are smaller than amounts. Text-only summary values retain a compact text size.
Mobile summaries use two columns with reduced padding and allow long values to wrap.

- Navigation remains grouped by workspace, operations, intelligence, and organization.
- The current page is marked with `aria-current`; branch scope is a labeled,
  explicit POST action.
- Search is a native dialog with a labeled combobox and listbox. Arrow keys choose
  a result, Enter navigates, Escape dismisses, and focus returns to the opener.
- Mobile navigation contains focus, makes the background inert, and returns focus
  when dismissed. The header assistant is available on mobile and uses a
  full-screen dialog with focus return.
- Form labels, password-manager attributes, server validation, CSRF tokens,
  revisions, and submission-retry protection remain in place.
- Focus outlines, reduced-motion settings, forced colors, and print layouts are
  covered by the shared stylesheet.
- Charts use different line styles and exact-data tables in addition to color.

## Implementation

`static/css/product.css` is the shared product layer loaded after the existing
component foundations. `static/css/planning.css` supplies the HQ planning, input
guidance, and assistant components. The canonical brand source regenerates all
seven SVG variants together. Its historical `emerald` key is a compatibility name;
its current value is the product blue.

`templates/components/overview.html` owns the overview composition. Other operational
screens retain their working forms and data readers and use the same shared visual
system. `static/js/command-palette.js` adds navigation search without a framework,
remote dependency, or hard-coded permission list.

## Review and verification

The review uses newly created SYN-T15-V1 databases, never an existing database.
Screenshots and detailed browser results are generated beneath the ignored
`data/private/ui-redesign/` directory.

Run the connected review with a working local Python environment and existing
Chrome or Edge:

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_ui_redesign.py
```

If Windows application control blocks native scientific-library imports, the
review can use a WSL interpreter with the project dependencies installed. The
default `--wsl` interpreter is `python3` on WSL PATH; select a virtual environment
explicitly with `--wsl-python` when needed:

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_ui_redesign.py --wsl --wsl-python /path/to/venv/bin/python
```

The WSL bridge sends temporary synthetic login credentials through process pipes
only, shuts down its temporary backend, and checks that business facts remain
unchanged. It runs the backend from the current release directory and does not install dependencies or repair a host dependency-policy issue. The historical `--before` comparison requires a private before-source capture and is not reproducible from the source-only release.

### Verified results

- **Passing regression checks:** connected shell, view, accessibility,
  presentation, reporting, and operational-workspace checks, including the
  Analytics compatibility alias and hidden retry tokens, plus recommendation
  quantity parity, assistant scope authorization, and branch-name presentation.
  The expanded suite passed 138 checks; the remaining branch-context assertion
  was corrected to expect the existing HTTP 403 contract and passed in a focused
  13-check rerun.
- **91 passing browser page/viewport checks** in installed Chrome, across 320,
  390, 768, 1024, and 1440 pixel widths for signed-in pages, plus desktop and
  mobile access screens. The review covers HQ administrator, HQ analyst, branch
  manager, and clerk views.
- **9 passing interaction groups:** role-scoped search and dialog/navigation
  behavior for all four roles, including arrow-key selection, Enter navigation,
  Ctrl+K, empty results, Escape, focus return, and opening the assistant from
  the mobile header, plus Analytics assistant date and branch parity.
- Browser-computed checks passed for sampled text contrast, control boundaries,
  form labels, and document overflow. No JavaScript page errors were observed.
- Business facts reconciled before and after the review without changes.
- Canonical SVG generation and JavaScript syntax checks passed.

Representative local screenshots: `data/private/ui-redesign/after/901-overview-1440.png`,
`901-overview-390.png`, `901-receiving-1440.png`, and `sign-in-1440.png`.
The full browser result is `data/private/ui-redesign/after/verification.json`.

The current planning and assistant decisions, research links, and data boundaries
are documented in [HQ_PLANNING_REDESIGN.md](HQ_PLANNING_REDESIGN.md).
