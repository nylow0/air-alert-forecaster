# D1 verifier, phase 1

The final mock run passed all 24 executions, 12 workflows at desktop 1440×900 and mobile 390×844. Three UI findings remain for the lead. One is an intermittent uncaught ResizeObserver error observed in earlier runs. Product code and `docs/audit.md` were left unchanged.

## Delivered

- `web/tests/dashboard.spec.ts`: API-driven map coverage, all horizons, scrubber, step controls and bounds, play/pause, polygon selection, accessible history data, keyboard exploration, sorted list, onset scores, duration/selection/notes, latest mode, overflow, and URL reloads.
- `web/tests/global-setup.ts`: live-backend prerequisite check with a five-second request timeout, metadata readiness, and proxy/backend metadata comparison.
- `web/playwright.config.ts`: two Chromium projects, `E2E_TARGET=mock|live`, fixed frontend port 5187, two workers, zero retries, and traces for every test.
- `web/tests/README.md`: this evidence and wave-4 handoff. It stays within D1 ownership; the later audit document is untouched.
- `web/package.json`: only the `e2e` script and Playwright development dependency were added. `web/bun.lock` records that dependency.
- Screenshots and traces are git-ignored under `web/test-results/artifacts/mock/`. The HTML report is `web/test-results/report/mock/index.html`. Live runs use separate `live/` directories, so they preserve mock evidence.

## Verified

### Commands and real output

From `.\web`, `bun add -d @playwright/test` exited 0:

```text
bun add v1.3.13 (bf2e2cec)
Resolving dependencies
Resolved, downloaded and extracted [11]
Saved lockfile

installed @playwright/test@1.64.0 with binaries:
 - playwright

3 packages installed [1.63s]
```

`bunx playwright install chromium` exited 0. The final installation check emitted no output. Actual Chromium executions below verify that the browser is available.

Final `bunx playwright test`, default mock target, exited 0:

```text
[WebServer] $ vite --mode mock --host "127.0.0.1" --port "5187" --strictPort

Running 24 tests using 2 workers

  24 passed (21.5s)
```

Both projects passed all 12 workflows. No retries or skips were used. This final run recorded no console errors, uncaught exceptions, browser ErrorEvents, or unhandled rejections. Earlier runs exposed an intermittent error described below; a passing final run does not close that finding.

`bunx tsc --ignoreConfig --noEmit --strict --target ES2022 --module ESNext --moduleResolution Bundler --types node playwright.config.ts tests/global-setup.ts tests/dashboard.spec.ts` exited 0 with no output.

`git diff -- package.json bun.lock` showed only the authorized script/dependency changes. `git check-ignore test-results/artifacts/mock/map.png test-results/report/mock/index.html` returned both paths. There was no D1 change to `docs/audit.md` or product code.

### Browser evidence

- The mock API supplied 119 forecast raions and 139 GeoJSON polygons. Every forecast ID had exactly one rendered, labelled SVG path. The other 20 polygons had null IDs, no-data labels, and `#111` fills.
- Every horizon changed the URL and the fills for API rows whose probabilities differed. Adjacent replay hours were selected from API responses, rather than a fixture timestamp.
- The step and range controls changed the displayed Kyiv time, UTC URL, and probability labels/fills. The range reached both endpoints and disabled the corresponding step button.
- Playback advanced the hour. After Pause, a 1,250 ms observation, more than three playback intervals, left the displayed hour, URL, and fills unchanged.
- The selected panel's three probabilities matched the API to two decimal places. Replay outcomes and every displayed hourly-history row matched their API responses. Keyboard chart exploration preserved the dashboard forecast time.
- The list contained all forecast rows in descending order for each horizon, with all three probabilities and active/quiet state checked against the API.
- Every API onset score was checked in its split and row-set table. BSS, Brier, log-loss, and PR-AUC matched to three decimal places. Both validation and test were present in this mock report. Duration metrics matched their respective two-decimal or percentage precision, and selection and notes matched verbatim.
- Latest showed the snapshot time and latest API probabilities, with replay controls, outcome panel, and outcome legend absent. Returning to Replay restored the prior replay hour.
- Document and body widths stayed within the viewport on the map, selected panel, list, and report at both sizes. Wide report tables scroll within their own containers.

### Screenshots opened and reviewed

All four captures are full-page images from the required CSS viewports. I opened the final desktop and mobile map captures myself. I also reviewed the report layout in the saved desktop and mobile report captures during verification.

- [Desktop map](../test-results/artifacts/mock/dashboard-clicking-a-polyg-8930a-replay-outcomes-and-history-desktop/map.png): the amber Ukraine map is on the left, with white onset outlines, red active dots, and dark excluded regions. Chernihivskyi's panel is on the right with three probabilities, active status, remaining duration, replay outcomes, and the history line. Controls fit in one row and the legend is below the map.
- [Mobile map](../test-results/artifacts/mock/dashboard-clicking-a-polyg-8930a-replay-outcomes-and-history-mobile/map.png): controls wrap, the scrubber spans the available width, and the complete map fits. The selected panel stacks below the map. Its three probabilities, duration, outcomes, and history chart remain readable without horizontal page scrolling.
- [Desktop report](../test-results/artifacts/mock/dashboard-map-selected-pan-af504-wport-and-deep-links-reload-desktop/report.png): fixture disclosure, selection, validation/test metric tables, duration tables, and three reliability charts per split are visible on the black page.
- [Mobile report](../test-results/artifacts/mock/dashboard-map-selected-pan-af504-wport-and-deep-links-reload-mobile/report.png): the reliability charts stack vertically. Wide metric tables have their own horizontal scroll areas; the page itself fits the viewport.

