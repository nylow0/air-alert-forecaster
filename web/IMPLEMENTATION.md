# A3 frontend phase 2 implementation report

The dashboard is verified against the live FastAPI backend with real forecasts and scores at 1440×900 and 390×844. All five requested fixes are integrated. Check and build pass, the mock e2e suite passes repeatedly, and the final live suite passes all 24 executions. All servers started for this task have been stopped.

For normal operation, run `uv run airalert serve --port 8000` from the repository root and `bun run dev` from `web/`, with `VITE_MOCK_API` unset or `0`. Use `bun run dev:mock` for synthetic fixtures.

## Delivered

- `web/src/lib/MapView.svelte`, `web/src/app.css`: removed `bind:clientWidth` and the measured-width-to-SVG-size feedback path. The map has a fixed 800×528 coordinate space inside a CSS aspect-ratio container. Projection paths depend only on geometry; CSS handles resizing. Tooltips use proportional coordinates. No ResizeObserver error is suppressed or filtered.
- `web/src/App.svelte`, `web/src/lib/gaps.ts`: outage bands on the scrubber, a source-outage notice, and a shorter recovery notice near the time readout. The same disclosure appears in Latest when its snapshot falls in recovery.
- `web/src/lib/RaionPanel.svelte`, `web/src/lib/HistoryChart.svelte`, `web/src/lib/RaionList.svelte`: gap bands and a key in history, unknown observation wording during outages, and unknown list status. Null onset outcomes remain “Unknown.” The map outlines actual onset only when the API value is explicitly true. Active dots are hidden during outages because alert status is unknown.
- `web/src/lib/ReliabilityChart.svelte`: five-unit label leaders entirely outside the plot, starting at x=278 while the plot ends at x=272. Nothing extends a model curve beyond its last real bin. The label column expands for longer model IDs.
- `web/src/lib/ModelReport.svelte`: copy names the mean absolute error of the median (P50) prediction. The table header is “P50 mean absolute error.” Identical notes repeated by report fragments display once, with their text preserved.
- `web/vite.config.ts`, `web/src/env.d.ts`: mock middleware and the development banner share one build-time flag. Production builds set it false. The recovery duration is read from the existing Python configuration at build time rather than duplicated in the frontend.
- `web/IMPLEMENTATION.md`: this phase 2 handoff replaces the phase 1 fixture-only evidence.

All source edits are in A3-owned paths. Lead-owned types and fixtures, D1 tests/config, dependency manifests, and backend/model files were not edited. No git state changes were made.

## Verified

### Check and build

From `.\web`, final `bun run check`, exit 0:

```text
$ svelte-check --tsconfig ./tsconfig.app.json && tsc -p tsconfig.node.json
Loading svelte-check in workspace: .\web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

Final `bun run build`, exit 0:

```text
$ vite build
vite v8.3.4 building client environment for production...
transforming...
✓ 329 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                              0.40 kB │ gzip:  0.27 kB
dist/assets/onest-cyrillic-ext-wght-normal-DOFToQv1.woff2   10.54 kB
dist/assets/onest-vietnamese-wght-normal-BBxOatbv.woff2     12.40 kB
dist/assets/onest-cyrillic-wght-normal-DayYxulF.woff2       15.86 kB
dist/assets/onest-symbols-wght-normal-DxQzqQfC.woff2        18.16 kB
dist/assets/onest-math-wght-normal-Bl4ZGkZl.woff2           25.48 kB
dist/assets/onest-latin-ext-wght-normal-QAhDxQuo.woff2      28.45 kB
dist/assets/onest-latin-wght-normal-Dun_Rd-l.woff2          33.76 kB
dist/assets/index-DaCN5ong.css                              26.03 kB │ gzip:  7.27 kB
dist/assets/index-QYChtVs7.js                              136.18 kB │ gzip: 49.77 kB

✓ built in 346ms
```

### Unchanged D1 e2e suite

Ran `$env:E2E_TARGET='mock'; bunx playwright test` repeatedly. The last three consecutive mock completions, each exit 0, were:

```text
Running 24 tests using 2 workers
  24 passed (18.1s)

Running 24 tests using 2 workers
  24 passed (16.2s)

Running 24 tests using 2 workers
  24 passed (17.5s)
