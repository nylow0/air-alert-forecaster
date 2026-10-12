# Features and labels

B1 builds features from `records.parquet` and labels from `episodes.parquet`.
The full rebuild on 2026-10-12 took **10.02 seconds**, including loading, labels,
and writing all four files. It produced **4,626,125 rows** and 23 float32 features.

## Time and counting rules

`build_features(records, times)` accepts arbitrary aware timestamps, including
episode start times for duration inference. It converts them to UTC microseconds
and returns every raion in `load_raions()`, even when a supplied as-of record view
has no history for that raion. The optional `FeatureContext` holds fixed geography
and the saved regime cutoff, so repeated evaluations can avoid reloading them.
Keys remain `t`, `raion_id`, and `oblast_id`.

Record starts must be strictly before `t`. A record end is known when it is at or
before `t`, provided the record started before `t`. This follows the prescribed
as-of reconstruction, which masks only ends after `t`. Coverage has already ended
at its exact end timestamp. Zero-length records contribute starts and known ends,
but never active coverage or alert minutes.

All start-count windows are **[t-W, t)**. Counts use distinct **record start times
per raion**, after dropping exact `(raion_id, level, started_at, finished_at)`
duplicates. Two levels with the same start count once. Different record starts
during an existing alert count separately. These are record activity features;
the onset target uses merged episode starts. No feature closes the merge gap.

Minute ages are NaN when there is no eligible history. Features do not infer an
end from a future start, and minutes since an end refer to any known record end,
including an end while another overlapping record is still active.

## Feature dictionary

| Name | Definition | Why it is as-of safe |
|---|---|---|
| `f_active` | 1 when at least one positive-length record started before `t` and has no end at or before `t`; otherwise 0 | Counts starts `< t` and eligible ends `<= t`; ignores zero-length coverage |
| `f_minutes_since_start` | Minutes since the most recent distinct record start `< t` | Searches only past starts |
| `f_minutes_since_end` | Minutes since the most recent known record end | Searches positive-length ends `<= t` and zero-length ends `< t`; unknown ends are absent |
| `f_starts_1h` | Distinct record starts in `[t-1h, t)` | Searches only past starts |
| `f_starts_6h` | Distinct record starts in `[t-6h, t)` | Searches only past starts |
| `f_starts_24h` | Distinct record starts in `[t-24h, t)` | Searches only past starts |
| `f_starts_168h` | Distinct record starts in `[t-168h, t)` | Searches only past starts |
| `f_alert_minutes_24h` | Minutes in the union of actual coverage intervals over `[t-24h, t)`; short gaps remain uncovered | Both coverage integrals stop at or before `t`; future starts and replacing future ends by open ends cannot change that prefix |
| `f_lat` | Raion centroid latitude | Fixed geography, independent of record activity |
| `f_lon` | Raion centroid longitude | Fixed geography, independent of record activity |
| `f_adjacent_active_share` | Fraction of adjacent universe raions active at `t`; 0 for an isolated raion | Aggregates the same as-of active flags; excludes self and duplicate neighbors |
| `f_adjacent_starts_30m` | Sum of distinct start counts per adjacent raion in `[t-30m, t)` | Aggregates only eligible past starts; simultaneous starts in different raions each count |
| `f_adjacent_starts_60m` | Sum of distinct start counts per adjacent raion in `[t-60m, t)` | Same rule with a 60-minute window |
| `f_oblast_active_share` | Fraction of the oblast's fixed-universe raions active at `t`, including the current raion | Aggregates as-of active flags with a fixed denominator |
| `f_country_active` | Number of universe raions active at `t` | Aggregates as-of active flags |
| `f_country_started_15m` | Number of universe raions with at least one start in `[t-15m, t)` | Aggregates past-start indicators, counting each raion once |
| `f_country_started_60m` | Number of universe raions with at least one start in `[t-60m, t)` | Same rule with a 60-minute window |
| `f_hour` | Fractional local hour in `Europe/Kyiv`; integral on hourly forecast rows | Deterministic from `t`, including daylight saving |
| `f_hour_sin` | `sin(2*pi*f_hour/24)` | Deterministic from `t` |
| `f_hour_cos` | `cos(2*pi*f_hour/24)` | Deterministic from `t` |
| `f_weekday` | Kyiv weekday, Monday 0 through Sunday 6 | Deterministic from `t` |
| `f_weekend` | 1 on Kyiv Saturday or Sunday | Deterministic from `t` |
| `f_regime_raion` | 1 when `t >= snapshot.json`'s regime switch, currently 2025-08-14 | Uses the required saved cutoff as fixed metadata; does not re-estimate it from the supplied records |

