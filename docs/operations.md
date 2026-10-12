# Running the v2 backend

From the repository root, run `uv run airalert serve`. The API listens on
`http://127.0.0.1:8000`. Keep one serving process per data directory. Stop it
with Ctrl+C; shutdown stops the news worker and waits for an active publication
thread to finish. A forced stop is recoverable, but can leave an incomplete
nightly run that startup must retry.

The current-hour forecast comes from `airalert.production.forecast`, with
canonical history through yesterday and live observations received strictly
before the hour. First-issued hourly files are immutable. Repeating a request,
refitting, or restarting cannot change an already issued hour. Later observations
first affect the following hour. Latest responses never contain actual labels.

Missing production artifacts queue background maintenance. `/api/system`
reports `production.onset="fitting"` while fitting. Existing complete caches
stay readable. If the current hour has no issued forecast yet, `/api/forecast`
returns HTTP 503 with `Retry-After: 2`; it never substitutes a dated snapshot.
`/api/meta.latest_at` still identifies the current UTC hour.

## Schedules and manual maintenance

| Schedule | Work |
|---|---|
| Startup | Load complete caches; poll live and obtain the current-hour forecast. Queue maintenance if the ledger is behind yesterday, artifacts are missing, or the previous pipeline was incomplete. |
| About every 60 seconds | Poll live sources, persist the raw response and receipt-tagged observations, and publish all 139 display units. Small scheduling jitter avoids synchronized requests. |
| Every UTC hour boundary | Compute or recover the immutable production forecast, append it to replay, and reload the serving cache. |
| Midnight Europe/Kyiv | Catch up complete archive days, build v2 features, refit onset, refit D2 duration, score mature issued forecasts, rebuild/extend replay, reload, and publish the current hour. |
| News enabled | Public-preview polling and bounded extraction/evaluation according to local settings and the sidecar's rate limits. |

Midnight is constructed with the IANA `Europe/Kyiv` timezone, including 23-hour
and 25-hour DST days. Model publication and replay reloads are serialized.
Live polling has its own lock and continues during long fits.

To run maintenance immediately, send `POST /api/system/reconcile` from a local
same-origin client. HTTP 202 means the run was queued; `queued=false` means one
is already queued or active. Inspect `/api/system` and `data/live/nightly.json`
for completion. The JSON records each step's status, start, finish, duration,
result, and any sanitized error. This endpoint does not score held-out test2.

`AIRALERT_DISABLE_SCHEDULER=1` disables scheduled data jobs for isolated HTTP
tests. It does not disable an explicitly enabled news worker. Use settings to
turn news off.

## Data and state

| File or directory | Purpose |
|---|---|
| `data/processed/records.parquet` | Canonical air-raid coverage, severity and provenance. Features read records, never merged episodes. |
| `data/processed/episodes.parquet` | Merged labels and duration targets. |
| `data/processed/snapshot.json` | Reconciled historical boundary and accepted coverage. Live polls do not advance it. |
| `data/processed/features/v2/` | Separate v2 train, validation, test2 and latest tables. |
| `data/processed/preds/v2/onset_selected.parquet` | Frozen selected validation/test2 predictions. |
| `data/processed/preds/v2/issued/` | Immutable hourly production predictions, issuance time, model generation and the B0 reference captured at issuance. |
| `data/processed/forecasts.parquet` | Atomically published API replay cache. Contains exactly `ForecastRow` fields plus `t`; served choices and input signature are Parquet metadata. |
| `data/live/raw/` | Cached source responses. |
| `data/live/state.json` | Last complete live display state. |
| `data/live/observations/` | Successful live snapshots with receipt clocks for model availability. |
| `data/live/ledger.json` | Last reconciled Kyiv day, accepted partitions and unresolved coverage gaps. |
| `data/live/nightly.json` | Durable maintenance progress and results. |
| `artifacts/production/current.json` | Pointer to a complete onset generation. |
| `artifacts/production/duration/current.json` | Independent complete D2 generation. |
| `reports/v2/metrics.json`, `duration_scores.json`, `selection.json` | Frozen v2 evaluation and selection inputs. |
| `reports/v2/track_record.json` | Rolling 7/30-day scores of timely, first-issued forecasts with reconciled outcomes. |
| `data/settings.local.json` | Git-ignored news settings. |
| `data/news/news.sqlite` | News cursors, message versions, runs, validated events, evaluations, quotas and breaker state. |

