# B2 handoff

## Delivered

- `src/airalert/backtest.py`: shared splits, embargo and gap masks; validation-first scoring for every onset/duration predictions file; identical-row BSS; reliability figures.
- `src/airalert/metrics.py`: pure numpy-array metrics and validated, atomic merging of Report fragments.
- `src/airalert/models/baseline.py`: training-only B0 tables, validation selection of window and positive shrinkage strength, predictions for validation, test and latest.
- `tests/test_split.py`: 12 fast synthetic checks, including complete parquet/report workflows for onset and duration.
- `data/processed/preds/onset_B0.parquet`: 279,293 rows, with 174,216 validation, 104,958 test and 119 latest predictions. Times are `timestamp[us, tz=UTC]`.
- `artifacts/B0/`: per-horizon raion counts, oblast rates, oblast fallback rates, and `metadata.json` with tuning comparisons, provenance and current gap policy.
- `reports/metrics/scores_val.json`, `reports/metrics.json`: three B0 onset scores and two duration scores, all validation.
- `reports/figures/reliability_val_{1,3,12}h.png`: black-background reliability figures, each with one axis and a diagonal reference.

No files owned by another agent were edited. No git state changes were made.

## Verified

Real B1 labels were available through `.lead/B1_LABELS_READY`. They had the required keys, labels and `f_active`. The actual B0 training and backtest CLI completed successfully. Production test labels were not scored.

### Actual training command and output

`uv run airalert train --model B0`, exit 0:

```text
B0 H=1 full: train_n=4346832 val_n=174216 best_k=1 val_Brier=0.087268
B0 H=1 raion-regime: train_n=831096 val_n=174216 best_k=1 val_Brier=0.085156
H=1 train masks: {'rows': 4346832, 'embargo': 0, 'data_gap': 0, 'null_label': 0, 'excluded_union': 0}
H=1 val masks: {'rows': 174216, 'embargo': 0, 'data_gap': 0, 'null_label': 0, 'excluded_union': 0}
B0 H=3 full: train_n=4346594 val_n=173978 best_k=1 val_Brier=0.164328
B0 H=3 raion-regime: train_n=830858 val_n=173978 best_k=1 val_Brier=0.152845
H=3 train masks: {'rows': 4346832, 'embargo': 238, 'data_gap': 0, 'null_label': 0, 'excluded_union': 238}
H=3 val masks: {'rows': 174216, 'embargo': 238, 'data_gap': 0, 'null_label': 0, 'excluded_union': 238}
B0 H=12 full: train_n=4345523 val_n=172907 best_k=1 val_Brier=0.156459
B0 H=12 raion-regime: train_n=829787 val_n=172907 best_k=24 val_Brier=0.139615
H=12 train masks: {'rows': 4346832, 'embargo': 1309, 'data_gap': 0, 'null_label': 0, 'excluded_union': 1309}
H=12 val masks: {'rows': 174216, 'embargo': 1309, 'data_gap': 0, 'null_label': 0, 'excluded_union': 1309}
Wrote onset_B0.parquet: 279293 rows {'latest': 119, 'test': 104958, 'val': 174216}; 9.18s
```

### Actual scoring command and output

`uv run airalert backtest`, exit 0:

```text
val H=1 masks: {'rows': 174216, 'embargo': 0, 'data_gap': 0, 'null_label': 0, 'excluded_union': 0}
val H=3 masks: {'rows': 174216, 'embargo': 238, 'data_gap': 0, 'null_label': 0, 'excluded_union': 238}
val H=12 masks: {'rows': 174216, 'embargo': 1309, 'data_gap': 0, 'null_label': 0, 'excluded_union': 1309}
Wrote scores_val.json: 3 onset and 2 duration scores
B0 val: H=1 n=174216 base_rate=0.102092 Brier=0.085156 logloss=0.294586 PR-AUC=0.225166
B0 val: H=3 n=173978 base_rate=0.255923 Brier=0.152845 logloss=0.459399 PR-AUC=0.524431
B0 val: H=12 n=172907 base_rate=0.534328 Brier=0.139615 logloss=0.443871 PR-AUC=0.875006
Validated metrics.json: 3 onset, 2 duration scores
```