The regime cutoff was determined retrospectively by A1. This is the requested
regime feature, not an online regime detector. No additional features were added.

## Labels and publication

`build_labels(episodes, times, snapshot)` labels an episode start in **(t, t+H]**,
using `config.HORIZONS`. The left endpoint is excluded and the right endpoint is
included. `y_H` is null when `t+H > snapshot`, including all latest labels.
Labels use pandas nullable `Int8`; Parquet stores nullable `int8`.

The early files contained `t, raion_id, oblast_id, f_active, y_1, y_3, y_12`.
They were published in 5.53 seconds, after the label tests passed, and signaled by
`.lead/B1_LABELS_READY` at `2026-10-12T09:10:56.189727+00:00`. The full files keep
the early columns' types and meanings. Each file is now staged and atomically
replaced so a concurrent reader cannot open a partially written Parquet footer.

The CLI rebuilds from raw records and target episodes, without reading existing
feature files. It processes one split at a time. Production loops iterate over
raions, oblasts, neighbors, and a handful of windows, never over forecast rows.
Sorted arrays, `searchsorted`, and cumulative union lengths evaluate all times
together. All feature arrays are float32; the integer union integrals retain
microsecond precision before converting minutes to float32.

| Split | Rows | First `t`, UTC | Last `t`, UTC | Parquet size, MiB |
|---|---:|---|---|---:|
| train | 4,346,832 | 2022-04-01 00:00 | 2026-05-31 23:00 | 56.12 |
| val | 174,216 | 2026-06-01 00:00 | 2026-07-31 23:00 | 1.79 |
| test | 104,958 | 2026-08-01 00:00 | 2026-09-06 17:00 | 1.15 |
| latest | 119 | 2026-09-07 05:00 | 2026-09-07 05:00 | 0.02 |

There is no embargo or data-gap mask in these files. Backtest owns both. The test
file retains all **12,376 rows** inside the 104-hour source outage. These base
rates are descriptive labels over unmasked rows, not scored test metrics.

## Leakage guard

`tests/test_leakage.py` samples 50 distinct hourly timestamps with seed
`20261012`: 10 before the regime switch, 10 after it within train, 15 in val,
and 15 in test. It adds the latest snapshot and these real-data adversarial probes:

- `cherkaska/cherkaskyi` at its record start, `2026-06-01T19:14:02Z`.
- One microsecond after that start, `2026-06-01T19:14:02.000001Z`.
- Exactly at the end, `2026-06-02T02:56:09Z`.
- `2022-04-01T12:52:47Z`, inside a 4-minute-48-second gap in that raion between
  `12:50:23Z` and `12:55:11Z`.

For every probe, it drops starts `>= t` and masks ends `> t`, then compares every
`f_*` column with full-data features, using zero relative tolerance and absolute
tolerance `1e-6`, with matching NaNs. Fixed keys are checked too. A synthetic case
checks feature values for overlapping coverage, duplicate and cross-level starts,
zero-length records, null ends, window boundaries, and a two-minute gap. It runs
even when real artifacts are absent; the real-data test is skipped only then.

The planted leak reads the final duration of an alert still running at `t`.
Full data gives 30 minutes; the as-of view gives 0. The same checker must raise
`AssertionError` containing `As-of mismatch in f_leak`. `pytest.raises` requires
that failure, so a checker that accepts the leak makes the test fail.

## Busy-day sanity check

The day with the most combined distinct record starts for
`kharkivska/chuhuivskyi` and `kyiv-city/kyiv-city` in validation was
**2026-07-19 UTC**, with 17 starts. The following saved-feature rows were printed
beside the deduplicated raw records and checked by eye. Minute ages are rounded
to two decimals here; stored values remain float32.

### Chuhuiv

