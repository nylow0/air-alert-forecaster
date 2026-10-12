# Onset v2 and production, M2a

The locked served forecast achieves test2 Brier skill of **15.92%, 21.31%,
and 28.52%** against B0 at 1, 3, and 12 hours. Selection uses validation only.
Test2 was scored once. Production refit took **56.56 seconds** and a fresh
process produced a valid 119-raion forecast with D2 remaining-duration ends.

| Horizon | Locked configuration | Validation served BSS | Test2 served BSS | Test2 90% BSS interval |
|---|---|---:|---:|---|
| 1 h | M1 raion regime, geography, isotonic | 14.56% | 15.92% | 14.30% to 17.36% |
| 3 h | M1 raion regime, geography, isotonic | 16.45% | 21.31% | 18.83% to 23.56% |
| 12 h | M1 full history, isotonic | 15.76% | 28.52% | 24.98% to 32.01% |

The repository suite initially reported 114 passed and 11 API fixture errors.
Every error came from the S1-owned fixture indexing the now-empty
`config.DATA_GAPS`. The assigned tests and real HTTP checks are documented
below. This report does not claim a green full repository suite.

## Protocol

The reconciled snapshot is `2026-10-11T21:00:00Z`, the start of October 12
in Europe/Kyiv. The ledger covers all 44 complete archive days through
October 11. The accepted cutover is `2026-08-29T06:00:00Z`. The lead cleared
the repaired configured outage; reconciled coverage also has no gaps.
See [I1's ingest evidence](ingest.md).

| Split | Forecast times, UTC | Purpose | Published rows |
|---|---|---|---:|
| Train | [2022-04-01, 2026-08-01) | Fit models; inner stopping; train-tail calibration | 4,521,048 |
| Validation | [2026-08-01, 2026-09-08) | Compare models and calibration; choose each horizon | 108,528 |
| Test2 | [2026-09-08T00:00Z, 2026-10-11T09:00Z], inclusive end | One untouched onset evaluation | 95,438 |
| Latest | 2026-10-11T21:00Z | Reconciled snapshot prediction; no labels | 119 |

The test2 end is twelve hours before the exclusive end of the last complete
Kyiv day. Split boundaries use UTC. Calendar features and bootstrap blocks
use Europe/Kyiv. Labels cover starts in `(t, t+H]` and are null beyond the
observation boundary. A label ending exactly on the next split boundary is
eligible; a label crossing it is embargoed. Validation therefore has
108,528, 108,290 and 107,219 eligible rows at 1, 3 and 12 hours.

The v2 gap mask unions unresolved configured gaps with snapshot coverage
gaps. It uses the existing label-overlap rule and 168-hour feature recovery.
The list is empty for this evaluation. The initial training command started
with the old configured mask loaded. Before final scoring, validation
calibration, predictions and selection were refreshed under the cleared
policy. The training period ends before the repaired outage, so classifier
weights and train-tail calibrators did not require another fit.

Validation begins with an already-seen v1 test period. It is a tuning period,
not fresh evidence of generalization. Revised archive targets after cutover
also mean this is not an identical-row comparison with frozen v1 scores.
No test2 target column was requested by model fitting, calibration, or
selection. Feature/label construction can publish test targets; fitting
reads test2 keys and features without those targets.

## Chronological calibration and artifacts

Validation calibration uses the six-week train tail
`[2026-06-20T00:00Z, 2026-08-01T00:00Z)`, with horizon-specific embargo.
A prefix model predicts this tail using only matured rows ending by June 20.
Its early stopping uses an eight-week inner holdout entirely before that
cutoff. Isotonic fits use these forward tail predictions. The classifier
then refits all eligible training rows at the inner-selected round count,
and the saved train-tail calibrator applies to validation.

Test2 uses a separate isotonic fit on eligible validation rows. Its last
label ends at `2026-09-08T00:00Z`; its fit forecast times are strictly before
every test2 application time. No calibration fit is applied backward in time.
The legacy helper now also moves forward, leaving its first validation month
raw. Frozen v1 scores and predictions were not regenerated.

Every candidate saves its final model, prefix model, validation calibrator,
test2 calibrator, feature order, categorical vocabulary, calibration bounds,
window and shrinkage/round choices. All 36 final/prefix bundles were reloaded
from a separate process. Thirty sampled validation/test2 forecast pairs
reproduced saved served probabilities with maximum absolute difference zero,
without reading target columns. CLI serialization uses the importable
`airalert.models.onset.HorizonModel` class.

Train-tail calibration is transferred from the prefix model to the model
refitted on all training rows. This meets chronology and all-matured-data
requirements, but does not guarantee calibration survives that refit or a
later source/rate shift. Prospective stored-forecast scores are needed to
measure production calibration.

## Selection and serving

Candidates are B0, regime-only B1, regime-only M1, full-history M1, and both
M1 windows with the optional geography columns. Each has raw and isotonic
variants. B0's history window and shrinkage are chosen on an inner training
holdout before the calibration tail. B1 retains its handpicked persistence
features and the raion-regime training window.

Each horizon chooses minimum validation Brier **after** the serving transform.
The transform is cumulative maximum, `p_1 <= p_3 <= p_12`, used identically
in validation, test2 and production. Independent horizon choices are combined
and projected again. The resulting ensemble receives its own scores, since
mixing models can change projection results. BSS always compares projected
B0-raw on the same eligible rows.

| Horizon | Candidate saved in selection.json | Selected rounds | Candidate validation served Brier | Combined forecast served Brier |
|---|---|---:|---:|---:|
| 1 h | M1+geo-cal | 212 | 0.083612 | 0.083612 |
| 3 h | M1+geo-cal | 129 | 0.141656 | 0.141656 |
| 12 h | M1-full-cal | 97 | 0.113562 | 0.113575 |

[Selection](../reports/v2/selection.json) was written at
`2026-10-12T17:17:47.129005Z`. Final scoring claimed test2 at
`2026-10-12T17:18:54.692955Z`. The claim retains selection SHA256
`1b2df62e6c41abb222189515384b5cdb31ec6c984bd7528725fa23c99f62ee0b`.
Selection hashes all 13 candidate/combined prediction files. Final scoring
checks these hashes, the snapshot and the gap policy, then checks selection
and prediction hashes again after scoring. A durable exclusive claim rejects
another final evaluation or selection refresh.

## Validation

`-raw` and `-cal` distinguish calibration choices. "Before projection"
means the candidate probabilities before cumulative maximum, including
isotonic calibration for `-cal` candidates. The JSON also contains log-loss,
PR-AUC, reliability bins, and oblast/quiet/active breakdowns for both versions.
BSS in these tables is a fraction.

| Model | H | n | Brier before projection | Served Brier | Served BSS |
|---|---:|---:|---:|---:|---:|
| B0-cal | 1 | 108,528 | 0.096452 | 0.096452 | 0.014382 |
| B0-cal | 3 | 108,290 | 0.162774 | 0.162771 | 0.040017 |
| B0-cal | 12 | 107,219 | 0.127894 | 0.127978 | 0.050726 |
| B0-raw | 1 | 108,528 | 0.097859 | 0.097859 | 0.000000 |
| B0-raw | 3 | 108,290 | 0.169558 | 0.169556 | 0.000000 |
| B0-raw | 12 | 107,219 | 0.134817 | 0.134817 | 0.000000 |
| B1-cal | 1 | 108,528 | 0.089202 | 0.089202 | 0.088468 |
| B1-cal | 3 | 108,290 | 0.159526 | 0.159528 | 0.059142 |
| B1-cal | 12 | 107,219 | 0.143784 | 0.143784 | -0.066510 |
| B1-raw | 1 | 108,528 | 0.089991 | 0.089991 | 0.080408 |
| B1-raw | 3 | 108,290 | 0.161673 | 0.161673 | 0.046494 |
| B1-raw | 12 | 107,219 | 0.145917 | 0.145917 | -0.082335 |
| M1+geo-cal | 1 | 108,528 | 0.083612 | 0.083612 | 0.145591 |
| M1+geo-cal | 3 | 108,290 | 0.141640 | 0.141656 | 0.164550 |
| M1+geo-cal | 12 | 107,219 | 0.117646 | 0.117785 | 0.126333 |
| M1+geo-raw | 1 | 108,528 | 0.083818 | 0.083818 | 0.143489 |
| M1+geo-raw | 3 | 108,290 | 0.142772 | 0.142788 | 0.157871 |
| M1+geo-raw | 12 | 107,219 | 0.117818 | 0.117905 | 0.125447 |
| M1-cal | 1 | 108,528 | 0.083697 | 0.083697 | 0.144718 |
| M1-cal | 3 | 108,290 | 0.141857 | 0.141875 | 0.163257 |
| M1-cal | 12 | 107,219 | 0.117362 | 0.117520 | 0.128297 |
| M1-full+geo-cal | 1 | 108,528 | 0.083677 | 0.083677 | 0.144929 |
| M1-full+geo-cal | 3 | 108,290 | 0.141767 | 0.141797 | 0.163714 |
| M1-full+geo-cal | 12 | 107,219 | 0.113576 | 0.113704 | 0.156606 |
| M1-full+geo-raw | 1 | 108,528 | 0.083769 | 0.083769 | 0.143988 |
| M1-full+geo-raw | 3 | 108,290 | 0.142297 | 0.142338 | 0.160523 |
| M1-full+geo-raw | 12 | 107,219 | 0.113711 | 0.113813 | 0.155797 |
| M1-full-cal | 1 | 108,528 | 0.083757 | 0.083757 | 0.144109 |
| M1-full-cal | 3 | 108,290 | 0.141780 | 0.141820 | 0.163579 |
| M1-full-cal | 12 | 107,219 | 0.113345 | 0.113562 | 0.157655 |
| M1-full-raw | 1 | 108,528 | 0.083782 | 0.083782 | 0.143854 |
| M1-full-raw | 3 | 108,290 | 0.142275 | 0.142319 | 0.160637 |
| M1-full-raw | 12 | 107,219 | 0.113454 | 0.113597 | 0.157401 |
| M1-raw | 1 | 108,528 | 0.084020 | 0.084020 | 0.141418 |
| M1-raw | 3 | 108,290 | 0.143098 | 0.143122 | 0.155904 |
| M1-raw | 12 | 107,219 | 0.117217 | 0.117334 | 0.129682 |
| selected | 1 | 108,528 | 0.083612 | 0.083612 | 0.145591 |
| selected | 3 | 108,290 | 0.141640 | 0.141656 | 0.164550 |
| selected | 12 | 107,219 | 0.113345 | 0.113575 | 0.157565 |

## Test2, evaluated once

| Model | H | n | Brier before projection | Served Brier | Served BSS |
|---|---:|---:|---:|---:|---:|
| B0-cal | 1 | 95,438 | 0.120252 | 0.120252 | 0.029487 |
| B0-cal | 3 | 95,438 | 0.196559 | 0.196537 | 0.068668 |
| B0-cal | 12 | 95,438 | 0.168159 | 0.168040 | 0.064383 |
| B0-raw | 1 | 95,438 | 0.123906 | 0.123906 | 0.000000 |
| B0-raw | 3 | 95,438 | 0.211030 | 0.211028 | 0.000000 |
| B0-raw | 12 | 95,438 | 0.179604 | 0.179604 | 0.000000 |
| B1-cal | 1 | 95,438 | 0.105723 | 0.105723 | 0.146744 |
| B1-cal | 3 | 95,438 | 0.175284 | 0.175275 | 0.169425 |
| B1-cal | 12 | 95,438 | 0.147504 | 0.147492 | 0.178795 |
| B1-raw | 1 | 95,438 | 0.106616 | 0.106616 | 0.139542 |
| B1-raw | 3 | 95,438 | 0.178272 | 0.178272 | 0.155221 |
| B1-raw | 12 | 95,438 | 0.149194 | 0.149194 | 0.169316 |
| M1+geo-cal | 1 | 95,438 | 0.104175 | 0.104175 | 0.159236 |
| M1+geo-cal | 3 | 95,438 | 0.165932 | 0.166061 | 0.213086 |
| M1+geo-cal | 12 | 95,438 | 0.141796 | 0.142101 | 0.208808 |
| M1+geo-raw | 1 | 95,438 | 0.104527 | 0.104527 | 0.156401 |
| M1+geo-raw | 3 | 95,438 | 0.168301 | 0.168374 | 0.202123 |
| M1+geo-raw | 12 | 95,438 | 0.140896 | 0.141209 | 0.213775 |
| M1-cal | 1 | 95,438 | 0.104338 | 0.104338 | 0.157924 |
| M1-cal | 3 | 95,438 | 0.166125 | 0.166395 | 0.211500 |
| M1-cal | 12 | 95,438 | 0.140783 | 0.141049 | 0.214664 |
| M1-full+geo-cal | 1 | 95,438 | 0.102480 | 0.102480 | 0.172919 |
| M1-full+geo-cal | 3 | 95,438 | 0.162266 | 0.162351 | 0.230666 |
| M1-full+geo-cal | 12 | 95,438 | 0.127653 | 0.127954 | 0.287574 |
| M1-full+geo-raw | 1 | 95,438 | 0.102476 | 0.102476 | 0.172949 |
| M1-full+geo-raw | 3 | 95,438 | 0.163106 | 0.163137 | 0.226942 |
| M1-full+geo-raw | 12 | 95,438 | 0.127185 | 0.127391 | 0.290710 |
| M1-full-cal | 1 | 95,438 | 0.102333 | 0.102333 | 0.174103 |
| M1-full-cal | 3 | 95,438 | 0.161549 | 0.161644 | 0.234018 |
| M1-full-cal | 12 | 95,438 | 0.127532 | 0.127689 | 0.289054 |
| M1-full-raw | 1 | 95,438 | 0.102310 | 0.102310 | 0.174289 |
| M1-full-raw | 3 | 95,438 | 0.162293 | 0.162333 | 0.230752 |
| M1-full-raw | 12 | 95,438 | 0.127258 | 0.127367 | 0.290843 |
| M1-raw | 1 | 95,438 | 0.104751 | 0.104751 | 0.154591 |
| M1-raw | 3 | 95,438 | 0.168756 | 0.168930 | 0.199489 |
| M1-raw | 12 | 95,438 | 0.140004 | 0.140168 | 0.219571 |
| selected | 1 | 95,438 | 0.104175 | 0.104175 | 0.159236 |
| selected | 3 | 95,438 | 0.165932 | 0.166061 | 0.213086 |
| selected | 12 | 95,438 | 0.127532 | 0.128373 | 0.285245 |

Full-history competitors beat the locked forecast on several test2 comparisons.
They remain comparisons; no choice was changed after test2 opened. At 12 h,
the combined serving projection raises Brier from 0.127532 to 0.128373.
Reporting the pre-projection value would overstate the skill actually served.

## Uncertainty

The bootstrap resamples 34 whole Kyiv calendar days with replacement. Each
draw pairs model and B0 squared-error sums, retaining spatial and within-day
dependence. There are 2,000 draws, seed 20261012. Intervals are the fifth and
95th percentiles of the paired BSS distribution. Partial edge days retain
their observed rows. These are pointwise horizon intervals, not simultaneous
intervals or protection against serial dependence across days.

| Horizon | n | BSS | 90% lower | 90% upper |
|---|---:|---:|---:|---:|
| 1 h | 95,438 | 0.159236 | 0.143014 | 0.173613 |
| 3 h | 95,438 | 0.213086 | 0.188334 | 0.235649 |
| 12 h | 95,438 | 0.285245 | 0.249847 | 0.320095 |

Exact values and block/sample counts are in
[bootstrap.json](../reports/v2/bootstrap.json).

## Source shift and geography

`f_source_archive` becomes one only after the first archive-sourced start
strictly before `t` for that raion. Future starts cannot activate it. Scalar
archive severity is not a feature, because it lacks historical observation
and transition clocks. All 31 float32 feature columns, including this flag
and seven geography columns, join the prefix-invariance test.

| Validation group | Rows | Mean starts, 24 h | Mean starts, 168 h | Active share |
|---|---:|---:|---:|---:|
| Before first archive start | 85,732 | 2.986983 | 20.132097 | 0.238464 |
| After first archive start | 22,796 | 4.516275 | 33.371555 | 0.367214 |

| Horizon | Before-archive n / served BSS | Archive-era n / served BSS |
|---|---|---|
| 1 h | 85,732 / 0.129398 | 22,796 / 0.186253 |
| 3 h | 85,704 / 0.132616 | 22,586 / 0.244539 |
| 12 h | 85,578 / 0.101351 | 21,641 / 0.328807 |

These different dates and raion mixtures do not establish that counts are
stable across cutover. They show the shift rather than hide it. I1's matched
overlap-day evidence reports an aggregate archive/CSV record-start ratio of
0.973137 and mean per-raion/day ratio of 0.987122. That controlled overlap
comparison is more informative about normalization than these era means.
The accepted episode gate has 96.55% aggregate recall and 97.82% precision;
the lead lowered the daily floor to 88% while retaining the 95% aggregate
floors. Source imperfections remain in the labels.

The flag is constant zero in evaluation training because cutover follows
August 1, so those models cannot learn an archive-specific adjustment from
training. Production refits do include matured archive-era rows. Full details
are in [source_shift.json](../reports/v2/source_shift.json).

The optional CSV covers all 119 units. It contributes border, coastline and
Crimea distances, area and neighbor count. It has no current front-line
distance. [M2c's report](geo-features.md) records the fixed source geometry,
definitions and deliberate front-line omission.

Geography reduces regime-calibrated validation served Brier by 0.000085
at 1 h and 0.000219 at 3 h, but increases it by 0.000265 at 12 h.
For full-history calibrated M1, it improves 1 h and 3 h slightly and worsens
12 h from 0.113562 to 0.113704. Geography was therefore selected only for
the two short horizons. These small validation differences are tuning evidence,
not a claim of a robust causal geography benefit.

## Production behavior

`refit(now)`, `forecast(now)`, `score_recent(now)` and `status()` implement
S1's single-aware-datetime interface. Refit builds all hourly rows from the
training start through the reconciled observation boundary. Each horizon
uses every matured, gap-eligible row in its locked training window. Calibration
uses forward predictions of a six-week-prefix model. Selected round counts
and all other model settings remain fixed. Three independent horizon fits
share read-only feature rows; each LightGBM fit keeps four threads.

Models, every calibrator and B0 references are written to a unique generation.
The small `artifacts/production/current.json` pointer switches only after the
generation is complete. A failed refit leaves the previous generation active.
The tested generation reports `trained_at=2026-10-12T17:21:01.983734Z` and
`data_through=2026-10-11T21:00:00Z`. Its measured refit time is 56.564256 s.

Forecasts use `floor(now)` and require model outcomes strictly before that
hour. Record starts at the hour are excluded and later ends are masked.
Live snapshots require `observed_at < t`; other hazards are excluded. A
disappearing live origin ends at its first successful absent poll, and an
absent raion can close stale archive-censored coverage. Failed polls have
no observation file and cannot clear an alert. Original live start times can
still be inexact. Empty successful polls use filesystem write time as a
conservative availability bound until I1 supplies a receipt manifest.

Remaining ends use M2b's `predict_remaining(raion_id/start rows, t)` when a
production D2 generation is present. Otherwise the existing D1/D0 onset-time
quantiles are returned. This implementation used the existing D2 generation;
it did not edit or refit M2b's module.

The first forecast for an hour is published atomically and never overwritten.
It stores the model generation, actual issuance clock and original projected
B0 probabilities. Repeat calls return that stored hour even after a refit.
The rolling 7/30-day track record scores only reconciled, matured, gap-eligible
outcomes from stored forecasts first issued within 60 seconds of their hour.
Backdated or late manual replays cannot become prospective track-record scores.

The real forecast at `2026-10-12T17:00:00Z` covers 119 raions, with 30 active.
It has ordered probabilities, ordered D2 ends for active rows, and null actual
onsets. The current track record has zero matured eligible forecasts and null
scores. No historical prospective skill was invented.

Evaluation scores describe the locked evaluation models. The later production
refit has different weights and recent calibration; its performance must be
measured on new stored forecasts.

## What changed from v1

- AUD-01: saved train-tail calibration precedes validation; saved validation
  calibration precedes test2. The backward June/July fit was removed.
- AUD-02: every candidate and the mixed forecast are scored after the same
  cumulative maximum used in serving. Pre-projection scores stay visible.
- AUD-03: both application-period calibrators and their prefix models are
  saved and loadable in a fresh server process.
- Horizon choices can differ, and geography is compared on validation.
- Fresh test2 has paired day-block uncertainty intervals.
- Production supports observed-before-hour live inputs, atomic refits,
  immutable forecast storage and an honest rolling track record.

The frozen v1 report and JSON are unchanged. Improved v2 BSS cannot be
attributed to these changes alone, because the period, source, targets,
training window and baseline all changed.

## Delivered

- `src/airalert/models/{onset,baseline}.py` and
  `src/airalert/{backtest,metrics,features,labels,production}.py`.
- `V2_*` constants only in the lead-owned `config.py`. The lead independently
  cleared `DATA_GAPS` and retained the v1 outage constant.
- `tests/test_{onset,split,leakage,production}.py`; existing label tests remain.
- `data/processed/features/v2/`, `data/processed/preds/v2/` and its `issued/`
  hourly store. Thirteen candidate/combined prediction files cover every key.
- `artifacts/v2/{B0,B1,M1,M1-full,M1+geo,M1-full+geo}/` and a complete onset
  generation under `artifacts/production/`.
- `reports/v2/{selection,metrics,bootstrap,source_shift,backfill_watch,track_record}.json`,
  onset fragments under `reports/v2/metrics/`, and this report.

The combined Report contains 156 onset score rows, representing raw/served
versions for validation and test2. It also merges M2b's separately owned
`duration_scores.json`, which currently contributes 92 duration score rows.
No v1 report, duration module, branch or commit was changed by M2a.

## Verified

| Actual command or workflow | Actual result |
|---|---|
| `uv run --no-sync python -m airalert.models.onset` | Exit 0; v2 features built in 23.21 s, all six candidate families trained, 78 validation onset scores. Training finished its final candidate at 1,506.5 s. This first run used the old mask and was superseded by the validation refresh. |
| `uv run --no-sync python -m airalert.models.onset --watch-backfill` | Exit 0; `Backfill: available; 2026-10-11T21:00:00+00:00; 0.0s`. First readiness poll found accepted coverage through yesterday; no further waiting was necessary. |
| `uv run --no-sync python -m airalert.models.onset --refresh-validation` | Exit 0; locked 1/3 h `M1+geo-cal`, 12 h `M1-full-cal`; 78 validation onset scores under cleared gaps. |
| `uv run --no-sync python -m airalert.models.onset --score-only --final` | Invoked once, exit 0; `TEST2 SCORED ONCE; locked selection unchanged`; 156 combined onset scores. |
| Fresh-process joblib reload | All 36 final/prefix candidate bundles loaded as the importable HorizonModel. |
| Saved-artifact reconstruction | 15 validation plus 15 test2 pairs, all horizons; maximum absolute served-probability difference 0.0; no target columns read. |
| `uv run --no-sync python -m airalert.production refit` | Exit 0; H=3 at 31.1 s, H=1 at 39.7 s, H=12 at 56.6 s; total 56.56 s; selected/D2 status with reconciled data-through time. |
| `uv run --no-sync python -m airalert.production forecast` | Exit 0; current-hour 119-raion forecast, 30 active; schema-validated D2 ends; immutable hourly publication. |
| `uv run --no-sync python -m airalert.production score` | Exit 0; 7/30-day windows have n=0 and null Brier/BSS while no eligible outcomes have matured. |
| `uv run --no-sync python -m airalert.production status` | Exit 0; selected onset, D2 duration, persisted trained-at/data-through. |
| Real FastAPI lifespan plus scheduler refresh, scheduling/news disabled, `Paths(metrics=V2_METRICS_PATH)` | `/api/forecast` 200, selected/D2, 119 rows and 30 active; `/api/system` 200 with selected production; `/api/report` 200 with validated 156 onset / 92 duration rows. No network/nightly job was triggered. |
| `uv run --no-sync pytest tests/test_onset.py tests/test_split.py tests/test_labels.py tests/test_production.py tests/test_leakage.py -q --tb=short` | Exit 0; final combined run: **33 passed in 36.56 s**, including the real 50-time leakage guard. |
| `uv run --no-sync pytest -q --tb=short` | 114 passed, 11 setup errors, one existing Starlette/httpx warning, 70.46 s. Every setup error was `tests/test_api.py:53` indexing empty `DATA_GAPS`. |
| Ruff over all assigned Python paths/tests | Exit 0; `All checks passed!` |
| `git diff --check` over assigned paths | Exit 0; only existing LF-to-CRLF notices. |
| Report/immutable-artifact audit | All 78 validation/test2 Markdown score rows equal the rounded metrics JSON. Selection and all 13 prediction hashes still match the final claim. The frozen report and four v1 JSON files match their earlier SHA256 values byte for byte. |

The focused tests check split/embargo/maturity, calibration bounds and saved
reproduction, monotone projection, paired day blocks, closed test2 during
selection, the exclusive final claim, synthetic live observation boundaries,
immutable forecasts and stored-baseline scoring. The real-record leakage
test still compares 50 random times plus adversarial edges, automatically
covering source/geography additions.

## Decisions/assumptions

- The complete-day boundary is read from the saved snapshot, never inferred
  from wall-clock time for evaluation. Wall-clock time sets only the bounded
  backfill-watch target and actual production issuance.
- Per-horizon selection uses served validation Brier. The mixed forecast is
  reported separately; test2 results cannot change its locked choices.
- Production preserves selected calibration/window/features/round settings,
  then refits on every matured row permitted by that window. Geography is
  frozen with the published generation.
- Source-group comparisons are descriptive. The archive flag is causally
  constructed, but is not proof of publication-time availability for the CSV
  or backfilled archive.
- D2 integration uses M2b's existing API and artifacts. Its evaluation and
  nightly refit ownership remain with M2b and the lead.

## Open issues

- The full repository suite is not green because the S1-owned API fixture
  assumes a configured outage exists. No assigned-path test failure remains.
- S1's default `Paths.metrics` still points to frozen v1 JSON. The HTTP check
  explicitly supplied v2 metrics; default live report wiring needs integration.
- The CSV is ended-only and historical archive publication clocks are absent.
  Record-prefix invariance proves event-time reconstruction, not historical
  publication availability. Archive recall/continuity imperfections persist.
- Current live source polls can be stale and original start timestamps can be
  inexact. Empty successful polls lack an explicit receipt-time row.
- Validation is reused for selection and test2 calibration. Only 34 dependent
  Kyiv days support test2 intervals, and no mature prospective track record
  exists yet. Front-line changes are not represented by static geography.
- Current production calibration is transferred from a prefix fit to a refit
  using all matured data. Future drift/reliability must be measured prospectively.

## Contract change requests

1. S1 should give the API fixture an explicit synthetic outage, independent of
   production `DATA_GAPS`, or use the preserved v1 outage constant for its
   frozen-history case. M2a did not edit `tests/test_api.py`.
2. S1/lead should select `reports/v2/metrics.json` for the live report while
   retaining frozen v1 report access. The existing `Paths(metrics=...)` override
   is verified; this does not require a Report schema change.
3. I1 should persist an observed-at manifest for empty successful snapshots,
   so availability does not depend on filesystem metadata. No private settings
   or ingest-owned file was changed by M2a.
4. The lead should keep M2b's `refit_production(now)` in nightly duration upkeep;
   the onset refit intentionally does not write M2b's duration generation.

## Dependencies added

None. The work uses installed LightGBM, scikit-learn, joblib, pandas/numpy and
standard-library concurrency. No commit or git state change was made.
