# F2 frontend on the real v2 API

This record supersedes the earlier mock-only F1 integration limits below. Real workflows were verified on October 12, 2026 at 1440×900 and 390×844. Backend and Vite are stopped, news is off, and no commit was made.

## Delivered

- `web/vite.config.ts`: preserves the browser Host when proxying to FastAPI. Vite's shorthand proxy changed Host to port 8000 while keeping the frontend Origin, which caused real Settings saves to return 403.
- `web/src/App.svelte`, `web/src/lib/api.ts`, `resource.svelte.ts`: distinguish missing 404 hours and production-pending 503 responses; clear unavailable forecasts and prevent old probabilities from appearing under another hour. Pending production forecasts retry after two seconds. Metadata refreshes each minute. Replay starts at its latest bound, discovers issued hours through history in ranges of at most 31 days, waits for responses during playback, and skips missing hours. Early seven-day history requests are clamped to the metadata start. News startup refreshes every two seconds until its state changes.
- `web/src/lib/RaionPanel.svelte`, `HistoryChart.svelte`, `LiveView.svelte`: remaining-duration copy and a neutral fallback for invalid/past quantiles; history lines break at unissued hours; live status becomes stale after three minutes without a fresh receipt as well as after an API error.
- `web/src/lib/ModelReport.svelte`, `ReportScores.svelte`, `SkillTable.svelte`, `web/src/app.css`: selected/served models and test scores first, then validation and API notes, with all other comparisons in a disclosure. The selected onset table uses one row per horizon and fits mobile width. The actual v2 row sets are served/unprojected variants, so they are no longer mislabeled as a text comparison. Selected D2 duration landmarks are grouped together; model ids and counts remain API-driven.
- `web/dev/mock-api.ts`: complete synthetic hourly history, consistent with the fixture forecast probabilities, so replay availability works in mock mode too.
- `web/tests/{dashboard,live,replay-storage,news-live}.spec.ts`, `global-setup.ts`, `web/playwright.config.ts`: real read-only map/layout/Settings/auth/footer workflows, sparse replay and disconnected history coverage, every report metric compared with the API, and one desktop-only real news round trip with complete Settings restoration in a finally block. Common live tests assert zero Settings/auth writes. Login/logout scenarios are registered only in mock mode. Batched table reads verify the larger report without hundreds of separate browser round trips.
- `web/README.md`, `web/tests/README.md`, `web/IMPLEMENTATION.md`: run commands, current behavior, verification and limitations.

No backend file, `web/src/lib/types.ts`, fixture JSON, Python fixture test, dependency manifest, lockfile or git state was edited by F2. Other agents were editing backend/setup paths concurrently.

## Verified

All commands exited 0 unless explicitly described as an earlier failed attempt. Commands ran from `web/`, except pytest and the final listener/settings probe, which ran from the repository root. The API used `uv run --no-sync airalert serve --port 8000`; manual Vite used `bun run dev --host 127.0.0.1 --port 5173 --strictPort`. Playwright owned and stopped its separate Vite server on 5187.

`bun run check`:

```text
$ svelte-check --tsconfig ./tsconfig.app.json && tsc -p tsconfig.node.json
Loading svelte-check in workspace: .\web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

`bun run build`:

```text
$ vite build
vite v8.3.4 building client environment for production...
transforming...
✓ 336 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                              0.40 kB │ gzip:  0.27 kB
dist/assets/index-BMKU1-qD.css                              35.23 kB │ gzip:  9.12 kB
dist/assets/index-Bowcy2_f.js                              168.96 kB │ gzip: 60.57 kB

✓ built in 946ms
```

The build also emitted the existing Onest font files.

`uv run --no-sync pytest tests/test_fixtures.py`:

```text
============================= test session starts =============================
platform win32 -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: .
configfile: pyproject.toml
plugins: anyio-4.15.1
collected 26 items

tests\test_fixtures.py ..........................                        [100%]

============================= 26 passed in 0.74s ==============================
```

Final mock `bunx playwright test`:

```text
[WebServer] $ vite --mode mock --host "127.0.0.1" --port "5187" --strictPort
Running 52 tests using 2 workers

  52 passed (1.1m)
