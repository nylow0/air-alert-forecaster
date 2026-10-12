# Independent v2 audit

**Verdict: not ready.** Five major findings remain in evaluation protection and data publication. The served onset scores reproduce exactly, the fresh-clone setup succeeds, and the isolated nightly run preserves frozen reports. Those results do not cover the failure paths below.

Auditor: V1. Audit date: October 12, 2026. Committed baseline: `b90f45bca004c020def3ca62233967633ba9fa80`. Source line references refer to that HEAD unless explicitly marked working tree. X3 changed ingestion and production during this audit. I checked the independent probes again against those working files and distinguish the resolved observation-file race from remaining findings. The known persistent-community-alert defect is X3's assignment, not a new finding here.

Only this file was written in the main repository. Scratch scripts, the clone, installed dependencies, test outputs and synthetic data are under `%TEMP%\airalert-v1-audit-d93813f5c74b48ebab1112acc0da2a08\`. Below, `%AUDIT%` denotes that directory. Ports 8000 and 5173 were untouched. The isolated API used port 8021 with `AIRALERT_DISABLE_SCHEDULER=1` and was stopped after verification. No real login, logout or news inference was performed.

## Findings

| ID | Severity | Area | Evidence | Why it matters | Suggested owner |
|---|---|---|---|---|---|
| V2-01 | major | Evaluation protection | `src/airalert/backtest.py:541`, `:544`, `:577`; `src/airalert/models/onset.py:794`, `:809`. `%AUDIT%\onset_freeze_probe.py` returned `BEFORE claim_exists True onset_rows 156 test_rows 78`, then `AFTER onset_rows 78 test_rows 0 claim_unchanged True`. | Validation-only scoring remains callable after the frozen claim. The public `--score-only` path overwrites the combined frozen report and drops all test2 onset scores. This was reproduced only in the temp clone. | M2, lead |
| V2-02 | major | Duration evaluation protection | `src/airalert/models/duration.py:725`, `:727`, `:875`, `:883`, `:893`. `%AUDIT%\duration_freeze_probe.py` returned `onset_claim_exists True`, `duration_marker_exists False`, `AUDIT: entered evaluation after frozen claim`. | The duration freeze exists only in ignored `artifacts/D2/test2_scored.json`, so it does not survive cloning. A fresh clone can enter validation evaluation and overwrite the committed duration report. Final evaluation checks existence before scoring and writes the marker afterward without an exclusive claim, allowing concurrent or interrupted rescoring. | M2, lead |
| V2-03 | major | Archive completeness | `src/airalert/sources/aiu_web.py:79`, `:84`; `src/airalert/ingest.py:571`, `:578`, `:584`, `:588`. Independent probe returned `EMPTY_ARCHIVE: accepted two empty national days; watermark=2026-10-26 gaps=[] archive_rows=0`. | A syntactically valid but empty national archive response is accepted as complete coverage. No per-day completeness check protects against an empty upstream outage or truncated response. The watermark advances, the day is no longer missing, and missing alerts become apparent quiet history rather than a gap. | I1 |
| V2-04 | major | Concurrent data publication | `src/airalert/ingest.py:479`, `:494`, `:528`, `:724`; `src/airalert/scheduler.py:91`, `:92`, `:128`, `:181`. Independent probe returned `LOST_UPDATE: successful poll wrote 1 live hazard; nightly publication left 0`. Same result in the current working tree. | Nightly publication and polling use different locks but both replace hazards and severity-transition files. Nightly can read an old generation, allow a successful poll to publish a new row, and then overwrite that row. Atomic replacement protects individual reads, not this lost update. | I1, S1 |
| V2-05 | major | Failed secondary polls | `src/airalert/ingest.py:769`, `:785`, `:788`; `tests/test_ingest.py:170`, `:175`. The existing real-source-shaped test explicitly requires a failed ubilling request to produce `coverage="unknown"`, `red=None`, `stale=False` after an inherited red alert. | The unconditional "a failed poll never clears alerts" promise does not hold for the Sevastopol fallback. Its last-known red layer disappears on failure while the overall poll remains fresh. The UI receives unknown coverage, not an explicit all-clear, but loses the prior alert rather than retaining it as stale. | I1, S1 |
| V2-06 | minor | Archive recovery | `src/airalert/sources/aiu_web.py:46`, `:54`, `:58`; `src/airalert/ingest.py:571`. Probe returned `CACHE: first schema-invalid 200 response cached; corrected upstream never retried; requests=1 failures=2`. | Valid JSON is cached before semantic decoding. Once a day contains invalid event/schema data, normal catch-up repeatedly reuses that bad cache even after upstream corrects it. The watermark stays safe, but ordinary retries cannot recover without explicit cache intervention. | I1 |
| V2-07 | minor | Runtime ignore coverage | `src/airalert/backtest.py:472`; `src/airalert/production.py:414`; `.gitignore:32`. `git check-ignore -v reports/v2/track_record.json` matches; the same command for `reports/v2/track_record.json.tmp` returns no match. | An interrupted rolling-report write leaves an unignored runtime file. It contains rolling metrics rather than credentials, but can enter a broad add/publish operation and violates the documented runtime-state separation. | Lead |
| V2-08 | minor | Documentation | `docs/privacy.md:72`, `:84`, `:90`; `README.md:101`; `docs/overview-v2.md:51`. Fresh setup completed in 524.08 seconds. `git ls-files reports/v2/track_record.json` returned nothing. README has no `prepare-models-and-serving-artifacts` heading. | Privacy still says a fresh clone cannot complete setup and links a removed blocker section. README and overview call an ignored, uncommitted rolling file "published" and link it as a repository artifact. These statements misdescribe the checkout a reader actually gets. | Documentation owner, lead |
| V2-09 | major, resolved in working tree | Observation publication | HEAD `src/airalert/ingest.py:797` writes directly to a discoverable `.parquet`; HEAD `src/airalert/production.py:147` reads it. HEAD probe returned `RACE: incomplete future poll causes load_observations -> ArrowInvalid`. Working-tree `ingest.py:814` now stages `.part`; rerun returned `RACE_RESOLVED: writer uses .part; incomplete poll invisible to load_observations`. | HEAD can miss an hourly publication when a simultaneous poll exposes an unfinished Parquet file, even if that poll is too late to be an eligible input. X3's concurrent staging change addresses the demonstrated race. This is recorded for commit/integration tracking and is not counted among the five remaining major findings. | X3, I1 |
| V2-10 | minor | Publishing privacy | HEAD `docs/audit.md:357`, blob `a811f87b380cc198dc778bb518deed893f2dfd77`, contains explicit local PIDs. Historical `docs/current-state.md:8`, `:9`, blob prefix `376b30f610`, also contains backend/frontend listener and parent PIDs. Values are intentionally omitted here. | Local process identifiers violate the project's tracked-file privacy rule. The guard does not scan this category. These are additional history/content items beyond the already-known personal email and absolute paths. | Lead |

## 1. Leakage and evaluation integrity

### Recorded evaluation order

Read `AGENTS.md`, the v1 plan, README, and the requested v2 overview, operations, ingest, report, duration, news and privacy documents. The v2 rules preserve the strict hourly record prefix, live receipt cutoff, frozen earlier evaluations and separate production refit.

The saved onset sequence supports selection before its final evaluation:

| Artifact | Embedded UTC time | Filesystem last-write UTC |
|---|---|---|
| `reports/v2/selection.json` | 2026-10-12 17:17:47.129005 | 17:17:47.161848 |
| `reports/v2/test2_claim.json` | `started_at` 17:18:54.692955 | 17:18:54.692955 |
| `reports/v2/metrics/scores_test2.json` | `generated_at` 17:18:54.723060 | 17:18:59.899243 |
| `reports/v2/metrics.json` | `generated_at` 17:18:59.900744 | 17:18:59.965648 |

`%AUDIT%\probe.py integrity`, run using `uv run --no-sync python`, returned:

```text
selection_hash_matches True
prediction_hashes 13 / 13
chronological_calibrators 36
```

The selection bytes match claim SHA256 `1b2df62e6c41abb222189515384b5cdb31ec6c984bd7528725fa23c99f62ee0b`. All 13 prediction-file hashes still match the locked selection. `backtest.run_v2(final=True)` checks snapshot, gap policy and prediction hashes before creating an exclusive claim, then rechecks selection and prediction hashes after scoring. The official final path rejects another claimed run. These are strong consistency checks; a local marker and timestamps are not a tamper-proof record of every possible invocation of a Python scoring helper.

The timestamp statement needs a target qualification. Duration test2 was already published at `2026-10-12T16:53:24.425453Z`, before the onset selection above. Its local marker's last-write time is `16:53:24.428960Z`, but its contents contain only the snapshot, with no selection hash or scoring timestamp. Duration uses its own earlier frozen models and chronological calibration. This ordering does not demonstrate onset leakage, but the onset claim does not prove that every test2-related evaluation waited for `reports/v2/selection.json`. V2-02 explains the weaker duration protection.

### Calibration and production cutoff

`onset.fit_calibrator`, `src/airalert/models/onset.py:525`, requires every fit forecast time to precede `apply_from` and every fit label to mature no later than that boundary. `fit_chronological`, line 542, fits a prefix model before a six-week tail, calibrates forward prefix predictions, then refits the classifier on matured training rows. Validation uses the earlier train-tail calibrator; test2 uses a separate validation-only calibrator. All 36 stored candidate calibration metadata entries satisfy those bounds.

The 16 saved onset/landmark duration calibrators also have target ends strictly before application:

| Application | Calibrators checked | Latest landmark target end | Application begins |
|---|---:|---|---|
| Validation | 8 | 2026-07-31 23:55:23Z | 2026-08-01 00:00Z |
| Test2 | 8 | 2026-09-07 23:38:23Z | 2026-09-08 00:00Z |

`production.refit`, line 71, sets data through the lesser of the reconciled snapshot and the forecast hour minus one microsecond. Labels only mature through that cutoff. `production.forecast`, HEAD line 303, rejects model outcomes at or after the forecast hour. `load_observations` filters `observed_at < t`; `as_of_records` separately filters live receipt availability and record starts, and masks later finishes. Failed primary polls supply no successful observation file. The working-tree code also excludes reconciled older receipts and adds explicit empty-poll receipt manifests.

Remaining-duration checkpoint features come from record context, not merged episode ends. `duration.build_landmark_features`, line 501, fixes onset context at start and derives checkpoint context separately. `_available_features`, line 474, filters live receipts strictly before each query; `build_start_features` handles the inclusive duration-start boundary and later unknown ends. Episode ends enter target construction and survival cohort selection, not the feature columns. Production interval fitting holds its recent calibration tail out of the interval-model fit; its median refit is constrained within that calibrated interval.

Independent execution against the current working tree:

```text
uv run --no-sync pytest tests/test_production.py tests/test_leakage.py tests/test_duration.py
  -q --tb=short -p no:cacheprovider --basetemp %AUDIT%\asof-tests