`GET /api/report` merges the v2 onset report with the duration fragment and
renders rolling scores in `notes`. `n=0` and null scores mean that no eligible
prospective outcomes have matured. They do not imply perfect accuracy.
`GET /api/report?version=v1` returns the frozen v1 report.

The existing schema calls test2 `splits.test` and score rows `split="test"`.
V2 validation is August 1 through September 8, and test2 starts September 8.
Replay contains every stored validation/test2 hour, then actual issued hours.
An hour never predicted before production was started, or during downtime,
has no issued forecast. Such an hour returns 404; history can have missing
points. The range in `Meta.replay` describes the outer bounds, not a promise
that downtime predictions were invented. History requests support up to
31 days each; query longer periods in successive ranges.

Historical replay uses frozen D2 evaluation weights and calibration completed
before the applicable split. Production D2 is trained on later outcomes and
must not be applied backward to replay. End quantiles condition on survival
and are later than the forecast hour. Calibration between trained landmarks
and beyond eight hours has not been independently measured.

## After downtime

Startup compares the saved watermark with yesterday in Kyiv. Missing complete
days reconcile in order; the watermark advances only after successful
publication. A failed archive day remains a gap and blocks advancing past it.
The subsequent feature/model/cache pipeline runs in the background.

A current ledger and completed pipeline reuse fitted artifacts. Startup obtains
the current forecast without repeating a finished refit. An interrupted or
failed downstream pipeline is retried, even when ingestion already advanced
the watermark. Complete model pointers and atomic caches keep readers away
from partial generations. A failed live poll keeps the previous alerts and
marks them stale; it never clears them. Three minutes without a fresh
successful poll also makes the display stale.

The repaired August outage is accepted by archive validation. Current coverage
gaps are empty. Archive imperfections, ended-only CSV history and missed events
between minute polls remain source limitations.

## Optional alerts.in.ua token

Set `ALERTS_IN_UA_TOKEN` in the serving process environment, or add that key to
the git-ignored repository-root `.env`. The adapter reads only that key; it
does not execute the file. Restart the server after changing its environment.
The unauthenticated siren mirror works without this optional token. The token
stays in the backend and must never be pasted into a report, browser setting,
tracked file or log. This integration run used the unauthenticated sources;
the authenticated source was not exercised.

## Optional news agent

News is off by default. It needs the installed Codex CLI, an existing ChatGPT
login and discovery access to the exact selected models and reasoning efforts.
`GET /api/auth/status` checks the real CLI state without reading its credential
cache. This integration did not log in or log out.

Read `GET /api/settings`, change `news_enabled` to true in that complete Settings
object, and send it to `PUT /api/settings`. Keep the existing model/channel
metadata. Local controls require a loopback client and Host, plus a matching
Origin when supplied. Turn the setting back to false to cancel active work and
stop polling. Turning off remains available if discovery later fails.

`GET /api/news` reads the cache immediately. Status moves through `starting`
and `running`; validation errors produce a delayed retry. New events can be
visible while adjustments remain empty, because publication, observation,
extraction and evaluation must all precede the hourly base cutoff. Unsupported
place names abstain. Empty validated events skip evaluation. The overlay is
experimental and has no measured forecasting benefit. See [news.md](news.md)
for budgets, source backoff, tool-attempt protection and its limitations.

## Troubleshooting

- Current forecast returns 503: inspect `/api/system` and the failed or running
  step in `nightly.json`. Do not relabel an older forecast as now.
- Live is stale: compare `live_fetched_at` with the system clock and inspect
  source-failure log categories. Last known alerts remain visible.
- Coverage is behind: inspect the ledger's first missing day, gate validation
  and cached raw responses. Retry maintenance after resolving that cause.
- Replay returns 404: check the requested hour is UTC, exactly on the hour and
  actually present in evaluation or issued storage.
- News says `needs_codex` or `needs_login`: inspect auth status and arrange CLI
  installation/login separately. Restarting the app does not authenticate it.
- Settings returns 403: preserve the browser Host through a frontend proxy and
  use a matching Origin. Local CLI requests can omit Origin.
- News inference/validation fails: the logs contain a fixed category, never
  model reasoning or source text. Inspect the git-ignored run archive; do not
  loosen provenance checks to make an output pass.
