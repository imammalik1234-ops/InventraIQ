# T19 model comparison and provisional selection

## Decision and evidence boundary

Select **two-week seasonal moving average (`seasonal_mean2`) provisionally** for
this approved synthetic comparison. It ties the four-week version at zero error
and wins the frozen simplicity tie-break. No ML model is promoted; no model is
published to live inference. These are limited-validation findings, with **no
locked-test accuracy claim**. Longer observations are required for qualification.

The unchanged T14 medium generator produces repeating weekly units 0–6 and
explicit missing cells. A seasonal rule naturally matches it. Zero error here
demonstrates implementation consistency on that pattern; it does not predict
real-world retail performance, latent demand or stockout-censored sales.

## Data, chronology and fairness

Data is exclusively T18-authorized, synthetic historical observations from
2001-01-01 through 2001-03-31, source `inventraiq-generated-history-v1`, revision
`synthetic-r1`. Fixed manifest selection: first 12 SKU ordinals per branch, all
three branches, 36 series. There are 3,240 calendar cells, 3,058 observed (426
genuine zeros), 182 explicitly missing. No operational simulation sale, financial
fact, current stock level or unapproved external feature enters training.

Minimum initial training is 56 calendar days. Expanding origins are March 3, 10,
17 and 24, each predicting the next seven original business dates. Each candidate
uses the same 953 observed validation labels across 144 fold-series forecasts;
89 horizons have all seven labels, 55 are partial. No future labels enter features
or preprocessing. Source selection and hyperparameter configurations are fixed
before evaluation. No neural network, random time split or broad search is used.

Full protocol requires at least 168 calendar days: eight validation origins in
56 days, then eight prequential locked-test origins over the final 56, running
only the frozen champion/reference once. Neither approved small (14 days) nor
medium (90 days) qualifies. **Fully eligible measured series: zero.** Small has
18 series, 242 observed and ten missing cells, no eligible origins, and no winner.
Unit-only long histories test the full mechanics; they are not measured evidence
from an expanded approved data profile.

## Algorithms and features

| Candidate | Fixed configuration and purpose |
|---|---|
| naive | Last observed value; simplest persistence reference |
| mean7 / mean28 | Observed-only trailing calendar-window averages; short/long level references |
| seasonal_naive7 | Previous-week same weekday; local missing slots fall back to observed mean28 |
| seasonal_mean2 / seasonal_mean4 | Mean of observed same weekdays over two/four prior weeks; bounded seasonal smoothing |
| sba_croston | SBA-adjusted Croston, alpha 0.1, complete calendars only; intermittent-demand reference |
| legacy_regression | Original unchanged `ai_model.forecast_demand`; complete calendars only |
| observed_trend | Coverage-safe OLS on elapsed day and observed targets; distinguishes missing-safe adaptation from original regression |
| calendar_lag_linear | OLS on eleven shifted features |
| ridge1 / ridge10 | Same features, Ridge alpha 1/10; bounded regularization comparison |
| hist_gradient_boosting | Same features, 40 iterations, seven leaf nodes, minimum eight samples/leaf, learning rate .08, L2 1, seed 42, early stopping disabled |

Features: elapsed day, weekday sine/cosine, lags 1/7/14/28, shifted observed means
7/28 and observed counts 7/28. They express trend, seasonality and coverage
without unavailable price/promotion/category assumptions. Every branch/SKU gets
its own model. No pooled identities or outcome-derived category features.
Training starts at index 28 with at least 20 observed target rows; no target is
imputed. Median imputation with missing indicators and standard scaling are
freshly fitted on each training fold. Future features use recursive predictions.
Finite negatives are clipped to zero; nonfinite outputs fail and are counted.

The original regression emits only a seven-day total; for daily comparison its
authorized total is distributed uniformly across seven days. Its native zero-fill
semantics are unsafe on incomplete T18 calendars, so all 144 measured calls use
the labeled mean28 fallback without fitting it. Croston likewise cannot count
unknown dates as zero intervals; all 144 calls use a labeled fallback. Their rows
below measure **fallback behavior**, not native algorithm skill. Native behavior
is exercised separately on complete original unit fixtures. No sparse benchmark
performance is claimed.

