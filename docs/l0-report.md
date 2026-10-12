# L0 wave-0 handoff

## Delivered

- Repository initialized with `git init`. No commit, branch change, stash, reset, or rebase.
- `.gitignore` covers the requested caches, virtual environment, generated data, artifacts, and frontend outputs. `data/geo/` stays trackable.
- `.python-version`, `pyproject.toml`, and `uv.lock` provide Python 3.12, the editable src-layout package, the `airalert` console script, pytest, and Ruff.
- `src/airalert/__init__.py`, `models/__init__.py`, and `text/__init__.py` establish the packages.
- `src/airalert/config.py` owns UTC split boundaries, horizons, merge gap, exclusions, Kyiv timezone, download constants, and all contracted paths. Dynamic prediction paths are Path templates containing `{model_id}`.
- `src/airalert/ids.py` implements the specified slug, oblast, and raion identifiers.
- `src/airalert/data.py` contains only the first fetcher. It streams a dated download, publishes it after completion, records SHA256, verifies cached copies, and returns a Path. A1 takes ownership next.
- `src/airalert/schemas.py` contains every requested API and report model. No public fields were added.
- `src/airalert/cli.py` dispatches every contracted subcommand lazily and identifies the owner of an absent stage.
- `web/` was created with `bun create vite web --template svelte-ts --no-interactive`. It uses Svelte 5, Vite, strict TypeScript, Tailwind 4 through the Vite plugin, and Onest. The dev proxy routes `/api` to `http://127.0.0.1:8000`.
- `web/src/App.svelte` and `web/src/app.css` provide the requested minimal black placeholder and fixture snapshot. A3 takes ownership of the frontend scaffold next.
- `web/src/lib/types.ts` hand-copies every public Python contract and adds polygon/multipolygon GeoJSON types.
- `web/src/lib/fixtures/{meta.json,raions.geojson,forecast_replay.json,forecast_latest.json,history.json,report.json}` provide six consistent development fixtures.
- `tests/test_fixtures.py` has nine focused checks covering schema validation, UTC serialization, forecast/map consistency, probability and duration ordering, hourly history, and comparable report aggregates.
- This handoff is stored separately from D2's `docs/report.md`.

## Verified

### Download and cache reuse

First `uv run airalert fetch`, exit 0:

```text
Saved data\raw\official_data_en_2026-10-12.csv
SHA256 b396f87696243069eec0219880e87dcd642b20beb0a7bae0d0e1f61e1ed20ec4
```

Second `uv run airalert fetch`, exit 0:

```text
Reused data\raw\official_data_en_2026-10-12.csv
SHA256 b396f87696243069eec0219880e87dcd642b20beb0a7bae0d0e1f61e1ed20ec4
```

The neighboring checksum is `data/raw/official_data_en_2026-10-12.csv.sha256`.

### Python fixture checks

Final `uv run pytest`, exit 0:

```text
============================= test session starts =============================
platform win32 -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: .
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1
collected 9 items

tests\test_fixtures.py .........                                         [100%]

============================== 9 passed in 0.11s ==============================
```

L0-owned paths, exit 0:

```text
uv run ruff check src/airalert/__init__.py src/airalert/config.py src/airalert/ids.py src/airalert/schemas.py src/airalert/cli.py src/airalert/data.py src/airalert/models/__init__.py src/airalert/text/__init__.py tests/test_fixtures.py
All checks passed!
```

Repository-wide `uv run ruff check` was also run. It initially found issues in concurrently written R1 and A2 files as well as two L0 line-length errors. I corrected the L0 errors. The final `uv run ruff check --output-format concise` still exited 1 with the following A2-owned findings:

```text
src\airalert\geo.py:3:1: I001 [*] Import block is un-sorted or un-formatted
src\airalert\geo.py:387:101: E501 Line too long (107 > 100)
src\airalert\geo.py:437:101: E501 Line too long (105 > 100)
src\airalert\geo.py:438:101: E501 Line too long (102 > 100)
src\airalert\geo.py:447:101: E501 Line too long (122 > 100)
src\airalert\geo.py:465:101: E501 Line too long (106 > 100)
Found 6 errors.
[*] 1 fixable with `--fix`.
```

I left A2's file untouched. The repository-wide clean-lint condition remains open.

### Missing-stage behavior

`uv run airalert episodes`, exit 1, no traceback:

```text
episodes: not implemented yet (owner A1)
```