24 passed in 75.42s
```

These include real-record prefix checks, delayed observations, checkpoint availability, merge-gap behavior and immutable production issuance. No additional leakage failure was found in these inspected paths. This does not establish prospective calibration quality. The reported duration undercoverage remains real.

### Independent test2 score reconstruction

`%AUDIT%\probe.py metrics` reads selected and B0 predictions, and reconstructs labels directly from sorted `episodes.parquet` start times. For each raion and horizon it compares the count of starts at or before `t+H` with the count at or before `t`, yielding starts in `(t,t+H]`. It does not call the project's label builder or scoring functions. Both prediction matrices receive cumulative maximum across horizons before squared errors are computed.

Bounds are `2026-09-08T00:00Z` through `2026-10-11T09:00Z`, inclusive. There are 95,438 rows and no coverage gaps. Reconstructed labels have **zero mismatches** with the saved test2 feature labels.

| Horizon | Positive labels | Independent served Brier | Independent projected B0 Brier | Independent BSS | Published served BSS |
|---|---:|---:|---:|---:|---:|
| 1 h | 14,187 | 0.10417526919096821 | 0.12390552155788198 | 0.15923626420229264 | 0.15923626420229287 |
| 3 h | 33,505 | 0.16606066975104766 | 0.21102779884212625 | 0.2130862821761189 | 0.2130862821761189 |
| 12 h | 61,061 | 0.12837279170487453 | 0.17960390421710212 | 0.2852449824826764 | 0.2852449824826764 |

The differences are floating-point reduction noise. In particular, the served 12-hour Brier is 0.128373, not the better unprojected 0.127532.

### Nightly writes versus frozen evaluation

The normal scheduler calls production refit, production duration refit and `score_recent`. It does not call onset selection or duration evaluation. `score_recent` writes only the rolling report, retains the original B0 at issuance, and excludes forecasts issued more than 60 seconds late. The temp clone's real manual maintenance run completed all eight steps in about 150 seconds:

```text
catch_up 0.046s; features 16.569s; refit 56.619s;
duration_refit 66.896s; score_recent 0.034s;
rebuild 0.131s; reload 0.169s; forecasts 5.503s
NIGHTLY frozen_reports 10 changed []
NIGHTLY first_issued_unchanged 1 rolling_report_exists True
```

This exercises the fresh clone's issued-only replay path. Static inspection of prepared-checkout replay also shows no report-writing call. Nightly rebuilds feature/label tables and may update replay from revised canonical history; those tables are not an immutable archive of the evaluation inputs. The saved report files remain separate. V2-01 and V2-02 are reachable evaluation-entrypoint problems, not observed contamination by the normal nightly pipeline.

## 2. Scheduler and data safety

`Scheduler.lock` serializes hourly model publication, nightly model/cache rebuilding and reload. API data reload constructs a complete `_Loaded` object before swapping `app.state.loaded`; existing readers keep their prior object. Production publishes complete model generations before replacing pointers, and issued hourly predictions use first-writer atomic hardlinks. The observation-file publication race at HEAD is now resolved in X3's working tree, as V2-09 records.

The separate polling lock keeps polling available during long fits. It also leaves shared hazards and transitions exposed to V2-04. The probe interleaves the actual `_save_hazards` function between nightly's read of the prior file and its replacement of the staged file. It observes the newly written row before publication and its disappearance afterward. A crash during the multi-file records/episodes/snapshot publication can also leave files from different generations: `_publish` replaces them in order and its rollback handles caught exceptions, not process termination. Startup retry is the intended recovery; there is no transactional filesystem commit of the whole generation. This is a recovery limitation in addition to the reproduced shared-writer loss.

The independent DST probe tested both transitions using the real `day_bounds` and `next_midnight` functions:

```text
2026-03-29 hours=23 next_midnight=2026-03-29T21:00:00+00:00
2026-10-25 hours=25 next_midnight=2026-10-25T22:00:00+00:00
```

Both functions construct consecutive local midnights with `ZoneInfo`, rather than adding 24 elapsed UTC hours. The catch-up loop starts at watermark plus one day and processes every complete Kyiv day through yesterday in order. The fresh clone reconciled all 44 archive days from August 29 through October 11.

Two independent failure injections let the first missing day succeed and fail the next day with either a changed schema or HTTP 500. Both returned:

```text
watermark=2026-10-25 gap=2026-10-26 next_day_not_requested=True
```

Thus explicit decode/HTTP errors retain the last successful watermark, record the failed day and block later days. V2-03 concerns apparently valid empty data; V2-06 concerns a bad response remaining in the raw cache. Primary siren failure retains prior polygon layers and their successful fetch time, marking them stale. Secondary failure is the exception in V2-05. An optional aiu_api failure also does not preserve that source's distinct prior contribution before publishing the siren-derived snapshot.

## 3. News agent security

The inspected runner uses an argv list with `subprocess.Popen`, no shell, an empty scratch working directory, a host-only schema outside that child cwd, a 120-second deadline and bounded JSON-event output. Model and reasoning choices pass the discovered catalog check in the settings route and worker; reasoning effort is also a schema literal. Source text goes through stdin, not argv or a shell command. The environment allowlist excludes project/API tokens and other arbitrary environment entries while retaining the OS/profile variables the CLI needs. It uses read-only sandboxing, disables configured MCP/search/features, and ignores user configuration and rules.

`codex_runner.unsafe_event` permits only known text/reasoning event types. The reader kills the child tree and trips the persistent breaker before forwarding an unsafe item. The independent fake child deliberately emitted a valid final JSON message and `turn.completed` **before** a tool event. `%AUDIT%\test_audit_probe.py` returned:

```text
TOOL_GUARD: valid final + turn.completed + tool event -> ToolAttempt; no result returned
```

The existing news test also checks that a child descendant cannot survive a tool attempt. A reported tool event cannot contribute a completion result. The protection remains reactive: a tool could begin before its first event reaches the host. The documentation states this limitation; it is not evidence of a strict no-tools sandbox.

Host validation owns event identity, timestamps, sources, geographic IDs, revisions, decay, caps and probability arithmetic. Extraction accepts only known batch source/version keys and literal evidence substrings. Target mentions must occur in cited input and resolve through the local alias map. Unsupported origins/headings abstain. Evaluation effects must identify an existing incident revision and supported raion; duplicate effects fail. LLM confidence, type/status classification and uplift strength remain model judgments constrained by schemas, not independently proven facts. Source text, summaries and metadata remain prompt-injection inputs; regex filtering and instructions are additional defenses, not a guarantee that the model interprets them correctly. No unvalidated model-supplied URL, arbitrary summary or geographic identifier was found flowing into the feed/adjustment paths.

Actual HTTP mutation probes against the isolated API used a complete Settings object:

| Origin or Host | `PUT /api/settings` | `POST /api/auth/logout` |
|---|---:|---:|
| Origin `https://example.invalid` | 403 | 403 |
| Origin `http://localhost:5173` against port 8021 | 403 | 403 |
| Origin `null` | 403 | 403 |
| Host `example.invalid` | 403 | Not invoked |
| Host `127.0.0.1.example.invalid` | 403 | Not invoked |
| Matching Origin `http://127.0.0.1:8021`, news off | 200 | Not invoked |