| UTC hour | `f_active` | Minutes since last start | Starts in last 1h | `y_1` | `y_3` |
|---|---:|---:|---:|---:|---:|
| 00:00 | 1 | 314.65 | 0 | 1 | 1 |
| 01:00 | 1 | 34.12 | 1 | 0 | 0 |
| 02:00 | 1 | 94.12 | 0 | 0 | 0 |
| 03:00 | 1 | 154.12 | 0 | 0 | 1 |
| 04:00 | 1 | 214.12 | 0 | 0 | 1 |
| 05:00 | 0 | 274.12 | 0 | 1 | 1 |
| 06:00 | 1 | 3.72 | 1 | 0 | 1 |
| 07:00 | 1 | 63.72 | 0 | 1 | 1 |
| 08:00 | 1 | 4.47 | 1 | 1 | 1 |
| 09:00 | 1 | 18.40 | 1 | 1 | 1 |
| 10:00 | 1 | 19.72 | 1 | 0 | 1 |
| 11:00 | 1 | 79.72 | 0 | 1 | 1 |
| 12:00 | 1 | 27.57 | 1 | 0 | 1 |
| 13:00 | 0 | 87.57 | 0 | 1 | 1 |
| 14:00 | 0 | 53.25 | 1 | 0 | 1 |
| 15:00 | 0 | 113.25 | 0 | 1 | 1 |
| 16:00 | 1 | 24.57 | 1 | 0 | 1 |
| 17:00 | 1 | 84.57 | 0 | 1 | 1 |
| 18:00 | 1 | 21.92 | 1 | 0 | 1 |
| 19:00 | 1 | 81.92 | 0 | 0 | 1 |
| 20:00 | 0 | 141.92 | 0 | 1 | 1 |
| 21:00 | 1 | 14.27 | 2 | 1 | 1 |
| 22:00 | 1 | 34.58 | 1 | 0 | 1 |
| 23:00 | 1 | 94.58 | 0 | 1 | 1 |

Raw records are all raion-level. Times are on July 19 unless a date is shown.

| Start, UTC | End, UTC |
|---|---|
| July 18 18:45:21 | 00:05:42 |
| 00:25:53 | 04:44:54 |
| 05:56:17 | 07:36:50 |
| 07:55:32 | 08:17:14 |
| 08:41:36 | 09:10:17 |
| 09:40:17 | 11:15:53 |
| 11:32:26 | 12:44:23 |
| 13:06:45 | 13:45:41 |
| 15:35:26 | 17:26:12 |
| 17:38:05 | 19:07:12 |
| 20:31:53 | 20:35:53 |
| 20:45:44 | 21:08:01 |
| 21:25:25 | 23:33:44 |
| 23:47:19 | July 20 01:05:44 |

At 05:00, coverage ended at 04:44:54, so active is 0; the 05:56:17 start makes
`y_1=1`. At 21:00, the 20:45:44 record is still active, its age is 14.27 minutes,
and the starts at 20:31:53 and 20:45:44 give a count of 2. At 03:00 the next
05:56:17 start is outside 1h but inside 3h, giving `y_1=0, y_3=1`.

### Kyiv city

| UTC hour | `f_active` | Minutes since last start | Starts in last 1h | `y_1` | `y_3` |
|---|---:|---:|---:|---:|---:|
| 00:00 | 1 | 98.72 | 0 | 0 | 0 |
| 01:00 | 0 | 158.72 | 0 | 0 | 1 |
| 02:00 | 0 | 218.72 | 0 | 0 | 1 |
| 03:00 | 0 | 278.72 | 0 | 1 | 1 |
| 04:00 | 0 | 35.10 | 1 | 0 | 1 |
| 05:00 | 0 | 95.10 | 0 | 0 | 1 |
| 06:00 | 0 | 155.10 | 0 | 1 | 1 |
| 07:00 | 1 | 4.83 | 1 | 0 | 0 |
| 08:00 | 0 | 64.83 | 0 | 0 | 0 |
| 09:00 | 0 | 124.83 | 0 | 0 | 0 |
| 10:00 | 0 | 184.83 | 0 | 0 | 1 |
| 11:00 | 0 | 244.83 | 0 | 0 | 1 |
| 12:00 | 0 | 304.83 | 0 | 1 | 1 |
| 13:00 | 0 | 43.10 | 1 | 0 | 0 |
| 14:00 | 0 | 103.10 | 0 | 0 | 0 |
| 15:00 | 0 | 163.10 | 0 | 0 | 0 |
| 16:00 | 0 | 223.10 | 0 | 0 | 0 |
| 17:00 | 0 | 283.10 | 0 | 0 | 0 |
| 18:00 | 0 | 343.10 | 0 | 0 | 0 |
| 19:00 | 0 | 403.10 | 0 | 0 | 1 |
| 20:00 | 0 | 463.10 | 0 | 0 | 1 |
| 21:00 | 0 | 523.10 | 0 | 1 | 1 |
| 22:00 | 0 | 33.75 | 1 | 0 | 0 |
| 23:00 | 0 | 93.75 | 0 | 0 | 0 |