```

The last mock run followed the final recovery-notice copy adjustment. An earlier independent three-run sequence also passed 24/24 at 18.4s, 18.6s, and 18.5s. The error guards stayed enabled throughout, including browser ErrorEvents without exception objects. No ResizeObserver errors, retries, or skips occurred in these successful runs.

With FastAPI running, final `$env:E2E_TARGET='live'; bunx playwright test`, exit 0:

```text
[WebServer] $ vite --host "127.0.0.1" --port "5187" --strictPort

Running 24 tests using 2 workers

  24 passed (20.2s)
```

The final live run covers all 12 workflows in both Chromium viewports. Every displayed onset metric and duration score is compared with the live API. Both validation and test scores were available. The served models were M1 and D1; onset comparisons included B0, B1, B1-full, M1, M1-full, and M1-raw.

Two earlier live attempts exposed separate issues:
- One Vite process exited with code 9 after 12 passing workflows. Subsequent failures were connection refusals. The cause of that one process termination was not established; later complete live runs passed.
- The next run passed 22 workflows but failed the two report-note checks because identical API notes rendered twice. Deduplicating displayed notes resolved this without changing the API or D1 tests.

Reports and traces are under `web/test-results/report/{mock,live}/` and `web/test-results/artifacts/{mock,live}/`. Rerunning the suite replaces its prior artifacts.

### Live browser observations

Used the T3 collaborative browser and the real API, plus D1's actual Chromium workflows. No API values were replaced with fixtures.

| Flow | Evidence |
|---|---|
| Map and horizons | All 119 forecast raions render against real geometry. Switching 1/3/12 h changes probability paint and URL state. Selected and no-data polygons retain their respective meanings. |
| Replay | Bounds come from Meta, 2026-08-01T00:00Z through 2026-09-06T17:00Z. Scrubbing, step controls, bounds, play/pause, keyboard selection, and deep-link reloads pass in both viewports. |
| Active duration | At 2026-08-01T03:00Z, Novhorod-Siverskyi displayed 0.08/0.51/0.91 and an estimated median end in 72 min. Kryvorizkyi displayed 0.12/0.51/0.91 and “The predicted median end passed 12 min ago. The alert is still active.” Both use real D1 end quantiles. |
| Outage | At 2026-08-30T12:00Z, all 119 API rows had null outcomes at all horizons. The selected panel showed three “Unknown” outcomes, the map had zero actual-onset outlines and zero active dots, and history had one clipped outage band. The mobile list showed 119 unknown statuses. |
| History inspection | During the outage, the visible readout and slider ARIA text identify alert observations as unknown. Forecast probabilities remain visible and explicitly unscored. |
| Recovery | At the exact gap end, 2026-09-02T14:00Z, the notice switches to recovery. The historical outage remains shaded. |
| List and report | Probability sorting and panel selection pass for every horizon. Real report variants render in chart groups of at most three; all values match the API. Identical evaluation notes display once. |
| Latest | Snapshot 2026-09-07T05:00Z appears as 08:00 Kyiv. Replay controls and actual outcomes are absent. The recovery and missing-running-alert limitations are visible. |
| Layout and banner | No horizontal page overflow on map, panel, list, or report at either viewport. Wide report tables scroll within their regions. The synthetic-fixture banner is absent against the live API. |

The loaded report was generated at `2026-10-12T09:48:03.912882Z`. Forecasts were last written at 12:48:20 local time, before this task's backend startup around 12:49, so the live verification used the regenerated M1/D1 artifacts.

### Playback and stale requests

Measured a 12-second playback observation at each viewport, with a selected raion and history fetching. Tool action boundaries added a few more advances before Pause; each sample covered 33 distinct hourly changes.

| Measurement | Desktop 1440×900 | Mobile 390×844 |
|---|---:|---:|
| Hourly advances | 33 | 33 |
| Forecast requests / distinct hours | 33 / 33 | 33 / 33 |
| History requests | 33 | 33 |
| Forecast fetch P50 | 9.6 ms | 9.5 ms |
| Forecast fetch P95 | 14.3 ms | 30.3 ms |
| Maximum forecast fetch | 24.1 ms | 95.2 ms |
| Frame P95 | 16.7 ms | 16.8 ms |
| Frames sampled | 872 | 875 |
| Frames above 50 ms | 0 | 0 |
| Captured browser errors | 0 | 0 |

Playback remains at 400 ms per hour, with one forecast request per change. Existing Resource cancellation and previous-response retention are preserved.

For the stale-response check, the browser briefly held the first real forecast response for 500 ms while dispatching ten scrubber changes 25 ms apart. Its AbortSignal became aborted, nine superseded signals were aborted, and the final panel still matched the latest real API row at 2026-08-02T22:00Z: 0.25/0.64/0.91. The delayed older result did not overwrite it. The wrapper changed timing only, not response values, and was restored immediately.

### Screenshots opened and reviewed

The full-page D1 captures use the exact requested CSS viewports. T3 viewport screenshots use the measured 1440×900 layout but export a 1280×800 raster; its mobile raster is at double density.

- [Desktop map with selected active raion](test-results/artifacts/live/dashboard-clicking-a-polyg-8930a-replay-outcomes-and-history-desktop/map.png): real amber geometry, white onset outlines, red active dots, and Novhorod-Siverskyi beside the map. At the captured 01:00Z hour its probabilities are 0.10/0.37/0.89 and its median remaining end estimate is 13 min.
- [Desktop report](test-results/artifacts/live/dashboard-map-selected-pan-af504-wport-and-deep-links-reload-desktop/report.png): actual validation/test tables for all six onset models, D0/D1 duration scores, corrected error labels, and separate chart-label leaders.
- [Mobile map](test-results/artifacts/live/dashboard-clicking-a-polyg-8930a-replay-outcomes-and-history-mobile/map.png): complete map within 390 px, wrapped controls and scrubber, and the active raion panel/history stacked below without horizontal page scrolling.
- [Hour inside the outage]([private home path removed] striped scrubber interval, source-outage notice, unknown outcomes/status, and no onset or active marks. The selected-raion outline remains a selection mark.
- [History around the outage]([private home path removed] a grey band covers the missing observations behind the probability curve, separate from red actual-episode bands, with an explicit source-outage key.
- [Reliability detail]([private home path removed] B0 ends at its last observed bin; short coloured label ticks sit beyond the plot. Variant IDs remain readable.

The gap/detail captures are local T3 browser artifacts rather than committed files.

### Ownership and shutdown

`git -c core.autocrlf=false diff --check -- web` exited 0 with no output. The diff query for `web/src/lib/types.ts`, `web/src/lib/fixtures`, `web/tests`, and `web/playwright.config.ts` returned no paths. Product-source searches found no remaining “median absolute error,” “Median MAE,” `bind:clientWidth`, or ResizeObserver code.

Started FastAPI on 8000 and a separate live Vite server on 5188. Playwright managed its own servers on 5187. Sent Ctrl+C to both long-running sessions and checked listeners:

```text
StartedServerListeners : 0
Existing5173           : 1
```

The first count checks 8000, 5187, and 5188. The pre-existing 5173 server was left untouched. This task's T3 browser tab was also closed.

## Decisions/assumptions

- Preserved the established black/Onest dashboard design and the existing API client. Real data is the default; fixtures remain an explicit development mode.
- Outage intervals and recovery intervals are half-open, matching the scoring policy. Only outages are shaded; recovery gets text because observations have resumed but feature lookbacks remain affected.
- Recovery duration comes from `src/airalert/config.py`, currently 168 h. Neither that file nor Meta/types was edited.
- Null onset labels follow the API even before an outage starts when a horizon's label window touches it. They are never converted to “No.”
- Report selection, notes, models, and values stay data-driven. Duplicate note display is collapsed without rewriting the text.

## Open issues

No remaining frontend blocker found in the requested flows. The one earlier Vite exit with code 9 remains unexplained; it did not recur in the final complete live runs. Measurements are local browser observations, not a claim about slower networks or hardware.

## Contract change requests

Nonblocking: add the recovery duration to Meta and its TypeScript twin so a deployed frontend can follow the live backend policy directly. The current build-time adapter reads the single existing Python constant and fails clearly if it is absent. A different deployment policy would currently require rebuilding the frontend.

## Dependencies added

None.
