# B3 duration model handoff

D1 improves validation mean pinball loss by 8.06% and median MAE by 5.95% over D0. The selected model trains on the raion regime only. Its P10-P90 interval covers 77.43% of validation episodes, below the nominal 80%. No test metrics were computed.

## Delivered

- `src/airalert/models/duration.py`: start-time features, episode eligibility, D0 empirical quantiles, D1 quantile boosting, and CLI training.
- `tests/test_duration.py`: seven checks covering synthetic and real as-of equality, the merge gap, duplicates, split/gap/censoring rules, zero durations, fallback cells, and quantile ordering.
- `data/processed/preds/duration_D0.parquet` and `duration_D1.parquet`: 31,647 rows each, with exactly `raion_id, start, split, q10_min, q50_min, q90_min`.
- `artifacts/D0/`: three empirical-cell Parquet tables and `metadata.json` with global quantiles and validation scores.
- `artifacts/D1/`: `q10_min.txt`, `q50_min.txt`, `q90_min.txt` LightGBM boosters and `metadata.json` with the candidates and selection.
- `docs/duration.md`: this write-up and verification evidence.

Each prediction file contains 2,289 train-history episodes, 18,293 val episodes, 10,932 test episodes, and 133 latest episodes. All episodes starting at or after `VAL_START - 7 days` are included, including episodes ineligible for scoring. Timestamps are UTC microseconds. Both saved model formats reproduced five exported validation predictions when reloaded.

## Verified

Both commands exited successfully on the real dataset. Output from the final run follows.

`uv run airalert train --model D0`

```text
D0: train episodes=396,606; val episodes=18,262; raion-regime train=71,160
Start-time features: 431,094 rows in 2.22s
D0 val n=18,262: pinball q10=12.2980, q50=50.2715, q90=45.2417; median MAE=100.5430 min; 80% coverage=78.2773%
Wrote 31,647 predictions to .\data\processed\preds\duration_D0.parquet; 2.61s
```

`uv run airalert train --model D1`

```text
D1: train episodes=396,606; val episodes=18,262; raion-regime train=71,160
Start-time features: 431,094 rows in 2.14s
D1 candidate full, leaves=15, train=396,606: mean pinball=33.9759; iterations=[176, 350, 170]
D1 candidate full, leaves=31, train=396,606: mean pinball=33.7033; iterations=[160, 349, 193]
D1 candidate raion-regime, leaves=15, train=71,160: mean pinball=33.0420; iterations=[171, 181, 176]
D1 candidate raion-regime, leaves=31, train=71,160: mean pinball=33.0635; iterations=[128, 144, 147]
D1 val n=18,262: pinball q10=12.1642, q50=47.2816, q90=39.6803; median MAE=94.5632 min; 80% coverage=77.4340%
Wrote 31,647 predictions to .\data\processed\preds\duration_D1.parquet; 23.03s
```

`uv run pytest tests/test_duration.py`

```text
collected 7 items
tests\test_duration.py .......                                           [100%]
============================= 7 passed in 10.27s ==============================
```

`uv run ruff check src/airalert/models/duration.py tests/test_duration.py`

```text
All checks passed!
```

B2's scorer became available during this task. `uv run airalert backtest` exited successfully without `--final`:

```text
val H=1 masks: {'rows': 174216, 'embargo': 0, 'data_gap': 0, 'null_label': 0, 'excluded_union': 0}
val H=3 masks: {'rows': 174216, 'embargo': 238, 'data_gap': 0, 'null_label': 0, 'excluded_union': 238}
val H=12 masks: {'rows': 174216, 'embargo': 1309, 'data_gap': 0, 'null_label': 0, 'excluded_union': 1309}
Wrote scores_val.json: 3 onset and 2 duration scores
B0 val: H=1 n=174216 base_rate=0.102092 Brier=0.085156 logloss=0.295375 PR-AUC=0.224269
B0 val: H=3 n=173978 base_rate=0.255923 Brier=0.152842 logloss=0.459571 PR-AUC=0.523801
B0 val: H=12 n=172907 base_rate=0.534328 Brier=0.139615 logloss=0.443871 PR-AUC=0.875006
Validated metrics.json: 3 onset, 2 duration scores
```

The `duration` entries read from `reports/metrics.json` and validated against `Report` were:

```json
[
  {
    "model": "D0",
    "split": "val",
    "n": 18262,
    "pinball_q10": 12.297950991129122,
    "pinball_q50": 50.271518955572596,
    "pinball_q90": 45.241690523126344,
    "mae_q50_min": 100.54303791114519,
    "coverage_80": 0.7827729711970212
  },
  {
    "model": "D1",
    "split": "val",
    "n": 18262,
    "pinball_q10": 12.164183138096618,
    "pinball_q50": 47.28158724353541,
    "pinball_q90": 39.68025832525756,
    "mae_q50_min": 94.56317448707082,
    "coverage_80": 0.7743401598948636
  }
]
```