The routes check loopback client, loopback Host, matching Origin when supplied, and reject cross/same-site fetch metadata. The rejected logout requests did not reach logout. Remote-client rejection is covered by the committed route tests; a localhost HTTP connection cannot directly simulate a non-loopback peer.

Source inspection found no app read of the Codex credential file. Authentication delegates to CLI status/login/logout and private stdio model discovery. Login child stdout/stderr go to the null device. Status inspects the ChatGPT auth marker and discards other output. News logs contain fixed categories/timings rather than source text, reasoning, tokens or login URLs. The isolated server's observed logs were consistent with this policy; the fake auth tests cover suppression of a credential-bearing login URL. This is scoped evidence, not proof about arbitrary future CLI logging behavior outside the app.

`%AUDIT%\off_probe.py` ran the actual worker lifecycle for 65.11 seconds with counting HTTP/auth/model adapters and news disabled. Thirteen feed checks and an explicit additional cycle all stayed off:

```json
{"duration_seconds":65.11,"telegram_requests":0,"model_calls":0,"auth_calls":0}
```

The isolated HTTP server also logged `news state=off telegram_polls=0 model_calls=0`.

## 4. Privacy and publishing

The HEAD scan examined all 238 tracked files using the guard's exact patterns, excluding its own pattern-definition file, plus broader email, home-directory, PID and machine-name checks. Result: **zero guard findings**. Broader email matches are public source/channel contact information in research samples and docs, plus `@row-set.parquet` filename false positives in tests/handoff prose. No additional owner credential, owner email in file contents or local machine name was identified at HEAD. A stronger follow-up scan for plain `PID <number>` found the local process identifiers in V2-10, which the initial equals/colon-style PID pattern missed.