## Metric definitions

MAE and WAPE pool only observed eligible daily labels; signed bias is forecast
minus actual in units per scored day. Primary selection is sum of absolute
complete-seven-day cumulative errors (`sum |sum(prediction - actual)|`), with daily
MAE then frozen configuration order as tie-breaks. Partial weeks never masquerade
as complete totals. This total-error criterion can cancel opposing daily errors
within a week; the daily metrics are retained to expose that limitation.

WAPE and percentage bias are null for zero actual-unit denominators. MASE/RMSSE
use observed seasonal-lag-7 training-pair scales; zero/missing scales are null,
not perfect scores. All measured medium scales are zero, so defined scaled-metric
forecast count is zero for every candidate. Complete zero-actual horizons still
contribute absolute unit errors. No probabilistic score or interval is invented.

<!-- Measured tables below are copied from the serial medium-verified JSON. -->

## Measured validation results

| Candidate | Daily MAE | WAPE % | Signed bias units/day | Sum absolute 7-day error | Mean absolute 7-day error | Native / 144 |
|---|---:|---:|---:|---:|---:|---:|
| naive | 2.334732 | 75.449305 | 0.059811 | 1155.000000 | 12.977528 | 144 |
| mean7 | 1.703043 | 55.035605 | -0.028332 | 147.000000 | 1.651685 | 144 |
| mean28 | 1.691107 | 54.649893 | -0.055690 | 34.192308 | 0.384183 | 144 |
| seasonal_naive7 | 0.133183 | 4.303936 | 0.060538 | 126.923077 | 1.426102 | 78 |
| seasonal_mean2 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 144 |
| seasonal_mean4 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 144 |
| sba_croston | 1.691107 | 54.649893 | -0.055690 | 34.192308 | 0.384183 | 0 |
| legacy_regression | 1.691107 | 54.649893 | -0.055690 | 34.192308 | 0.384183 | 0 |
| observed_trend | 1.701546 | 54.987220 | -0.050343 | 93.113539 | 1.046220 | 144 |
| calendar_lag_linear | 0.210676 | 6.808205 | 0.045789 | 51.385328 | 0.577363 | 144 |
| ridge1 | 0.202891 | 6.556640 | 0.032103 | 43.444544 | 0.488141 | 144 |
| ridge10 | 0.227420 | 7.349311 | 0.011396 | 31.812361 | 0.357442 | 144 |
| hist_gradient_boosting | 0.254429 | 8.222148 | 0.010610 | 26.941132 | 0.302709 | 144 |

### Per-branch errors

Each entry is daily MAE / sum absolute complete-7-day error, in units.
Branch 1: 317 scored days, 29 complete weeks; branches 2 and 3 each have
318 scored days and 30 complete weeks. No branch is omitted.

| Candidate | DEMO-01 | DEMO-02 | DEMO-03 |
|---|---:|---:|---:|
| naive | 2.347003 / 385.000000 | 2.349057 / 399.000000 | 2.308176 / 371.000000 |
| mean7 | 1.701893 / 45.500000 | 1.701258 / 49.000000 | 1.705975 / 52.500000 |
| mean28 | 1.691382 / 10.769231 | 1.689571 / 11.576923 | 1.692370 / 11.846154 |
| seasonal_naive7 | 0.123756 / 39.230769 | 0.133043 / 42.307692 | 0.142719 / 45.384615 |
| seasonal_mean2 | 0.000000 / 0.000000 | 0.000000 / 0.000000 | 0.000000 / 0.000000 |
| seasonal_mean4 | 0.000000 / 0.000000 | 0.000000 / 0.000000 | 0.000000 / 0.000000 |
| sba_croston | 1.691382 / 10.769231 | 1.689571 / 11.576923 | 1.692370 / 11.846154 |
| legacy_regression | 1.691382 / 10.769231 | 1.689571 / 11.576923 | 1.692370 / 11.846154 |
| observed_trend | 1.702004 / 30.135491 | 1.700236 / 31.914417 | 1.702399 / 31.063632 |
| calendar_lag_linear | 0.207459 / 16.030635 | 0.209748 / 17.175911 | 0.214810 / 18.178781 |
| ridge1 | 0.200576 / 14.094445 | 0.201489 / 14.787989 | 0.206601 / 14.562109 |
| ridge10 | 0.226215 / 10.698881 | 0.225946 / 10.793216 | 0.230095 / 10.320264 |
| hist_gradient_boosting | 0.253156 / 8.445460 | 0.254592 / 9.020829 | 0.255535 / 9.474843 |