```

Full real `$env:E2E_TARGET = 'live'; bunx playwright test`, including the single successful news activation:

```text
[WebServer] $ vite --host "127.0.0.1" --port "5187" --strictPort
Running 41 tests using 2 workers
Real news states=off -> starting -> running, events=0, adjustments=0, last_extraction_at=null
Real news off remained stopped for 65 seconds.
  ok 14 [desktop] › tests\news-live.spec.ts:5:1 › real news starts, runs, and stops after restoring the original settings (2.1m)

  41 passed (2.4m)
```

After the final history/report refinements, the complete read-only live set ran again without repeating real news activation:

```text
$env:E2E_TARGET = 'live'
bunx playwright test --grep-invert 'real news starts'
Running 40 tests using 2 workers

  40 passed (51.9s)
```

No retries or skipped tests in these successful runs. The mock sample contains neither probability extreme, which its existing test records as two unexercised optional assertions. Live exercised both <0.01 and >0.99 through the map, panel, history and list.

Strict test/config checking returned no diagnostics:

```text
bunx tsc --ignoreConfig --noEmit --strict --target ES2022 --module ESNext --moduleResolution Bundler --types node playwright.config.ts tests/global-setup.ts tests/dashboard.spec.ts tests/live.spec.ts tests/news-live.spec.ts tests/replay-storage.spec.ts
```

Scoped `git diff --check` also returned no output.

### Real workflow evidence

- Live returned 139 display units and 139 polygons per layer. The actual inherited oblasts were Crimea, Luhanska and Sevastopol City. The only unknown unit in the final probe was Chornobyl. Counts varied during verification and tests used the response that actually rendered, rather than fixed expected counts.
- Normal browser refreshes advanced the freshness line from 21:25 Kyiv to 21:26 and then 21:29. Consecutive backend receipts were `2026-10-12T18:25:57.072690Z` and `2026-10-12T18:26:58.547263Z`, about 61.5 seconds apart. Both had 139 units.
- The backend was briefly stopped while Live stayed open. The banner read "Live updates are delayed. The last known status stays on the maps." Both maps retained 278 paths, 43 red and 32 yellow. After restarting and retrying, the banner cleared and counts refreshed to 43 red and 28 yellow.
- Now returned `at=2026-10-12T18:00:00Z`, 119 rows and `selected/D2`. The active Umanskyi panel showed 0.11 / 0.33 / 0.80 and 76 minutes remaining, P10–P90 12–236 minutes at that forecast hour. The backend end quantiles were later than the forecast hour. The unavailable fallback replaces the old "predicted median end passed" wording.
- Replay bounds were `2026-08-01T00:00:00Z` through `2026-10-12T18:00:00Z`. The real sparse-replay workflow found an unissued hour from API history, confirmed HTTP 404, scrubbed into it, checked that old map/panel probabilities disappeared, and verified stepping/playback to stored hours. Its chart assertion checked multiple disconnected line segments. `meta.data_gaps=[]` and no outage was assumed.
- The report returned 156 onset and 92 duration scores, selected `selected/D2`. Tests compared every onset metric to three decimals, every duration metric to its displayed precision, selection and all notes. The selected test section uses `test2-served`; the disclosure retains models such as `M1+geo-cal`, `D1c` and `D2@60`. Rolling notes were present. Bootstrap notes were not present in this response; any future notes use the same unfiltered rendering.
- Settings loaded the real extraction/evaluation choices, poll interval and channels. The enabled news save succeeded through the corrected proxy, status moved through starting/running, the on/off round trip restored the complete original Settings object, and off remained unchanged for 65 seconds. The real feed had zero events and adjustments. The positive event/adjustment views were exercised with mock data.
- Auth returned installed=true, logged_in=true and login_in_progress=false; Settings displayed "Logged in with ChatGPT". No real login, cancel or logout was called.
- The system footer displayed real data-through and next-nightly times, with no coverage-gap line for the empty gap list. Layout checks covered mobile plus 1024, 1280 and 1440 px, retained the separate dock/dialog bounds, and found no horizontal page overflow.

Earlier failures identified the history 422 and Settings 403 integration bugs above. An added pause assertion also raced automatic end-of-replay on mobile; it now stops through the stable Replay mode control, while the existing transport test still verifies Pause. A later report test was disrupted by a development reload during edits; the final runs occurred with product source stable.

### Screenshots opened and inspected

The following are real API captures from the final read-only live run. Desktop is exactly 1440×900; mobile is exactly 390×844. All eight requested files and the separate mobile selected-panel capture were opened and inspected. These local artifacts are git-ignored.

| View | Desktop | Mobile | What is visible |
|---|---|---|---|
| Live maps | [Open](test-results/artifacts/live/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/live.png) | [Open](test-results/artifacts/live/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/live.png) | Yellow above red, actual API counts/freshness, inherited hatching and unknown dots. Both maps and their key are visible after a small scroll; the separate Settings dock does not cover them. Landing-position captures are also saved as `live-landing.png`. |
| Forecast Now | [Open](test-results/artifacts/live/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/forecast-now.png) | [Open](test-results/artifacts/live/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/forecast-now.png) | Current-hour amber map with active Umanskyi selected. Desktop shows the active panel beside it. Mobile stacks that panel below the map; [its separate capture](test-results/artifacts/live/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/forecast-now-panel.png) shows remaining duration and the broken history line. |
| Model report | [Open](test-results/artifacts/live/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/report.png) | [Open](test-results/artifacts/live/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/report.png) | Selected/D2 precede the test period and the compact horizon-row onset table. On mobile, metrics fit the content width. Reliability, duration landmarks, notes and the comparison disclosure follow below. |
| Settings open | [Open](test-results/artifacts/live/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/settings.png) | [Open](test-results/artifacts/live/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/settings.png) | Real ChatGPT login state, saved models/efforts, interval and Cyrillic channel titles. Dialog content scrolls within the viewport above the dock. News is off. Logout is visible but was never clicked. |

Visual problems corrected: unissued hours displaying an older forecast under a new time; a straight history line inventing continuity across downtime; onset-duration copy describing already-passed ends; the oversized/unorganized v2 score list; mobile selected-score tables requiring a wide horizontal scroll; and v2 row sets incorrectly called a text-signal comparison.

## Decisions/assumptions

The fixed forecast universe and the larger live polygon set stay separate. Remaining minutes are measured at the immutable forecast hour, not decremented as if a new model prediction existed every minute. Replay availability uses one forecast raion's history because hourly forecasts cover the complete universe. Source outages and unissued forecasts are separate: an empty data-gap list does not imply every hourly prediction was issued. D2 landmark grouping uses the existing `model@minutes` ids without adding a schema field.

## Open issues

No unresolved frontend integration defect remains in the requested workflows. This real news cycle had no validated events or adjustments, so positive real overlay content could not be inspected. The test observed the worker's running state; it does not claim a completed extraction because `last_extraction_at` was null. Mock workflows cover event links and positive adjustment rendering.

## Contract change requests

None. Real payloads used the existing TypeScript contract; no backend or type edit was needed.

## Dependencies added

None.

## Server shutdown

The manually started backend and Vite processes were interrupted. Playwright stopped its own Vite process. The final real socket/settings probe returned:

```text
8000 listening=False
5173 listening=False
5187 listening=False
news_enabled=False
```

# Earlier F1 frontend v2 implementation

## F1b frontend polish

### Delivered

- `web/src/App.svelte`: put the page in its own scroll viewport and Settings in a separate bottom-left dock. The dock reserves 64 px plus the device safe area at every width, so neither maps nor footer text can pass behind the button. Added a Live-specific class for compact desktop spacing.
- `web/src/app.css`, `web/src/lib/LiveMap.svelte`: moved each live layer's label and count into a compact row above its full-width map; removed the redundant status subtitle; shortened the desktop Live header and preview notice; increased map height from 278 to 340 px at 1440×900, with a viewport-based height and 250/380 px limits. The geographic footprint is about 485 px wide, up from 397 px, without changing the projection. Yellow remains above red. Coverage legend text has more contrast.
- `web/src/app.css`: news follows Live below 1191 px and Forecast below 1400 px. At wider widths it stays in an independent sidebar with bounded height and its own scrolling. Closing it releases the sidebar width to the map canvas. Forecast retains the map and selection panel side by side from 1024 px when news is stacked.
- `web/src/app.css`: bounded Settings above the dock, accounted for safe areas, added scroll padding for its sticky heading, and prevented scroll chaining. Model labels can shrink and channel/news text can wrap. Removed the obsolete 88 px footer padding now that the dock has its own space.
- `web/tests/live.spec.ts`: added one focused layout workflow at 390, 1024, 1280 and 1440 px; checked dock separation, footer safety text, news dismissal and recovered width, Forecast stacking, dialog bounds and the final Settings action. Extended the existing map workflow to check pointer tooltips alongside keyboard tooltips and the new scroll viewport's horizontal overflow. Captures now use exact viewport sizes, with both landing and slightly scrolled Live views.
- `web/IMPLEMENTATION.md`: this report. All F1b edits are in these five web files.

### Verified

Commands ran from `web/` and exited 0:

- `bun run check`: `svelte-check found 0 errors and 0 warnings`.
- `bun run build`: `335 modules transformed`; `built in 1.04s`.
- `bunx playwright test`: `50 passed (1.2m)`, with no retries or skipped tests. The existing extreme-probability workflow still reports that the sampled mock hour has no probabilities below 0.01 or above 0.99, so those optional assertions were not exercised.
- `bunx tsc --ignoreConfig --noEmit --strict --target ES2022 --module ESNext --moduleResolution Bundler --types node playwright.config.ts tests/global-setup.ts tests/dashboard.spec.ts tests/live.spec.ts`: no diagnostics.

The first full run passed 49/50. The new scroll-distance assertion used Playwright's centering scroll, which moved farther than needed. Using `scrollIntoView({ block: 'nearest' })` checks the minimum movement instead; the final complete run above passed.

The mock was also inspected in the collaborative browser, including the 1024 px Forecast layout and scrolling the mobile selected-raion and Settings panels. The manual mock server on 5188 was stopped; Playwright stopped its server on 5187. The final listener check found zero listeners on both ports.

### Screenshots opened and inspected

All captures below are from the final passing mock run. Desktop files are 1440×900; mobile files are 390×844.

| View | Desktop | Mobile | Observations |
|---|---|---|---|
| Live, both maps | [Open](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/live.png) | [Open](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/live.png) | Yellow over red, clear count rows, readable hatching/dots and legends, with Settings in its own strip. Desktop needs a 79 px scroll to show both complete maps and the key. Mobile is scrolled past the page header to show both complete maps; news follows them. |
| Live, landing position | [Open](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/live-landing.png) | [Open](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/live-landing.png) | These retain the app header and show the initial viewport honestly. The bottom of the red map is below the scroll viewport, with the button separated from it. |
| Settings open | [Open](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/settings.png) | [Open](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/settings.png) | Bottom-left dialog stays fully above the dock. Model/effort fields and Cyrillic channels fit; later channels and the news-reopening action are reachable by scrolling inside the dialog. |
| Forecast with news | [Open](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/forecast-news.png) | [Open](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/forecast-news.png) | Desktop keeps the map, selected raion and news in separate columns. Mobile stacks controls, map, selected panel and news. The selected panel was separately scrolled into view and visually checked in the browser; its comparison table and history remain clear above the dock. |

### Decisions/assumptions

The small desktop scroll is intentional to give the maps more size while preserving geographic proportions. The normal 1440×900 mock view needs 79 px to include both maps and their key, within the layout test's 100 px allowance. Loading/error banners can add height. Verification used the mock, as requested.

### Open issues

Real v2 backend integration remains for the lead to verify. No unresolved overlap was found in the requested mock workflows.

### Contract change requests

None.

### Dependencies added

None.

## F1 initial integration record

The following is the earlier F1 delivery record. Its layout and screenshot observations are superseded by the F1b report above.

The v2 frontend is ready for backend integration. Live is the default landing tab, with independent yellow and red maps stacked vertically. The required mock UI workflows and screenshots were verified at 1440×900 and 390×844. Every server started by F1 has been stopped.

## Delivered

- `web/src/App.svelte`, `web/src/app.css`: Live / Forecast / Model report navigation, minute polling, current-hour Now forecasts, persistent news dismissal, optimistic settings writes with rollback, and the bottom-left settings control.
- `web/src/lib/LiveMap.svelte`, `LiveView.svelte`, `live-format.ts`: all 139 display polygons keyed by the crosswalk `display_key`, independent red/yellow status, API-derived counts, Kyiv freshness, inherited hatching, unknown dots, and pointer/keyboard/touch tooltips. Tooltips include both alert start times and elapsed durations, coverage explanations, and other hazards. Failed live requests retain the last maps and show the delay banner.
- `web/src/lib/NewsCard.svelte`: off, needs Codex, needs login, starting, running, stale, error, and quota-limited states; three recent validated events with type icons, oblasts, ages, and source links; persistent close and settings-based reopening.
- `web/src/lib/SettingsPanel.svelte`: a native modal dialog with focus containment, Esc closing and focus restoration; news toggle; ChatGPT login/status polling/cancel; installation guidance; confirmed machine logout; extraction/evaluation model and effort controls; poll interval; channel toggles and subscriber counts.
- `web/src/lib/SystemFooter.svelte`: data coverage, next Kyiv midnight update, coverage gaps, and failed nightly-update status from `/api/system`.
- `web/src/lib/MapView.svelte`, `RaionPanel.svelte`, `RaionList.svelte`, `format.ts`, `api.ts`: experimental Base / News-adjusted switching, a base/adjusted comparison with probability-point changes and the API's logit shifts, linked source events, and excluded polygons showing current live status with “No forecast.” Replay keeps its original probabilities and outcome/history behavior.
- `web/dev/mock-api.ts`, `web/vite.config.ts`: current-hour fixture shifting, all v2 routes, per-browser mock state, isolated failure/status scenarios, and simulated login. `dev:mock` selects mock mode without requiring an ignored env file. Production builds exclude the mock middleware.
- `web/src/lib/fixtures/`: the full real 139-polygon geometry with crosswalk IDs and 16 v2 JSON fixtures covering live/stale, system, settings, eight news states, and four auth states. The logged-out fixture is named `auth_logged_out.json` because the repository ignores `auth.json`.
- `tests/test_fixtures.py`: every new fixture validates and round-trips through its pydantic v2 model. Geometry/live key coverage, independent alert layers, inherited/unknown coverage, and adjustment/event relationships are also checked.
- `web/tests/dashboard.spec.ts`, `web/tests/live.spec.ts`: API-driven replay/Now checks and focused v2 browser workflows. Existing `web/playwright.config.ts` already supplies the required two viewports and was retained.
- `web/README.md`, `web/tests/README.md`, `web/IMPLEMENTATION.md`: current run instructions, mock controls, evidence, and integration limits.

All product source changes are inside the authorized web paths; the only backend-side edit is the authorized fixture test. `web/src/lib/types.ts`, pydantic schemas, v1 model reports, dependency manifests, and lockfiles were not edited. No commits, branch changes, or other git state mutations were performed.

## Verified

Final commands ran from `web/` unless noted. All commands below exited 0.

`bun run check`:

```text
$ svelte-check --tsconfig ./tsconfig.app.json && tsc -p tsconfig.node.json
Loading svelte-check in workspace: .\web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