### Frontend installation and type checking

In `web/`, `bun install` and `bun run check`, exit 0:

```text
bun install v1.3.13 (bf2e2cec)

Checked 62 installs across 114 packages (no changes) [23.00ms]
$ svelte-check --tsconfig ./tsconfig.app.json && tsc -p tsconfig.node.json
Loading svelte-check in workspace: .\web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

`bun run dev --host 127.0.0.1 --port 5173 --strictPort` started the real Vite server:

```text
$ vite --host "127.0.0.1" --port "5173" --strictPort
11:37:34 [vite] (client) Forced re-optimization of dependencies

  VITE v8.3.4  ready in 855 ms

  ➜  Local:   http://127.0.0.1:5173/
```

PowerShell fetched `http://localhost:5173` with Invoke-WebRequest:

```text
HTTP 200
<title>Air Alert Forecaster</title>
```

The actual page was opened and inspected in the T3 collaborative browser at 1280 × 800 and 390 × 844 CSS pixels. It rendered:

```text
Air Alert Forecaster
Dataset snapshot 7 September 2026 at 08:00 Kyiv time
```

Browser measurements confirmed `rgb(0, 0, 0)`, `"Onest Variable", sans-serif`, a loaded Onest font, no horizontal overflow, and no console errors. At mobile width, `innerWidth = 390` and `document.documentElement.scrollWidth = 390`.

The Vite process was stopped with Ctrl+C. A subsequent Get-NetTCPConnection check found no listener on port 5173.

### Identifier and CSV audit

A pandas audit through `uv run python -` checked all 25 unique oblast names, all 118 distinct nonempty oblast/raion pairs, and all hromada parent pairs. No oblast or raion ID collisions and no empty slugs were found. Every hromada parent raion was populated. All timestamps parsed with the exact format below, and every populated timestamp matched the same UTC-offset pattern.

`git check-ignore` confirmed raw data, the virtual environment, and node_modules are ignored; `data/geo/raions.geojson` was not ignored. `git log -1 --oneline` reported that the current branch has no commits.

## Decisions/assumptions

### CSV facts

The verified raw path is [datasets/official_data_en.csv](https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/main/datasets/official_data_en.csv). The [upstream dataset documentation](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset/blob/main/datasets/README.md) states that all timestamps are UTC and explains the omitted permanent alerts.

- Saved copy date: 2026-10-12 UTC.
- Size: 31,025,633 bytes.
- Rows: 299,275.
- Columns in order: `oblast, raion, hromada, level, started_at, finished_at, source`.
- Levels: 130,235 oblast; 132,251 raion; 36,789 hromada.
- Earliest start: `2022-03-15T16:10:34Z`.
- Latest start: `2026-09-07T05:28:22Z`.
- Latest finish and latest timestamp overall: `2026-09-07T05:39:33Z`.
- Hour-floored snapshot: `2026-09-07T05:00:00Z`.
- Timestamp text format: `YYYY-MM-DD HH:MM:SS+00:00`, parsed as `%Y-%m-%d %H:%M:%S%z`. There are no fractional seconds in this saved copy.
- Empty `finished_at`: 0 in this copy. The processed-data contract still permits null finishes; this observation does not prove every future source snapshot is complete.
- Raw distinct oblast/raion pairs: 118.
- Final universe after exclusions: 119 units, consisting of those 118 raions plus Kyiv City.
- Hromada rows missing their parent raion: 0. The parent is the ordinary `raion` column. For example, `Umanska terytorialna hromada` has `Umanskyi raion` in `Cherkaska oblast`.

Distinct oblast names, exactly as written:

```text
Cherkaska oblast
Chernihivska oblast
Chernivetska oblast
Dnipropetrovska oblast
Donetska oblast
Ivano-Frankivska oblast
Kharkivska oblast
Khersonska oblast
Khmelnytska oblast
Kirovohradska oblast
Kyiv City
Kyivska oblast
Luhanska oblast
Lvivska oblast
Mykolaivska oblast
Odeska oblast
Poltavska oblast
Rivnenska oblast
Sumska oblast
Ternopilska oblast
Vinnytska oblast
Volynska oblast
Zakarpatska oblast
Zaporizka oblast
Zhytomyrska oblast
```

Kyiv is `Kyiv City` with empty raion/hromada fields. Luhansk is `Luhanska oblast`, also without raion-level pairs. Crimea and Sevastopol have no rows, so their dataset English names cannot be verified from this CSV. They are also absent from the inspected upstream geographic state list.