Reachable historical file blobs were scanned separately. Path matches were older absolute-path material in `AGENTS.md`, earlier audit/design/frontend documents, news-harness research and its sample call/probe artifacts. Those are within the already-known path-history exposure. **Additional material to scrub:** explicit local process IDs in historical `docs/current-state.md`, and the current/earlier `docs/audit.md` blobs identified in V2-10. No additional token/private-key or machine-name item was identified. The known personal email in author/committer metadata remains the lead's planned history rewrite and is not copied here.

### Runtime write inventory

`git check-ignore -v` verified each representative path below. Directory rules also cover temporary files within those ignored trees. This inventory covers setup/serve and their scheduled jobs; explicit research/evaluation/geography commands intentionally write publishable artifacts and are outside normal serving writes.

| Runtime output | Ignore coverage |
|---|---|
| `data/raw/official_data_en_*.csv`, checksum and download staging | Ignored by `data/*` |
| `data/live/raw/` poll/archive/location/dictionary gzip caches | Ignored |
| `data/live/normalized/*.parquet` and `.part` day partitions | Ignored |
| `data/live/{state,ledger,validation,nightly,setup}.json` and staging files | Ignored |
| `data/live/observations/*.parquet`, receipt `.json`, `.part` staging | Ignored, including X3's new receipt files |
| `data/processed/{records,episodes,raions,snapshot,hazards,severity_transitions}` with their contracted extensions | Ignored |
| `data/processed/features/v2/{train,val,test2,latest}.parquet` and staging | Ignored |
| `data/processed/preds/v2/issued/*.parquet` and hidden staging | Ignored |
| `data/processed/forecasts.parquet` and staging | Ignored |
| `artifacts/production/` onset/duration generations, boosters, joblib files, metadata and pointers | Ignored by `artifacts/` |
| `data/settings.local.json` and `data/settings.local.tmp` | Ignored |
| `data/news/news.sqlite`, WAL, SHM and journal files | Ignored |
| `reports/v2/track_record.json` | Ignored and untracked |
| `reports/v2/track_record.json.tmp` | **Not ignored**, V2-07 |
| Python environment/bytecode and frontend dependency/build/test directories | Covered by the existing environment/build ignore rules |
| News CLI scratch cwd and schema | OS temp directory, outside the repository |

