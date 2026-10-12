# A1 implementation report

## Delivered

- `src/airalert/data.py`: retained the wave-0 fetcher; added CSV validation, the fixed universe, raw coverage expansion, vectorized episode merging, regime detection, the CLI build and public loaders.
- `tests/test_episodes.py`: five focused synthetic tests for oblast and special-city coverage, hromada parents, duplicate preservation, exclusions, nested overlaps, strict merge gaps, finer-level ties and censoring.
- `data/processed/raions.csv`: 119 units, including 118 raions and `kyiv-city/kyiv-city`.
- `data/processed/records.parquet`: **875,633** raw expanded records.
- `data/processed/episodes.parquet`: **431,094** episodes, zero censored.
- `data/processed/snapshot.json`: snapshot **2026-09-07T05:00:00Z**, regime switch **2025-08-14**, and the source SHA256.
- `docs/eda.md` and five black-background PNGs in `docs/eda/`: daily starts, Kyiv local hours, level mix and switch evidence, duration quantiles, and episode counts by raion.
- `docs/eda/reproduce.py` and `docs/eda/verify.py`: reproduce the descriptive analysis and complete-artifact checks.

Only assigned paths were written. No git state changes or dependency changes were made by A1. The original dated CSV and checksum remain unchanged.

## Verified

`uv run airalert episodes` rebuilds from the raw CSV without loading any existing processed artifacts. Final real output:

```text
Source official_data_en_2026-10-12.csv: 299,275 rows; SHA256 b396f87696243069eec0219880e87dcd642b20beb0a7bae0d0e1f61e1ed20ec4
Universe: 119 units; excluded source rows: 2
Records: 875,633; episodes: 431,094; censored: 0
Snapshot: 2026-09-07T05:00:00+00:00; regime switch: 2025-08-14
Timings: load 1.29s; expand 0.92s; merge 0.74s; write 0.35s; total 3.30s
```

The initial build took 3.18 seconds and produced the same counts. Both are below the two-minute limit.

`uv run pytest tests/test_episodes.py`:

```text
============================= test session starts =============================
platform win32 -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: .
configfile: pyproject.toml
plugins: anyio-4.15.1
collected 5 items

tests\test_episodes.py .....                                             [100%]

============================== 5 passed in 0.59s ==============================
```

`uv run ruff check src/airalert/data.py tests/test_episodes.py`:

```text
All checks passed!
```

`uv run ruff check docs/eda/reproduce.py docs/eda/verify.py` also printed `All checks passed!`. `git diff --check -- src/airalert/data.py` reported no whitespace errors.

`uv run python docs/eda/verify.py` checks membership and boundaries over every record and episode, then selects three raions using random seed 42. Real output:

```text
Full-artifact invariants: 875,633 records contained; all 431,094 extents match; all adjacent gaps >= 5 min; UTC us / censor / unique episode checks passed.
RAION kyivska/buchanskyi
RAW RECORDS
  level                started_at               finished_at
hromada 2022-03-16 15:35:56+00:00 2022-03-16 21:05:03+00:00
hromada 2022-03-16 15:35:56+00:00 2022-03-16 21:05:03+00:00
 oblast 2022-03-16 20:36:23+00:00 2022-03-16 21:04:41+00:00
 oblast 2022-03-16 20:36:23+00:00 2022-03-16 21:04:41+00:00
EPISODE
                    start                       end source_level  censored
2022-03-16 15:35:56+00:00 2022-03-16 21:05:03+00:00      hromada     False
Gaps to running end, minutes: [nan, -28.6667]
RAION sumska/shostkynskyi
RAW RECORDS
 level                started_at               finished_at
 raion 2022-03-29 19:14:53+00:00 2022-03-29 21:27:01+00:00
 raion 2022-03-29 19:14:53+00:00 2022-03-29 21:27:01+00:00
oblast 2022-03-29 20:15:54+00:00 2022-03-29 21:27:07+00:00
oblast 2022-03-29 20:15:54+00:00 2022-03-29 21:27:07+00:00
EPISODE
                    start                       end source_level  censored
2022-03-29 19:14:53+00:00 2022-03-29 21:27:07+00:00        raion     False
Gaps to running end, minutes: [nan, -71.1167]
RAION chernihivska/chernihivskyi
RAW RECORDS
 level                started_at               finished_at
oblast 2022-03-22 09:06:38+00:00 2022-03-22 12:52:47+00:00
oblast 2022-03-22 09:06:38+00:00 2022-03-22 12:52:47+00:00
 raion 2022-03-22 10:20:39+00:00 2022-03-22 12:52:29+00:00
 raion 2022-03-22 10:20:39+00:00 2022-03-22 12:52:29+00:00
EPISODE
                    start                       end source_level  censored
2022-03-22 09:06:38+00:00 2022-03-22 12:52:47+00:00       oblast     False
Gaps to running end, minutes: [nan, -152.1333]
```