`bun run build`:

```text
$ vite build
vite v8.3.4 building client environment for production...
transforming...
✓ 335 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                              0.40 kB │ gzip:  0.27 kB
dist/assets/index-Dqd-8c2W.css                              33.69 kB │ gzip:  8.82 kB
dist/assets/index-DTYdc0F_.js                              164.17 kB │ gzip: 59.04 kB

✓ built in 409ms
```

The build also emitted the existing Onest font files.

From the repository root, `uv run pytest tests/test_fixtures.py`:

```text
============================= test session starts =============================
platform win32 -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: .
configfile: pyproject.toml
plugins: anyio-4.15.1
collected 26 items

tests\test_fixtures.py ..........................                        [100%]

============================= 26 passed in 0.26s ==============================
```

The first fixture run passed without an override. For the final run, the shell used `UV_NO_SYNC=1` because a parallel backend process held the editable package executable open and uv's attempted reinstall failed. The final run used the installed environment and current source, without stopping that backend process.

From the repository root, `uv run ruff check tests/test_fixtures.py`, with the same no-sync environment:

```text
All checks passed!
```

Final mock `bunx playwright test`:

```text
[WebServer] $ vite --mode mock --host "127.0.0.1" --port "5187" --strictPort

Running 48 tests using 2 workers

  48 passed (41.9s)
```