- News breaker is set: toggling or restarting cannot clear it. Follow the
  reviewed, stopped-app recovery procedure in [news.md](news.md).

## X1 integration report

### Delivered

- `src/airalert/{api,scheduler,precompute}.py`: production startup, v2 replay and
  reports, maintenance order and timing, independent polling, immutable hourly
  append, and the local manual-maintenance control.
- `tests/test_{api,live,integration}.py`: independent synthetic outage, polling
  during fitting, cold/restart publication checks, and explicit real-server
  probes. Normal pytest runs do not poll sources or invoke Codex.
- `docs/operations.md`: this runbook and evidence.
- Necessary interface edits outside S1: `production.py` declares D2 for the
  scheduler's ordered production lifecycle; `models/duration.py` adds the frozen
  batch replay adapter and shares onset's live closures while preserving receipt
  availability; `news/worker.py` adds safe stage/status logging; `news/extract.py`
  supplies supported place mentions per cited message. Validators are unchanged.
- Git-ignored evidence is under `data/live/x1/`. No frontend, schema, v1 report,
  commit, branch or dependency changes were made.

### Verified

The commands below ran against real data and the running API on October 12,
2026. Full JSON evidence is local; trimmed results are included here.

1. **Current production forecast.** `uv run airalert serve`, then
   `uv run --no-sync python tests/test_integration.py http` returned HTTP 200,
   `at=2026-10-12T17:00:00Z`, `rows=119`, `active=30`, `selected/D2`.
   API probabilities equal the first-issued producer file. `latest_at` is the
   current hour. The cold running-server probe returned `fitting` after 3.58 s
   and completed real onset/D2 fits before returning a current-hour 119-row
   forecast. It reused existing features and isolated model output paths.
   The final HTTP check returned `2026-10-12T18:00:00Z`, 119 rows and 49 active.
2. **V2 replay/history.** `uv run airalert precompute` returned
   `historical=rebuilt`, `issued_hours=1`, `rows=204085`. The HTTP probe checked
   August 1 00:00, repaired August 29 06:00, September 8 00:00 and October 11
   09:00 UTC. Each returned 119 rows; active counts were 31, 28, 38 and 43.
   Probabilities equal the selected predictions after the required cumulative
   maximum. Active state equals strictly-before-hour raw records, labels equal
   v2 feature labels, and every active row has ordered future D2 ends. Early and
   late seven-day histories each returned 169 points. `data_gaps=[]`. The
   `lifecycle` probe observed the actual 18:00 UTC boundary and its 119 newly
   appended rows. Stored replay became 204,204 rows and 1,716 available hours;
   `replay.end=latest_at=2026-10-12T18:00:00Z`.
3. **Reports.** The same HTTP probe returned 156 onset and 92 duration score
   rows, `selection=selected/D2`, and rolling notes. `Report` validation passed.
   `/api/report?version=v1` equals the frozen stored report exactly.
4. **Real nightly pipeline.** `POST /api/system/reconcile` returned 202.
   `data/live/nightly.json` recorded `reason=manual`, `ok=true`, with ordered
   durations: catch-up 0.050 s; features 19.052 s; onset refit 72.378 s; D2
   refit 69.435 s; score 0.031 s; rebuild 7.118 s; reload 0.900 s; forecast
   publication 1.719 s. Catch-up was current through October 11, features
   produced 4,521,048/108,528/95,438/119 rows, and refits were real. The final
   post-restart run also returned `ok=true`, finishing at 17:59:57.366052Z.
   Its per-step durations/results from `data/live/nightly.json` were:

   | Step | Seconds | Real result |
   |---|---:|---|
   | Catch-up | 0.036 | October 11 watermark retained; no missing day. |
   | Features | 16.667 | 4,521,048 train; 108,528 val; 95,438 test2; 119 latest rows. |
   | Onset refit | 66.356 | selected/D2; trained at 17:58:41.063445Z; data through October 11 21:00Z. |
   | Duration refit | 67.408 | 442,859 ended episodes; 1,479,590 surviving landmarks. |
   | Score recent | 0.012 | Both rolling windows n=0; scores null. |
   | Rebuild | 6.477 | Historical cache rebuilt, one issued hour; 204,085 rows before the next boundary. |
   | Reload | 0.778 | Complete generation loaded. |
   | Forecast publication | 1.574 | Current immutable hour retained and appended; later 18:00 published at its real boundary. |
