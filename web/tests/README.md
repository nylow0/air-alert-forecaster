# Frontend browser workflows

Run from `web/`:

```powershell
bunx playwright test
```

The default target is mock. Playwright starts and stops Vite on 5187, uses Chromium at 1440×900 and 390×844, and runs with two workers and no retries.

The final F1 v2 run passed 48/48 executions in 41.9 seconds. See [IMPLEMENTATION.md](../IMPLEMENTATION.md) for exact check/build/pytest output, the six inspected screenshots, and backend integration limits.

## Coverage

`dashboard.spec.ts` checks forecast map coverage, horizons, replay controls and bounds, playback, selected probabilities/outcomes/history, keyboard selection, sorted list, API report metrics, current-hour Now behavior, overflow, and deep-link reloads.

`live.spec.ts` adds ten API-driven workflows for both independent 139-polygon live maps, their order/counts/coverage, stale and failed polling, news dismissal persistence and reopening, settings PUT/rollback and field persistence, mock login/cancel/confirmed logout, missing-Codex guidance, news adjustment/source links, all news states, and screenshots with overflow checks.

Expected counts, IDs, probabilities, settings, models, and event links come from the API. Live polling uses Playwright's clock to advance one 60-second interval. Authentication and scenario controls use isolated dev middleware and never invoke the Codex CLI.

## Mock controls

`POST /api/mock` accepts `stale`, `news_state`, `auth` with `in|out|missing`, and `fail_settings`. Each browser context has independent state. Simulated login becomes logged in after two seconds. A pending login can be cancelled.

For a manual running-news preview, open `/?mock_news=running` in a fresh browser session.

## Real backend

Start the prepared backend separately, then run:

```powershell
$env:E2E_TARGET = 'live'
bunx playwright test
```

`global-setup.ts` verifies metadata readiness and the Vite proxy against port 8000. The v2 auth/scenario file explicitly skips in live mode; its fixture controls and machine-wide auth mutations belong only in the mock suite. The original forecast/replay/report workflows remain available.

F1's required evidence covers mock mode. Combined v2 backend verification remains the lead's integration step.

## Artifacts

Screenshots and traces are under `test-results/artifacts/mock/`; the HTML report is `test-results/report/mock/index.html`. Live runs use separate `live/` directories. Rerunning a target replaces its prior artifacts. No screenshot-diff baselines are used.

Strict test type checking:

```powershell
bunx tsc --ignoreConfig --noEmit --strict --target ES2022 --module ESNext --moduleResolution Bundler --types node playwright.config.ts tests/global-setup.ts tests/dashboard.spec.ts tests/live.spec.ts
```