The screenshot procedure returns to the page top before capture. This prevents an offscreen fixed skip link from appearing inside a full-page image captured while the document is scrolled. No screenshot-diff baselines were added.

### Live prerequisite check

With FastAPI absent, this command exited 1 before running browser workflows:

```powershell
$env:E2E_TARGET = 'live'
bunx playwright test --grep 'map covers'
```

The real error began:

```text
Error: E2E_TARGET=live requires the prepared FastAPI backend at 127.0.0.1:8000. From the repository root, run uv run airalert serve --port 8000 before Playwright. GET /api/meta failed: apiRequestContext.get: connect ECONNREFUSED 127.0.0.1:8000
```

This verifies the prerequisite failure, not the live dashboard. Full live verification remains wave-4 work.

## Decisions/assumptions

- Expected values come from `/api/meta`, `/api/raions`, `/api/forecast`, `/api/raion/{id}/history`, and `/api/report` at test time. Tests import only lead-owned TypeScript types; they do not import product formatting or colour functions, load fixture JSON, replace API responses, or hardcode model scores, dates, raion counts, or names.
- The three horizon keys, formatting precision, Kyiv timezone, and labels are UI contracts. Tests compare formatted API values and require paint changes where API probabilities change. They do not duplicate the colour interpolation implementation.
- Controls and tables use roles and labels. Existing `data-raion-id` attributes identify SVG geometry during bulk inspection. Semantic element reads inspect polygon paint, time attributes, and table cells. No new test hooks were needed.
- Each project finds adjacent varying hours within the first 25 replay hours, avoids source gaps, and leaves room for playback. If those API hours are all constant, the setup fails explicitly rather than claiming that forecast colours changed.
- Error capture includes console errors, Playwright page errors, browser ErrorEvents without exception objects, and unhandled rejections. ResizeObserver errors are not filtered out. The extra listeners exist only in the test browser.
- Playwright starts and stops Vite using its [webServer configuration](https://playwright.dev/docs/test-webserver). Port 5187 must be free; existing servers are never silently reused. The mock and live projects run the same test source.

### Wave-4 live run

Prepare the real processed data, forecasts, map, and report through the lead's pipeline first. In terminal 1, from the repository root, start and leave FastAPI running:

```powershell
Set-Location .
uv run airalert serve --port 8000
```

In terminal 2, run the unchanged suite:

```powershell
Set-Location .\web
$env:E2E_TARGET = 'live'
bunx playwright test
```

FastAPI must already answer `http://127.0.0.1:8000/api/meta`. Playwright starts `bun run dev --host 127.0.0.1 --port 5187 --strictPort` itself, forces `VITE_MOCK_API=0`, and proxies `/api` to FastAPI. Do not start another frontend on 5187. Live screenshots/traces go to `test-results/artifacts/live/`, and the HTML report goes to `test-results/report/live/`.

To use mock mode again in the same PowerShell terminal:

```powershell
$env:E2E_TARGET = 'mock'
bun run e2e
```

## Open issues

1. **Intermittent uncaught ResizeObserver errors.** The polygon/panel/history capture workflow produced `ResizeObserver loop completed with undelivered notifications.` twice in a mobile focused run and twice in a desktop focused run. Their summaries were `1 failed, 3 passed (5.6s)` and `1 failed, 3 passed (7.3s)`. All value assertions passed; the error guard failed. A separate T3 browser resize to 390×844 also emitted this event. The final full suite passed with the same strict listener, so this is intermittent. The precise trigger within layout changes and full-page capture is unresolved. Lead/A3 should investigate; the suite may fail until it is addressed. No error suppression, retry, or product fix was applied.
2. **Replay does not disclose source-outage hours.** At `2026-08-29T06:00:00Z`, `/api/meta.data_gaps` identifies the outage through `2026-09-02T14:00:00Z`. The actual rendered mock dashboard showed the normal forecast and actual-onset legend, with no outage, gap, or unscored warning. The synthetic-data banner was present, but it did not identify this missing-observation interval. The live renderer shares this code. Lead/A3 should decide how to mark these hours and distinguish unavailable observations before live signoff. This is a presentation finding, not a claim about live backend responses.
3. **Duration copy names the wrong statistic.** The report says `Pinball loss and median absolute error are in minutes.` The `mae_q50_min` value is mean absolute error for the median prediction, confirmed by `src/airalert/models/duration.py` computing `np.abs(errors[:, 1]).mean()`. The displayed values match the API, but this sentence can mislead readers about how the errors are aggregated. Owner: A3.

The full live workflow has not been exercised because the backend was not running. Phase 1 verifies the mock UI and provides the unchanged live suite; it is not a model-quality or leakage signoff.

## Contract change requests

None required for locating or testing the UI. Existing API contracts and ARIA labels were sufficient. The outage finding can use the existing `Meta.data_gaps` contract; the lead owns any presentation policy decision. No changes were made outside D1's owned paths and the explicitly allowed dependency/script files.

## Dependencies added

- Development dependency `@playwright/test@1.64.0`, declared as `^1.64.0` through `bun add -d`.
- Transitive lock entries `playwright@1.64.0` and `playwright-core@1.64.0`.
- Chromium installed/verified through `bunx playwright install chromium`.