| H | n | Base rate | Brier | Log-loss | PR-AUC |
|---:|---:|---:|---:|---:|---:|
| 1 | 174,216 | 0.102092 | 0.085156 | 0.294586 | 0.225166 |
| 3 | 173,978 | 0.255923 | 0.152845 | 0.459399 | 0.524431 |
| 12 | 172,907 | 0.534328 | 0.139615 | 0.443871 | 0.875006 |

B0's BSS is zero, including each nonempty oblast/state slice, because the reference predictions are identical.

The real duration files were scored on 18,262 eligible validation episodes each:

| Model | Pinball P10, min | Pinball P50, min | Pinball P90, min | Median MAE, min | 80% coverage |
|---|---:|---:|---:|---:|---:|
| D0 | 12.297951 | 50.271519 | 45.241691 | 100.543038 | 0.782773 |
| D1 | 12.164183 | 47.281587 | 39.680258 | 94.563174 | 0.774340 |

A separate `uv run python -` read both generated JSON files and validated them with `Report.model_validate_json`. It also inspected the prediction schema and computed gap exclusions from the test clock and fixed universe only. No test labels or predictions were used for those counts. Actual output:

```text
scores_val.json VALID 3 onset 2 duration
metrics.json VALID 3 onset 2 duration
Test scores present: False
scores_test.json exists: False
Recovery: 168
Prediction schema: [('t', 'timestamp[us, tz=UTC]'), ('raion_id', 'large_string'), ('split', 'large_string'), ('p_1', 'double'), ('p_3', 'double'), ('p_12', 'double')]
Unscored test mask counts 1 embargo 0 gap 24395
Unscored test mask counts 3 embargo 0 gap 24633
Unscored test mask counts 12 embargo 0 gap 25704
```

There are no configured gaps inside training or validation, so their gap exclusion counts are zero. Embargo removes 0 / 238 / 1,309 rows in each of training and validation for H=1/3/12. The current 168-hour recovery window extends beyond the test end, so affected replay hours after the outage remain unscorable.

### Synthetic checks and lint

`uv run pytest tests/test_split.py`, exit 0, actual final output:

```text
============================= test session starts =============================
platform win32 -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: .
configfile: pyproject.toml
plugins: anyio-4.15.1
collected 12 items

tests\test_split.py ............                                         [100%]

============================= 12 passed in 4.00s ==============================
```

`uv run ruff check src/airalert/backtest.py src/airalert/metrics.py src/airalert/models/baseline.py tests/test_split.py`, exit 0:

```text
All checks passed!
```

The synthetic workflows prove that default scoring filters predictions to validation before reading them, never opens test features, ignores an invalid stale `scores_test.json`, and rejects undeclared missing forecast rows. The restricted comparison produces BSS=-3 against B0 on its one matching row. The duration parquet workflow uses B3's real helper to exclude censored, gap-overlapping and split-crossing episodes, clips a zero-length target to one minute, and verifies explicit final scoring and report selection. All final-scoring test data lives in pytest temporary directories.

### Reliability figure inspected

I opened `reports/figures/reliability_val_3h.png` myself. The labels, legend, diagonal and B0 curve are readable on black. B0 underpredicts in most middle bins: mean prediction 0.2489 corresponds to observed frequency 0.3292 across 29,142 rows; 0.3499 corresponds to 0.4390 across 22,515 rows. The 0.55 bin is close to the diagonal. The lowest bin slightly overpredicts, and the highest occupied bin has only 809 rows. The figure contains the eight occupied bins, while the Report contains all ten bins with null means for empty bins.

## Decisions/assumptions

### Shared functions for C1/C2

