# Air Alert Forecaster: rules for every agent

Predicts air raid alerts per Ukrainian raion: P(an alert starts within 1/3/12 h) and the P10/P50/P90 duration of an alert once it starts. A black Svelte dashboard replays the test period on a map.

- **Plan:** `docs/plan.md` (copy of https://a5f99090aaae9dcaad8479b273d99a69.llm-plans.com/). Read it before you start.
- **This file** records the architect decisions made after the plan. Where the two disagree, this file wins.
- **Owner:** project owner makes the decisions. The lead agent (architect) owns contracts and integration. Implementation agents own the paths assigned to them below.

## Hard rule: the as-of contract

A forecast made at time `t` may only use data from **strictly before `t`**. For every feature function, the result with the full data must equal the result with "the data as it stood at `t`". To rebuild that view, drop records with `started_at >= t` and set `finished_at` to null where `finished_at > t`. `tests/test_leakage.py` enforces this. Known ways to break it are listed in `docs/plan.md` §04. One more trap, found by the architect:

- **The merge gap leaks.** Episodes merge records separated by less than `MERGE_GAP_MIN`. At a time `t` inside such a gap, the full-data episode says "active" while the as-of data says "ended". So features must be computed from as-of *records* (`records.parquet`), never from merged `episodes.parquet`. Merged episodes are only for labels and duration targets.

**The duration model is deliberately different:** its prediction is made *at* the episode start `s`, the moment the alert is issued, so its features may use records with `started_at <= s` (including the starting record itself), with ends after `s` masked. This follows plan §03 "Target (d)". The strict `< t` rule governs every hourly onset forecast.

## Rules for implementation agents

1. Write only to paths you own (see the ownership table). Everything else is read-only to you. If a contract file is wrong or missing something, don't edit it. Put a **Contract change request** in your final report, and work around it locally until the lead decides.
2. Exceptions: anyone may run `uv add <pkg>` / `bun add <pkg>` (in `web/`) to add a dependency. That's the only allowed edit to `pyproject.toml`, `uv.lock`, `web/package.json` and `bun.lock`. If the lock is busy, retry. List added dependencies in your report.
3. No git state changes: don't commit, checkout, reset, stash or rebase. The lead commits after verifying each wave. Don't delete files you don't own.
4. Before saying anything is done, run your brief's "done when" check for real (the actual CLI command, the actual tests, the actual page in a browser), and paste the evidence into your report. A compile or a narrow unit test is not "done".
5. Tests are few and focused. Each one guards a real failure (see `docs/plan.md` §10). No snapshot or smoke-test sprawl.
6. Comments: brief docstrings that tell the next agent how a function or module is used and why it is shaped this way. Keep them accurate when you change code.
7. Python: type hints everywhere and `ruff check` clean. TypeScript: strict mode, never `any`, prefer inferred types.
8. Use vectorised numpy/pandas (`searchsorted` on sorted per-raion times). Never loop in Python over the ~4.7M feature rows.
9. Finish with this report: **Delivered** (paths), **Verified** (commands run and their real output), **Decisions/assumptions**, **Open issues**, **Contract change requests**, **Dependencies added**.

## Facts verified in wave 0 (saved CSV, sha256 `b396f876…`)

- The CSV **ends on 2026-09-07**, not on the plan date. `snapshot` = `2026-09-07T05:00:00Z`. Test = `[2026-08-01T00:00Z, 2026-09-06T17:00Z]` (about 37 days). Always read the snapshot from `snapshot.json`, never from today's date.
- 299,275 rows. Timestamps look like `YYYY-MM-DD HH:MM:SS+00:00`. Levels: 130k oblast, 132k raion, 37k hromada. A hromada's parent raion is in the ordinary `raion` column.
- Universe: **119 units** (118 raions plus `kyiv-city/kyiv-city`). Kyiv appears as oblast `Kyiv City` with an empty raion. Luhansk (`luhanska`) has only oblast-level rows. Crimea and Sevastopol have no rows at all.
- **Regime switch = 2025-08-14** (A1: the first day the trailing 14-day raion-level share stays above 50%). It's in `snapshot.json`, and it supersedes the plan's "~December 2025". So the raion-regime training window is `[2025-08-14, 2026-06-01)`.
- **Data gap:** there are no alert events anywhere from 2026-08-29 05:55Z to 2026-09-02 13:48Z, a source outage inside the test period. `config.DATA_GAPS` holds it hour-aligned as `[2026-08-29T06:00Z, 2026-09-02T14:00Z)`. **Policy:** exclude a forecast row from training, calibration and scoring when `(t, t+H]` intersects a gap, or when `t` is in `[gap.start, gap.end + DATA_GAP_FEATURE_RECOVERY_H)`. Recovery = **168 h**, the longest feature lookback (`f_starts_168h`), so no scored row has a lookback that touches the outage. That leaves scorable test as roughly 2026-08-01 to 2026-08-29. Exclude episodes that overlap a gap from duration training and scoring. The replay still shows those hours, and `Meta.data_gaps` lets the UI mark them. The mask function lives in `backtest.py` and every model uses it.
- **Exact duplicates:** 113,845 of the 299,275 source rows are exact duplicates, and `records.parquet` keeps them. Anything that **counts** records or starts must drop exact duplicate `(raion_id, level, started_at, finished_at)` rows first. Doing so is as-of safe.
- One zero-length episode exists (`kharkivska/bohodukhivskyi`, 2025-11-17). Duration code must clip the duration to at least 1 minute before taking the log.
- Descriptive onset base rates (A1, full history): 1 h ≈ 9%, 3 h ≈ 23%, 12 h ≈ 53–56%.
- `finished_at` is never empty: the dataset lists an alert only after it ends. So alerts still running at the snapshot are invisible in the `latest` hour. Note this as a limitation; it doesn't break the as-of rule.

## Conventions

- Python 3.12 via uv, package `airalert` under `src/`, CLI `uv run airalert <cmd>`. Frontend: `web/` uses Svelte 5, Vite, TypeScript, Tailwind 4 and bun.
- Timestamps are always timezone-aware UTC: `datetime64[us, UTC]` in pandas, `timestamp[us, tz=UTC]` in Parquet, ISO 8601 with `Z` in JSON. Forecast times are hourly, on the hour, in UTC. Calendar features and the UI use `Europe/Kyiv`.
- Constants live only in `src/airalert/config.py`: horizons, `MERGE_GAP_MIN`, split dates, excluded oblasts and paths. Never hardcode them anywhere else.
- Data layout: `data/raw/` (dated CSV copies with `.sha256`), `data/processed/`, `data/geo/` (committed), `data/text/`, `artifacts/<model_id>/` (fitted models), `reports/` (metrics and figures). Everything under `data/` is git-ignored except `data/geo/`. `artifacts/` is git-ignored.

## Identifiers

- `slug(s)`: NFKD-normalise to ASCII, lowercase, drop apostrophes (`'` `’` `ʼ` `` ` ``), replace each run of other non-`[a-z0-9]` characters with `-`, and strip `-` at the ends.
- `oblast_id` = `slug(<dataset English oblast name>)` with a trailing ` oblast` removed first. Example: `cherkaska`.
- `raion_id` = `<oblast_id>/<slug(<dataset English raion name>)>`, with a trailing ` raion` removed first. Example: `cherkaska/umanskyi`.
- A city with special status that has no raions in the dataset (for example Kyiv) is a single unit: `raion_id = <oblast_id>/<oblast_id>`.
- Implemented once, in `src/airalert/ids.py`. The map side is matched to these ids, never the other way round.
- **G1 fallback:** if more than 5% of dataset raions have no polygon, the unit becomes the oblast (`raion_id = oblast_id`). Every module must treat `raion_id` as an opaque string so this switch is cheap.

## Raion universe

Every raion that appears in the dataset's raion- or hromada-level records (any date), plus special-status city units, **minus** the excluded oblasts: Luhansk, the Autonomous Republic of Crimea and Sevastopol. They have been under permanent alerts that the dataset doesn't list. The universe is fixed by geography. Never filter it by activity in any period. `data/processed/raions.csv` is the one list everyone uses.

## Data contracts (Parquet unless noted)

| Path | Producer | Columns |
|---|---|---|
| `data/processed/records.parquet` | A1 | `raion_id`, `oblast_id`, `level` (`oblast`/`raion`/`hromada`), `started_at`, `finished_at` (null = still active at the snapshot). One row per (source record × covered raion), after expansion (oblast → all its raions, hromada → parent raion). Sorted by `raion_id`, `started_at`. **The as-of feature input.** |
| `data/processed/episodes.parquet` | A1 | `raion_id`, `oblast_id`, `start`, `end` (null if censored), `source_level` (level of the earliest record in the episode), `censored` (bool). Merged with `MERGE_GAP_MIN`. **Labels and duration targets only.** |
| `data/processed/raions.csv` | A1 | `raion_id`, `oblast_id`, `oblast_name`, `raion_name` |
| `data/processed/snapshot.json` | A1 | `{"snapshot": ISO, "csv_sha256": str, "regime_switch": ISO date}`. `snapshot` = latest timestamp in the CSV, floored to the hour. |
| `data/geo/raions.geojson` | A2 | FeatureCollection, simplified to under 600 KB. Each feature's properties are `raion_id`, `oblast_id`, `name` (English). Polygons with no dataset raion get `raion_id: null`. |
| `data/geo/adjacency.json`, `data/geo/centroids.csv`, `data/geo/crosswalk.csv` | A2 | Adjacency: `{raion_id: [raion_id, ...]}`. Centroids: `raion_id,lat,lon`. Crosswalk: map polygon → `raion_id`, with the match method. |
| `data/processed/features/{train,val,test,latest}.parquet` | B1 | Keys `t`, `raion_id`, `oblast_id`. Features are named `f_*` and are float32, and **`f_active` (0/1, active as-of `t`) is required** because backtest slices quiet vs active on it. Labels `y_1`, `y_3`, `y_12` are nullable: null when `t + H` is past the snapshot, always null in `latest`. One row per (hourly `t` × raion in the universe). B1 publishes an early version (keys, labels and `f_active`) first, then signals with `.lead/B1_LABELS_READY` (the one `.lead/` file B1 may write). |
| `data/processed/preds/onset_<model_id>.parquet` | each onset model | `t`, `raion_id`, `split` (`val`/`test`/`latest`), `p_1`, `p_3`, `p_12` (calibrated). |
| `data/processed/preds/duration_<model_id>.parquet` | each duration model | `raion_id`, `start`, `split`, `q10_min`, `q50_min`, `q90_min`, for every episode with `start >= VAL_START − 7 days`, so the replay and history panel are covered. `split` is decided by the episode **start**: `train`/`val`/`test`, or `latest` for starts after the test end. Only `val`/`test` get scored. The duration episode split (start in range, the episode ends before the next split starts, no data-gap overlap, not censored) lives in `models/duration.py`. The forecast-row split, embargo and gap mask live in `backtest.py`. |
| `data/processed/forecasts.parquet` | C2 | Exactly the `ForecastRow` fields from `schemas.py`, plus `t`. Monotonicity across horizons is enforced here. |
| `reports/metrics/<fragment>.json` → `reports/metrics.json` | backtest (B2) | Each fragment is a partial `Report` (see `schemas.py`). `backtest` scores every preds file and writes `scores_val.json` (and `scores_test.json` with `--final`). C1 writes `selection.json` (the served models). `airalert.metrics.merge_reports()` concatenates the lists into `reports/metrics.json`. Only one writer per fragment file. Model variants are just distinct model ids (for example `M1-raionregime`), so they get scored like any other model. |

**Splits** (forecast time `t`, in UTC): train `[2022-04-01, 2026-06-01)`, val `[2026-06-01, 2026-08-01)`, test `[2026-08-01, snapshot − 12h]`, latest = `{snapshot}`. Embargo: for horizon H, drop rows whose label window crosses the next split's start (train rows with `t > 2026-06-01 − H`, val rows with `t > 2026-08-01 − H`). The split and embargo code lives only in `backtest.py`. Test metrics are computed only with `airalert backtest --final`. Writing test *predictions* is allowed, because the replay needs them. Scoring test without `--final` is not.

**Model ids:** `B0` climatology, `B1` logistic regression, `M1` LightGBM onset, `D0` empirical duration, `D1` LightGBM quantile duration. A stage-2 text variant adds `+text` (for example `M1+text`).

## CLI contract (`src/airalert/cli.py`, lead-owned)

Each subcommand imports its target lazily, so a module that isn't built yet doesn't break the others.

| Command | Calls | Owner |
|---|---|---|
| `fetch` | `airalert.data.fetch()` | A1 (L0 writes a first version) |
| `episodes` | `airalert.data.build_episodes()` | A1 |
| `geo` | `airalert.geo.build()` | A2 |
| `features` | `airalert.features.build()` | B1 |
| `train --model {B0,B1,M1,D0,D1}` | `models.baseline.train()` / `models.onset.train(id)` / `models.duration.train(id)` | B2 / C1 / B3 |
| `backtest [--final]` | `airalert.backtest.run(final)`: scores every preds file it finds and writes fragments, then `metrics.json` | B2 |
| `precompute` | `airalert.precompute.run()` | C2 |
| `serve [--port 8000]` | `airalert.api.serve(port)` | C2 |

## API contract

`src/airalert/schemas.py` (pydantic) is the source of truth. `web/src/lib/types.ts` is a hand-copy of it. The fixtures in `web/src/lib/fixtures/` must validate against the schemas (`tests/test_fixtures.py`). The API runs on `127.0.0.1:8000`, and Vite proxies `/api` to it.

- `GET /api/meta` → `Meta`
- `GET /api/raions` → the GeoJSON from `data/geo/raions.geojson`
- `GET /api/forecast?at=ISO` → `ForecastResponse`. Without `at`, it returns the latest hour (`mode: "latest"`, and `actual_onset_*` is null).
- `GET /api/raion/{raion_id}/history?from=ISO&to=ISO` → `HistoryResponse`. `raion_id` contains a `/`, so the route uses a path converter.
- `GET /api/report` → `reports/metrics.json` exactly as stored (`Report`)

## Ownership

| Path | Owner |
|---|---|
| `AGENTS.md`, `docs/plan.md`, `docs/design-notes.md`, `.lead/`, `src/airalert/{config,ids,schemas,cli}.py`, `web/src/lib/types.ts`, `web/src/lib/fixtures/`, `tests/test_fixtures.py`, `.gitignore` | lead (wave 0 written by L0) |
| `src/airalert/data.py`, `tests/test_episodes.py`, `docs/eda.md`, `data/raw/`, `data/processed/{records,episodes}.parquet`, `raions.csv`, `snapshot.json` | A1 |
| `src/airalert/geo.py`, `data/geo/`, `data/raw/geo/`, `docs/geo.md`, `docs/geo_map.png` | A2 |
| `web/` (except the lead-owned files above and `web/tests/`) | A3 |
| `docs/research/text-sources.md` | R1 |
| `src/airalert/features.py`, `src/airalert/labels.py`, `tests/test_leakage.py`, `tests/test_labels.py`, `data/processed/features/`, `docs/features.md` | B1 |
| `src/airalert/backtest.py`, `src/airalert/metrics.py`, `src/airalert/models/baseline.py`, `tests/test_split.py`, `reports/` (except fragments owned by others), `data/processed/preds/onset_B0.parquet` | B2 |
| `src/airalert/models/duration.py`, `tests/test_duration.py`, `data/processed/preds/duration_*`, `artifacts/D*/`, `docs/duration.md` | B3 |
| `src/airalert/models/onset.py`, `tests/test_onset.py`, `docs/onset.md`, `data/processed/preds/onset_{B1,M1}*`, `artifacts/{B1,M1}*/`, `reports/metrics/selection.json`, `reports/figures/onset_*` | C1 |
| `src/airalert/api.py`, `src/airalert/precompute.py`, `tests/test_api.py`, `data/processed/forecasts.parquet` | C2 |
| `docs/research/text-signal.md`, `scripts/text_probe.py` | R2 |
| `web/tests/`, `web/playwright.config.ts`, `docs/audit.md` | D1 |
| `README.md`, `docs/report.md`, `scripts/report_tables.py` | D2 |
| `src/airalert/text/`, `tests/test_text_leakage.py`, `data/text/` | T1 |

## Frontend notes

Full `#000` background and a dark UI only. Onest variable font (`@fontsource-variable/onest`, which covers Cyrillic). Draw the map as SVG with `d3-geo`, using a projection fitted to Ukraine, with no tiles. Use a single-hue sequential scale for probability and a white outline for raions where an alert really started. The layout must work from 1440 px down to 390 px. Every number on the report page comes from `/api/report`. Read `[private home path removed]` and `docs/design-notes.md` (if it exists) before building UI.

# v2: live data, news agent (architect decisions, 2026-10-12)

The v1 results (`reports/`, `docs/report.md`) are **frozen history**. v2 extends the data to "now", adds a live map and an optional news agent. Where v2 rules conflict with v1 sections above, v2 wins.

## Sources (from `docs/research/live-sources.md`)
- **Live (every 60 s):** `siren.pp.ua/api/v3/alerts` (UkraineAlarm mirror: no token, red/yellow `activeAlertLevels`, 50 req/min). Optional supported alerts.in.ua API if the env var `ALERTS_IN_UA_TOKEN` is set (read from the process env or a git-ignored `.env`; never logged, never sent to the browser).
- **Backfill and nightly reconcile:** the alerts.in.ua web day-archive `api.alerts.in.ua/v3/alerts/day/{war_day}.json`, Kyiv-calendar days. It must be validated against the CSV on overlapping days before use. Be polite: ≥ 2 s between requests, a descriptive User-Agent, and cache raw responses.
- The CSV (`official_data_en_*.csv`) stays the source up to its cutover. Telegram is **never** a label source.
- Provenance is kept on every row (`source`). Mirrors are deduplicated by origin event.

## Data contracts v2
- `data/processed/records.parquet` gains `severity` (`red`/`yellow`/`unknown`; CSV rows = `unknown`) and `source` (`csv`/`aiu_web`/`siren`/`aiu_api`). It stays **air-raid only**. Other hazard types (artillery, urban fights, …) go to `data/processed/hazards.parquet`. A severity escalation inside an alert is a transition, not a new record start.
- `data/live/` (git-ignored, under `data/`): `raw/` (gzip poll and archive responses), `state.json` (the current live state), `ledger.json` (the reconciled Kyiv-day watermark and coverage gaps).
- **Display units:** all 139 map polygons, keyed by crosswalk `shape_id`. The forecast universe stays 119 `raion_id`s. Crimea, Luhansk and Sevastopol polygons show live status (inherited oblast-level, marked `inherited`), with no forecast. The Chornobyl zone inherits only Kyivska **oblast-wide** alerts, and is otherwise `unknown`.
- `config.DATA_GAPS` becomes data-driven: the outage is removed only if the archive fill validates; otherwise it stays.
- The as-of rule is unchanged. Live features at `t` use only records observed before `t`.

## Schedules (one process: `uv run airalert serve`)
- Every 60 s: poll live → `state.json` and the raw archive. A failed poll keeps the last state and marks it `stale`; it never clears alerts.
- At 00:00 Europe/Kyiv (an IANA-aware scheduler): reconcile every missing complete Kyiv day through yesterday (the ledger watermark), rebuild records, episodes and features, refit production models, recompute forecasts, and score yesterday's forecasts (the rolling live track record). On startup: the same catch-up runs if the watermark is behind.
- Every hour on the hour: a "now" forecast from current data.

## Ownership v2 (supersedes the v1 table where they overlap)
| Path | Owner |
|---|---|
| `src/airalert/sources/`, `src/airalert/ingest.py`, `src/airalert/data.py`, `tests/test_ingest.py`, `docs/ingest.md`, `data/live/` | I1 |
| `src/airalert/live.py`, `src/airalert/scheduler.py`, `src/airalert/api.py`, `src/airalert/precompute.py`, `tests/test_live.py`, `tests/test_api.py` | S1 |
| `src/airalert/models/`, `src/airalert/backtest.py`, `src/airalert/metrics.py`, `src/airalert/features.py`, `src/airalert/labels.py`, `src/airalert/production.py`, `reports/v2/`, `docs/report-v2.md` | M2 |
| `src/airalert/news/`, `tests/test_news.py`, `docs/news.md`, `data/news/` | N1 |
| `web/` (as before) | F1 |
| `AGENTS.md`, `src/airalert/{config,ids,schemas,cli}.py`, `web/src/lib/types.ts`, `.gitignore`, `scripts/pre_commit_secret_guard.py` | lead |
Each backend feature exposes a FastAPI `APIRouter` from its own module; `api.py` (S1) includes them.

## Secrets and privacy (hard rule)
Never write tokens, the ChatGPT/Codex auth, the owner's email, `~` paths, or local PIDs into a tracked file. LLM calls go through the local Codex CLI (`codex exec`), whose credentials live in `[private home path removed]` and are never read, copied or logged by this app. Local settings: `data/settings.local.json` (git-ignored).