### Final identifier rule

NFKD-normalize to ASCII, lowercase, drop `'`, `’`, `ʼ`, and backticks, fold other runs to `-`, and strip edge separators. Strip a trailing ` oblast` before forming an oblast ID and a trailing ` raion` before forming its raion component. A special-status city without a raion uses its oblast ID for both components. Empty slugs raise ValueError.

Five verified examples:

- `Cherkaska oblast / Umanskyi raion` → `cherkaska/umanskyi`
- `Kharkivska oblast / Kharkivskyi raion` → `kharkivska/kharkivskyi`
- `Kyivska oblast / Buchanskyi raion` → `kyivska/buchanskyi`
- `Odeska oblast / Bilhorod-Dnistrovskyi raion` → `odeska/bilhorod-dnistrovskyi`
- `Kyiv City / empty raion` → `kyiv-city/kyiv-city`

`EXCLUDED_OBLASTS` is `{"luhanska", "autonomous-republic-of-crimea", "sevastopol-city"}`. Only `luhanska` is directly verified in the saved CSV. The absent-region IDs follow the explicit names `Autonomous Republic of Crimea` and `Sevastopol City`; the latter follows the observed Kyiv City naming convention. These are reserved geography exclusions, not names claimed to occur in the source. They have no effect on the 118 observed raion pairs.

The identifier rule already gives unique IDs, so AGENTS.md and docs/plan.md were not edited.

### Fixture choices

- The metadata uses the real CSV hash, real snapshot, real split boundaries, and 119-unit count.
- `regime_switch` is null until A1 establishes the exact date.
- Both forecasts cover all 119 real universe IDs.
- GeoJSON has seven rough rectangles with real IDs plus one null-ID rectangle. It is a development placeholder, not boundary data.
- Replay is `2026-09-06T03:00:00Z`; latest is the snapshot. Replay ends at snapshot minus 12 hours.
- The history is a seven-day half-open range ending at replay time, with 168 hourly observations and three episodes. Its last episode is still open at replay time and agrees with the selected active forecast row.
- Forecast probabilities and absolute UTC end quantiles are ordered. Quiet rows have null duration fields, and latest rows have null actual-onset labels.
- The report has 18 OnsetScores, covering B0/B1/M1 × 1/3/12 h × val/test, each with ten reliability bins. It also has four D0/D1 DurationScores and a Selection.
- Report metrics are derived from deterministic synthetic observations. Models share the same labeled rows within a horizon/split, reliability and slice counts reconcile, BSS uses B0 on those rows, and median pinball loss equals half the median MAE. Notes clearly label all scores as synthetic.
- Pydantic rejects extra fields and naive datetimes, normalizes offsets to UTC, and serializes datetimes with Z. TypeScript keeps date/time fields as strings. No public API fields were added.
- The placeholder uses Onest, a black background, white text, muted gray secondary text, left alignment, and restrained spacing. No map, model, feature, or API implementation was added by L0.

## Open issues

1. Repository-wide Ruff still reports six findings in concurrently written `src/airalert/geo.py`, owned by A2. L0's files pass. The lead/A2 must resolve these before claiming the full workspace is lint-clean.
2. The downloaded CSV ends on September 7, 2026, rather than the October 12 date anticipated in the plan. Downstream stages should use the recorded snapshot, not today's date.
3. Crimea and Sevastopol have no source rows, so their exact source English names remain unverifiable. If either appears in a future CSV, A1/A2 should check its canonical ID against the reserved exclusions.
4. Actual data processing, boundaries, features, models, API serving, and the dashboard remain with their assigned agents. This handoff verifies the wave-0 scaffold only.

## Contract change requests

None. No identifier changes or added schema fields were needed.

## Dependencies added

- Python runtime: numpy, pandas, pyarrow, scikit-learn, lightgbm, fastapi, uvicorn, pydantic constrained to v2, httpx, matplotlib.
- Python development: pytest, ruff.
- Build backend: hatchling.
- Frontend template: Svelte 5, Vite, TypeScript, @sveltejs/vite-plugin-svelte, @tsconfig/svelte, @types/node, svelte-check.
- Frontend additions: tailwindcss, @tailwindcss/vite, @fontsource-variable/onest.
- Exact resolutions are in `uv.lock` and `web/bun.lock`.
