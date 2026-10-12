# F1 frontend v2 implementation

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
