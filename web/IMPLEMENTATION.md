# A3 frontend implementation report

The fixture-backed dashboard is implemented and verified in the real browser at 1440×900 and 390×844. Start it from `web/` with `bun run dev:mock`. The verification server remains at http://127.0.0.1:5173/.

## Delivered

- `web/src/App.svelte`: Replay/Latest modes, horizon controls, hourly scrubber, play/pause, step buttons, keyboard controls, shareable URL state, page tabs, and loading/error states.
- `web/src/lib/api.ts`: the one typed `/api/*` client, including slash-containing raion history paths and readable fetch errors.
- `web/dev/mock-api.ts`, `web/.env.mock`, `web/vite.config.ts`, `web/package.json`: dev-only mock routes and the `dev:mock` script. Real GeoJSON is preferred, with fixture geometry as the fallback.
- `web/src/lib/MapView.svelte`: cached D3 SVG geometry, OKLCH sequential probability fills, gradient legend, actual-onset outlines, active dots, selection, hover and keyboard-focus tooltips, and no-data styling.
- `web/src/lib/RaionPanel.svelte`, `web/src/lib/HistoryChart.svelte`: three probabilities, active-alert timestamps, relative duration quantiles, honest overdue-median wording, replay outcomes, seven-day history, episode bands, crosshair inspection, and accessible numerical data.
- `web/src/lib/RaionList.svelte`: the probability-sorted accessible map twin, with all three horizons, oblast, active/quiet status, and selection.
- `web/src/lib/ModelReport.svelte`, `web/src/lib/SkillTable.svelte`, `web/src/lib/ReliabilityChart.svelte`: data-driven validation/test skill tables, fixed model colours, reliability diagrams, duration metrics, selection and notes, and conditional text-subset comparison. Additional models are rendered in groups of at most three chart series.
- `web/src/lib/resource.svelte.ts`, `web/src/lib/format.ts`: request cancellation and previous-response retention; shared Kyiv time, probability, and colour formatting.
- `web/src/app.css`: Onest, pure-black page, responsive layout, 360 px desktop panel, stacked mobile panel, visible focus, and mobile touch targets.
- `web/README.md`, `web/IMPLEMENTATION.md`, `web/.gitignore`, `web/bun.lock`, `web/screenshots/`: operating notes, this evidence, ignored screenshot artifacts, and added dependencies.
- Removed the unused Counter, hero image, Svelte/Vite logos, and template public icons.

All edits are within A3-owned `web/` paths. `types.ts`, fixtures, D1 tests/config, and files outside `web/` were not edited. No git state was changed.

## Verified

### Commands

From `.\web`, final `bun run check` output, exit 0:

```text
$ svelte-check --tsconfig ./tsconfig.app.json && tsc -p tsconfig.node.json
Loading svelte-check in workspace: .\web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

Final `bun run build` output, exit 0:

```text
$ vite build
vite v8.3.4 building client environment for production...
transforming...
✓ 328 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                         0.40 kB │ gzip: 0.27 kB
dist/assets/index-B9H97Fu1.css          25.20 kB │ gzip: 7.07 kB
dist/assets/index-CDG6A5ug.js           133.33 kB │ gzip: 49.03 kB
✓ built in 331ms
```

The build also emits the Onest font subsets, including Cyrillic.

Ran `bun run dev:mock --host 127.0.0.1 --port 5173 --strictPort`:

```text
VITE v8.3.4 mock ready in 1022 ms
Local: http://127.0.0.1:5173/
```

`git diff --name-only -- web/src/lib/types.ts web/src/lib/fixtures web/tests web/playwright.config.ts` returned no paths. `git check-ignore web/screenshots/desktop-map.png web/screenshots/mobile-map.png` returned both paths.

### Real browser workflows

Used the T3 collaborative browser against the running Vite server, with its viewport explicitly set to each requested size. These were actual rendered DOM/SVG interactions and API responses. No tests or fixtures were written into D1/lead paths.

| Check | Observed result |
|---|---|
| Geometry source | `/api/raions` returned `X-Airalert-Geometry: real`; 139 SVG polygons, 119 unique non-null raion IDs. All 119 forecast rows matched geometry; no unmatched forecast IDs. |
| Desktop overflow | `innerWidth=1440`, `innerHeight=900`, `document.documentElement.scrollWidth=1440`. |
| Mobile overflow | `innerWidth=390`, `innerHeight=844`, `document.documentElement.scrollWidth=390`. This held on map, list, and report. |
| Horizon | Desktop 3→12 h and mobile 3→1 h changed polygon fills and the URL horizon. Desktop path strings stayed identical. |
| Scrubber | Hourly changes altered time, URL, and fills. Native ArrowLeft on the focused mobile range input also changed time and fills. |
| Play/pause | Desktop play advanced from 02:00 to 05:00 UTC and changed fills. A 1250 ms mobile observation during playback recorded four additional hours and changed fills; Pause then stopped it. |
| Raion selection | Clicking Kharkivskyi opened the panel with three figures, active status, end quantiles, actual outcomes, and the history SVG. At the sampled 03:00 UTC forecast, figures were 0.25 / 0.49 / 0.81 and the median remaining duration was 50 min. |
| Mobile layout | Panel position was below the map; the grid had one 358 px column. All rendered buttons measured at least 44 px high. |
| Keyboard polygons | Focus showed the matching Umanskyi tooltip; Enter selected Umanskyi. Focus priority remained correct when scrolling triggered pointer events. |
| History exploration | Focusing the chart and pressing ArrowLeft showed Kyiv time, P1/P3/P12, and active/quiet status. The forecast time stayed unchanged. Numerical history and episode data are available in the expandable table. |
| List | 119 rows, descending by the selected horizon, on desktop and mobile. Selecting Cherkaskyi in the mobile list updated the panel and history. The mobile table fit its 358 px container. |
| Latest | The snapshot showed 07 Sept 2026, 08:00 UTC+03:00. Scrubber and replay outcomes were hidden; the latest fixture had null actual-onset flags throughout. |
| Report values | All full-row-set onset scores matched the API fixture's displayed values; duration MAEs and report notes also matched. Six model rows and six reliability charts rendered across validation/test. Selection showed M1 and D1 with its fixture note. |
| Report variants | A temporary browser-only response containing `Experimental-v2` rendered the extra model. Non-full scores displayed the text-comparison section. A validation-only response omitted test results. Each chart retained at most three series. Original fixtures were restored afterward. |
| Reliability markers | Minimum rendered mobile marker diameter was 9.29 px, above the required 8 px. Empty bins have no markers. |
| Loading | A held forecast request retained the same paths, dimmed the previous map, displayed Updating, and set `aria-busy=true`; releasing it resumed rendering. |
| Error/retry | Browser-only 503 responses for forecast, history, and report showed their retry buttons and retained previous content. Retry recovered each resource. The final forecast message was `Could not load the forecast. Try again.` |
| Empty | Browser-only empty forecast, history, and report responses showed explicit empty messages. Empty forecasts changed all polygons to no-data fills. |
| Overdue duration | A browser-only response with P50 ten minutes in the past displayed `The predicted median end passed 10 min ago. The alert is still active.` P10/P90 remained relative to forecast time, with an explanation for negative values. |
| Fresh load | Reopened `/` without query parameters after verification. It rendered 139 polygons, used the default hourly replay time, and displayed no error banner. |
| Errors | After correcting the initial timestamp race and focus-tooltip precedence, normal workflows and injected error/retry cases recorded no uncaught page errors, unhandled rejections, or console errors. |

Temporary response changes were made only in the browser's fetch function and were removed by navigation. The source fixtures remained untouched.

### Screenshots reviewed

These files were saved under `web/screenshots/` and visually inspected. The desktop browser viewport was 1440×900; T3 exports its screenshot raster at 1280×800. Mobile captures came from the 390×844 viewport and were exported at double pixel density. The dimensions used for layout verification above are measured CSS pixels.

- [Desktop overview](screenshots/desktop-overview.png): title, tabs, all controls, the real amber Ukraine map, and Kharkivskyi's three probabilities and alert state beside it.
- [Desktop map detail](screenshots/desktop-map.png): full real polygons with excluded regions receding, white onset outlines, red active dots, gradient/status legend, and the side panel/history.
- [Desktop report heading](screenshots/desktop-report-heading.png): fixture notes, selection and its note, plus the beginning of the skill table.
- [Desktop report detail](screenshots/desktop-report.png): validation values across all horizons and three reliability diagrams with consistent B0/B1/M1 colours and direct labels.
- [Mobile map](screenshots/mobile-map.png): wrapped controls, usable scrubber, and the full Ukraine map within the narrow page.
- [Mobile panel](screenshots/mobile-panel.png): three large probabilities, duration/outcomes, and the single probability axis with real-episode bands.
- [Mobile list](screenshots/mobile-list.png): raion/oblast names, all three probabilities, selected-horizon emphasis, and text-paired active status fit on screen.
- [Mobile report](screenshots/mobile-report.png): stacked reliability diagrams, readable markers/direct labels, and access to bin data.

Vertical scrolling is intentional on the dashboard and report. There is no horizontal page scroll. Wide report tables scroll within their focusable containers.

## Decisions/assumptions

- Followed `docs/design-notes.md` and the frontend-design skill: Onest, black page, quiet controls, a single warm probability hue, separate status marks, and fixed categorical report colours.
- Geometry is read afresh by the dev middleware, so an A2 update appears on reload. D3 winding correction is local and does not change A2 files.
- Mode, horizon, and time are written to the URL. Selection, view, and tab are also preserved for useful deep links. Replay remains hourly and bounded by `/api/meta`.
- The mock preserves monotonic horizons. It shifts fixture timestamps/history to the requested hour; its actual-outcome flags stay those of the fixture. All raions reuse the synthetic history series. The development banner and report notes disclose the synthetic data.
- Compiler-warning exceptions are limited to focusable table scroll regions. They retain `role=region`, accessible labels, and keyboard focus as required for scrolling. The interactive history chart has a slider role and its required ARIA values.
- Turn the mock off by stopping `dev:mock` and running `bun run dev` with `VITE_MOCK_API` unset or `0`. The same client then uses the existing `/api` proxy to port 8000. Mock middleware is dev-only and not installed for builds or production preview.

## Open issues

No frontend blockers found against the fixtures. The live backend was not exercised because it is outside this task and does not exist yet. Synthetic replay/history demonstrates the interactions; it is not a model evaluation or an account of real alerts.

## Contract change requests

None. Lead-owned types and fixtures were sufficient.

## Dependencies added

- Runtime: `d3-geo@3.1.1`, `d3-scale@4.0.2`.
- Development: `@types/d3-geo@3.1.1`, `@types/d3-scale@4.0.9`.
- Onest, Svelte, Tailwind, Vite, and TypeScript were already scaffolded. Dependency changes were made with `bun add` and recorded in `web/bun.lock`.
