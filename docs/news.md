# Optional news agent

The news sidecar polls public Telegram previews, extracts threat reports through the local Codex CLI and computes experimental upward adjustments to an hourly base forecast. News is off by default. The base app does not wait for this worker, and news never changes alert labels, duration forecasts or the frozen v1 report.

This implementation follows [R4's research](research/news-harness.md) and the lead's N1 decisions. The overlay has no measured accuracy or calibration benefit. A reported threat is not proof that an attack will happen.

## Integration for S1

Include `airalert.news.router.router` in the FastAPI app. From the lifespan, await `airalert.news.worker.start(base_provider)` and await `airalert.news.worker.stop()` on shutdown. The provider has the exact type `Callable[[], ForecastResponse | None]`. It must return an already computed current-hour base forecast immediately. Returning `None`, raising, returning a replay or returning a different hour yields an empty adjustment list and an explanatory status message.

The module-level lifecycle owns one worker. Shutdown closes its HTTP pool, SQLite connection and app-owned login child; another start constructs a fresh worker. Instance `Worker.start` and `Worker.stop` also support restarting within one event loop. Settings writes wake the loop, cancel an old in-flight cycle and discard its outputs. HTTP requests for news read the archive and perform local arithmetic; they never wait for a model or an auth check.

The router uses the lead's schemas without adding response fields:

| Route | Response and behavior |
|---|---|
| `GET /api/news` | `NewsFeed`, with validated events and any eligible adjustments |
| `GET /api/settings` | `Settings`, defaults when the local file is absent |
| `PUT /api/settings` | Validated `Settings`, atomically saved locally; discovery checks the exact model and effort |
| `GET /api/auth/status` | `AuthStatus`, from the CLI status and discovered model catalog |
| `POST /api/auth/login` | Starts one hidden Codex login child and reports progress; CLI owns the browser callback |
| `POST /api/auth/cancel` | Cancels only that login child and refreshes status |
| `POST /api/auth/logout` | Explicit Codex logout; response warns that it affects every Codex use on this machine |

Every mutation requires a loopback client, a loopback Host and a matching Origin if present. Cross-site and same-site browser fetches are rejected. A frontend proxy must preserve the browser's Host so that its Origin matches; rewriting Host to a different backend port causes a deliberate 403. Local CLI clients may omit Origin. Changing model settings requires live discovery. Turning news off with unchanged model choices remains available even if Codex cannot be reached.

## Pipeline and archive

`channels.py` contains the exact 19 verified R4 handles, titles and subscriber counts, in R4 order. All are enabled by default inside the otherwise disabled news feature. Subscriber rank never sets credibility or uplift strength.

`scraper.py` uses one persistent HTTP pool and one shared request-start limiter, at least 1.05 seconds apart. The normal cycle is 120 seconds with ten percent jitter; quiet cycles back off to at least five minutes. First activation fetches each latest preview and records that older history is unavailable. Thereafter an exclusive `?after=` cursor drains forward pages, at most ten per channel per cycle. Cursor advancement and message inserts share a transaction. A latest-page overlap every ten minutes captures edits without skipping a lagging forward cursor. Media-only posts advance the cursor too.

403 pauses the source. 429 honors Retry-After, and transient failures back off for 2, 5, 15 or 30 minutes. Missing channel metadata or message elements on a latest page marks a source unavailable. A genuinely empty forward page with valid metadata is allowed. The worker never bypasses restrictions. Deleted posts and edits older than the overlap page cannot be reconstructed.

SQLite is `data/news/news.sqlite`. The tables retain channel cursors and backoff, immutable message versions, publication and first-observation timestamps, each version's observation time and text hash, extraction/evaluation runs and usage, model outputs as untrusted local data, validated evidence quotes, incident revisions, evaluation model choices and adjustments. A changed text hash appends a version. Reverting to an older text also appends a version with its new availability time. All data remains git-ignored.

`extract.py` filters Ukrainian and Russian takeoff, missile, ballistic, drone and monitoring vocabulary. Ads, fundraising, historical summaries, aftermath, obvious prompt injections and bare alert mirrors are dropped. Identical text reported within six hours is treated as a copy, not a fresh confirmation. This conservative rule can suppress a distinct incident whose short text is identical; it trades recall for avoiding repeated uplift. Filtered edits invalidate earlier event lineage. Vocabulary gaps and the heuristic filter can miss real threats.

An extraction batch contains at most 30 whole messages and 24 KiB of serialized message text. Overflow stays queued. An individually oversized message abstains rather than being truncated. Metadata, instructions and the explicit geography allowlist add prompt bytes beyond that text budget.

Host validation requires every cited source/version to be in the batch and every source to have its own literal evidence substring. It rejects fabricated sources, quotes and instruction-bearing messages. The host owns timestamps. Geographic mentions must appear in the cited text and resolve through the local allowlist; an unsupported or ambiguous destination abstains. A failed batch contributes no incident.

`geography.py` joins the actual `raions.csv` universe to matched `crosswalk.csv` polygons, then adds explicit Ukrainian/Russian city aliases, inflections and oblast names. Origins and headings also require a local alias, an approved external-origin alias or direction vocabulary, plus support in the cited text; otherwise those fields abstain. External origins never become target raions. The list covers common cities and selected route locations, not every settlement. Unsupported places need an explicit reviewed alias.

`incidents.py` determines identity from source overlap, type, target set, origin, heading and a ten-minute matching window. The model does not assign identity. A multi-route post contributes one observation with verified targets. An edited source supersedes its earlier lineage. All-clears remove matching prior uplift; they never lower the base. Same-type effects use a per-horizon maximum within a raion, so uncertain identity and syndicated reports cannot stack as independent evidence. Separate groups with similar geography can be merged conservatively. This policy needs prospective review.

Sol evaluates only a changed incident state. It sees validated typed events, explicit revisions and allowed target raions. Host validation rejects invented revisions, duplicate effect keys and unsupported targets. Changing configured models invalidates old evaluations; model choices never silently substitute.

## Hourly availability and notch math

For a base forecast at `t`, every cited version must have publication and observation strictly before `t`, and both extraction and evaluation must finish strictly before `t`. The host derives an incident's publication and observation from the latest timestamp among all its cited versions. SQLite incident revisions preserve earlier availability. A later edit, correction or completion never changes an earlier forecast retroactively.

The feed can show a newly validated incident while its adjustment list stays empty for the current hour. An event completed at 16:23 is unavailable to a 16:00 base, even when its post was published before 16:00. It can affect a subsequent hourly forecast if still unexpired. News events displayed in the feed are current; `status.news_as_of` is the base cutoff used for adjustments.

The host converts relevance strength to a decayed logit shift and performs all probability arithmetic. Shifts are upward only, with an operating cap of 0.5 and an absolute hard cap of 1.0. The current implementation always enforces the stricter operating cap. Active raions are skipped. Exact zero and one endpoints remain unchanged.

| Type | Decay tau | Expiry | Weights for 1 / 3 / 12 hours |
|---|---:|---:|---|
| Bomber takeoff | 120 min | 6 h | 0.25 / 1 / 0.5 |
| MiG takeoff | 10 min | 30 min | 1 / 0.4 / 0.1 |
| Drone group | 30 min | 90 min | 1 / 0.4 / 0.1 |
| Ballistic or missile launch | 10 min | 30 min | 1 / 0.4 / 0.1 |

Age starts at the cited publication time, not extraction completion. No-message periods never imply safety. Expiry and decay happen locally without another Sol call. Bounded isotonic projection enforces `p_1 <= p_3 <= p_12` within each horizon's base-to-capped bounds. The persisted deltas are recomputed effective logit shifts after projection, rather than the original proposed strengths. Invalid or nonmonotone base rows abstain.

## Codex safety model

`codex_runner.py` starts fixed executable argv without a shell. Each completion gets a newly created empty working directory under OS temp; its host-only output schema is outside that empty directory. The environment is an allowlist of OS executable, profile, temp and certificate variables. App orchestration variables, provider keys and other inherited credentials are omitted.

The invocation uses `--ignore-user-config`, `--ignore-rules`, `--ephemeral`, `--skip-git-repo-check`, `--sandbox read-only`, `--json`, `--output-schema`, stdin `-`, the exact selected model and effort, `approval_policy=never`, disabled web search and empty MCP servers. `capabilities.py` pins all 125 nonremoved features advertised by the installed CLI to false, including browser/computer use, code mode, hooks, memory, shell, apps, plugins, multi-agent, image and skill features. Host skill discovery is the inverse switch and is explicitly skipped. Shell environment inheritance is disabled. Removed features cannot be switched off; unknown future action events fail closed. [Official CLI reference](https://learn.chatgpt.com/docs/cli/reference).

The parent checks JSON events as they arrive. Any tool, command, patch, unknown action item or unknown event type kills the child process tree immediately and discards the result. It then persists an error circuit breaker. Prompt writes and stream reads run separately so a blocked stdin cannot evade the deadline. Output size, event queue and completion time are bounded. Stderr, model reasoning and auth URLs are never written to logs or returned to the browser. Only successful structured final output enters the local run archive.

This guard is reactive. A tool could begin before the parent receives its event. Read-only sandboxing still permits reads; stock exec cannot prove an empty tool set, and disabled feature switches do not establish that guarantee. Ephemeral mode also does not promise that Codex writes no internal logs or auth refreshes. The OS profile required for the owner's ChatGPT login is available to the CLI. A verified construction-time tool ceiling or stronger process isolation would be needed to remove this residual risk. Schema output, injection filtering and literal quotes validate structure and provenance, not the truth of a report or semantic accuracy of an extraction.

`auth.py` only invokes `codex login status`, `codex login`, `codex logout` and private stdio `codex app-server` model/list. It never opens the credential cache. Login status accepts the ChatGPT auth-method marker and discards all other output, including possible API-key excerpts. Model discovery is paginated and checks supported efforts. A catalog entry is not proof of inference entitlement. [Official app-server documentation](https://learn.chatgpt.com/docs/app-server).

One app-owned login child is allowed, hidden on Windows, with stdout/stderr sent directly to the null device. Status polling notices completion or cancels it after five minutes. Canceling news does not log out. Explicit logout affects every Codex use on this machine, including other sessions, and both its docstring and response say so.

A tool attempt trips a durable breaker immediately. Three inference or validation failures also trip it. A settings save, process restart or toggle does not silently reset it. After inspection, the local operator can remove the `breaker` row in SQLite's `control` table while the app is stopped. There is deliberately no unauthenticated reset endpoint in this contract.

## Settings and cost controls

The normal file is `data/settings.local.json`, absent by default. Defaults are news disabled, extraction `gpt-6-luna` / high, evaluation `gpt-6.1-sol` / high, a 120-second poll interval and all 19 verified channels enabled. Poll intervals accept 120 through 3600 seconds. Only the channel enabled bit is configurable; source identity and verified metadata stay fixed.

Extraction starts at most once per two minutes; evaluation starts at most once per five minutes, with one immediate exception for a newly validated launch or takeoff. Start times survive restarts. There is one in-flight call per stage, with both stages also serialized by the runner. Empty candidates skip extraction, and unchanged incident state skips evaluation.

Daily limits use Europe/Kyiv dates and count starts, including failed calls: 200 extraction calls, 60 evaluation calls and 5,000,000 reported input-plus-output tokens. A 100,000-token reserve prevents launching near the token ceiling. Failed, canceled or timed-out calls with unknown usage consume that reserve in the local accounting. Reported reasoning tokens are not added twice. An unusually large single call could still exceed its reserve because CLI token accounting arrives after completion; this is a host launch budget, not a provider-enforced account quota. Exhaustion sets `quota_limited`. Existing eligible uplifts may decay until their normal expiry.

These limits govern this sidecar only. ChatGPT allowance is shared with the owner's other use, and reported token counters do not establish dollar cost or subscription quota decrement. The current `Settings` schema has no daily-limit fields, so the limits are enforced internally and documented here. The lead must extend the contract before they can be settings-visible or configurable.

## Telegram terms

The owner chose to enable this opt-in feature. R4 recorded that Telegram's content-licensing terms address AI use, including deployment; owner opt-in alone does not establish the source-user consent described there. This implementation does not establish a licensing basis. Polling is limited to public previews, follows source backoff and does not bypass access restrictions. See [Telegram content licensing](https://telegram.org/tos/content-licensing) and [API terms](https://core.telegram.org/api/terms).

## N1 delivery report

### Delivered

- `src/airalert/news/`: channels, scraper, SQLite store, isolated Codex runner, extraction and validation, explicit geography, deterministic incidents, host adjustment math, settings, auth, worker, APIRouter and an explicit real-run probe.
- `tests/test_news.py`: ten focused offline workflows with fake HTTP, fixed clocks and fake Codex/auth executables.
- `docs/news.md`: integration, settings, safety, limitations and this report.
- `data/news/e2e-20261012T161354098578Z/`: the isolated real-run SQLite archive and JSON evidence. The normal settings file was not created or enabled.
- `pyproject.toml` and `uv.lock`: dependency added through the allowed `uv add` exception.

### Verified

`uv run --no-sync pytest -q tests/test_news.py` returned `10 passed`, with one upstream Starlette warning that its httpx TestClient integration is deprecated. Tests cover caps, decay, expiry, active-state exclusion, bounded projection, injection and fabricated provenance, as-of edits and completions, child-tree termination, durable breaker state, defaults and round-trip settings, news-off isolation, cursors and edit overlap, Retry-After, local mutation protection, settings-driven lifecycle restarts, persistent quotas and recent-copy deduplication. Fake login/cancel/logout tests run only the fake executable.

`uv run --no-sync ruff check src/airalert/news tests/test_news.py` returned `All checks passed!`. `uv run --no-sync ruff format --check src/airalert/news tests/test_news.py` returned `17 files already formatted`. A working-tree scan using the repository privacy guard's exact patterns checked the 18 N1 source/test/document files and found zero matches. The normal settings file remains absent, and loading defaults returns `news_enabled: false`. `git diff --check` reported no whitespace errors in the scoped changes.

`uv run --no-sync python -m airalert.news.probe` fetched the 19 channels once, all successful, and archived 354 message versions. The first cycle took 63.623 seconds including a 44.265-second Luna call, with 22,208 input and 4,234 output tokens. Host validation rejected that first output. It produced no events or adjustments.

The bounded retry used the same archive without another network poll. A 35.031-second Luna call passed validation, with 22,208 input and 3,774 output tokens. It produced six deterministic incidents; five were already expired and one Boryspil drone report was still eligible. The first Sol attempt hit the 120-second deadline and produced no evaluation. After the persisted five-minute interval, one Sol-only retry completed in 8.985 seconds, with 12,280 input and 132 output tokens. It returned strength 0.25 for the validated Boryspil incident. No observed tool event tripped the real-run breaker.

The real feed returned zero adjustments for the synthetic 16:00 UTC base because extraction and evaluation finished after 16:00. A separate deterministic prospective check at 17:00 UTC, using those already completed jobs and a still-unexpired event, returned one synthetic Boryspil row:

| Horizon | Synthetic base | News adjusted | Effective logit delta |
|---|---:|---:|---:|
| 1 h | 0.09 | 0.0929425572 | 0.0354107864 |
| 3 h | 0.23 | 0.2325180881 | 0.0141643145 |
| 12 h | 0.55 | 0.5508762609 | 0.0035410786 |

The final capability check disabled the complete installed inventory of 125 nonremoved feature switches. A small Luna structured completion returned `ok: true` in 6.641 seconds, reporting 10,449 input and 15 output tokens. A Sol evaluation of the same archived Boryspil event then completed in 8.516 seconds, reporting 11,577 input and 122 output tokens and the same 0.25 strength. The table above was recomputed using this latest evaluation, which finished at 16:35:36Z. The final archive-only feed correctly marked polling stale because it had not repeated the 16:14 network poll.

That prospective arithmetic check was not served as a current forecast, and it measured no predictive accuracy. The four completed pipeline model calls reported 76,535 total tokens. Including the separate final Luna capability check gives 86,999 reported tokens. The timed-out call's actual usage is unknown; pipeline accounting reserved another 100,000, for 176,535 accounted tokens in SQLite. Existing login remained intact. No real login or logout was performed.

### Decisions and assumptions

The local alias list abstains for unrecognized settlements. Exact recent copies and same-type effects are treated conservatively. Events are current in the feed, while overlay availability uses the hourly base cutoff. The worker enforces limits locally while waiting for the lead-owned settings/config extension. Production news remains off.

### Open issues

S1 still needs to include the router and wire the lifecycle to its real current-hour base provider. The installed application and F1 UI integration are outside N1 ownership and were not verified here. Stock exec's reactive tool guard, preview edit coverage, location recall, incident identity and heuristic effect sizes retain the limitations described above. The real run required one extraction retry and one evaluation retry; it does not establish reliability or tail latency.

### Contract change requests

1. Add daily extraction/evaluation/token limits to `Settings`, mirrored frontend types and fixtures, so the requested limits can be visible and configurable. Optionally expose usage counters in `NewsStatus`; its current fields cannot display them.
2. Move the N1-local paths, intervals, batching limits, caps, decay policy and daily limits in `news/policy.py` to the lead-owned `config.py`. No lead-owned file was edited.

### Dependencies added

`selectolax` for Telegram preview HTML parsing. The first `uv add selectolax` hit a running `airalert.exe` lock; `uv pip install selectolax` installed the package without touching that executable, and `uv add selectolax --no-sync` recorded the dependency and lock. No unrelated running process was stopped, and no git state was changed.