There were no test retries or skipped tests in the mock run. The existing extreme-probability workflow logged that the sampled API hour contained neither extreme, so those two optional sample assertions were not exercised. Replay, report metrics, history, selection, list, and Now workflows otherwise remained active.

The ten v2 workflows ran in both viewports. They verified 139 polygons per map, API-derived counts and coverage, yellow-over-red order, stale status, a failed poll after a simulated 60-second interval without clearing state, dismissal/reload/reopening, Esc/focus restoration, settings PUTs and rollback, model/effort/poll/channel persistence, login/cancel/logout confirmation, missing-Codex guidance, exact-hour news adjustment and source links, all news states, and no horizontal document/body overflow.

Strict test/config type checking passed with no output:

```text
bunx tsc --ignoreConfig --noEmit --strict --target ES2022 --module ESNext --moduleResolution Bundler --types node playwright.config.ts tests/global-setup.ts tests/dashboard.spec.ts tests/live.spec.ts
```

A direct Vite configuration check for `command=serve, mode=mock` returned:

```json
{"mock":"true","mockMiddleware":true}
```

### Screenshots opened and inspected

The real mock page was opened in the T3 collaborative browser at both required viewports. The Playwright workflows also captured the same real page; all six final files below were opened and inspected. Live and forecast captures include the full page. Settings captures use the exact viewport so the dialog remains readable.

