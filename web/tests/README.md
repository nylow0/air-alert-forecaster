# Frontend browser workflows

Run from `web/`:

```powershell
bunx playwright test
```

The default target is mock. Playwright starts and stops Vite on 5187, uses Chromium at 1440×900 and 390×844, and runs with two workers and no retries.

See [IMPLEMENTATION.md](../IMPLEMENTATION.md) for current check/build/pytest output, mock/live summaries and inspected real-data screenshots.

## Coverage

`dashboard.spec.ts` checks forecast map coverage, horizons, replay controls and bounds, playback, selected probabilities/outcomes/history, keyboard selection, sorted list, API report metrics, current-hour Now behavior, overflow, and deep-link reloads.

`live.spec.ts` checks both live maps, API-derived counts/coverage, failed polling, dismissal/reopening, dialog/layout safety, system status, read-only real Settings/auth state, and real screenshots. Settings edits, login/cancel/logout, fixture stale state, adjustment fixtures and news scenarios are registered only in mock mode. Common live workflows assert that they make no Settings or auth writes.

`replay-storage.spec.ts` verifies that 404 hours clear old probabilities, stepping/playback skip missing hours, and history breaks its line. The mock injects one missing hour in the browser. Live uses an actual hole found in the API's stored times; if no hole exists, it records that coverage limit without assuming a specific date.

Expected counts, IDs, probabilities, settings, models, and event links come from the API. Live polling uses Playwright's clock to advance one 60-second interval. Authentication and scenario controls use isolated dev middleware and never invoke the Codex CLI.

## Mock controls

`POST /api/mock` accepts `stale`, `news_state`, `auth` with `in|out|missing`, and `fail_settings`. Each browser context has independent state. Simulated login becomes logged in after two seconds. A pending login can be cancelled.

For a manual running-news preview, open `/?mock_news=running` in a fresh browser session.

## Real backend

Start `uv run --no-sync airalert serve --port 8000` from the repository root, then run from `web/`:

```powershell
$env:E2E_TARGET = 'live'
bunx playwright test
```

`global-setup.ts` verifies metadata readiness and the Vite proxy against port 8000. The desktop-only `news-live.spec.ts` performs one authorized news on/off round trip, waits for starting/running, checks event links when present, then observes off for 65 seconds. A finally block restores the complete original Settings object. It never calls login, cancellation or logout. Run this test with news initially off; enabling it invokes the real optional news worker.

After a successful round trip, repeat only the read-only workflows with `bunx playwright test --grep-invert 'real news starts'`. This avoids another real model invocation while checking visual changes. Mock-only tests are not registered in live mode, so they are not reported as skipped.

## Artifacts

Screenshots and traces are under `test-results/artifacts/mock/`; the HTML report is `test-results/report/mock/index.html`. Live runs use separate `live/` directories. Rerunning a target replaces its prior artifacts. No screenshot-diff baselines are used.

Strict test type checking:

```powershell
bunx tsc --ignoreConfig --noEmit --strict --target ES2022 --module ESNext --moduleResolution Bundler --types node playwright.config.ts tests/global-setup.ts tests/dashboard.spec.ts tests/live.spec.ts tests/news-live.spec.ts tests/replay-storage.spec.ts
```
