# Air Alert Forecaster dashboard

Run these commands from `web/`:

```powershell
bun install
bun run dev:mock
bun run check
bun run build
```

`dev:mock` selects Vite's mock mode directly, so no local env file is required. `VITE_MOCK_API=1` also enables mock routes in a development server. The dev-only middleware in `dev/mock-api.ts` serves schema-validated fixtures, including all 139 real map polygons with their crosswalk `display_key` values. `X-Airalert-Geometry: fixture` identifies the committed fixture geometry.

The browser always calls `/api/*` through `src/lib/api.ts`. To use the backend, stop the mock server and run `bun run dev` with `VITE_MOCK_API` unset or set to `0`. Vite proxies `/api` to `http://127.0.0.1:8000`. No component changes are needed. Production builds do not include fixture data or mock routes.

## Replay and keyboard controls

Live is the default tab. Its independent yellow and red maps stay vertically stacked. Live, news, and system status refresh every 60 seconds. Forecast calls without `at` return the current hour; the UI calls this mode Now. Forecast adjustments appear only when a running news agent supplies adjustments for that exact current hour.

The bottom-left Settings button opens a native modal dialog. Esc closes it and restores focus. Settings save on change, with rollback after a failed PUT. ChatGPT login, cancellation, and confirmed machine logout use the backend auth routes. No credentials enter the frontend.

The URL stores `mode`, `horizon`, `at`, and `tab=live|map|report`. It also preserves `raion` and `view=list` when selected. Invalid horizons fall back to 3 h; replay times are clamped to metadata bounds and rounded down to the hour.

Example shared state:

```text
http://localhost:5173/?mode=replay&horizon=3&at=2026-09-06T03:00:00Z&raion=kharkivska/kharkivskyi
```

Arrow keys step the replay by an hour. Native range input keys adjust the scrubber. Play advances one hour every 400 ms and stops at the replay end. Focus a polygon and press Enter or Space to select it. Focus the history chart and use Left/Right to inspect hourly probabilities without advancing the replay. Tables have focusable scroll regions and charts include expandable numerical data.

## Mock limitations

The mock starts with news off and ChatGPT logged out. Open `/?mock_news=running` in a fresh browser session to preview the enabled news card and adjustments. A dev-only `POST /api/mock` configures isolated per-browser scenarios: `stale`, `news_state`, `auth` (`in`, `out`, `missing`), and `fail_settings`. The Playwright v2 workflows use these controls; auth workflows never run against the real backend. Mock login completes after two seconds and never invokes Codex.

Forecast probabilities vary deterministically by replay hour and preserve P1 ≤ P3 ≤ P12. Active and end timestamps shift with the requested time; outcome flags remain those from the replay fixture. History reuses the fixture series and episodes for the requested raion and shifts them to the requested seven-day window. This demonstrates interactions; these are not fitted forecasts or historical evidence. The dashboard and model report say so.

The client keeps the last successful response during refetch, aborts superseded requests, and offers retries after errors. GeoJSON paths only rebuild when geometry or viewport dimensions change; probabilities change path fills.

See [IMPLEMENTATION.md](IMPLEMENTATION.md) for A3's delivered paths, browser verification, and screenshots. Screenshots are local artifacts ignored by `web/.gitignore`.