| View | Desktop 1440×900 | Mobile 390×844 | Observation |
|---|---|---|---|
| Live | [Screenshot](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/live.png) | [Screenshot](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/live.png) | Yellow stays above red. Both desktop maps fit in the first viewport. The fixture counts are 46 yellow and 34 red. Hatching is visible over inherited eastern/Crimean units, with dotted unknown units. On mobile the maps remain full-width and the quiet news card follows them. |
| Forecast with news | [Screenshot](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/forecast-news.png) | [Screenshot](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/forecast-news.png) | The amber probability map, selected Cherkaskyi panel, and narrow news card share the desktop layout. News-adjusted is selected, showing 0.08 / 0.23 / 0.50 and the corresponding base/adjusted table. Mobile stacks the selected panel, history, and news card without horizontal overflow. Excluded regions retain visibly distinct red/yellow live paint. |
| Settings | [Screenshot](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-desktop/settings.png) | [Screenshot](test-results/artifacts/mock/live-system-footer-reports-621a5-apture-desktop-mobile-views-mobile/settings.png) | A bottom-left dark dialog dims the page. Model/effort fields and Cyrillic channel titles remain readable at 390 px. Long content scrolls inside the dialog; the final channel and reopening control remain reachable. |

The HTML report and traces are under `web/test-results/report/mock/` and `web/test-results/artifacts/mock/`. These artifacts are git-ignored and a later run replaces them.

