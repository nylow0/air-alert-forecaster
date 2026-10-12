# Air Alert Forecaster

Hourly probabilities that an air raid alert starts within 1, 3 or 12 hours in each Ukrainian raion, plus P10/P50/P90 alert duration at onset. A Python pipeline computes forecasts in advance; a black Svelte dashboard replays the test period and shows the latest dataset snapshot.

Hourly onset features use raw records, dropping starts `>= t` and masking ends `> t`; merged episodes supply targets. Duration is predicted at episode onset `s` and may include records with `started_at <= s`, with later ends masked, as specified in [AGENTS.md](AGENTS.md#hard-rule-the-as-of-contract). The [report](docs/report.md#the-as-of-rule) explains the merge-gap trap and the source's publication limitation. Validation calibration has a separate chronology caveat described below.

## From a fresh clone

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and [bun](https://bun.sh/docs/installation), with both on PATH. uv manages Python 3.12 and the locked Python dependencies. Windows/PowerShell works; run the commands below one line at a time from the clone's root. Internet access is needed for dependencies, the alert CSV, and uncached boundaries.

This rebuild replaces generated data and reports. The committed selection at `reports/metrics/selection.json` freezes M1/D1 for the reported run. The training commands compare windows within each model family; they do not rewrite that selection.

In terminal 1:

```powershell
uv sync
uv run airalert fetch
uv run airalert episodes
bunx mapshaper@0.7.83 -v
uv run airalert geo
uv run airalert features
uv run airalert train --model B0
uv run airalert train --model D0
uv run airalert train --model D1
uv run airalert train --model B1
uv run airalert train --model M1
uv run airalert backtest
```

The last command scores validation only. Review validation and freeze the model selection before the final evaluation. For the reported CSV, retain the committed M1/D1 selection. A different CSV requires a new validation decision.

**Score test once, after selection is frozen.** The reported test set has already been scored once. The following command is the final-evaluation step for a fresh rebuild; do not rerun it to tune models or refresh the existing frozen results.

```powershell
uv run airalert backtest --final
uv run python scripts/report_tables.py
uv run airalert precompute
uv run airalert serve --port 8000
```

Leave the backend running at `http://127.0.0.1:8000`. In terminal 2, starting from the clone's root:

```powershell
cd web
bun install
bun run dev
```

Open the Local URL printed by Vite, normally `http://localhost:5173`. Vite proxies `/api` to the backend. Stop either server with Ctrl+C.

After final evaluation, a validation-only `backtest` omits test scores from the merged report. Reading `reports/metrics.json` or running `scripts/report_tables.py` prints existing results without scoring again.

Recorded stage times on the build machine are approximate, not hardware guarantees:

| Stage | Recorded time | Evidence |
|---|---:|---|
| Episodes | 3.18 s | [Data notes](docs/eda.md) |
| Features | 10.02 s | [Feature notes](docs/features.md) |
| B0 | 9.18 s | [B2 handoff](reports/B2-handoff.md) |
| D0 / D1 | 2.61 s / 23.03 s | [Duration notes](docs/duration.md) |
| B1 / M1 | 22.41 s / 250.61 s | [Onset notes](docs/onset.md) |
| Precompute | 1.51 s | [Onset integration evidence](docs/onset.md) |

Install, fetch, geography, and backtest times were not recorded in those handoffs. Network and cache state affect installation and downloads. Cache mapshaper before `geo`, which runs the pinned tool with `bunx --no-install`.

## Reproducibility

`fetch` downloads the live GitHub `official_data_en.csv`, saves a UTC-dated copy under `data/raw/`, and records a `.csv.sha256` checksum. It reuses and verifies an existing copy for the same download date. The reported results use this SHA-256, recorded in [AGENTS.md](AGENTS.md) and `data/processed/snapshot.json`:

```text
b396f87696243069eec0219880e87dcd642b20beb0a7bae0d0e1f61e1ed20ec4
```

The saved copy is named `official_data_en_2026-10-12.csv`, but its observation snapshot is **2026-09-07T05:00:00Z**. A different download can produce different dates, units, predictions, and scores. Keep the dated CSV and checksum for exact reproduction; raw data and fitted artifacts are git-ignored. `episodes` reads the newest dated CSV in `data/raw/`; the CLI has no historical-download flag.

The snapshot and regime date come from `snapshot.json`, not today's clock. The scoring policy excludes the source outage and a 168-hour onset-feature recovery. Frozen metrics and selection are committed under `reports/`. See the [full report](docs/report.md) for their provenance and limitations.

Only the all-validation M1 calibrators used for test/latest are saved; validation-fold calibrators are not. The [independent audit](docs/audit.md#findings) reproduced validation predictions exactly by refitting the folds from saved-model raw outputs and validation labels.

## Results

These headlines come from [the frozen metrics](reports/metrics.json):

- Selected M1, 3-hour test BSS versus B0: **13.843%**.
- Its 3-hour test Brier score: **0.142332**.
- M1-full, which was not selected, has 3-hour test BSS **14.059%**.
- D1's test P10-P90 duration interval covers **75.98%**, below its nominal 80%.

The selected M1 loses slightly to B0 at 12 hours on validation. Full-history M1 wins test Brier at every horizon, but switching after seeing test would be test-set tuning. All models, including losses, appear in the [results tables](docs/report.md#results).

M1's validation calibration uses July outcomes for June probabilities and June outcomes for July probabilities. Validation scores are model-comparison diagnostics, not chronological as-of forecasts. Test calibration uses all validation before test, so the headline test results remain chronologically valid under record-time reconstruction. The protocol stayed frozen after the audit to avoid re-opening selection after seeing test outcomes.

The headlines score per-horizon outputs before serving orders the probabilities. The [report's results](docs/report.md#results) give the audit's served-score comparison, and its [independent audit section](docs/report.md#independent-audit) summarizes rebuild and browser evidence.

## Tests and UI development

After preparing the data, run from the repository root:

```powershell
uv run pytest
uv run ruff check
```

From `web/`, install Chromium once, then run the default mock e2e suite:

```powershell
bunx playwright install chromium
bunx playwright test
```

For the same workflows against real data, leave `uv run airalert serve --port 8000` running in another terminal. In PowerShell, from `web/`:

```powershell
$env:E2E_TARGET = 'live'
bunx playwright test
$env:E2E_TARGET = 'mock'
```

Playwright starts its own Vite server on port 5187; leave that port free. Desktop and mobile projects check rendered interactions and API values. See [e2e instructions](web/tests/README.md) for prerequisites and artifacts. The [independent audit](docs/audit.md#browser-verification) records fresh-clone live and mock results; A3's subsequent UI follow-up is noted in the [report](docs/report.md#independent-audit).

For UI work without a backend, from `web/`:

```powershell
bun run dev:mock
```

Mock forecasts and history are synthetic. Return to real data with `bun run dev` and `VITE_MOCK_API` unset or `0`. `bun run check` and `bun run build` provide frontend type and production-build checks.

## Layout

| Path | Purpose |
|---|---|
| `src/airalert/` | CLI, ingestion, as-of features, models, scoring, precompute, FastAPI |
| `tests/` | Focused Python checks for leakage, boundaries, models, and API contracts |
| `web/` | Svelte dashboard, mock API, and Playwright workflows |
| `data/raw/`, `data/processed/`, `artifacts/` | Downloaded inputs, generated Parquet, and fitted models; git-ignored |
| `data/geo/` | Committed map, adjacency, centroids, and crosswalk |
| `reports/`, `scripts/report_tables.py` | Frozen scores, figures, selection, and Markdown table generator |
| `docs/` | [Report](docs/report.md), [plan](docs/plan.md), and implementation evidence |

## Sources and attribution

Alert records are the official series in [Vadimkin/ukrainian-air-raid-sirens-dataset](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset), maintained by Vadym Klymenko. The upstream repository declares the [MIT license](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset/blob/main/LICENSE). Preserve its copyright and license notice when redistributing covered material. The volunteer series is not used for targets.

Boundaries come from [geoBoundaries gbHumanitarian UKR ADM2](https://www.geoboundaries.org/api/current/gbHumanitarian/UKR/ADM2/) and [OCHA COD-AB / Kartographia](https://data.humdata.org/dataset/cod-ab-ukr), under **CC BY 3.0 IGO**. This project matches dataset names and simplifies the boundaries for display; [geography notes](docs/geo.md) record downloads, hashes, and changes.

Stage 2 text signals were not integrated. The [source study](docs/research/text-sources.md) found early-warning examples, but [Telegram's content-licensing terms](https://telegram.org/tos/content-licensing) prohibit platform-data use for ML without the relevant explicit consent. No text features or text-model scores are included.

