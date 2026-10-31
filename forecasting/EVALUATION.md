# T19 offline evaluation contract

This evaluator reads authorized T18 synthetic histories in a **new disposable
SQLite target**. It has no live prediction publication, model deserialization,
operational-sale training, migration or existing-database option. T18's HTTP
interfaces and T20 gates remain unchanged.

Run from the repository using its installed Python environment:

```powershell
$env:APP_ENV='testing'
$env:PYTHON_DOTENV_DISABLED='1'
$env:DATABASE_URL='sqlite:///:memory:'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
python scripts/backtest.py --profile small --output data/private/t19/small-new.json
python scripts/backtest.py --profile medium --skus 12 --seconds 300 --memory-mb 1024 --output data/private/t19/medium-new.json
```

Output filenames must be new JSON files beneath `data/private/t19`; that whole
tree is Git-ignored. Traversal, existing files and symlink escapes are refused.
Profiles are the unchanged T14 small/medium generators, seed 13, source
`inventraiq-generated-history-v1`, revision `synthetic-r1`. Selection is the first
1–12 manifest SKU ordinals (at most six in small), in each of all three branches.
There is no selection by observed accuracy or future outcomes. The medium target
imports all 120 source SKUs; the evaluation bounds the selected subset to 36 series.
The T15 operational scenario provisions identities and authorization but its
transactions never enter the T18 historical query or model features.

## Chronology and features

Version `inventraiq-t19-evaluation-v1` requires 56 initial calendar days. With at
least 168 days, the preceding 56 days are validation and the final 56 locked test.
Eight non-overlapping weekly validation origins select a frozen configuration;
only that configuration and its reference baseline enter the test. The eight
test origins form one prequential evaluation: earlier test observations become
training information at later origins, without retuning the selected algorithm.
The test is not a single 56-step prediction. Run results are immutable local
evidence, not a cross-run registry preventing an operator from rerunning a test.

Short histories get up to eight complete validation weeks after the 56-day
minimum, with no invented locked test. Medium's 90 days support four origins;
small's 14 support none. At every origin, T18 reconstructs coverage/exposure as
known at that origin. Dates remain original; branch-local business dates are
already resolved by T18. No random split, future price, promotion or category
feature is used.

Eleven shifted features: elapsed day, weekday sine/cosine, lags 1/7/14/28,
observed-only trailing means 7/28, and observed counts 7/28. Branch/SKU series
are fitted separately, so no pooled identity encoding is necessary. Missing lags
remain NaN until a fresh training-fold median imputer with missing indicators;
scaling is also fitted within that fold. Missing targets are excluded, never
zero-filled. Training rows start at day index 28; ML requires 20 observed targets.
Seven future steps are recursive, using predictions rather than future labels.
In recursive feature rows, the count features count available numeric inputs,
including earlier predictions; they do not assert that future coverage was
observed. Original T18 coverage masks and scored-label eligibility remain separate.

See [comparison](../T19_MODEL_COMPARISON.md) for all frozen candidates and results.
Finite ML predictions are clipped to zero; nonfinite predictions are rejected.
Unavailable/failed candidates use an explicitly counted observed trailing-28-day
mean fallback (all observed history if that window is empty). With no observed
training at all the forecast stays unavailable. Exceptions record type only.

## Metrics and selection

Daily MAE, WAPE and signed bias use only observed eligible labels. WAPE and
percentage bias are null when actual-unit sum is zero. Bias is forecast minus
actual. Seven-day error is the signed sum of daily errors **only for complete
seven-label horizons**; report its mean signed value and sum/mean absolute value.
Primary selection minimizes the sum of absolute seven-day errors, then daily
MAE, then the fixed configuration order (simpler configuration first).

MASE/RMSSE use observed seasonal-lag-7 training pairs only. Zero or missing scales
are undefined/null, not zero or infinity. Report the number of defined forecasts;
aggregate scaled metrics are means across defined fold-series values. Partial
horizons contribute daily metrics and explicit coverage counts, not manufactured
complete weekly errors. Every candidate uses identical label eligibility masks.

ML needs at least 5% lower primary error than the best credible baseline, no
branch error above 110% of its baseline, equal scored-day coverage, native
forecasts and no classified errors. Undefined branch comparisons fail closed.
A zero-error reference cannot establish relative improvement. Resource-incomplete
runs have no accepted decision. No confidence probabilities/intervals are invented.

## Reproduction and limits

JSON records manifests, per-series semantic identities/coverage, source hashes,
fold dates, feature/config versions, all candidate parameters, seed 42, versions,
grouped metrics, native/fallback/error counts and fit/inference times. The semantic
SHA-256 excludes timings. It is environment-pinned reproducibility evidence;
cross-library bitwise floating-point equivalence is not promised. Models and
pickles are never persisted. Timings called inference include feature/estimator
construction and orchestration outside `fit`; they are not serving latency.

One worker, OMP/OpenBLAS one thread, maximum 36 series, bounded 1,096-day T18
histories, configured wall 30–900 seconds, RSS 256–2,048 MiB, temporary-tree limit
128 MiB. Defaults: 300 seconds/1,024 MiB. The supervisor samples every 200 ms and
terminates over-budget or unmeasurable workers. These are sampled local limits,
not hard OS quotas: overshoot is possible; RSS includes the launcher and actual
worker on Windows; temporary bytes exclude unrelated OS scratch. No kernel CPU
quota or production capacity claim. Raw child logs stay temporary and are deleted.

No genuine category metadata or sparse cohort exists in the approved medium
observations. Such evidence remains unavailable; unit-only sparse fixtures test
algorithm behavior without establishing real performance. Stockout-censoring,
launch and publication-lag evidence is also incomplete. Do not treat a short
periodic synthetic benchmark as production model qualification.
