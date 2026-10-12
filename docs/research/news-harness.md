# Optional Telegram news agent through the ChatGPT login

R4 research, verified on 2026-10-12 UTC. This is an implementation proposal backed by live public-page requests and three small model calls. It does not implement the feature or establish improved forecast accuracy.

Use 19 channels, including 13 nationwide sources and six regional channels. Extract with `gpt-6-luna` at high reasoning, then evaluate changed, recent events with `gpt-6.1-sol` at high reasoning. Both worked through this machine's existing ChatGPT login. Keep the news feature off by default and preserve the complete alerts-only app.

Two conditions need explicit handling. Stock Codex 0.162.1 did **not** provide a verified no-tools completion mode. Also, this app's latest base forecast is dated **2026-09-07T05:00:00Z**. October news cannot adjust that September forecast. Those findings affect the implementation, not the owner's decision to pursue the optional feature.

## Delivered

- [r4_channels.json](samples/r4_channels.json) contains the 19 selected channels, exact subscriber integers, titles, recent-page message counts, latest post timestamps, languages, roles, and poll metrics.
- [r4_candidates.json](samples/r4_candidates.json) records 37 live-checked candidate handles, including failed or stale handles. [r4_profiles.json](samples/r4_profiles.json) preserves exact landing-page subscriber evidence.
- [r4_polling.json](samples/r4_polling.json) and [r4_cursor_checks.json](samples/r4_cursor_checks.json) record the full-set poll and actual backward/forward paging.
- [r4_models.json](samples/r4_models.json), [r4_tool_probe.json](samples/r4_tool_probe.json), [r4_extraction_call.json](samples/r4_extraction_call.json), [r4_adjustment_call.json](samples/r4_adjustment_call.json), and [r4_verification.json](samples/r4_verification.json) preserve model discovery, actual outputs, usage, latency and validation. Matching `r4_*_prompt.txt` and `r4_*_schema.json` files make the three calls reviewable.
- [scripts/r4_probe.py](../../scripts/r4_probe.py) is a throwaway probe. It emits JSON to stdout, spaces Telegram requests, uses an unrelated temporary working directory for completions, redacts credential-shaped text found in public posts, and validates saved output. It never opens the auth cache.

No project dependencies were added. `selectolax` and `jsonschema` were supplied temporarily with `uv run --with`. Only the authorized R4 paths were written. Login and logout were not performed.

## Verified

### Installed CLI and existing login

The real commands returned:

| Command | Actual result |
|---|---|
| `codex --version` | `codex-cli 0.162.1` |
| `codex login status` | `Logged in using ChatGPT` |
| `Test-Path -LiteralPath '[private home path removed]'` | `True` |
| `codex exec --help` | Advertises `--model`, `--sandbox`, `--cd`, `--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, `--json`, `--output-schema`, and stdin `-` |
| `codex login --help`; `codex logout --help` | Browser/device authentication and removal of stored credentials documented; no account changes executed |
| `uv run ruff check scripts/r4_probe.py` | `All checks passed!` |

The auth cache's contents were never read, printed or copied. Existence and CLI status are the only auth-cache checks.

### Public pages and paging

`uv run --with selectolax python scripts/r4_probe.py telegram <the 19 handles below> --cursors` fetched each chosen preview once. The archived call used the default three retained message excerpts per page. Its initial full-set poll, excluding three later cursor requests, measured:

| Measurement | Observed |
|---|---:|
| Chosen-channel requests | 19, all HTTP 200, no redirects |
| Downloaded compressed response bodies | 322,131 bytes |
| Decoded HTML bodies | 2,498,656 bytes |
| Wall time including courtesy waits | 19.1006 seconds |
| Median HTTP latency | 0.1454 seconds |
| Maximum HTTP latency | 0.2482 seconds |
| Minimum request-start spacing per client | 1.05 seconds |

Subscriber landing-page checks ran concurrently in a separate client during this measurement. These are one-run client timings, not isolated-load latency percentiles. The production poller should use one shared limiter for all Telegram requests. Bytes exclude HTTP/TLS overhead, images, videos and linked pages.

`uv run --with selectolax python scripts/r4_probe.py profiles <the 19 handles below>` fetched exact subscriber integers from `t.me/<handle>`. Preview headers round counts to K/M; those rounded values are retained separately rather than presented as exact subscribers.

`uv run --with selectolax python scripts/r4_probe.py telegram war_monitor --cursors --max-messages 0` independently verified actual ID ranges:

| Request | Visible IDs | Count |
|---|---|---:|
| Latest preview | 47536 through 47555 | 20 |
| `?before=47536` | 47516 through 47535 | 20 |
| `?after=47536` | 47537 through 47555 | 19 |
| `?after=47555` | Empty | 0 |
| `?after=47496` | 47497 through 47516 | 20 |

Thus `after` and `before` were exclusive in these probes. Forward paging returned the next page rather than jumping straight to the latest messages. The empty forward page still downloaded **4,917 bytes**. Cursors reduce repeated content; they do not eliminate requests or HTML boilerplate.

### Models, tiny calls and output checks

`uv run --with selectolax python scripts/r4_probe.py models` initialized a private stdio `codex app-server` and called `model/list`. It returned ten entries with no next page, including these seven visible IDs:

`gpt-6.1-sol`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`.