### Server shutdown

The manual mock server on 5188 was interrupted. Playwright stopped its own server on 5187. The final listener check returned:

```text
port 5187: 0 listeners
port 5188: 0 listeners
```

F1 did not start or stop a backend server.

## Decisions/assumptions

- Status colors are lemon yellow `#eee34b` and crimson red `#e54862`. Their hues stay separate from the amber/orange sequential probability ramp. Each live layer has a single categorical active fill; text labels, coverage patterns, and tooltips carry the remaining meaning. The page remains `#000` with Onest throughout.
- The forecast universe stays 119 raion IDs; live display uses 139 crosswalk keys. Excluded polygons have live status but no forecast. Their status remains explicitly current during replay.
- News adjustments apply only when the agent is running and enabled, with `base_at` equal to the returned Now forecast hour. Stale/off/error and historical replay use base probabilities.
- API `delta_*` values are logit shifts, per the pydantic contract. The UI separately computes and labels the probability difference in percentage points.
- Polling live/news/system uses 60 seconds. The news poll interval setting controls the backend news agent; it does not change the live-map poll interval. Login status polls every second while the login dialog is in progress.
- The poll interval input accepts whole seconds from 60 to 3600. Models come from `AuthStatus.available_models`; the saved default selections are Luna/high for extraction and Sol/high for evaluation.
- The mock's state is isolated by a development-only browser cookie. Auth actions simulate the backend without invoking Codex. The v2 auth/scenario test file is skipped for `E2E_TARGET=live` so it cannot sign the machine in or out.

## Open issues

The real v2 backend integration and actual external ChatGPT browser login were not exercised in this mock-first phase. The lead should verify the combined application after the parallel routes and scheduler are ready. No unresolved frontend defect was found in the required mock workflows.

## Contract change requests

None. The existing v2 types were sufficient and remained unchanged.

## Dependencies added

None.