### Computational cost

Measured totals over all 144 calls/configuration in serial medium-verified;
fit is only estimator.fit. Other orchestration/feature costs are included in
inference, so these are not production serving-latency measurements.

| Candidate | Fit ms total | Other/inference ms total | Fallback count and reason |
|---|---:|---:|---|
| naive | 0.000 | 1.959 | 0 |
| mean7 | 0.000 | 1.434 | 0 |
| mean28 | 0.000 | 1.383 | 0 |
| seasonal_naive7 | 0.000 | 2.646 | 66: missing_seasonal_slots |
| seasonal_mean2 | 0.000 | 2.853 | 0 |
| seasonal_mean4 | 0.000 | 3.584 | 0 |
| sba_croston | 0.000 | 1.359 | 144: croston_incomplete_calendar |
| legacy_regression | 0.000 | 1.314 | 144: legacy_requires_complete_calendar |
| observed_trend | 166.546 | 195.088 | 0 |
| calendar_lag_linear | 509.253 | 894.634 | 0 |
| ridge1 | 493.623 | 862.177 | 0 |
| ridge10 | 498.322 | 889.211 | 0 |
| hist_gradient_boosting | 3229.704 | 1457.502 | 0 |

## Promotion, trade-offs and limitations

All six legacy/ML configurations fail the predeclared promotion gate. The best
baseline has zero aggregate weekly error, so a 5% relative improvement is
undefined and cannot be claimed. Every fitted ML model also has positive weekly
error in each branch compared with zero reference error; the branch guard fails.
The original regression additionally fails native-coverage qualification. No
exceptions occurred in the measured runs. Seasonal naive uses 66 missing-slot
fallbacks; the two/four-week means each have 144 native forecasts.

Ridge alpha 1 has the lowest daily error among fitted ML candidates; boosting
has the lowest primary weekly-total error among them. Neither beats seasonal
means. Boosting costs substantially more fit/inference time; its complexity has
no measured benefit over the selected rule. Mean28 illustrates why both metrics
matter: weekly totals can be reasonable while within-week daily accuracy is poor.

Measured category breakdown consists only of `unavailable_source_category`,
identical to the overall row, because historical categories are absent. All
measured series belong to the training-only `regular` cohort (positive/observed
ratio at least .2). The sparse/intermittent benchmark segment has zero eligible
series and undefined metrics. Unit tests establish sparse fallback mechanics,
not sparse comparative accuracy. Launches, stockouts, price changes, peaks and
returns are not represented adequately by this fixture.

Two serial medium runs reproduce every semantic metric and selection exactly:
SHA-256 `ff45ea1ac08c4f9b6aed568bad5466207b12f02ee1788c52155c2c9d773aa0fa`.
Wall times were 24.530 and 27.301 seconds including setup/import; sampled peak RSS
254,521,344 and 254,541,824 bytes. Both completed within 300 seconds, 1,024 MiB RSS
and 128 MiB temporary-tree caps. No accepted result is issued for incomplete runs.
There is no statistical significance or production capacity claim. Original
long-history unit tests isolate locked-test mechanics without promoting their
results into these measured tables.

Before any real-world promotion: obtain rights-cleared, fingerprinted observations;
retain a genuinely untouched final test with enough prior training; predeclare
stores/cohorts/features; collect authoritative categories, launch/availability and
as-reported publication timing; evaluate multiple seasonal regimes and genuinely
intermittent, sparse, zero, peak and missing histories. Compare branch/category
coverage and daily versus lead-time totals, quantify uncertainty appropriately,
review censoring, validate MySQL/scoped-query behavior and resources, and complete
independent release review. Additional approved synthetic stress histories can
test robustness but cannot replace this real-world evidence. M5 rights remain
unresolved. T20 publication and T21 operational decisions remain separate tasks.
