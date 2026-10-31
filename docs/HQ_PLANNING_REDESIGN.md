# Headquarters planning and assistant redesign

## Product decisions

Analytics answers **what happened**: recorded stock, sales, refunds, branch comparisons,
and CSV export. Reports duplicated that purpose, so it is a compatibility alias for
Analytics rather than another destination.

Planning answers **what should HQ review next**: demand evidence, usable stock,
incoming supply, supplier policy, and replenishment checks. The overview surfaces a
bounded sample of branch/product reviews and open supply reconciliation. Full
planning evidence stays one click away. These examples are not an urgency ranking.
HQ plans; branches carry out their authorized operational work.

The demo branches are Central, Eastside, and Westside. Display aliases cover the old
demo labels without overwriting custom operational names or changing branch IDs.

The sidebar density setting and promotional assistant card were removed. The
assistant is available in the header and beside decisions, where it can review
the selected branch, product, or reporting period.

## Research and rationale

- [GOV.UK question pages](https://design-system.service.gov.uk/patterns/question-pages/)
  recommends asking only necessary questions and explaining why information is
  needed. Entry fields therefore have visible required/optional status; decision
  reasons, delivery references, quantities, and evidence have short purpose hints.
  Reason and inspection evidence receive stronger visual treatment. Conditional
  expiry requirements are labeled accurately rather than described as optional.
- [IBM Carbon for AI](https://www.carbondesignsystem.com/building-blocks/foundations/carbon-for-ai)
  emphasizes identifiable AI and contextual explanations. The assistant presents a
  structured planning brief, with facts and recommendation checks first and exact
  sources available in expandable evidence. Contextual actions replace the old
  decorative sidebar card.
- [Nielsen Norman Group on explainable AI](https://www.nngroup.com/articles/explainable-ai/)
  informs the separation of observable facts, estimates, missing inputs, and sources.
  Missing forecasts produce “Not ready” or “Inputs needed,” never an invented zero.
  A genuine calculated zero remains zero.

## Data and interaction boundaries

The current assistant is a clearly labeled **local deterministic demo**, not a live
AI model. It reads authorized server tools. This redesign does not activate an
external provider, generate forecasts, approve orders, or alter stock. Numeric
recommendations retain the existing qualified synthetic-environment gate.

Analytics passes its actual date range to the assistant. Page branch context and
explicit branch/product prompts narrow reads; server authorization remains decisive.
Recommendations retain their decision identifiers and exact quantities. The
assistant's brief exposes missing inputs and links back to the relevant review.

The chatbot now has a visible scope, four useful starting prompts, a labeled
composer, structured metric/recommendation cards, expandable sources, cancellation,
and conversation reset. It uses a native dialog, keyboard dismissal, focus return,
and a full-screen mobile layout.

## Verification

`scripts/verify_ui_redesign.py --wsl` (with optional `--wsl-python` interpreter selection) uses disposable synthetic data to check page
layouts, form semantics, sampled contrast, overflow, role-scoped navigation, assistant
interactions, and Analytics date/scope parity. It compares business facts before and
after. Screenshots and machine-readable results are saved in the ignored
`data/private/ui-redesign/after/` directory.

Regression coverage includes recommendation quantity parity, missing-input handling,
scope authorization, conversation behavior, reporting, template rendering, and
accessibility. No existing operational database is used for the review.
