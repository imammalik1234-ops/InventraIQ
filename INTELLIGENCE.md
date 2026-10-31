# Intelligence and forecasting

InventraIQ separates forecasting, explicit planning policy and authorized inventory execution. Missing evidence is unavailable, never invented as zero demand or an automatic purchase quantity.

## Current scoped forecasting and planning

[Historical data](forecasting/README.md) uses authorized branch/SKU series with explicit observed, missing and exposure masks. Missing targets are not zero-filled. The [offline evaluation protocol](forecasting/EVALUATION.md) compares fixed baselines and fitted candidates chronologically. The [recorded comparison](T19_MODEL_COMPARISON.md) provisionally selects a two-week seasonal mean on repeating synthetic data; no ML candidate is promoted and no shipped profile qualifies for locked-test accuracy.

[Snapshot jobs](forecasting/SNAPSHOTS.md) persist provisional synthetic forecast batches and their provenance and integrity checks. They run explicitly in attested disposable targets, with no scheduler or real-data publication. Reads enforce permissions, source identity and freshness; unknown forecasts remain unavailable.

[Recommendation previews](services/RECOMMENDATIONS.md) apply reviewed synthetic lead/review time, expiry, inbound, safety, pack and minimum-order policy. Numeric output requires complete authorized inputs. These previews neither fit models nor move inventory. Forecast confidence probabilities and production policy qualification are not claimed.

## Retained legacy vertical slice

`ai_model.py` retains daily linear regression used by the compatibility intelligence path. It aggregates sales by product ID, fills unrecorded calendar days with zero, validates chronologically and predicts a seven-day total. This legacy zero-fill differs from the scoped historical-data contract. Minimum history is 14 calendar days with seven selling days; last activity must be within seven days. Holdout MAE must be at most max(1 unit/day, mean holdout sales). This gate is policy, not confidence.

In that legacy path, shortfall is max(0, ceil(predicted demand - available stock)); buffered replenishment is max(0, ceil(predicted demand * 1.20) - available stock). Invalid inventory withholds recommendations. These formulas do not describe the current scoped policy engine. Low-stock thresholds, expiry alerts and movement clustering are separate operational signals.

The assistant plans bounded read tools and renders factual explanations locally; see [Gemini controls](T26_STAGE_B_GEMINI.md). Proposal execution is disabled by default and requires separate policy and human approval. No automatic ordering, autonomous stock action or real-world accuracy is established.
