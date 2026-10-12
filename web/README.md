# Air Alert Forecaster dashboard

Run these commands from `web/`:

```powershell
bun install
bun run dev:mock
bun run check
bun run build
```

`dev:mock` selects Vite's mock mode directly, so no local env file is required. `VITE_MOCK_API=1` also enables mock routes in a development server. The dev-only middleware in `dev/mock-api.ts` serves schema-validated fixtures, including all 139 real map polygons with their crosswalk `display_key` values. `X-Airalert-Geometry: fixture` identifies the committed fixture geometry.

The browser always calls `/api/*` through `src/lib/api.ts`. From the repository root, start `uv run --no-sync airalert serve --port 8000`, then run `bun run dev` in `web/` with `VITE_MOCK_API` unset or set to `0`. Vite proxies `/api` to `http://127.0.0.1:8000` and preserves the browser Host so same-origin Settings writes pass the backend's local-control checks. Production builds do not include fixture data or mock routes.

## Replay and keyboard controls

Live is the default tab. Its independent yellow and red maps stay vertically stacked. Live, news, and system status refresh every 60 seconds. Forecast calls without `at` return the current hour; the UI calls this mode Now. Forecast adjustments appear only when a running news agent supplies adjustments for that exact current hour.

The bottom-left Settings button opens a native modal dialog. Esc closes it and restores focus. Settings save on change, with rollback after a failed PUT. ChatGPT login, cancellation, and confirmed machine logout use the backend auth routes. No credentials enter the frontend.

The URL stores `mode`, `horizon`, `at`, and `tab=live|map|report`. It also preserves `raion` and `view=list` when selected. Invalid horizons fall back to 3 h; replay times are clamped to metadata bounds and rounded down to the hour.

Example shared state:

```text
http://localhost:5173/?mode=replay&horizon=3&at=2026-09-06T03:00:00Z&raion=kharkivska/kharkivskyi
```

Arrow keys and step buttons move to the next or previous stored forecast. Playback checks every 400 ms, waits for each response, skips unissued hours and stops at the replay end. The available hours come from history in requests of at most 31 days. Scrubbing directly to an unissued hour shows "No forecast stored for this hour" and clears the previous probabilities. History lines break across missing forecasts. Focus a polygon and press Enter or Space to select it. Focus the history chart and use Left/Right to inspect stored probabilities without advancing the replay. Tables have focusable scroll regions and charts include expandable numerical data.

An active raion's duration panel shows remaining-time quantiles at the forecast hour. Invalid or expired quantiles show a neutral unavailable estimate. The model report leads with the selected models' test scores, followed by validation, API notes and live track record. All other model and row-set comparisons are in a disclosure. Selected duration variants such as `D2@60` describe estimates after the alert has survived 60 minutes.

## Mock limitations

The mock starts with news off and ChatGPT logged out. Open `/?mock_news=running` in a fresh browser session to preview the enabled news card and adjustments. A dev-only `POST /api/mock` configures isolated per-browser scenarios: `stale`, `news_state`, `auth` (`in`, `out`, `missing`), and `fail_settings`. The Playwright v2 workflows use these controls; auth workflows never run against the real backend. Mock login completes after two seconds and never invokes Codex.

Forecast probabilities vary deterministically by replay hour and preserve P1 ≤ P3 ≤ P12. Active and end timestamps shift with the requested time; outcome flags remain those from the replay fixture. History generates the same synthetic probabilities for every requested hour and shifts the fixture episodes into the requested window. These are interaction samples, not fitted forecasts or historical evidence. The dashboard and model report say so.

The client keeps the last successful response during refetch, aborts superseded requests, and offers retries after errors. GeoJSON paths only rebuild when geometry or viewport dimensions change; probabilities change path fills.

See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the real v2 verification and screenshots, and [tests/README.md](tests/README.md) for both e2e modes. Screenshots are local artifacts ignored by `web/.gitignore`.