5. **News/auth.** `uv run --no-sync python tests/test_integration.py news`
   round-tripped Settings through PUT. Off counters remained unchanged for
   65 s. Logs said `news state=off telegram_polls=0 model_calls=0`. The successful
   on-cycle moved `starting -> running`, polled the enabled sources and completed
   extraction at `2026-10-12T17:56:05.995209Z`. It returned `events=[]`,
   `adjustments=[]`; no eligible event required evaluation. Run count advanced
   from 2 to 3 and message versions from 342 to 345. After PUT off, counters
   stayed unchanged for another 65 s. Logs recorded
   `news poll completed enabled_channels=19 failed_sources=0` and
   `news model stage=extract completed seconds=9.000`.
   Two earlier real outputs were rejected
   for geography unsupported by their citations. `/api/auth/status` returned
   installed=true, logged_in=true, login_in_progress=false and seven available
   models. No login/logout command was invoked.
6. **Live/system.** `uv run --no-sync python tests/test_integration.py lifecycle`
   observed successful real polls at 17:55:18.144861Z and 17:56:19.522762Z;
   both had 139 units, 39 red and 36 yellow. System fields matched the live
   cache, complete ledger, snapshot, production metadata and Kyiv midnight.
7. **Checks.** `uv run pytest -q --tb=short` returned
   `128 passed, 1 warning in 95.15s`. The warning is the existing upstream
   Starlette/httpx deprecation. After the final news prompt edit,
   `uv run pytest tests/test_news.py tests/test_duration.py tests/test_integration.py -q --tb=short`
   returned `26 passed, 1 warning in 40.24s`. `uv run ruff check` returned
   `All checks passed!`.
8. **Restart/startup.** The owned serving process was forcibly stopped and
   restarted with `uv run airalert serve`. It retained the completed manual
   run and trained generation; no startup refit was queued.
   `uv run --no-sync python tests/test_integration.py startup` started separate
   HTTP servers with isolated ledger/cache paths.
   A copied stale October 10 watermark triggered `reason=startup` and completed
   in 9.015 s; the injected reconciliation updated only that copy. The cold
   server responded after 3.58 s with `fitting`, then ran actual model fits.
   The canonical ledger remained byte-for-byte unchanged by both simulations.
   The cold HTTP pipeline completed in 157.985 s, including real isolated
   model fitting; successful live receipts continued during those fits.
9. **Operations.** This guide documents the command, schedules, files, downtime,
   optional token, news prerequisites and troubleshooting. Ctrl+C produced
   `Application shutdown complete`. `uv run --no-sync python -` with the
   socket/settings verification script returned
   `8000 listening=False`, `8001 listening=False`, `news_enabled=False`.
   No server was left running. A scoped scan with the pre-commit guard's exact
   privacy patterns returned `privacy_findings=[]`; Ruff and whitespace checks
   passed. No commit was made.

### Decisions/assumptions

The fixed 119-raion forecast universe and 139 display polygons remain distinct.
The unchanged schemas use `test` for test2 and model-id strings for fitting
status. Historical replay uses earlier fitted/calibrated D2, while production
uses the ordered D2 refit. Source receipt clocks govern live feature availability.
Issued predictions are preserved even after a later model refit; rolling scores
exclude forecasts first issued more than 60 seconds after their hour.

### Open issues

There are no invented forecasts between the final evaluation hour and initial
production issuance, or for missed downtime hours. The existing Meta range
cannot describe those sparse hours. Source imperfections and duration interval
calibration limitations from the M2 handoffs persist. The authenticated live
adapter was not tested because no token was configured. The successful news
cycle had no eligible events, so this integration verified its evaluation-skip
path; N1's separate real-event evaluation evidence remains in `docs/news.md`.

### Contract change requests

No schema change is required for this integration. A future UI may benefit from
an explicit available-hour list or range list in Meta to skip unissued replay
hours. The existing schema comment describing duration ends as onset duration
should be updated by the lead to describe v2 remaining-duration ends. Existing
N1 requests for visible budgets and I1's empty-poll receipt manifest remain
outside X1 ownership.

### Dependencies added

None.
