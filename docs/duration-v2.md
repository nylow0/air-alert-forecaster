# Duration v2: M2b handoff

Chronological conformal calibration improves onset interval coverage, but does
not reach the 80% target on the held-out data. Validation coverage rises from
76.49% to 79.01%; test2 rises from 73.99% to 77.26%. The landmark model produces
remaining-duration forecasts and improves the long-alert baselines, but its
test2 intervals also under-cover. These results are preserved without tuning
against test2.

## Delivered

- `src/airalert/models/duration.py`: chronological CQR, record-based landmark
  features, fixed quantile fitting, v2 evaluation, bounded backfill polling,
  production refitting, and `predict_remaining(active, now)`.
- `tests/test_duration.py`: checkpoint leakage with delayed live observations,
  real-record prefixes, chronology, raw-coverage merge gaps, zero/90-day targets,
  and prediction through actual saved LightGBM artifacts. The existing split
  test now injects a synthetic outage rather than assuming the real gap remains.
- `docs/duration-v2.md`: this handoff, all requested metrics and calibration by
  elapsed checkpoint.
- `reports/v2/duration_scores.json`: schema-valid partial `Report`, containing
  46 validation and 46 test2 `DurationScore` rows.
- `artifacts/D1c/`: pre-validation onset models and validation calibration.
- `artifacts/D2/`: landmark models, calibration, rich
  `evaluation_val.json`/`evaluation_test.json`, the one-shot test2 marker, and
  backfill polling evidence.
- `artifacts/production/duration/`: an atomic generation pointer and a generation
  containing calibrated interval models and a median refitted on all eligible
  ended episodes.
- `data/processed/preds/duration_v2/`: 22 prediction files covering four onset
  references and seven D2 checkpoints for each evaluation split. The nested
  directory prevents the v1 backtest from discovering and rescoring them.

No commits, branch changes, new dependencies, frozen v1 report edits, or
overwrites of `artifacts/D0/` and `artifacts/D1/` were made.

## Decisions/assumptions

### Chronology and accepted backfill

The lead-owned `V2_VAL_START`, `V2_TEST_START`, and
`V2_CALIBRATION_WEEKS=6` are used. Evaluation-model fitting uses eligible
raion-regime episodes from 2025-08-14 whose starts and ends precede
2026-06-20T00:00:00Z. There are 75,940 such episodes. The six-week calibration
window is `[2026-06-20, 2026-08-01)`, with 13,479 eligible onset targets.
Calibration targets must end strictly before validation begins.

Validation is `[2026-08-01, 2026-09-08)`. Its 13,082 eligible episodes also
end strictly before September 8. Test2 starts on September 8 and includes
14,820 uncensored, observed, non-gap episodes through the saved snapshot,
2026-10-11T21:00:00Z. Duration evaluation needs observed complete episode
targets, rather than an onset label's additional 12-hour future window.

The initial ingest report recorded a failed gate. While implementation was
under way, the lead accepted the archive under the revised 88% daily floor.
The first backfill poll saw the extended snapshot, so a two-hour wait was
unnecessary. `snapshot.json` explicitly records `archive_gate_passed=true`,
the August 29 cutover, and `coverage.gaps=[]`. V2 duration eligibility uses
those accepted coverage gaps; absent archive acceptance it retains the
configured outage. No raw research response is sufficient to clear a gap.

An initial validation run still used the frozen CSV outage mask. Its scores
were superseded before test2 by the complete accepted-archive validation.
Test2 was then scored once, after model and calibration choices were frozen.
The fragment's `split="test"` means test2 because `DurationScore` permits
only `val` and `test`. The fragment notes explain this mapping.

### Onset references and conformal intervals

`D0` is empirical duration, refitted on the same pre-calibration training
cohort. `D1` reuses the v1-selected architecture, refitted on that cohort with
15 leaves, minimum leaf size 80, learning rate 0.05, and 180 fixed rounds per
quantile. V2 validation and test2 never choose tree settings or stopping rounds.
`D1v1` loads the original frozen v1 boosters as a separate reference. Its
training and earlier validation tuning remain exactly the v1 protocol.
`D1c` calibrates the newly fitted `D1`, rather than reusing the v1 tuning window
as if it were an independent calibration sample.

