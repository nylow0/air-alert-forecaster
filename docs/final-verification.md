# Final v2 verification

Integration date: October 12, 2026. All clocks below are UTC unless marked Kyiv.
This note separates the current integration checks from the earlier source and
UI evidence in [operations.md](operations.md) and [ingest.md](ingest.md).
Open the [dashboard](http://localhost:5173).

## Delivered scope

- Live map with 139 display units, separate red/yellow layers and inherited
  coverage for excluded forecast regions. Chornobyl inherits only a Kyivska
  oblast-wide alert.
- Hourly forecasts for 119 raions, with 1/3/12-hour onset probabilities and
  D2 remaining-duration quantiles. Live inputs use successful observations
  received strictly before the forecast cutoff.
- Desktop and mobile dashboard, replay, raion history, model report and local
  settings. Late serving repairs preserve original first-issued predictions.
- One serving process with polling about every 60 seconds, hourly publication,
  startup catch-up and midnight maintenance in Europe/Kyiv. Maintenance
  reconciles archive days, rebuilds features, refits production onset/D2 models,
  extends replay and scores mature first-issued forecasts. Frozen v1/v2 results
  remain separate from this rolling track record.
- Archive completeness checks before advancing coverage, shared publication
  locks, stale-state retention after any enabled source fails, retry of invalid
  archive caches, and staging of successful observation files.
- Durable onset/duration evaluation freeze claims. Validation and final-scoring
  entry points cannot overwrite claimed reports in a fresh checkout. Separate
  experiments require `--out`.
- Optional news extraction/evaluation through the local Codex CLI and existing
  ChatGPT login, with settings controls and an experimental probability overlay.
- Centralized integration thresholds in `src/airalert/config.py`. Values remain
  72 hours for persistent local alerts, 100 national archive events, 14 baseline
  days, 20% of the baseline median and 120 seconds of live/archive timing
  tolerance. Exactly 72 hours of community coverage still counts. Older
  persistent community origins stay in the live display and canonical history;
  forecast inputs exclude them.

## Verification evidence

| Check | Actual result |
|---|---|
| Focused threshold tests | `uv run --no-sync pytest tests/test_ingest.py::test_archive_completeness_blocks_bad_days_and_retries tests/test_production.py::test_persistent_threshold_is_strict_and_broad_origins_are_retained tests/test_production.py::test_persistent_community_is_displayed_but_excluded_from_forecast -q --tb=short` returned `6 passed in 3.23s`. |
| Changed Python files | `uv run --no-sync ruff check src/airalert/config.py src/airalert/ingest.py` returned `All checks passed!`. |
| Patch formatting | `git diff --check` exited 0; Git emitted only LF/CRLF notices. |
| Current system, checked by the lead at 19:41 | `/api/system` reported a successful previous nightly run, watermark `2026-10-11`, data through `2026-10-11T21:00:00Z` and no coverage gaps. That boundary is October 12 midnight in Kyiv. The next nightly run was `2026-10-12T21:00:00Z`, October 13 midnight in Kyiv. Live receipt `19:41:13Z` was fresh. |
| Current metadata, same check | `/api/meta` reported latest hour `2026-10-12T19:00:00Z`, replay from August 1 through that hour and 119 forecast raions. |
| News configuration and authentication, same check | News was off, with 19 configured channels, extraction `gpt-6-luna/high` and evaluation `gpt-6.1-sol/high`. CLI status recognized an existing ChatGPT login and a catalog containing both selected models. Credentials were never read, copied or logged. |
| Full Python suite, lead-run | `164 passed, 1 warning in 102.96s`. The warning is the existing Starlette test-client deprecation. |
| Full Ruff check, lead-run | `All checks passed!`. |
| Frontend check and build, lead-run in `web/` | `bun run check` returned zero errors and zero warnings. `bun run build` completed with 336 transformed modules. |
| Real live browser suite, lead-run | 41 checks passed in 2.4 minutes at desktop 1440 x 900 and mobile 390 x 844. News moved from off to starting to running, then remained stopped for 65 seconds after disabling. Settings were restored to news off, ChatGPT logged in, the selected high-effort models and 19 channels. |
| Fixture browser suite, lead-run | 52 checks passed in 52.0 seconds. |
| Backend restart and poll cadence, lead-run | The restart loaded the integrated code around `19:45:55Z`. Fresh receipts were `19:45:55.051023Z`, `19:46:55.266762Z` and `19:47:57.324625Z`, about 60.2 and 62.1 seconds apart, with no stale state. |
| Actual consistency CLI, lead-run at 19:47 | `uv run --no-sync python tests/test_integration.py consistency` checked 119 forecast rows, 33 active raions, zero mismatches and zero persistent local live records in duration inputs. The 19:00 forecast's eligible receipt was `18:59:05Z`, 54.97 seconds before cutoff. |
| Visual review, lead-run | The lead inspected the live desktop/mobile screenshots from the real browser suite. Yellow layers appeared above red, and both layouts rendered correctly. |

Earlier real workflow evidence remains linked in the source reports. Y1
completed a real poll with 139 polygons, tested a no-op catch-up through
October 11 and checked system coverage. X3 recorded zero active-state
mismatches across 119 raions, two full live UI runs with 41 passing checks
each, 52 passing fixture UI checks and desktop/mobile browser interactions.
Those runs preceded the final constant move. The fresh lead-run checks above
cover the integrated code after restart. The full nightly refit pipeline was
not repeated in this final pass; its earlier real run and durable success record
are recorded in [operations.md](operations.md). Additional direct browser
automation was unavailable during this pass, so current visual evidence is the
real browser suite and its reviewed screenshots.

## Privacy publication status

Public-history cleanup and its final privacy review are still pending. The
scoped scan of the five final-integration files found zero email, home-path,
numeric process-ID or credential-literal matches. This note uses repository
paths and contains no credentials or personal identifiers. App authentication
delegates to Codex; its credential cache was never read.

## Remaining limitations

- The optional authenticated alerts.in.ua adapter has fixture coverage but was
  not exercised with a real token in these integration runs.
- The archive completeness gate catches severe omissions and contradictions
  with observed coverage. It cannot detect every missing event. CSV history
  lists ended alerts, minute polling can miss brief events, and siren onset
  times remain inexact proxies.
- Failed sources preserve the complete snapshot as stale. The API has a single
  stale flag, so it does not publish a partly fresh display.
- Publication is atomic per file. Forced termination during a multi-file
  update can require startup recovery.
- Replay has no invented production forecasts for downtime or hours before
  production started. Late serving repairs preserve original first issuances
  and cannot count as timely prospective forecasts.
- D2 calibration between trained landmarks and beyond eight hours has not
  been independently measured. The optional news overlay is experimental and
  has no measured forecasting benefit.
- News model judgments and source provenance checks do not prove that a report
  is true. The CLI event guard is reactive; it cannot establish a strict
  construction-time prohibition on tool execution. See [news.md](news.md).
- Run an initial final evaluation and validation/model refresh sequentially
  when they share a new evaluation directory. Report writes recheck the freeze
  claim, and changed onset predictions fail hash verification. Duration model
  artifacts do not have an equivalent weight-hash check, so concurrent initial
  evaluation remains a reproducibility risk. Current claimed reports reject
  new evaluation entry points and remain protected.

## Manual handoff

Open Settings at the bottom left of the dashboard and enable News agent when
you want the optional overlay. This machine's existing ChatGPT session is
recognized, so another login is not required. News remains off until enabled.

On a machine showing `Not logged in`, click `Login with ChatGPT`, complete the
browser flow opened by Codex, and wait for Settings to show `Logged in with
ChatGPT`. Then enable News agent. Authentication stays with the local Codex
CLI; the app never opens its credential cache. The browser step belongs to the
operator. No login or logout was performed for this verification.