Raw records are all oblast-level and cover the special-status city unit.

| Start, UTC | End, UTC |
|---|---|
| July 18 22:21:17 | 00:13:42 |
| 03:24:54 | 03:47:48 |
| 06:55:10 | 07:16:05 |
| 12:16:54 | 12:38:49 |
| 21:26:15 | 21:41:32 |

At 04:00 the 03:24:54 start is in the previous hour, but its record ended at
03:47:48, so the count is 1 and active is 0. At 07:00 the 06:55:10 record is
active and is 4.83 minutes old. At 12:00 there is no active record, while the
12:16:54 start makes both labels 1. These comparisons confirm the displayed
values against the raw records rather than relying on label base rates alone.

## B1 delivery report

### Delivered

- `src/airalert/labels.py`
- `src/airalert/features.py`
- `tests/test_labels.py`
- `tests/test_leakage.py`
- `data/processed/features/{train,val,test,latest}.parquet`
- `.lead/B1_LABELS_READY`
- `docs/features.md`

### Verified

`uv run airalert features`, final full rebuild:

```text
train: 4,346,832 rows; y_1=8.974789%; y_3=23.640642%; y_12=55.263742%
val: 174,216 rows; y_1=10.209166%; y_3=25.619346%; y_12=53.491069%
test: 104,958 rows; y_1=10.197412%; y_3=25.267250%; y_12=49.750376%
latest: 119 rows; y_1=null; y_3=null; y_12=null
Elapsed: 10.02s
```

`uv run pytest tests/test_leakage.py tests/test_labels.py`:

```text
collected 6 items
tests\test_leakage.py ...                                                [ 50%]
tests\test_labels.py ...                                                 [100%]
6 passed in 23.56s
```

`uv run ruff check src/airalert/features.py src/airalert/labels.py tests/test_leakage.py tests/test_labels.py`:

```text
All checks passed!
```

An additional read of the saved artifacts checked unique `(t, raion_id)` keys,
119 raions in every split, binary active flags and historical labels, alert
minutes within `[0, 1440]`, null latest labels, the exact split endpoints, and
the Parquet schema. Its output included:

```text
23 float32 features; UTC microsecond t; nullable int8 labels
outage hours retained: 12376
rows 4626125
```

The 48-row saved-feature sanity table and 19 deduplicated raw records above were
printed and checked by eye. No model was fitted and no test prediction was scored.

### Decisions/assumptions

- Count distinct raw record starts per raion, not merged episode starts.
- Preserve actual short gaps in coverage-minute features and active flags.
- Use `[t-W, t)` for historical windows and the specified known-end rule at `t`.
- Keep NaN ages when history is absent; downstream models own missing-value handling.
- Use the fixed universe and saved regime metadata, independent of the record view.
- Publish early labels first and atomically replace each completed full split file.

### Open issues

No B1 implementation blockers remain. The source lists alerts only after they
end, so genuinely ongoing alerts absent from the saved CSV cannot appear in
latest features. The source outage corrupts lookbacks and labels; B2 must apply
the configured gap and recovery masks before training or scoring.

The configured 24-hour feature recovery does not restore `f_starts_168h`.
After recovery, **9,044 test rows across 76 hours**, from
`2026-09-03T14:00:00Z` through `2026-09-06T17:00:00Z`, still have a 168-hour
lookback overlapping the source outage. This is a coverage-policy issue,
not as-of leakage. The files retain these rows as required.

### Contract change requests

Lead decision requested: reconcile the 24-hour gap recovery policy with the
168-hour start-count feature. Options include extending recovery to 168 hours,
or explicitly omitting or treating that feature as missing while its window
overlaps a source gap. B1 has not changed the masking policy or downstream code.

### Dependencies added

None.