Confirmed by eye: Buchanskyi's hromada interval contains the later oblast interval; Shostkynskyi's oblast record extends the earlier raion end by six seconds; Chernihivskyi's earlier oblast interval contains the raion interval. Every reported union starts at the first record and ends at the furthest end. The repeated raw rows remain visible as required.

`uv run python docs/eda/reproduce.py` exited successfully and printed the counts, onset numerator/denominator pairs, regime evidence and quality statistics recorded in the EDA, followed by:

```text
Saved five figures in docs/eda/
```

All five images were opened and checked for readable labels, black backgrounds, correct dates and visible quantiles. The daily plot was inspected again after marking the suspected coverage gap. Physical Parquet schemas were inspected: all four time columns use `timestamp[us, tz=UTC]`, and `censored` uses `bool`.

## Decisions/assumptions

The regime rule uses unexpanded source rows. The first UTC day whose trailing 14-day raion share stays strictly above 50% through the observed end is **2025-08-14**. August 13 is 47.08% raion; August 14 is 50.30%, and no later window falls below that threshold. Deduplicating gives the same date. The alternative sustained oblast-share-below-50% rule yields August 5. The final partial day is included. This is retrospective dataset metadata.

`source_level` chooses the earliest start, breaking ties toward hromada, then raion, then oblast. A null end absorbs subsequent coverage into one censored episode with null `end`. The running-end calculation uses integer microseconds, preserving the strict five-minute boundary.

Source duplicates and zero-length intervals are retained. Negative durations fail validation. Geographic identifiers come from `airalert.ids`; exclusions, merge gaps and artifact paths come from configuration. The universe includes all dated parent raions, not only recently active ones.

Public loaders return `pd.DataFrame` with documented columns, sort order and UTC microsecond dtypes. `load_snapshot()` returns a frozen `Snapshot` with `snapshot`, `regime_switch` and `csv_sha256`; both timestamp fields are UTC, and the regime timestamp is midnight. Downstream features must use `load_records()`, then apply the strict as-of mask. Episodes are for targets only.

Overall onset base rates use all 119 raions and hourly forecast times from configured `TRAIN_START`, 2022-04-01, through `snapshot - H`. Positive means an episode starts in `(t, t+H]`. Regimes are determined by forecast time, including windows crossing the switch. These are descriptive EDA rates over the saved data, including its suspected gap; they must not be used as fitted climatology.

| Regime | P(onset within 1 h) | P(onset within 3 h) | P(onset within 12 h) |
|---|---:|---:|---:|
| Oblast | 9.1109% | 23.9445% | 55.8519% |
| Raion | 8.8510% | 23.1360% | 52.6021% |

There are 113,845 exact source duplicates, four zero-length source rows, no negative source durations, 8,672 episodes with cross-level overlaps, and 11,135 episodes below ten minutes. No raion has zero episodes in the raion regime.

## Open issues

1. No source starts or finishes occur from **2026-08-30 through 2026-09-01**. Twenty-eight raions have records spanning these days. This is a suspected coverage gap inside the test period and may distort onset labels and durations. A1 preserves the CSV and does not fabricate or suppress labels. The lead should investigate and decide how affected evaluation windows are handled before final scoring.
2. One zero-duration episode remains at `kharkivska/bohodukhivskyi`, `2025-11-17T01:13:53Z`. B3 must explicitly handle it before applying a logarithm. It is retained for faithful onset targets.
3. The dataset lists alerts after they end, so ongoing alerts at the snapshot are absent. The build supports null-end records, but that does not recover invisible alerts from this copy.

No A1 implementation check remains outstanding.

## Contract change requests

Update the lead-owned `docs/plan.md` regime description from its December 2025 approximation to the documented **2025-08-14** sustained-raion-majority rule, or explicitly select a different rule. No schema or CLI change is needed. Any treatment of suspected coverage gaps belongs in lead-approved label/backtest policy, outside A1's ownership.

## Dependencies added

None. Existing pandas, NumPy, PyArrow, matplotlib, httpx, pytest and Ruff were sufficient.
