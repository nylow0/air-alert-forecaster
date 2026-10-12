# Air Alert Forecaster

A local research dashboard for Ukrainian air raid alerts. It shows separate live red and yellow maps, hourly probabilities of a new alert within 1, 3 or 12 hours, and estimated remaining time for active alerts. The server updates its data and production models nightly. An optional news agent reads public Telegram previews and adds experimental probability adjustments.

Live display covers 139 map polygons; forecasts cover the fixed 119-unit universe. Crimea, Luhansk and Sevastopol can show inherited live status but have no forecast. Chornobyl has limited inherited coverage. See the [v2 overview](docs/overview-v2.md), [ingest evidence](docs/ingest.md) and [operations guide](docs/operations.md).

**Research demo only. Do not use these forecasts or map colours for safety decisions. Follow official alert channels and instructions, including [UkraineAlarm](https://www.ukrainealarm.com/). Yellow is an active alert. A low forecast probability is not an all-clear.**

## Quickstart from a fresh clone

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Bun](https://bun.sh/docs/installation). uv manages Python 3.12 and the locked Python dependencies. Run each command from the repository root unless a block says otherwise. Downloads require internet access.

### Build the data once

The dates below reproduce the accepted source overlap and cutover in [the ingest report](docs/ingest.md). The archive gate must pass for the downloaded CSV before backfill can publish data. If the CSV changes, review and revalidate the overlap rather than treating old agreement as current.

```powershell
uv sync
uv run airalert fetch
uv run airalert episodes
bunx mapshaper@0.7.83 -v
uv run airalert geo
uv run python -m airalert.ingest validate --days 2026-08-20 2026-08-21 2026-08-22 2026-08-23 2026-08-24 2026-08-25 2026-08-26 2026-08-27 2026-09-03 2026-09-04 2026-09-05 2026-09-06
uv run python -m airalert.ingest backfill --cutover 2026-08-29T06:00:00+00:00
uv run airalert backfill
uv run airalert features
```

The `bunx` call caches the pinned mapshaper used by `geo`. The ingest module exposes validation and initial cutover selection; the main `airalert backfill` command exposes neither option. The module call initializes the ledger and reconciles through yesterday. The following `airalert backfill` resumes that ledger and is also the manual catch-up command.

The adopted cutover repairs the old CSV outage. With an empty ledger and no configured gaps, plain `airalert backfill` instead starts on the CSV's last day. Set the reviewed cutover on initialization. Existing ledgers retain their cutover.

`airalert features` builds the main feature tables. V2 evaluation builds its separate tables through `airalert.features.build_v2()`, called by the onset module below. Committed `data/geo/static_features.csv` supplies static geography; rebuilding it uses `uv run python -m airalert.geo_features`. See [geography provenance](docs/geo-features.md).

### Prepare models and serving artifacts

**Fresh-clone release blocker:** the current checkout includes `reports/v2/test2_claim.json`, which makes `train_v2()` reject retraining. Evaluation models, predictions and serving Parquet are git-ignored. Production refit needs those evaluation models, and API startup needs serving Parquet. A plain clone therefore cannot finish model setup with the existing entrypoints. It needs a release artifact bundle or a bootstrap command that preserves the frozen evaluation. Neither is supplied here. See [operations](docs/operations.md).

These are the implemented v2 evaluation entrypoints, documented in [onset](docs/report-v2.md#verified) and [duration](docs/duration-v2.md#verified). They rebuild evaluation artifacts and write validation results; they are not a workaround for the frozen claim:

```powershell
uv run python -m airalert.models.onset
uv run python -m airalert.models.duration
```

There is no `airalert train --v2` or `airalert train --model D2`. The main `train` command is the older pipeline. Do not remove test2 markers or rescore the published evaluation to start the app.

On a prepared checkout with the evaluation artifacts and predictions present, initialize the production duration model, refit the locked onset choices, issue a current-hour forecast, and build serving Parquet:

```powershell
uv run python -m airalert.models.duration --production
uv run python -m airalert.production refit
uv run python -m airalert.production forecast
uv run airalert precompute
uv run airalert serve
```

Production refit keeps the chosen model settings and uses eligible matured outcomes. It does not reopen test2 selection. `precompute` uses v2 predictions when present. The API binds to `http://127.0.0.1:8000`; `serve --port 8000` is also supported. Keep one server process running.

### Start the dashboard

In a second terminal, from the repository root:

```powershell
cd web
bun install
bun run dev
```

Open the Local URL printed by Vite, normally `http://localhost:5173`. Vite proxies `/api` to the backend. Stop the servers with Ctrl+C.

For a dashboard preview without data or models, run `bun run dev:mock` from `web/` after installing dependencies. It uses synthetic fixtures and labels them as mock data. The [frontend implementation report](web/IMPLEMENTATION.md) records browser checks.

## What the server does automatically

Once initialized, `uv run airalert serve` owns the background jobs:

- About every 60 seconds, poll live status and cache the raw response. A failed poll preserves the last state and marks it stale; it never clears alerts.
- Every UTC hour on the hour, publish a forecast for now from inputs observed before that hour. The first stored forecast for an hour is immutable.
- At 00:00 Europe/Kyiv, reconcile missing complete Kyiv days through yesterday, rebuild records, episodes and features, refit production duration and onset models, refresh serving data, and score matured stored forecasts, including yesterday's eligible forecasts.
- On startup, run the same catch-up when the ledger watermark is behind yesterday. Failed reconciliation stops at the failed day and resumes there later.

The midnight scheduler handles Kyiv daylight-saving changes. Long jobs and upstream failures can delay polling or publication. Read live freshness and pipeline status in the UI; current live maps stay current even while viewing historical replay. Implementation is in [scheduler.py](src/airalert/scheduler.py); [operations](docs/operations.md) covers running it.

## Optional features

### News agent

News is off by default. Install the [Codex CLI](https://learn.chatgpt.com/docs/codex/cli), make `codex` available on PATH to the backend, and use **Login with ChatGPT** in Settings. Run the backend under the same OS account as that CLI login. Codex owns the browser flow, credential storage and refresh; the app never reads the credential cache. [Official authentication documentation](https://learn.chatgpt.com/docs/auth) explains the login.

Enable news deliberately in Settings after checking the source terms. Both selected models and reasoning efforts must be available to the signed-in account. Calls share that account's usage allowance. An unavailable model, quota limit or invalid output leaves the base forecast available.

The agent archives observed message versions locally, validates quoted evidence, and applies bounded upward adjustments only to an eligible current-hour base. Telegram never supplies alert labels. The overlay has no measured predictive benefit, and the CLI's reactive tool guard is not proof of a strict no-tools sandbox. See [news behavior and limits](docs/news.md), [harness research](docs/research/news-harness.md) and [privacy](docs/privacy.md).

### alerts.in.ua token

The default live source, siren.pp.ua, needs no token. The supported alerts.in.ua API is optional. Request a project token through the [official form](https://alerts.in.ua/api-request), copy [.env.example](.env.example) to a local `.env`, and set `ALERTS_IN_UA_TOKEN` there or in the backend process environment. The token stays server-side and must never reach git, logs or browser code. A token does not establish permission or unlimited retention for the private web archive. See [source research](docs/research/live-sources.md).

## Results

These are the locked combined forecast's **served test2** scores against monotone-projected B0 on identical rows, after enforcing `p_1 <= p_3 <= p_12`. Values come from [v2 metrics](reports/v2/metrics.json), with intervals from [bootstrap.json](reports/v2/bootstrap.json).

| Horizon | Brier skill versus B0 | 90% interval |
|---|---:|---|
| 1 h | 15.92% | 14.30% to 17.36% |
| 3 h | 21.31% | 18.83% to 23.56% |
| 12 h | 28.52% | 24.98% to 32.01% |

Test2 runs from September 8 through October 11, 2026, with 95,438 rows per horizon. The intervals resample 34 whole Kyiv calendar-day blocks in 2,000 paired draws. Correlated raion-hours are not independent samples. Selection used validation before test2 opened; test2 was scored once. [Protocol and uncertainty](docs/report-v2.md) give the exact boundaries and limits.

Duration improved but remains under its nominal 80% coverage. D1c onset coverage is 77.26% on test2; D2 remaining-time coverage at eight elapsed hours is 70.23%. These values come from [duration_scores.json](reports/v2/duration_scores.json); [duration notes](docs/duration-v2.md) explain the comparison.

V1 is a frozen earlier evaluation in [docs/report.md](docs/report.md) and [reports/metrics.json](reports/metrics.json). Its numbers are unchanged. Different periods, sources and targets prevent attributing the v2 gain to a single change. The production refit and news overlay need prospective evaluation; the published [rolling track record](reports/v2/track_record.json) currently has no matured eligible scores.

## Sources, licenses and terms

| Source | Use and terms |
|---|---|
| [Vadimkin CSV](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset) | Historical official-series alerts before the accepted cutover. The repository declares [MIT](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset/blob/main/LICENSE); preserve its license and copyright notice when redistributing covered material. |
| [siren.pp.ua / uasiren](https://github.com/PaulAnnekov/uasiren), [UkraineAlarm](https://api.ukrainealarm.com/) | Current red/yellow air-raid levels. siren is an unofficial mirror with shared upstream inputs. The [source study](docs/research/live-sources.md) verified no general data/ML license or availability guarantee. |
| [alerts.in.ua](https://devs.alerts.in.ua/) | Optional supported token API; historical reconciliation uses its web day archive. The archive is a private upstream schema and may change or break. Requests are cached, use a descriptive User-Agent and start at least two seconds apart. Archive acceptance and remaining discrepancies are in [ingest notes](docs/ingest.md). Public access is not a broad reuse license. |
| [Ubilling](https://wiki.ubilling.net.ua/doku.php?id=aerialalertsapi) | Secondary, display-only permanent-alert evidence for Sevastopol. It is inherited status, not direct red/yellow observation or a forecast label. See [ingest handling](docs/ingest.md#actual-live-handling). |
| [geoBoundaries humanitarian UKR ADM2](https://www.geoboundaries.org/api/current/gbHumanitarian/UKR/ADM2/), [OCHA / Kartographia](https://data.humdata.org/dataset/cod-ab-ukr) | Raion boundaries under CC BY 3.0 IGO, matched and simplified for display. Preserve attribution and identify modifications. [Geography notes](docs/geo.md) record provenance. |
| [Natural Earth](https://www.naturalearthdata.com/about/terms-of-use/) | Public-domain border and coastline data for static geography. [M2c's report](docs/geo-features.md) pins the sources and explains the omitted front line. |
| [Telegram public previews](https://telegram.org/tos/content-licensing) | Opt-in news only. Telegram's terms restrict AI use, including deployment, and describe explicit source-user consent for exceptions. The owner chose to pursue this feature; that choice does not establish the required consent or a reuse license. [News terms](docs/news.md#telegram-terms) record the unresolved basis. |

Source-level evidence and availability matter. Historical event timestamps do not prove publication before a forecast. Live inputs retain an observation clock; CSV severity is unknown, and historical archive colour coverage is incomplete. Forecasts target air-raid episode starts; other hazards stay separate.

## Development and publishing

Focused checks are `uv run pytest` and `uv run ruff check` from the root, plus `bun run check`, `bun run build` and `bun run e2e` from `web/`. Install the browser once with `bunx playwright install chromium`; see [e2e instructions](web/tests/README.md) for real and mock workflows.

Before publishing, follow [the privacy checklist](docs/privacy.md#pre-publish-checklist) and install the staged-content guard. Downloaded data, models, local settings and credentials stay out of git. The [v1 audit](docs/audit.md) is historical evidence; v2 evidence is in [onset](docs/report-v2.md), [duration](docs/duration-v2.md) and [frontend](web/IMPLEMENTATION.md) reports.