The catalog also returned hidden `gpt-reserve`, `gpt-5.5`, and `codex-auto-review`. All visible entries advertised high reasoning. Only the requested Luna and Sol were inference-tested; a catalog entry alone is not proof of entitlement. The current official model pages also identify [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) and [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

The three real completion commands used the pattern `Get-Content -Raw -Encoding UTF8 <saved prompt> | uv run --with selectolax python scripts/r4_probe.py complete --model <id> --schema <saved schema> --timeout 120`. The two Ukrainian-text prompts explicitly set PowerShell's output encoding to UTF-8. Full argv are archived in each call JSON.

| Call | Model / effort | Input | Latency | Input tokens | Output tokens | Reported reasoning tokens |
|---|---|---|---:|---:|---:|---:|
| Capability discovery | Luna / high | Tiny request to list advertised tools without invoking them | 6.579 s | 11,217 | 188 | 125 |
| Extraction | Luna / high | 29 public-message excerpts plus one synthetic injection, 18,265 prompt bytes | 43.985 s | 16,135 | 4,718 | 2,231 |
| Adjustment | Sol / high | Three curated recent events, four synthetic raions, 4,312 prompt bytes | 25.358 s | 13,127 | 980 | 227 |

All three exited 0 and emitted `turn.completed`. Cached-input and cache-write counters were zero. These are the CLI's reported fields; do not add reasoning tokens to the reported output total as a separate billing estimate. No dollar cost or subscription-plan quota decrement was measured.

`uv run --with selectolax --with jsonschema python scripts/r4_probe.py verify` validated all three final JSON objects against their schemas. It also checked source IDs, literal evidence substrings, confidence bounds, the synthetic injection's exclusion, all 12 adjustment keys, allowed geography, shifts in [0, 0.5], unchanged unsupported Kyiv values, and monotonic computed probabilities. It found zero observed tool-action items and zero credential-pattern matches in the saved sample files.

Luna returned **22 events** and ignored the synthetic instruction. It also grouped an 11:55 tracking post, 12:37/12:43 Vinnytsia reports and a 15:19 official report into one event, then emitted a second event reusing `vanek_nikolaev/43345` and `43346`. This is a concrete double-counting risk. The checks establish structural/provenance validity, not correct event identity or extraction recall.

Sol's proposed 1-hour shift for the synthetic Haisynskyi row was 0.24, changing 0.080000 to 0.099540 after deterministic conversion. Its synthetic Kyiv rows remained 0.09 / 0.23 / 0.55. No real forecast was changed. The probe cut-off was 15:25Z, while outputs completed at 15:27:28Z and 15:30:13Z; they were never available for a real 15:25 forecast. Sol's manually curated observation timestamps were rounded to seconds. Production must use exact archive timestamps.

## Recommended channels

Counts came from live landing pages between 15:23:35Z and 15:23:54Z. Rank is within this verified candidate list, not a claim that these are Ukraine's exhaustive largest 19 channels. UK means predominantly Ukrainian, RU predominantly Russian; quoted speech can differ.

| Rank | Exact handle and live source | Title | Subscribers | Recent posts/day estimate | Language | Role |
|---:|---|---|---:|---:|---|---|
| 1 | [`@truexanewsua`](https://t.me/s/truexanewsua) | Труха⚡️Україна | 3,147,096 | 77.5 | UK | National news; alert mirror/aftermath |
| 2 | [`@vanek_nikolaev`](https://t.me/s/vanek_nikolaev) | Николаевский Ванёк | 2,605,587 | 3.5 | RU | National tracking; southern emphasis |
| 3 | [`@insiderUKR`](https://t.me/s/insiderUKR) | INSIDER UA \| Україна \| Новини | 2,189,775 | 76.0 | RU | National news; sampled aftermath |
| 4 | [`@lachentyt`](https://t.me/s/lachentyt) | Лачен пише | 1,591,448 | 70.3 | UK | National news; sampled aftermath |
| 5 | [`@kievreal1`](https://t.me/s/kievreal1) | Реальний Київ \| Украина | 1,560,042 | 79.4 | UK | Kyiv tracking and alert mirrors |
| 6 | [`@voynareal`](https://t.me/s/voynareal) | Реальна Війна \| Україна \| Новини | 1,511,612 | 65.1 | UK | National news and alert mirrors |
| 7 | [`@ukraina_novosti`](https://t.me/s/ukraina_novosti) | Україна 24/7 - новини | 1,373,007 | 57.5 | RU | National news; sampled aftermath |
| 8 | [`@kievinfo_kyiv`](https://t.me/s/kievinfo_kyiv) | Київ ІНФО \| Новини Україна🇺🇦 | 1,250,167 | 69.2 | UK/RU | Kyiv and national news |
| 9 | [`@odessa_infonews`](https://t.me/s/odessa_infonews) | Одесса INFO⚓️🇺🇦Новости \| Одеса | 991,068 | 132.1 | RU | Odesa drone approaches |
| 10 | [`@war_monitor`](https://t.me/s/war_monitor) | monitor | 968,491 | 15.6 | UK | National early-warning candidate |
| 11 | [`@kpszsu`](https://t.me/s/kpszsu) | Повітряні Сили ЗС України / Air Force of the Armed Forces of Ukraine | 908,899 | 288.0 | UK | Official national threat tracking |
| 12 | [`@dnipro_alerts`](https://t.me/s/dnipro_alerts) | Дніпро Alerts🚀🚨 | 810,055 | 149.1 | UK | Dnipro drone routes; frequent edits |
| 13 | [`@uniannet`](https://t.me/s/uniannet) | УНИАН - новости Украины \| война с Россией \| новини України \| війна з Росією \| УНІАН | 703,767 | 96.7 | RU | National news and alert mirrors |
| 14 | [`@kharkivlife`](https://t.me/s/kharkivlife) | Харьков life \| Харків🇺🇦 | 666,916 | 121.3 | UK | Kharkiv drone locations; frequent edits |
| 15 | [`@lvivych_news`](https://t.me/s/lvivych_news) | Львівич \| Новини | 436,292 | 65.5 | UK | Lviv news and warning mirrors |
| 16 | [`@UkraineNow`](https://t.me/s/UkraineNow) | Ukraine NOW | 408,999 | 19.9 | UK | National news; sampled aftermath |
| 17 | [`@operativnoZSU`](https://t.me/s/operativnoZSU) | Оперативний ЗСУ | 363,574 | 93.3 | UK | National military news/aftermath |
| 18 | [`@suspilnenews`](https://t.me/s/suspilnenews) | СУСПІЛЬНЕ НОВИНИ | 262,449 | 66.3 | UK | National editorial news/aftermath |
| 19 | [`@monitorwarr`](https://t.me/s/monitorwarr) | monitoring | 85,759 | 109.1 | UK | National early-warning candidate |

Posting rate is `24 × (visible_count − 1) / hours_between_first_and_last_visible_post`. A bursty 20-post page can inflate it; stale pages and media/service posts distort it. It is a recent-window estimate, not a measured full-day total. Individual counts, latest UTC posts, page hashes and assessments are in [r4_channels.json](samples/r4_channels.json).

The substantive signal core is `kpszsu`, `war_monitor`, `monitorwarr`, `vanek_nikolaev`, plus the Kyiv, Kharkiv, Odesa and Dnipro trackers. Subscriber rank determines presentation, not credibility or adjustment strength. Keep the smaller `monitorwarr` because live route reports and R1's retrieved bomber/launch posts support its subject coverage.

Only R1 demonstrated positive bomber-warning lead in one aligned attack. This live study confirms content that **may precede an onset in a still-quiet destination**, not measured pre-alert lead for every tracker. MiG, ballistic and drone warnings often arrive after some alerts start. The news-heavy sources are coverage and corroboration channels; bare alerts, aftermath, ads and ordinary headlines should yield no new threat event. [R1 timing evidence](text-sources.md#one-day-samples-and-real-alert-alignment).

For Kyiv, both large verified channels are retained. For the other cities the largest successful active candidate was selected from the tested set. `truexakharkiv`, `dnepr_operativ`, `lvivtruexa`, `Lv1256` and `RBC_ua_news` remain alternatives in the candidate evidence.

Do not silently substitute lookalike handles. `u_now` was a small stale channel, `kyivoperativ` a small reserve, `odessahello` an old stories channel, and `lviv24_7`/`lviv1256` small stale channels. The active large Lviv handle is `lvivych_news`. `TSNua`, `tsn_news`, `UkrainaOnlline`, `hueviykharkov`, `dnepr_info`, `lvivych` and `truexalviv` did not produce usable verified previews in these probes. This is an access/identity finding, not proof that the intended brands have no channel.

## Harness design

All defaults below are proposals. They are not learned effect sizes.

### A separate optional worker

Run one background poll/extraction worker independently of FastAPI request handling. Start it only after opt-in, installation/auth checks, exact-model discovery and capability checks. Bind control endpoints to localhost; protect mutating login/settings routes against cross-origin requests. Use a fixed executable, fixed argv and model/channel allowlists, never a shell command built from user or channel text.

The existing base API, precompute files and report remain authoritative for the alerts-only system. Store news events and adjustments separately. Add an optional sidecar response with `base_forecast_time`, `news_as_of`, `computed_at`, model IDs/efforts, status, source links, applied shifts and both probability sets. Those fields require lead-owned schema, config, API and frontend contract changes before implementation; R4 did not edit them.

### Polls, cursor store and recovery

Poll all 19 channels every **120 seconds** by default, with 10% jitter. Use one persistent HTTP connection pool and one global request-start limiter of at least 1.05 seconds. In quiet mode back off to five minutes. With the measured client cost, a two-minute full-page cycle implies about 13,680 requests and 232 MB of compressed bodies per day before catch-up pages. That is an extrapolation, not a day-long measurement. Forward pages should usually be smaller.

Use SQLite for channel cursors, append-only message versions, extraction status and event lineage. Key messages by handle plus numeric ID; store publish time, first observation, each version's observed time, text hash, visible edit marker and source URL. Exact hashes remove replay/retry duplicates. Deterministic incident matching should distinguish separate sorties and separate drone groups, and prevent syndicated copies from becoming independent confirmations.

At bootstrap, fetch the latest page and mark the pre-bootstrap history unavailable. Thereafter fetch `?after=<last_committed_id>`, advance through full pages, and stop when empty or no cursor advances. Persist messages and cursor in the same transaction. A full page needs another fetch; a deleted ID is a hole, not necessarily an ingestion failure. Cap catch-up at ten pages per channel per cycle, retain a lagging status, and resume next cycle.

Poll a recent overlapping page occasionally to notice edits. A forward cursor alone never revisits old posts. Re-fetching the latest page cannot guarantee detection of changes to older messages, so preserve this limitation. A visible edit without a previously captured original gets availability from first observation, never the older publish time.

Honor `Retry-After`; pause the source on 403, and back off on 429 or transient errors, for example 2, 5, 15, 30 minutes. Avoid bypassing restrictions. HTML without channel metadata or expected message elements must mark a source unavailable, not silently mean no threat. The numeric rate is our courtesy setting, not a published Telegram service quota.

### Codex invocation and the no-tools limit

The probe's real subprocess argv used `codex exec --ignore-user-config --ignore-rules --ephemeral --skip-git-repo-check --sandbox read-only --json --color never --cd <TEMP> --model <id> --output-schema <absolute schema path>`. Overrides set high reasoning, `approval_policy=never`, `web_search=disabled`, empty configured MCP servers, disabled shell/unified exec/apps/plugins/subagents/image/skill-search features, and no inherited shell environment. Prompt bytes entered stdin using `-`.

The child process ran in `[private home path removed]`, outside this repo. The host CLI read the supplied schema file; no model-generated repository tool action was observed. A production runner should use a dedicated empty scratch directory and environment allowlist. Keep existing credential storage under Codex management. Parent-side timeout was 120 seconds and the probe kills only its child process tree on timeout. There is no advertised native exec timeout flag. See the [CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli) and [non-interactive guide](https://learn.chatgpt.com/docs/non-interactive-mode).

`--ignore-user-config` still uses Codex's auth storage. `--ephemeral` avoids saved session rollouts; it does not promise zero logs, caches or auth-refresh writes. Read-only is a command sandbox policy and still permits reads. It is not a tool-selection policy. [Sandbox documentation](https://learn.chatgpt.com/docs/sandboxing).

The capability probe still reported `functions.exec`, `functions.wait`, clock and collaboration tools. This self-report is not an exhaustive nested-tool inventory, but it disproves an empty advertised top-level set. Feature switches were not a strict all-tools ceiling. All calls also emitted an under-development-feature warning for `skip_host_skill_discovery`; do not silently depend on that flag for a production security guarantee.

Independent source inspection of release `rust-v0.162.1` confirmed that `apply_patch` can be registered independently of shell disabling. The internal extension API has `ToolPolicy.allowed_tools = Some([])`, which excludes all tools, but no stock exec flag/config bridge was found. [Pinned tool builder](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/core/src/tools/spec_plan.rs#L1344), [pinned internal tool policy](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/ext/extension-api/src/tool_policy.rs#L10).

For the requested strict no-tools deployment, use a Codex-owned runner that actually applies that construction-time ceiling, or a supported ChatGPT inference integration with tools omitted. Verify its advertised inventory and behavior before enabling untrusted-channel processing. Both paths need implementation work; neither was tested here. Do not label the stock CLI path no-tools, and do not rely on killing a process after a tool event as a guarantee that it could not already act.

### Login with ChatGPT

For a local owner-run app, a button can start a single hidden `codex login` child, then poll its exit state and `codex login status`. Codex opens the authentication browser, owns the local callback, stores credentials and refreshes them. The app never accepts a password or reads the credential cache. Do not log the full login URL or forward credential-bearing callback parameters. Offer cancellation and a bounded waiting state. The backend must run under the same OS user as the owner.

`codex login --device-auth` is the documented headless alternative when enabled on the account. `codex logout` removes stored login credentials and affects other processes sharing that Codex account, so distinguish stopping this feature from explicit account logout. Login/logout were described from CLI help and official documentation, not executed against the owner's working session. [Authentication reference](https://learn.chatgpt.com/docs/auth).

A structured integration can instead use private stdio app-server requests. After initialization, `account/login/start` with type `chatgpt` returns a login ID and browser URL. Codex hosts the callback. Watch `account/login/completed` and reconcile through `account/read` without refreshing tokens in the host; cancellation and logout have dedicated methods. Use `model/list` for supported IDs/efforts, `account/rateLimits/read` for quota state, and `account/usage/read` for account token summaries. Never use the externally supplied-token login mode. Only `model/list` was exercised here. Managed app-server auth is documented for local/open-source apps; hosted/commercial deployment needs the separate Sign in with ChatGPT integration. [App-server reference](https://learn.chatgpt.com/docs/app-server), [inference access caveat](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server).

If Codex is missing, show installation guidance and keep news off. If not logged in, show login-required and continue serving base forecasts. If either selected model is unavailable, keep base forecasts and expose model-unavailable; do not silently substitute a model or reasoning effort. Refresh discovery on deliberate configuration changes. Pin the verified IDs until an explicit upgrade, even though the defaults are called latest.

### Extraction, provenance and untrusted text

Batch at most 30 new relevant messages or 24 KB of text, whichever comes first. Treat overflow as queued work; never truncate a multi-route report and silently present extraction as complete. The probes deliberately retained excerpts, so they cannot measure full-message recall.

Luna returns event type, preliminary/reported/cleared status, raw geographic mentions, origin, heading, extraction confidence, source keys, publish metadata and a short literal evidence quote. The saved [extraction schema](samples/r4_extraction_schema.json) requires fields and forbids extra properties. Production should also require per-source/version references and event observation time. Confidence measures extraction certainty, not the likelihood that the threat report is true.

The application owns canonical timestamps and geography. Validate IDs against supplied messages, require evidence substrings, reject fabricated sources, and derive availability from all cited observed versions. Ignore generated timestamps if they disagree with the archive. Resolve Ukrainian/Russian inflections and city aliases through an explicit local crosswalk into the existing `raions.csv`; keep IDs opaque. Distinguish source location, transit and destination. An airfield in Russia is an origin, not a Ukrainian target. Unknown or ambiguous destinations should abstain.

All channel text, forwarded material, quoted instructions and returned reasoning remain untrusted. Serialize each message as a JSON string with metadata outside its text. Disable actual tools, links and external context at the runner; schema-constrained output and a prompt alone do not remove tool access. Validate in the host with output-size limits and a deadline. Pass only validated typed events to Sol, never concatenated raw channel prose or credentials. Treat Sol's reasons as text, never commands or URLs to execute.

The probe found duplicate cross-source grouping, so do not let the LLM define incident identity alone. Store deterministic source/version lineage; reconcile overlapping evidence before aggregation. Multiple reports may describe one drone or different drones. Unknown identity should reduce aggregation weight, not imply independence.

### Time correctness and stale base forecasts

Every eligible message version must have **published_at < t** and **observed_at < t**. Use the UTC footer time, not a time claimed inside prose. An edited version first observed after `t` is unavailable at `t`, even if its original publish timestamp is earlier. Preserve earlier versions immutably. Today's preview text cannot prove what existed during the August/September replay.

To issue a live forecast at `t`, extraction and adjustment must also have completed before issuance. With this one-run timing, sequential full poll plus Luna plus Sol costs about **88.4 seconds**. Poll and compute ahead of the hourly tick, then freeze a snapshot already available before it. Late jobs enter a later forecast, never a retroactively relabelled one. Include source lag and processing latency in status.

Do not apply today's news to the current saved September snapshot or any historical replay hour. Leave those displays at base forecasts unless an eligible historical message/version archive exists. For a future live mode, the lead must first provide a fresh base forecast at the same time `t` using fresh alert inputs. A two-minute UI overlay needs a matching two-minute base or an explicitly defined new estimand; it cannot silently inherit the hourly contract.

### Bounded notches

Have Sol propose per-raion/per-horizon effects only for whitelisted raions and validated event targets. The host performs all arithmetic and enforces every cap.

Use a logit adjustment:

`p_news(r,H,t) = sigmoid(logit(p_base(r,H,t)) + delta(r,H,t))`.

Start with upward adjustments only, a conservative operating cap of **0.5**, and an absolute hard cap of **1.0**. A +1 shift multiplies odds by about 2.72, not probability by 2.72; it moves 10% to about 23.2%. Apply explicit all-clears/corrections by removing the associated prior uplift. Absence of messages is not negative evidence. A general negative-notch capability could be capped at -0.25 later, but should require evaluation and must never manufacture an all-clear.

Age effects deterministically, for example `delta = clip(sum(effect × exp(-age/tau) × horizon_weight), 0, cap)` after incident deduplication. Initial proposals: bomber takeoff `tau=120 min`, expiry six hours; MiG takeoff `tau=10 min`, expiry 30 minutes; drone movement `tau=30 min`, expiry 90 minutes; ballistic/launch `tau=10 min`, expiry 30 minutes. Verified current updates may refresh an incident's state, while unrelated repeated summaries cannot extend its life indefinitely.

Suggested weights are [0.25, 1.0, 0.5] for bomber takeoffs at 1/3/12 hours and [1.0, 0.4, 0.1] for transient drone/MiG/ballistic signals. Sol should evaluate relevance and uncertainty; code should derive age, expiry, weights, caps and probabilities. These numbers need prospective validation. An already-active raion cannot be treated as predicting this same already-observed start; skip that incident's onset uplift there.

Horizon-specific shifts can violate `p_1 <= p_3 <= p_12`. Project the adjusted triple onto that ordering with per-horizon bounds `[p_base, sigmoid(logit(p_base)+cap)]`. The base triple is a feasible fallback. For a future signed mode, use the corresponding lower logit bound too. Recompute and persist effective shifts after projection; sorting probabilities alone can mislabel horizons. Preserve exact 0/1 base endpoints under finite shifts.

Label the result **News-adjusted, experimental** beside **Base model**. Show forecast time, news cut-off, stale/error state, adjustment size and supporting source links. Do not imply that the base model's held-out scores or calibration validate the overlay. Duration remains a separate onset-time model and is outside this proposal.

### Cost control and failure behavior

Polling is cheap relative to calling a model on every poll. The extraction sample consumed over 16K input tokens for an 18KB prompt, while the tiny tool-discovery prompt still consumed over 11K. Codex's built-in context materially affects usage. Measure tokens in the actual selected runner rather than estimating from message characters.

Skip Luna when there are no new relevant candidates. Use a cheap prefilter to suppress ads, fundraising, duplicate hashes, summaries and alert-only mirrors, while preserving Ukrainian/Russian takeoff, missile and drone slang. Track filtered counts to detect missed vocabulary. Call Sol only when validated incident state or geographic relevance changes. Update decay/expiry in code between evaluations.

Start with one in-flight call per stage, Luna no more than once per two minutes and Sol no more than once per five minutes, with one immediate exception for a newly validated launch/takeoff. Add explicit daily limits, for example 200 extraction calls, 60 Sol calls and a 5M reported-token ceiling; disable further inference on exhaustion and display quota-limited. Those limits are product defaults to review, not account allowances.

Calling both measured workloads every two minutes would imply 720 pairs per day, about 21.1M input and 4.1M output tokens before retries. That arithmetic makes unconditional per-poll Sol calls a poor default. ChatGPT allowance is shared with the owner's other work; API token prices do not establish subscription billing or entitlement. [Pricing and usage documentation](https://learn.chatgpt.com/docs/pricing).

On timeout, unavailable model, invalid JSON, invalid provenance/geography, exhausted quota or unsafe capability state, serve the base result. Preserve validated events for a later retry, but never let that retry appear available earlier. On a transient poll failure, a prior uplift may decay only until its recorded expiry; mark the source stale. Never leave an uplift frozen indefinitely. One extraction retry is enough; repeated model failures should back off and trip a circuit breaker. Base API requests never wait for an LLM.

## Open risks

- **Strict no-tools is unresolved in stock exec.** The requested security property needs a verified construction-time tool ceiling or a supported inference-only integration. The successful probes demonstrate usable structured completions, not this guarantee.
- **Fresh live base data is a prerequisite.** The current archive/snapshot app cannot honestly mix present news with its September forecast. No live base feed or new API contract was implemented.
- **Telegram terms still apply to opt-in inference.** The current content-licensing text expressly includes AI deployment, not only training or benchmarking. Owner opt-in does not by itself establish the source-user consent described by Telegram. The owner has chosen to pursue the feature; this research proceeded within the requested small samples. No licensing basis was established. [Telegram content terms](https://telegram.org/tos/content-licensing), [API terms 1.5](https://core.telegram.org/api/terms).
- **Subscriber size and channel labels do not prove reporting quality.** Major outlets echo one another; unofficial trackers can be wrong. Current snapshots verify channel identity/content and audience, not typical lead times, truth, or incrementally better onset predictions.
- **Edits, omissions and delayed observation remain material.** Forward cursors miss edits; preview pages cannot reconstruct deleted/original versions. Dnipro's 20-post sample was entirely marked edited. The production archive needs version availability.
- **Location and incident identity need deterministic checks.** Luna's duplicate source use is actual evidence of double-counting risk. Sol's probe used manually supplied geography and synthetic probabilities, so it does not establish automatic raion resolution.
- **The overlays are heuristic.** Decay, effect size and caps have not been calibrated or evaluated. Do not reopen the already-scored test period. A future comparison needs an untouched prospective period, identical rows for base and news, and clear separation of active versus quiet raions.
- **Operational measurements are small.** Three completion calls and one full-set poll do not establish reliability, account quota behavior, tail latency, all-channel edit coverage or full extraction recall. Browser/device login and logout were documented, not exercised.

Contract change requests go to the lead: separate news status/forecast schema and types, opt-in settings and login controls, a fresh live-base contract, centralized news constants, and implementation ownership for the worker/archive. No existing contract files were modified.

