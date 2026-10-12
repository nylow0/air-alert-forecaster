# Air Alert Forecaster dashboard

Run these commands from `web/`:

```powershell
bun install
bun run dev:mock
bun run check
bun run build
```

`dev:mock` loads `.env.mock`, which sets `VITE_MOCK_API=1`. The dev-only middleware in `dev/mock-api.ts` serves the API fixtures. `/api/raions` reads `../data/geo/raions.geojson` on each request when it exists, otherwise it uses the fixture geometry. Its `X-Airalert-Geometry` response header identifies the source.

The browser always calls `/api/*` through `src/lib/api.ts`. To use the backend, stop the mock server and run `bun run dev` with `VITE_MOCK_API` unset or set to `0`. Vite proxies `/api` to `http://127.0.0.1:8000`. No component changes are needed. Production builds do not include fixture data or mock routes.

## Replay and keyboard controls

The URL stores `mode`, `horizon`, and `at`. It also preserves `raion`, `view=list`, and `tab=report` when selected. Invalid horizons fall back to 3 h; replay times are clamped to metadata bounds and rounded down to the hour.

Example shared state:

```text
http://localhost:5173/?mode=replay&horizon=3&at=2026-09-06T03:00:00Z&raion=kharkivska/kharkivskyi
```

Arrow keys step the replay by an hour. Native range input keys adjust the scrubber. Play advances one hour every 400 ms and stops at the replay end. Focus a polygon and press Enter or Space to select it. Focus the history chart and use Left/Right to inspect hourly probabilities without advancing the replay. Tables have focusable scroll regions and charts include expandable numerical data.

## Mock limitations

Forecast probabilities vary deterministically by replay hour and preserve P1 ≤ P3 ≤ P12. Active and end timestamps shift with the requested time; outcome flags remain those from the replay fixture. History reuses the fixture series and episodes for the requested raion and shifts them to the requested seven-day window. This demonstrates interactions; these are not fitted forecasts or historical evidence. The dashboard and model report say so.

The client keeps the last successful response during refetch, aborts superseded requests, and offers retries after errors. GeoJSON paths only rebuild when geometry or viewport dimensions change; probabilities change path fills.

See [IMPLEMENTATION.md](IMPLEMENTATION.md) for A3's delivered paths, browser verification, and screenshots. Screenshots are local artifacts ignored by `web/.gitignore`.
