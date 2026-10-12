# Running the v2 backend

From the repository root, run `uv run airalert serve`. The API listens on
`http://127.0.0.1:8000`. Keep one serving process per data directory. Stop it
with Ctrl+C; shutdown stops the news worker and waits for an active publication
thread to finish. A forced stop is recoverable, but can leave an incomplete
nightly run that startup must retry.

The current-hour forecast comes from `airalert.production.forecast`, with
canonical history through yesterday and live observations received strictly
before the hour. First-issued hourly files are immutable. Repeating a request
or refitting cannot rewrite an issuance. An explicit input-policy repair can
publish a separate immutable serving correction, preserving the original file
and its scoring evidence. Later observations first affect the following hour.
Latest responses never contain actual labels.

Missing production artifacts queue background maintenance. `/api/system`
reports `production.onset="fitting"` while fitting. Existing complete caches
stay readable. If the current hour has no issued forecast yet, `/api/forecast`
returns HTTP 503 with `Retry-After: 2`; it never substitutes a dated snapshot.
`/api/meta.latest_at` still identifies the current UTC hour.

## Schedules and manual maintenance

### Live-state consistency

Forecasts exclude live community alerts older than 72 hours, while the live map
keeps them visible and gives active district/oblast origins precedence for
each layer's since time. The shared as-of records also feed remaining duration.
Successful absent polls end stale origins; failures retain the last observation.
The online view also excludes unresolved persistent archive continuations,
and closes censored coverage by raion and source level. Reconciled history
supersedes receipts before its boundary. See
[ingest.md](ingest.md#persistent-local-alerts) for the exact threshold and proxy rule.

Run `uv run --no-sync python tests/test_integration.py consistency` against port
8000. It compares every universe raion's served active flag and `f_active` with
an independent latest-pre-hour origin check and prints each mismatch. The latest
eligible receipt must be strictly before the hour and at most 180 seconds old,
allowing one minute polling plus network/scheduler jitter. This is receipt-age
tolerance, not permission to use observations received after the hour. `/api/live`
shows a newer clock; post-hour starts first affect the next hourly forecast.

`uv run --no-sync python tests/test_integration.py capture` records the two
reported raions and active-since age distribution under git-ignored
`data/live/x3/`. Legacy hours repaired under `persistent-local-v1` have a separate
file at `issued/corrections/persistent-local-v1/<hour>.parquet`. The original
hourly file is preserved. Serving and replay prefer the correction, while
prospective scoring still reads original first issuances only. Corrections
carry their actual later issuance clock and cannot count as timely predictions.

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
Shared hazards and severity-transition read/merge/replace operations use the
same short canonical publication lock as nightly replacement and rollback.
An OS file lock also serializes participating standalone CLI writers.

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
| `data/live/raw/` | Cached source responses; archive cache hits must pass semantic decoding. |
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

Before publication, each archive day must contain at least 100 unique national
air-raid records and 20% of the preceding 14 reconciled days' median. Successful
live receipt-time coverage must also appear in that day's archive, within the
reviewed 120-second timing tolerance. Empty/truncated days produce an explicit
ledger gap visible in `/api/system.coverage_gaps`, keep their records unpublished,
and force a fresh archive fetch on the next catch-up. See
[ingest.md](ingest.md#archive-completeness-and-publication-y1) for thresholds,
bootstrap behavior and limits. A quiet national day is essentially impossible
in this history; its recent daily starts number in the hundreds.

Archive HTTP/JSON/schema failures are not admitted to the raw cache. Existing
invalid cache entries are bypassed automatically, so normal catch-up retries
after an upstream repair without manual deletion.

A current ledger and completed pipeline reuse fitted artifacts. Startup obtains
the current forecast without repeating a finished refit. An interrupted or
failed downstream pipeline is retried, even when ingestion already advanced
the watermark. Complete model pointers and atomic caches keep readers away
from partial generations. A failure in siren, enabled `aiu_api`, or the ubilling
Sevastopol fallback keeps the entire last complete display snapshot and its
successful fetch clock, and marks the top-level API state stale. This retains
Sevastopol's inherited alert and distinct optional-source contributions. Failed
attempts never publish absence receipts; source-confirmed complete polls can
clear alerts and stale again. This conservative choice uses the existing schema
without per-unit staleness. Three minutes without a fresh successful poll also
makes the display stale.

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

## X3 live-state consistency report

### Delivered

- `src/airalert/ingest.py`: `PERSISTENT_LOCAL_ALERT_H = 72`, shared live-spell
  filtering, independent red/yellow district/oblast precedence, and atomic
  successful observation publication with explicit receipt sidecars.
- `src/airalert/production.py`: exclusion of persistent local live origins and
  unresolved archive continuations from online inputs; origin continuity across
  inexact color changes; closure on successful absence; archive closure by raion
  and source level; reconciliation receipt cutoff; immutable serving repairs.
- `src/airalert/precompute.py`: replay prefers a separately published serving
  repair while preserving the original first issuance.
- `tests/test_production.py`, `tests/test_integration.py`: real producer/map/D2
  input regression, strict threshold, absent/reappearing origins, inexact starts,
  receipt cutoffs, empty receipts, immutable repairs and the reusable real-data
  `consistency`/`capture` probes.
- `web/tests/dashboard.spec.ts`, `web/tests/live.spec.ts`: stored-hour readiness,
  page-local replay bounds and synchronization with hourly publication.
- `docs/ingest.md`, `docs/operations.md`: policy, operational probe and this evidence.

### Verified

All clocks below are UTC on October 12, 2026. Full selected-raion JSON is in
`data/live/x3/capture.json` before and
`data/live/x3/capture-20261012T190857Z.json` after. The latest consistency
result is `data/live/x3/consistency.json`.

Before, the 18:00 forecast captured at 18:54:15 returned:

- Kharkivskyi: `active=true`, `active_since=2026-03-10T09:05:43Z`,
  probabilities `0.013646 / 0.048724 / 0.048724`. Live red since
  `2026-03-10T14:01:38.075992Z`; yellow null.
- Nikopolskyi: `active=true`, `active_since=2026-09-17T07:22:11Z`,
  probabilities `0.013646 / 0.041923 / 0.043043`. Live red since
  `2026-10-11T08:33:39.714919Z`, yellow since
  `2026-09-17T10:06:09.751296Z`.

After, the corrected 19:00 forecast captured at 19:08:57 returned:

- Kharkivskyi: `active=true`, `active_since=2026-10-12T18:01:13.466849Z`,
  probabilities `0.128223 / 0.493827 / 0.799042`. Live red since the same
  district start; yellow null. A direct source fetch identified March origin
  UID 1284 as hromada and fresh UID 124 as district.
- Nikopolskyi: `active=true`, `active_since=2026-10-11T08:15:43Z`,
  probabilities `0.013646 / 0.041923 / 0.081716`. Live red/yellow starts remain
  as above. Red is a 34-hour community alert, so it still counts. The forecast
  component starts at an overlapping ordinary district record, 08:15:43 to
  09:35:38, followed by the 08:33:39 community alert. The September community
  continuations no longer contribute to forecast inputs, while yellow remains
  visible on the live map.

Across active forecast raions, before there were 49 active, with 38 ages at most
24h, nine at 24-72h and two above 72h. Median age was 0.931h, P90 25.484h,
P99 2,993.413h and maximum 5,192.905h. After there are 33 active, with 23 ages
at most 24h, ten at 24-72h and none above 72h. Median age is 1.931h, P90
26.484h, P99 32.097h and maximum 34.738h. Ordinary source changes between
18:00 and 19:00 also affect the active count.

- `uv run --no-sync python tests/test_integration.py consistency`: 119 raions,
  33 active, `mismatch_count=0`, `active_over_72h=[]`, and zero persistent live
  records in duration inputs. The last eligible receipt is 18:59:05.025054,
  54.975 seconds before the hour, inside the documented 180-second tolerance.
- `uv run --no-sync pytest -q --tb=short`: `138 passed, 1 warning in 84.25s`.
  The warning is the existing Starlette/httpx deprecation.
- `uv run --no-sync ruff check`: `All checks passed!`.
- In `web/`, `$env:E2E_TARGET='live'; bun run e2e`, final code twice:
  `41 passed (2.2m)` and `41 passed (1.9m)`. Each includes desktop/mobile step
  controls and the real news on/off round trip, with settings restored and
  off observed for 65 seconds.
- `$env:E2E_TARGET='mock'; bun run e2e`: `52 passed (49.9s)`.
- `bun run check`: zero errors and zero warnings. `git diff --check` passed;
  only LF/CRLF notices. A scoped privacy scan found zero findings.
- Actual browser probe on 5173: held history at 01:00 while map busy was false;
  clicking Step forward left 01:00 unchanged. After releasing history, the
  next click moved to 02:00. The test now waits for the required stored-hour
  response and uses the slider's current min/max instead of suite-start Meta.
- A diagnostic full live run across 19:00 had 38 passes and three failures in
  other Now/layout checks. They assumed immediate publication or a 10s map
  deadline. API publication actually took 25.5s. Those checks now respect 503
  and the UI clock tick within a bounded 30s wait. Both subsequent complete
  live runs passed.
- Original 19:00 rows still match the pre-correction capture. Correction
  publication is at 19:08:51, outside prospective scoring grace. All 119 replay
  rows match the correction. The real 5173 page renders 119 forecast paths,
  including Kharkivskyi `0.49` and Nikopolskyi `0.04` at 3h, with busy false.
- The real server was restarted with
  `uv run --no-sync airalert serve --port 8000` and left running. Port 5173 was
  not stopped or restarted. Final checks show both listeners present, HTTP 200
  with 119 current-hour rows, live not stale and news off.

### Decisions/assumptions

Only the online view excludes unresolved persistent archive rows. Contrary to
the initial assumption, canonical Nikopolskyi data contains a censored hromada
row from September 17. Its actual stored row, completed history, episodes,
training artifacts and frozen reports were preserved. District/oblast age has
no exclusion; exactly 72h community coverage still counts.

Inexact live timestamps remain proxies, never labels. A successful absent poll
ends an origin; failed polls do not. Original source identity is retained for
live spells. Canonical rows lack origin identity, so censored archive closure
uses raion and source level. Receipts before the published reconciliation
boundary are superseded by history. All onset and remaining-duration inputs
share this view.

Legacy first-issued files stay immutable. The `persistent-local-v1` repair is
a separate immutable serving file with its actual later issuance clock.
Replay and current serving prefer it. Rolling scoring reads original first
issuances, so a late repair cannot become prospective evidence.

### Open issues

The supported authenticated adapter was not exercised. Siren original onsets
remain inexact. The UI still ignores transport clicks before its stored-hour
list arrives; this task fixes the test's readiness assumption. An F1 follow-up
could disable those controls while loading. Older original issued forecasts
retain their original behavior unless a separate serving repair is published.
No required verification remains failing.

### Contract change requests

Resolved during final integration. `PERSISTENT_LOCAL_ALERT_H` now lives in
`config.py`; ingest imports the unchanged 72-hour threshold. No API schema or
frontend source change is required. The new
receipt sidecars resolve the former empty-poll receipt-manifest handoff request.

### Dependencies added

None. No commit or git state change was made.