The guard was installed in the temp clone using the documented supplied hook. A fake recognized token was staged there, then a real commit was attempted:

```text
Commit blocked by secret/privacy guard:
  audit_fake_token.txt: OpenAI-style key
commit_exit=1
HEAD=b90f45bca004c020def3ca62233967633ba9fa80
```

No commit was created. No hook, staging or git configuration was changed in the main repository. This test establishes the documented recognized-token path, not coverage of every possible secret format.

## 5. Literal fresh-clone Quickstart

Cloned committed HEAD locally with `git clone --no-hardlinks`. No data, models, settings, credentials or evaluation predictions were copied into the clone for setup. The evaluation predictions used for V2-01 were copied into separate scratch storage only after Quickstart and nightly verification had finished.

| Step | Actual result |
|---|---|
| `uv sync` | Exit 0; Python 3.12.13, all 48 locked packages installed |
| `uv run airalert setup` | Exit 0; all ten stages complete in 524.08 seconds |
| CSV fetch | 299,275 rows; checksum `b396f87696243069eec0219880e87dcd642b20beb0a7bae0d0e1f61e1ed20ec4` |
| Archive gate/catch-up | Twelve reviewed overlap days pass; 44 complete days reconciled through October 11 |
| Features | Train 4,521,048; validation 108,528; test2 95,438; latest 119 |
| Onset refit | 63.01 seconds; outcomes through 2026-10-11 21:00Z |
| Duration refit | 68.87 seconds; 442,859 ended episodes, 1,479,590 landmarks |
| `AIRALERT_DISABLE_SCHEDULER=1`, `uv run airalert serve --port 8021` | Application startup complete; localhost API reachable |
| Repeat setup with unchanged inputs | All ten stages skipped; completed in 2.00 seconds |