For each earlier calibration target, the CQR score in minutes is
`max(q10-y, y-q90)`. The widening offset is the nonnegative score at rank
`ceil(0.8*(n+1))`. P10 decreases by the offset, with a one-minute floor, and
P90 increases by it. Onset P50 stays unchanged. The validation offset is
1.592780 minutes. Test2 uses the same frozen boosters and recalibrates on
validation only; its offset is 2.279016 minutes. Its last calibration target
ends at 2026-09-07T23:38:23Z, before test2.

This follows [conformalized quantile regression](https://arxiv.org/abs/1905.03222).
The finite-sample coverage argument assumes exchangeability. Chronological
air-raid events and correlated geographic coverage do not establish that
assumption, so 80% is a calibration target, not a guaranteed future coverage.
See also [conformal prediction beyond exchangeability](https://arxiv.org/abs/2202.13415).

### Remaining duration

D2 is a landmark quantile model. Each completed episode contributes a row
at elapsed 0, 15, 30, 60, 120, 240, and 480 minutes if it is still active.
Targets are `log(max(remaining_minutes, 1))`. Zero-length episodes contribute
only the onset row. A checkpoint inside an absorbed merge gap is excluded
unless it is the onset, because a merged episode's apparent survival is not
enough to establish active raw coverage.

Its 27 features combine the 16 onset features with elapsed minutes,
`log1p(elapsed)`, current national/oblast active counts and recent starts,
current oblast active share, and changes in national/oblast active counts
since onset. This choice uses the existing record-based feature engine and
avoids inventing a complete distribution from only three onset quantiles.

Onset context is computed at the original start; checkpoint context at `c`.
Features include record starts `<= c` and do not inspect ends after `c`.
Live rows additionally require `observed_at < c`. An observation received
after onset cannot retrospectively enter onset features. Exact duplicate
coverage rows do not change distinct-unit counts. Full-data episode ends
and merge-gap closure never enter feature construction.

D2 uses a separate CQR offset for each elapsed checkpoint, plus the earlier
calibration sample's median residual shift for P50. Quantiles remain ordered
after the median shift. Arbitrary elapsed values use the model's elapsed
features and the preceding checkpoint's calibration. The interval calibration
has only been evaluated at the listed landmarks. Values between landmarks
and elapsed beyond 480 minutes have no measured coverage guarantee.

The checkpoint references `D0@e`, `D1@e`, `D1v1@e`, and `D1c@e` subtract
elapsed time from their onset quantiles and floor at one minute. They are
explicitly unconditioned baselines. Each uses the identical surviving,
raw-active episode cohort as D2 at that checkpoint.

### Production hook

`refit_production(now)` reads the accepted snapshot, excludes censored and
gap-overlapping episodes, and publishes an atomic generation under
`artifacts/production/duration/`. The real run used 442,859 ended episodes
and 1,479,590 surviving landmarks. The median booster trains on all of these.
The interval boosters stop before 2026-08-30T21:00:00Z; the subsequent six
weeks calibrate them. This preserves a disjoint interval fit/calibration
split while still updating the production median on every eligible ended
episode. The full-data median is clamped inside the calibrated interval.
It is a different median from the fixed-model evaluation's residual-adjusted
P50; its future live calibration has not yet been measured.

`predict_remaining(active, now)` accepts only `raion_id` and a known UTC
`start` for caller-confirmed active alerts. It loads the current production
generation and returns those keys plus absolute UTC `end_p10`, `end_p50`,
and `end_p90`. It preserves input order. Empty input returns a typed empty
frame. Naive `now`, missing/future starts, unknown raions, and artifacts
whose data cutoff exceeds `now` are rejected. All remaining quantiles have
a one-minute minimum, so every predicted end is later than `now`.

The feature view combines canonical records with the latest successful live
observation snapshot available strictly before `now`. Old open poll records
are not accumulated into permanently active coverage. Live hazard types
outside air raid are excluded. Availability is checked separately at onset
and the checkpoint.

## Onset results

Widths, MAE and pinball losses are minutes. V1's published 77.43% validation
and 75.98% test coverage remain frozen; the rows below use the new v2 splits.

| Model | Split | n | Pinball P10 / P50 / P90 | P50 MAE | Coverage | Mean width |
|---|---|---:|---:|---:|---:|---:|
| D0 | val | 13,082 | 11.85 / 48.80 / 45.58 | 97.60 | 77.48% | 272.52 |
| D1v1 | val | 13,082 | 11.90 / 47.21 / 41.82 | 94.42 | 76.27% | 268.22 |
| D1 | val | 13,082 | 11.91 / 47.01 / 41.19 | 94.02 | 76.49% | 265.37 |
| D1c | val | 13,082 | 11.84 / 47.01 / 41.22 | 94.02 | 79.01% | 268.55 |
| D0 | test2 | 14,820 | 10.66 / 43.59 / 41.32 | 87.18 | 76.03% | 258.37 |
| D1v1 | test2 | 14,820 | 10.85 / 44.46 / 38.98 | 88.91 | 73.77% | 266.56 |
| D1 | test2 | 14,820 | 10.83 / 44.42 / 38.59 | 88.84 | 73.99% | 260.65 |
| D1c | test2 | 14,820 | 10.70 / 44.42 / 38.63 | 88.84 | 77.26% | 265.21 |

The onset interval grows by 3.19 minutes on validation and 4.56 minutes on
test2. P50 MAE is unchanged by onset conformal calibration. P90 pinball is
slightly worse after widening, so the increased coverage is not a uniform
improvement across every metric.

## Calibration by elapsed time

Before means raw D2; after means earlier-window calibration. Widths, median
error and MAE are minutes. P50 below is the fraction of actual remaining
times at or below predicted P50; its calibration target is 50%. The signed
median error is `median(predicted P50 - actual)`. A calibrated median does
not require the mean signed error to be zero for a skewed duration target.
The richer JSON also retains mean signed errors and all quantile losses.

| Split | Elapsed | n | Coverage before | Coverage after | Width before | Width after | P50 below | P50 median error | P50 MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| val | 0 | 13082 | 78.12% | 79.24% | 283.56 | 285.21 | 52.95% | 3.15 | 93.31 |
| val | 15 | 11491 | 79.80% | 80.78% | 309.10 | 309.88 | 53.40% | 3.57 | 100.39 |
| val | 30 | 8906 | 78.01% | 78.70% | 349.65 | 350.35 | 53.26% | 5.01 | 117.49 |
| val | 60 | 5555 | 79.46% | 80.05% | 425.84 | 427.12 | 53.12% | 7.85 | 156.53 |
| val | 120 | 2972 | 75.44% | 76.92% | 537.57 | 541.18 | 53.63% | 13.00 | 229.96 |
| val | 240 | 1371 | 73.30% | 83.44% | 712.08 | 749.11 | 50.69% | 5.32 | 355.85 |
| val | 480 | 620 | 75.97% | 80.16% | 1001.60 | 1035.99 | 49.03% | -32.08 | 509.48 |
| test2 | 0 | 14820 | 75.76% | 78.10% | 279.95 | 282.60 | 54.97% | 5.85 | 87.54 |
| test2 | 15 | 13255 | 75.18% | 75.31% | 296.30 | 296.40 | 54.98% | 5.51 | 92.37 |
| test2 | 30 | 9940 | 75.50% | 77.32% | 327.22 | 329.45 | 53.42% | 4.64 | 108.19 |
| test2 | 60 | 5989 | 76.72% | 77.43% | 393.03 | 394.27 | 51.93% | 2.89 | 143.00 |
| test2 | 120 | 3094 | 72.59% | 76.63% | 488.44 | 497.44 | 49.64% | -0.66 | 211.08 |
| test2 | 240 | 1435 | 66.34% | 72.96% | 660.28 | 682.06 | 52.20% | 9.34 | 332.24 |
| test2 | 480 | 571 | 66.90% | 70.23% | 818.10 | 850.63 | 41.33% | -128.54 | 433.41 |

P50 is closest to nominal at validation's 4/8-hour checkpoints and test2's
1/2/4-hour checkpoints. At test2's 8-hour checkpoint, P50 is substantially
too early: only 41.33% of outcomes end by it, with a -128.54-minute signed
median error. Calling all elapsed buckets unbiased would be incorrect.

### Checkpoint comparison

The uncalibrated D1 refit and raw D2 rows are retained in the score fragment
and rich evaluation JSON. This table compares the empirical baseline, the
original v1 model, conformal onset intervals, and conditional D2. Every row
uses the cohort size in the preceding table.

| Split | Model / elapsed | Pinball P10 / P50 / P90 | P50 MAE | Coverage | Mean width |
|---|---|---:|---:|---:|---:|
| val | D0@0 | 11.85 / 48.80 / 45.58 | 97.60 | 77.48% | 272.52 |
| val | D1v1@0 | 11.90 / 47.21 / 41.82 | 94.42 | 76.27% | 268.22 |
| val | D1c@0 | 11.84 / 47.01 / 41.22 | 94.02 | 79.01% | 268.55 |
| val | D2@0 | 11.77 / 46.65 / 39.31 | 93.31 | 79.24% | 285.21 |
| val | D0@15 | 12.67 / 52.93 / 48.91 | 105.86 | 86.55% | 280.76 |
| val | D1v1@15 | 12.57 / 50.84 / 45.21 | 101.68 | 85.42% | 281.96 |
| val | D1c@15 | 12.64 / 50.67 / 44.57 | 101.34 | 86.93% | 281.85 |
| val | D2@15 | 12.53 / 50.20 / 42.61 | 100.39 | 80.78% | 309.88 |
| val | D0@30 | 14.85 / 63.83 / 56.92 | 127.66 | 86.40% | 286.91 |
| val | D1v1@30 | 14.83 / 60.52 / 52.86 | 121.03 | 87.54% | 299.18 |
| val | D1c@30 | 14.87 / 60.36 / 52.12 | 120.72 | 87.63% | 298.54 |
| val | D2@30 | 14.67 / 58.74 / 49.40 | 117.49 | 78.70% | 350.35 |
| val | D0@60 | 20.19 / 91.26 / 78.19 | 182.53 | 78.31% | 292.11 |
| val | D1v1@60 | 20.18 / 86.11 / 72.24 | 172.22 | 81.91% | 318.72 |
| val | D1c@60 | 20.18 / 85.66 / 71.43 | 171.31 | 81.93% | 321.57 |
| val | D2@60 | 19.46 / 78.26 / 62.94 | 156.53 | 80.05% | 427.12 |
| val | D0@120 | 29.66 / 140.32 / 127.60 | 280.64 | 63.73% | 303.17 |
| val | D1v1@120 | 29.66 / 136.71 / 117.28 | 273.42 | 68.84% | 348.74 |
| val | D1c@120 | 29.66 / 135.62 / 115.94 | 271.25 | 69.28% | 357.44 |
| val | D2@120 | 28.87 / 114.98 / 89.70 | 229.96 | 76.92% | 541.18 |
| val | D0@240 | 47.03 / 230.96 / 244.87 | 461.93 | 39.68% | 301.24 |
| val | D1v1@240 | 47.03 / 226.32 / 220.18 | 452.64 | 47.12% | 368.87 |
| val | D1c@240 | 47.03 / 224.45 / 216.23 | 448.90 | 48.72% | 384.60 |
| val | D2@240 | 46.29 / 177.93 / 136.44 | 355.85 | 83.44% | 749.11 |
| val | D0@480 | 70.57 / 352.83 / 450.87 | 705.67 | 19.19% | 291.64 |
| val | D1v1@480 | 70.57 / 352.60 / 410.14 | 705.20 | 27.26% | 352.13 |
| val | D1c@480 | 70.57 / 352.18 / 402.35 | 704.37 | 32.10% | 378.02 |
| val | D2@480 | 68.53 / 254.74 / 215.61 | 509.48 | 80.16% | 1035.99 |
| test2 | D0@0 | 10.66 / 43.59 / 41.32 | 87.18 | 76.03% | 258.37 |
| test2 | D1v1@0 | 10.85 / 44.46 / 38.98 | 88.91 | 73.77% | 266.56 |
| test2 | D1c@0 | 10.70 / 44.42 / 38.63 | 88.84 | 77.26% | 265.21 |
| test2 | D2@0 | 10.62 / 43.77 / 38.85 | 87.54 | 78.10% | 282.60 |
| test2 | D0@15 | 11.13 / 46.39 / 43.56 | 92.78 | 83.91% | 262.19 |
| test2 | D1v1@15 | 11.10 / 46.54 / 40.96 | 93.08 | 82.01% | 272.55 |
| test2 | D1c@15 | 11.11 / 46.55 / 40.63 | 93.10 | 84.73% | 270.56 |
| test2 | D2@15 | 11.18 / 46.18 / 40.78 | 92.37 | 75.31% | 296.40 |
| test2 | D0@30 | 13.22 / 57.02 / 51.01 | 114.05 | 84.31% | 265.11 |
| test2 | D1v1@30 | 13.20 / 54.63 / 47.64 | 109.27 | 87.09% | 283.19 |
| test2 | D1c@30 | 13.21 / 54.70 / 47.36 | 109.39 | 86.95% | 280.23 |
| test2 | D2@30 | 13.08 / 54.09 / 46.49 | 108.19 | 77.32% | 329.45 |
| test2 | D0@60 | 18.17 / 82.01 / 70.92 | 164.01 | 74.50% | 266.06 |
| test2 | D1v1@60 | 18.17 / 78.10 / 65.21 | 156.21 | 79.56% | 295.36 |
| test2 | D1c@60 | 18.18 / 78.08 / 65.09 | 156.17 | 79.08% | 293.40 |
| test2 | D2@60 | 17.67 / 71.50 / 58.80 | 143.00 | 77.43% | 394.27 |
| test2 | D0@120 | 26.93 / 126.27 / 116.78 | 252.53 | 57.34% | 273.55 |
| test2 | D1v1@120 | 26.93 / 124.51 / 107.50 | 249.02 | 62.54% | 314.16 |
| test2 | D1c@120 | 26.93 / 124.11 / 107.61 | 248.23 | 61.70% | 314.76 |
| test2 | D2@120 | 26.00 / 105.54 / 82.93 | 211.08 | 76.63% | 497.44 |
| test2 | D0@240 | 40.73 / 199.95 / 213.00 | 399.91 | 34.98% | 296.77 |
| test2 | D1v1@240 | 40.73 / 197.98 / 198.57 | 395.96 | 37.56% | 354.43 |
| test2 | D1c@240 | 40.73 / 197.95 / 198.22 | 395.90 | 37.56% | 358.37 |
| test2 | D2@240 | 40.09 / 166.12 / 123.78 | 332.24 | 72.96% | 682.06 |
| test2 | D0@480 | 66.39 / 331.93 / 407.85 | 663.86 | 6.83% | 232.14 |
| test2 | D1v1@480 | 66.39 / 331.85 / 374.39 | 663.71 | 12.78% | 296.13 |
| test2 | D1c@480 | 66.39 / 331.65 / 373.70 | 663.30 | 14.19% | 300.98 |
| test2 | D2@480 | 64.53 / 216.71 / 153.21 | 433.41 | 70.23% | 850.63 |

At test2's 8-hour checkpoint, D2 reduces median MAE from D1v1's 663.71 to
433.41 minutes and raises coverage from 12.78% to 70.23%. Its interval is
also much wider. Survival conditioning repairs the expired-onset-median
behavior; it does not establish nominal uncertainty calibration on test2.

## Verified

| Actual command or inspection | Actual result |
|---|---|
| `uv run --no-sync python -m airalert.models.duration` | Complete-archive validation: `Duration v2 val: 38 scores; onset coverage 76.4868% -> 79.0093%`. |
| `uv run --no-sync python -c "from airalert.models.duration import evaluate_v2; evaluate_v2(reuse_models=True)"` | Added the frozen v1 reference without refitting/tuning: `Duration v2 val: 46 scores; onset coverage 76.4868% -> 79.0093%`. |
| `uv run --no-sync python -m airalert.models.duration --final` | Exit 0, once: `Duration v2 test: 46 scores; onset coverage 73.9946% -> 77.2605%`. |
| `uv run --no-sync python -m airalert.models.duration --production` | Exit 0: `Production duration: 442,859 ended episodes, 1,479,590 landmarks`. |
| Real `predict_remaining` call using canonical records plus the latest accepted live snapshot | 39 active rows and 39 unique raions; `all_future=True`, `ordered=True`; all three end columns `datetime64[us, UTC]`. Median of predicted remaining P50 was 142.675565 minutes. |
| Real M2a `production._end_quantiles` adapter with non-default input indices | `duration_id=D2`, 39 rows, input index preserved, all ends future and ordered, all three columns `datetime64[us, UTC]`. |
| `Report.model_validate_json` and inspection of all 22 prediction files | 92 valid score rows, 46 per split; all saved predictions finite, >=1 minute, and ordered. |
| A second `evaluate_v2(final=True)` attempt | Rejected before scoring: `Test2 was already scored; preserve the frozen evaluation`. |
| `uv run --no-sync pytest tests/test_duration.py -q` before source-gap repair | 13 passed in 47.12 seconds; later checkpoint-only availability test passed in 4.94 seconds. |
| `uv run --no-sync pytest tests/test_duration.py -q` after synthetic-gap correction | 13 passed in 42.98 seconds. |
| `uv run --no-sync pytest tests/test_duration.py tests/test_api.py -q` during gap-contract update | 12 passed, 1 duration failure, 11 API setup errors in 53.33 seconds. Both causes were fixtures indexing the now-empty `config.DATA_GAPS`. The owned duration fixture was corrected to use a synthetic outage. API fixture changes belong to S1/lead. |
| `uv run --no-sync ruff check src/airalert/models/duration.py tests/test_duration.py --output-format concise` | `All checks passed!` |

API/installed-UI completion is not claimed.

## Open issues

- The requested nominal 80% coverage has not been reached on test2. Wider
  chronological intervals improve coverage but leave onset at 77.26%, D2's
  4-hour bucket at 72.96%, and its 8-hour bucket at 70.23%. No parameter was
  changed to repair these held-out scores. A future method change needs a
  new untouched evaluation window.
- Test2's 8-hour median remains too early. Arbitrary checkpoint calibration
  and elapsed beyond 8 hours have not been independently measured.
- Completed-episode training excludes censored alerts. The archive's known
  unresolved long-running source event and the source cutover remain data
  limitations. The present metrics do not identify the cause of the drift.
- The model hook itself is verified on real data. M2a has corrected its
  prediction caller to the required signature and absolute end columns.
  Nightly refit wiring and the full live forecast/UI workflow still need
  integrated verification.
- API fixtures assume `config.DATA_GAPS[0]` exists. Repaired coverage means
  the fixture must inject its own synthetic gap, rather than requiring a
  permanent production outage.

## Contract change requests

1. M2a: invoke `duration.refit_production(now)` in the production refit
   lifecycle before publishing its status. Its prediction adapter and D2
   artifact-pointer detection have already been corrected by M2a and were
   independently verified with 39 real active rows.
2. S1/lead: update API test fixtures to inject a synthetic source gap. These
   tests currently fail before exercising the production hook.

The required public signature remains exactly `predict_remaining(active,
now)`. No unowned production, API, config, schema, or frontend file was edited.

## Dependencies added

None. Existing pandas, NumPy, LightGBM, Pydantic, pytest and Ruff were used.