- `assign_split(t, *, snapshot=None)` accepts an aware datetime/string, pandas Series or DatetimeIndex. Scalars return a split name; arrays return an index-preserving Series. The snapshot defaults to the saved dataset snapshot.
- `trainable_mask(df, horizon, *, snapshot=None)` returns a boolean KEEP Series for eligible training rows only. It requires `t` and `y_<horizon>`.
- `scorable_mask(df, horizon, *, final=False, snapshot=None)` returns a boolean KEEP Series for eligible validation rows. Only `final=True` permits test rows. Training, latest and out-of-range rows never survive.
- `embargo_mask(df, horizon)` and `data_gap_mask(df, horizon)` expose the same underlying KEEP rules for diagnostics.

Split assignment uses forecast time and does not trust a caller's split column. A label window ending exactly at the next split start survives; one ending after it does not. A label window ending at a gap start intersects that gap and is excluded. Null labels never survive either main mask. Naive or null timestamps and nonbinary labels raise errors.

The lead updated `AGENTS.md` and `config.DATA_GAP_FEATURE_RECOVERY_H` to 168 during implementation. The code always reads the configured value; no recovery duration is hardcoded. Final verification and artifact metadata use 168.

### B0 selection

Per horizon, validation Brier chooses among positive pseudo-counts `1, 24, 168, 720, 2160`. The smallest candidate keeps shrinkage present even when validation prefers almost unsmoothed raion rates. Each window gets its own best k, then the lower validation Brier wins. Exact window ties favor full history. No refit uses validation labels.

| H | Full-window best k | Full-window Brier | Regime best k | Regime Brier | Served B0 window |
|---:|---:|---:|---:|---:|---|
| 1 | 1 | 0.087268 | 1 | 0.085156 | Raion regime |
| 3 | 1 | 0.164328 | 1 | 0.152845 | Raion regime |
| 12 | 1 | 0.156459 | 24 | 0.139615 | Raion regime |

The regime boundary comes from `snapshot.json`, 2025-08-14. Each fit uses only `trainable_mask` rows. Probabilities use `(positives + k * oblast_cell_rate) / (count + k)`. Unseen cells fall back to oblast cell, oblast overall, then the fitted training global rate. Local hour and weekday/weekend use Europe/Kyiv with DST. Test/latest reads select keys only, never target columns.

### Scoring and fragments

Full prediction files must cover the feature keys exactly. Restricted onset files must declare a nonempty `row_set` column or an `@row-set` filename suffix, for example `onset_M1+text@text-window.parquet`. Both forms are supported; if both appear, they must agree. The suffix is separated from the model id. Duplicate keys, unknown keys and invalid probabilities raise errors. B0 is scored first, and every BSS calculation joins its predictions onto the exact model/slice rows.

Duration eligibility remains owned by B3. The harness calls `models.duration.episode_split_mask` and `episode_split`, checks known episode keys and complete eligible coverage, and rejects crossed or negative quantiles. Losses and MAE use minutes.

`run(False)` writes validation scores only and omits stale test fragments from the merged Report. `run(True)` writes both fragments and logs `TEST SET SCORED (--final explicitly enabled)`. `merge_reports()` validates every fragment, rejects duplicate score identities and takes model selection only from `selection.json`. It defaults to merging all fragments for the final integration stage; validation-only backtests call it with `include_test=False`.

## Open issues

No B2 issues remain in the verified training/backtest workflow. Production test scoring remains for the lead's final evaluation. `selection.json` was absent during verification, so the combined Report has null selection until C1 publishes it. This handoff covers the CLI/data workflow; the dashboard belongs to the frontend and integration agents.

The source lists ended alerts only, so ongoing alerts at the snapshot are invisible. The saved snapshot and the source outage also limit the replay's interpretation. These dataset limitations remain unchanged.

## Contract change requests

None. B3 already exposes the shared duration eligibility functions, and real B1 columns match the required contract.

## Dependencies added

None. numpy, pandas, pyarrow, scikit-learn, matplotlib, pydantic, pytest and ruff were already present.