Actual `curl.exe` route probes returned 200 for `/api/meta`, `/api/raions`, `/api/forecast`, `/api/live`, `/api/system`, `/api/report`, `/api/report?version=v1`, `/api/settings` and `/api/news`. Additional parsed HTTP responses validated against the repository's Pydantic schemas:

```text
META latest_at=2026-10-12T18:00:00Z raions=119
FORECAST rows=119 active=30 models=selected/D2 labels_null=True
LIVE units=139 stale=False
REPORT onset=156 duration=92 models=selected/D2
SETTINGS news_enabled=False
HISTORY status=200 points=1
UNISSUED_REPLAY status=404
```

The 30-active result belongs to committed HEAD before X3's live-state corrections. It is evidence of API/bootstrap operation, not an endorsement of that known live-state behavior. Fresh replay initially contains only the issued hour, as README says. For the history probe, an equal `from`/`to` was rejected as an invalid interval; a valid one-hour interval ending at issuance returned the single stored point. The API did not invent September replay predictions. The frontend/Bun/browser workflow was outside the requested backend Quickstart audit and was not exercised.

Setup and the real isolated nightly pipeline both left all ten frozen report files unchanged. The intentionally destructive report-overwrite reproduction afterward affected only the temp clone. No Quickstart step failed. Its source acceptance remains dependent on the reviewed private upstream schema and the gate continuing to pass.

## 6. Documentation spot checks

All ten selected numeric claims match the saved JSON to the stated precision:

| # | Claim and location | JSON evidence | Result |
|---|---|---|---|
| 1 | README results: 1 h served BSS 15.92% | `metrics.json`, selected/test/test2-served/H1: 0.15923626420229287 | Match |
| 2 | Overview line 51: 3 h served BSS 21.31% | Same row for H3: 0.2130862821761189 | Match |
| 3 | README results: 12 h served BSS 28.52% | Same row for H12: 0.2852449824826764 | Match |
| 4 | README test2 rows: 95,438 per horizon | All three selected served test2 rows have `n=95438` | Match |
| 5 | README bootstrap: 34 day blocks | `bootstrap.json`, each horizon `days=34` | Match |
| 6 | README bootstrap: 2,000 draws | `bootstrap.json`, each horizon `samples=2000` | Match |
| 7 | README 1 h 90% interval: 14.30% to 17.36% | `bootstrap.json`: 0.1430144748879645 to 0.1736125192298607 | Match |
| 8 | README D1c test2 coverage: 77.26% | `duration_scores.json`, D1c/test: 0.772605 | Match after rounding |
| 9 | README D2 at eight hours coverage: 70.23% | `duration_scores.json`, D2@480/test: 0.702277, `n=571` | Match after rounding |
| 10 | Onset report validation served BSS at 12 h: 15.76% | `metrics.json`, selected/val/served/H12: 0.157565 | Match after rounding |

The other two bootstrap intervals and the validation 1/3-hour headline values were also checked and match. Duration remains below the nominal 80% target; the docs disclose that rather than promise calibrated coverage. The overlay has no measured forecasting benefit, and the rolling report has no mature eligible rows. Those caveats are supported.

The universal failed-poll statement needs V2-05's secondary-source qualification. README's claim that both onset and duration evaluation entrypoints are blocked after the frozen claim is contradicted by V2-01 and V2-02. The stale fresh-clone blocker and repository links to the ignored rolling report are V2-08. Earlier agent handoffs also retain resolved fixture/integration issues; they should be read as dated handoffs, not a current failure list.

## AGENTS.md rule 9 report

### Delivered

`docs/audit-v2.md`. Findings and evidence only; no implementation fixes or contract edits. All scratch work is under `%AUDIT%`.

### Verified

- Committed clone suite: `uv run --no-sync pytest -q --tb=short -p no:cacheprovider` returned **132 passed, 1 warning in 116.54 seconds**. The warning is the upstream Starlette/httpx deprecation.
- Eight independent probes: **8 passed in 5.05 seconds** on HEAD and **8 passed in 2.64 seconds** on the changed working tree. These assertions reproduce defects or check specified safety behavior; passing them does not mean the defects are repaired. The working-tree rerun specifically confirmed V2-09's resolution.
- Current production/leakage/duration tests: **24 passed in 75.42 seconds**. Main-repository invocations used `uv run --no-sync`, disabled bytecode writes and placed pytest scratch data under `%AUDIT%`.
- Independent reconstruction: 95,438 keys, zero label mismatches, all three served BSS values match.
- Literal clone setup: exit 0 in 524.08 seconds; isolated HTTP routes/schema checks and rejected cross-origin mutations verified.
- News-off observation: 65.11 seconds, zero Telegram requests, zero model calls, zero auth calls.
- Real isolated nightly pipeline: successful, ten frozen reports unchanged, prior first-issued file unchanged.
- Real temp-clone pre-commit attempt: rejected, exit 1, HEAD unchanged.
- V2-01 report overwrite and V2-02 missing-marker entry were exercised only in scratch. Main reports and git state were not changed by this audit.

### Decisions/assumptions

The verdict concerns the remaining failure paths, not a mismatch in the headline scores. Committed HEAD and concurrent working-tree evidence are separated. Source/CLI credentials were not accessed, copied or printed. Optional authenticated alerts.in.ua operation and real enabled-news inference were not exercised. The audit used no subagents and added no tests to the repository.

### Open issues

V2-01 through V2-08 and V2-10 remain actionable. V2-09 is addressed in X3's working tree and still needs the lead's integrated commit/release handling. The known persistent-community-alert repair remains X3's task. Prospective model/news benefit, duration calibration away from measured landmarks, and source completeness remain limitations beyond a passing setup or test suite.

### Contract change requests

None made. The lead should resolve the failed-secondary-poll behavior against the existing preservation rule and require a durable evaluation freeze for both targets. No contract file was edited.

### Dependencies added

None. `uv sync` installed only the existing locked dependencies in the temp clone.