Independent checks confirmed complete, unique prediction keys, start-based splits, UTC microsecond timestamps, finite predictions at least one minute long, and ordered quantiles on all 31,647 rows for each model. Local validation scores match B2's metric functions. Rebuilding historical episodes from five truncated record views also matched the recent-duration feature. The merged report contained no test scores.

Five eligible val episodes sampled with seed 42 are shown below. All values are minutes; each prediction cell is P10 / P50 / P90.

| Raion | Start UTC | Actual | D0 | D1 |
|---|---|---:|---|---|
| donetska/kalmiuskyi | 2026-07-10 09:28:14 | 119.48 | 26.92 / 212.68 / 910.28 | 29.68 / 276.64 / 1069.48 |
| khersonska/kakhovskyi | 2026-06-05 07:57:26 | 51.78 | 15.80 / 40.82 / 232.68 | 13.12 / 25.61 / 84.88 |
| mykolaivska/bashtanskyi | 2026-07-24 23:26:29 | 26.58 | 14.50 / 46.20 / 220.56 | 14.24 / 28.18 / 105.74 |
| dnipropetrovska/pavlohradskyi | 2026-06-26 12:41:18 | 52.43 | 15.50 / 38.87 / 200.32 | 19.10 / 47.87 / 213.23 |
| khersonska/kakhovskyi | 2026-07-01 09:49:27 | 25.70 | 12.67 / 34.77 / 148.23 | 14.02 / 26.64 / 67.25 |

## Decisions/assumptions

**Features.** `build_start_features(records, episodes, raions, regime_switch)` reads only episode keys, not episode targets. Its 16 columns are categorical raion/oblast, Kyiv hour/weekday/hour bucket, regime, starting source level, national and oblast counts of distinct raions active or started within 15/60 minutes, oblast active share, the median duration of the last five ended episodes, and minutes since the previous episode ended. Numeric columns are float32. Missing history remains missing for LightGBM.

The duration-specific boundary includes record starts exactly at `s`. Coverage uses raw records without merging gaps. Exact duplicate coverage rows are dropped before computing features. Recent-start windows include both endpoints. Source-level ties follow A1's hromada, raion, oblast priority. Active coverage requires a known record start and an end strictly later than `s`, or an unknown end.

Recent duration history is rebuilt from causal record prefixes using the configured merge gap. A current group's end is its prefix maximum at `s`, never the group's final full-data end. This preserves provisional ended history inside a short gap when a future record will extend the merged episode. Loops run over geographic units, not feature rows; timestamp queries use NumPy `searchsorted`.

**Episode eligibility.** B2 imports `episode_split_mask(episodes, split, snapshot=None)`. It enforces starts in the configured split, uncensored observed ends, ends strictly before the next split for train/val, and no overlap with a configured data gap. Test starts stop inclusively at `snapshot - TEST_END_OFFSET_H`; observed test ends may be later, up to the snapshot. Prediction assignment uses `episode_split(episodes, snapshot)` and remains separate from eligibility. There are 18,293 val-start episodes and 18,262 eligible val targets; 31 cross the next split boundary.

**D0.** Empirical quantiles use training episodes only. Cells with at least 30 episodes take priority in this order: oblast x six-hour Kyiv bucket x regime, oblast x regime, regime, global. Quantiles are sorted and floored at one minute.

**D1 tuning.** Three quantile models predict log minutes, at alpha 0.1/0.5/0.9. Targets are clipped to one minute before taking logs. Two tree settings are compared across full-history and raion-regime training, using only validation mean pinball loss after sorting the outputs. Learning rate is 0.05, L2 is 1, seed is 42, four threads are used, and boosting stops after 35 rounds without validation improvement or at 350 rounds. Early stopping measures pinball in minutes. Selected boosters are retained with training-only fitting; validation is not added to their training data.

| Training window | Eligible train episodes | Leaves | Minimum leaf | Mean val pinball |
|---|---:|---:|---:|---:|
| Full history | 396,606 | 15 | 80 | 33.9759 |
| Full history | 396,606 | 31 | 150 | 33.7033 |
| Raion regime | 71,160 | 15 | 80 | 33.0420 |
| Raion regime | 71,160 | 31 | 150 | 33.0635 |

The selected D1 starts training at the saved regime switch, 2025-08-14, with 15 leaves and minimum leaf size 80. Best iterations are 171 / 181 / 176. The full-history models include the regime flag. Snapshot 2026-09-07T05:00:00Z and source checksum are read from A1 metadata and saved with fitted artifacts.

## Open issues

No B3 implementation or backtest integration blockers remain. D1's interval undercovers by 2.57 percentage points on validation; D0 also undercovers by 1.72 points. Serving-model selection remains the lead's decision. The ended-alert-only source omits alerts still running at the snapshot. UI integration and test-set scoring belong to later owners and were not performed by B3.

## Contract change requests

None. B2's `episode_split_mask` import is implemented in the B3-owned module. No contract files were edited.

## Dependencies added

None. Existing NumPy, pandas, LightGBM and Pydantic installations were used.
