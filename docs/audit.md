# D1 independent audit, phase 2

Audited on 2026-10-12 against committed HEAD `e1789c3` in the main repository. Retained rebuild: `%TEMP%\aaf-audit-clone`.

## Summary verdict

**Ready with caveats for the frozen retrospective test replay.** The README rebuild succeeds, all 40 onset/duration score rows reproduce exactly, the 30-pair prediction reconstruction has zero difference, and both browser suites pass. No feature-prefix leak or use of test outcomes for model selection was found.

I would not sign off on a stronger claim that every validation probability was generated chronologically at its timestamp. June calibration uses July outcomes. The event-time feature invariant passes, but it does not cover this model-fitting dependency or historical publication availability.

The top three issues are:

1. **AUD-01, major:** validation cross-fitting runs backward in time for June. It is a model-comparison diagnostic, not a chronological as-of validation forecast.
2. **AUD-02, minor:** the UI reports scores for probabilities before the serving adjustment. The displayed map probabilities have different scores.
3. **AUD-03, minor:** saved M1 artifacts omit the validation-fold calibrators. Test reconstruction uses saved calibration; validation reconstruction requires fitting the omitted folds again.

The findings below are observations only. No implementation, configuration, dependency manifest, model, or frozen score in the main repository was changed.

## Findings

| ID | Severity | Area | Observation and evidence | Why it matters | Suggested owner |
|---|---|---|---|---|---|
| AUD-01 | major | Temporal validity of validation calibration | `src/airalert/models/onset.py:102` fits `second_fit` on July targets and `:108` applies it to June. With the saved booster and fixed features, replacing only July calibration targets with zeros changes 77,857 / 80,622 / 83,514 June probabilities at H=1/3/12; maximum change is 1.0 at every horizon. Command: `uv run python .audit/probe.py`, exit 0; JSON field `future_calibration_perturbation`. | Opposite-month fitting prevents a row fitting its own calibrator, but does not ensure training data predates the forecast. The prescribed cross-fit protocol is implemented correctly; its validation probabilities cannot support the blanket chronological as-of claim. This finding is limited to validation, with no observed test-label contamination. | C1 / lead |
| AUD-02 | minor | Scores versus served probabilities | `src/airalert/precompute.py:232` applies cumulative maximum after scoring. On eligible test rows, 5,508 H=3 and 1,837 H=12 probabilities change; maximum changes are 0.230110 and 0.363272. H=3 Brier changes from 0.142332186 to 0.142363441. `web/src/lib/ModelReport.svelte:18` says "How the forecasts compare, and which models are served" without identifying this distinction. | The API score table is numerically correct for the original model output, but it does not score the forecasts shown on the map. `docs/report.md:120` and `:233` disclose the limitation; the live report page does not. This is a reporting caveat, not a reproduction failure. | B2 / C2 / A3 |
| AUD-03 | minor | Saved calibration artifacts | `onset.py:118` returns the cross-fitted array and all-val calibrator, not either fold calibrator. `:279` saves only `calibrator_{1,3,12}h.joblib`. Those are the three calibration files present in `artifacts/M1/`. All-val saved fits reproduce exactly; validation folds were reconstructed with the saved booster and validation labels. | The requested validation probe cannot be performed using saved models and saved split-specific calibration alone. The cross-fitted predictions reproduce, but their calibrators are not retained as independent replay artifacts. | C1 |
| AUD-04 | minor | Horizon-dependent outage disclosure | At `2026-08-29T03:00Z`, H=12, the live mobile panel gives 3h and 12h outcomes as "Unknown", but there is no unscored notice. `web/src/lib/gaps.ts:5` considers only whether t is inside outage/recovery, and `web/src/App.svelte:143` shows the notice only for that state. H=3/12 label windows already touch the gap. Screenshot: clone `web/test-results/audit-pre-outage-mobile.png`. | The scoring policy is horizon-dependent before the outage begins. Unknown outcomes are handled correctly, but the dashboard's unscored disclosure misses these excluded forecast windows. | A3 |
| AUD-05 | minor | Map encoding | `web/src/lib/MapView.svelte:56` gives both selection and actual onset the same `#f0efec` stroke, distinguished only by width 2 versus 1.5. At the outage hour, selected Kryvorizkyi remains white while all actual-onset values are null; `:89` still gives white the legend "Alert actually started within 3 h". Screenshot: clone `web/test-results/audit-outage-mobile.png`. | Selection can look like a verified onset, especially at 390 px or during an outage. The actual-onset class is correctly absent; the ambiguity is visual. | A3 |
| AUD-06 | minor | Stale report contract statement | `docs/report.md:54` says inclusive duration starts differ from AGENTS.md and need clarification; `:231` repeats it. `AGENTS.md:15` already explicitly permits `started_at <= s` for duration. | The report describes a resolved contract decision as an open issue. The duration code and the current contract agree. | D2 |

No critical findings. The pre-existing source limitations, under-coverage of D1 intervals, retrospective regime cutoff, and validation reuse are already disclosed in the main report. They remain limits on interpretation, rather than fresh implementation findings.

## Rebuild log

I read AGENTS.md, the plan including sections 04/08/10, README.md, docs/report.md, and all requested handoffs before completing the audit. The main worktree was clean at entry. The requested clone path was absent, so no suffix was needed.

Commands in the README's build sequence were run in order, one at a time, without editing product files. A clone-only PowerShell wrapper captured stdout/stderr, exit status, UTC start time, and wall time. Its full records are `.audit/commands.jsonl`; each entry points to its complete `.audit/<timestamp>.log`. Table times include command startup overhead, so they differ from internal stage timers.

| Start, UTC | Command | Exit | Wall time, seconds | Evidence / README deviation |
|---|---|---:|---:|---|
| clone setup, start timestamp not captured | `git clone <source-repository> %TEMP%\aaf-audit-clone` | 0 | 0.358 | "Cloning into ... done." The source repository path is omitted. New empty directory; elapsed time captured with Stopwatch. |
| 10:17:23.289 | `uv sync` | 0 | 2.405 | Locked environment installed. |
| 10:17:37.559 | `uv run airalert fetch` | 0 | 3.215 | Required source hash matched exactly. |
| 10:17:59.459 | `uv run airalert episodes` | 0 | 3.779 | 299,275 source rows; 875,633 records; 431,094 episodes; 119 units. |
| 10:18:03.289 | `bunx mapshaper@0.7.83 -v` | 0 | 0.143 | Output `0.7.83`. README includes this prerequisite. |
| 10:18:03.438 | `uv run airalert geo` | 0 | 17.437 | 0/119 unmatched; 489,623-byte GeoJSON. |
| 10:18:20.891 | `uv run airalert features` | 0 | 10.188 | 4,626,125 rows across four splits; latest labels null. |
| 10:18:31.084 | `uv run airalert train --model B0` | 0 | 12.307 | 279,293 predictions. |
| 10:18:43.402 | `uv run airalert train --model D0` | 0 | 4.336 | 31,647 predictions; 18,262 eligible val episodes. |
| 10:18:47.744 | `uv run airalert train --model D1` | 0 | 23.849 | Same winner and iterations as main: regime window, 15 leaves, 171/181/176. |
| 10:19:11.603 | `uv run airalert train --model B1` | 0 | 24.061 | Selected raion-regime; full and selected predictions published. |
| 10:19:35.671 | `uv run airalert train --model M1` | 0 | 245.542 | Selected raion-regime; iterations 114/124/92. |
| 10:23:41.225 | `uv run airalert backtest` | 0 | 5.296 | "Validated metrics.json: 18 onset, 2 duration scores"; validation only. |
| 10:23:46.529 | `uv run airalert backtest --final` | 0 | 7.031 | "TEST SET SCORED (--final explicitly enabled)"; 36 onset, 4 duration scores. Exactly one final scoring invocation, in the clone. |
| 10:23:53.569 | `uv run python scripts/report_tables.py` | 0 | 0.280 | All 40 generated score rows equal the main report after normalizing line endings. |
| 10:23:53.860 | `uv run airalert precompute` | 0 | 3.208 | 279,293 rows; 119 raions; 2,347 hours; M1/D1. |
| 10:24:13 to 10:31:11 | `uv run airalert serve --port 8000` | 1 on Ctrl+C | 418, nearest second | Startup complete; real API HTTP 200s; "Application shutdown complete" and confirmation that the listener process finished. Exit 1 is deliberate interruption, not startup failure. |
| before 10:24:24 | `cd web` | 0 | included in wrapper | Second-terminal working directory set to clone `web/`. |
| 10:24:24.748 | `bun install` | 0 | 7.184 | 77 packages installed. |
| 10:24:54 to 10:31:10 | `bun run dev` | 1 on Ctrl+C | 376, nearest second | Vite ready in 919 ms; printed `http://localhost:5173/`. Clone listener was `[::1]:5173`. |

No README build step failed or required a product workaround on this machine. Internet fetch, uncached geography, and both dependency installs were exercised. The historical-copy fallback was unnecessary because the live CSV matched. The existing system-wide uv/bun installations, bun cache, and Chromium cache were used; this was a fresh repository clone, not a fresh operating system.

A pre-existing main-repo Vite listener used `127.0.0.1:5173`. The clone's literal `bun run dev` bound `[::1]:5173`; I navigated that explicit address to avoid testing the existing server. Playwright independently used `127.0.0.1:5187`. This was browser-target disambiguation, with no README command change.

### Additional verification commands and audit-tool deviations

| Start, UTC | Command | Exit | Wall time, seconds | Actual result |
|---|---|---:|---:|---|
| 10:19:56.028 | `uv run pytest` | 0 | 43.909 | `49 passed, 1 warning in 42.49s`; no skipped real-data probes. Existing Starlette/httpx deprecation warning. |
| 10:23:26.294 | `uv run ruff check` | 1 | 0.167 | 37 findings in my new clone-only `.audit/probe.py`, not product code. |
| 10:23:53.738 | `uv run ruff check` | 0 | 0.111 | `All checks passed!` after excluding the scratch probe with a file-level Ruff directive. No product edit. |
| 10:24:32.739 | `uv run python .audit/probe.py` | 0 | 65.322 | 30 model probes, 10 duration probes, exact metrics, instrumented validation-only gate, and report checks. |
| 10:25:05.970 | `bunx playwright install chromium` | 0 | 0.468 | No output; actual Chromium workflows subsequently passed. |
| 10:25:23.216 | `$env:E2E_TARGET='live'; bunx playwright test` | 0 | 21.017 | `24 passed (20.3s)`. |
| 10:26:22.827 | `$env:E2E_TARGET='mock'; bunx playwright test` | 0 | 17.668 | `24 passed (17.0s)`. |
| 10:29:45.418 | `bun run check` | 0 | 2.652 | `svelte-check found 0 errors and 0 warnings`. |
| 10:30:32.193 | `bun run build` | 0 | 1.319 | `built in 349ms`. |

One compound audit invocation attempted a second relative `./.audit/run.ps1` after the wrapper changed its working directory to `web/`. That wrapper lookup failed, exit 1; `bun run build` had not started. I reran it using the absolute wrapper path, as logged above. This is my audit tooling error, not a README failure. An exploratory read also named a nonexistent scratch log and a nonexistent `state.svelte.ts`; neither was a product prerequisite. The relevant App.svelte code and actual log were then read.

Source inspections, Git status/history, JSON reads, screenshot copies, and listener checks were read-only diagnostics or clone-only evidence collection. They did not rerun test fitting or test selection. T3 browser actions have their own tool history; their screenshot evidence is retained below.

## Reproducibility

Fresh fetch, saved source, and `snapshot.json` all give:

```text
b396f87696243069eec0219880e87dcd642b20beb0a7bae0d0e1f61e1ed20ec4
snapshot = 2026-09-07T05:00:00Z
regime_switch = 2025-08-14
```

I compared main versus clone scores by identity, not list position: onset uses model/split/horizon/row_set; duration uses model/split. All identities and nonnumeric score fields match. The comparison includes six onset variants at every horizon in both splits, and D0/D1 in both splits. Only generation times are expected to differ.

| Task | Metric | Compared scores, val / test | Max absolute Δ, val | Max absolute Δ, test |
|---|---|---|---|---|
| onset | n | 18 / 18 | 0 | 0 |
| onset | base_rate | 18 / 18 | 0 | 0 |
| onset | brier | 18 / 18 | 0 | 0 |
| onset | bss | 18 / 18 | 0 | 0 |
| onset | logloss | 18 / 18 | 0 | 0 |
| onset | pr_auc | 18 / 18 | 0 | 0 |
| duration | n | 2 / 2 | 0 | 0 |
| duration | pinball_q10 | 2 / 2 | 0 | 0 |
| duration | pinball_q50 | 2 / 2 | 0 | 0 |
| duration | pinball_q90 | 2 / 2 | 0 | 0 |
| duration | mae_q50_min | 2 / 2 | 0 | 0 |
| duration | coverage_80 | 2 / 2 | 0 | 0 |

The recursive comparison also checks every oblast/state slice, every reliability-bin count/edge/mean/frequency, and matching nulls. **Maximum absolute difference over all numeric score fields is 0**, including those nested fields. Raw JSON files differ in generated timestamps and line endings.

The selection JSON is semantically unchanged in the clone. The probe's raw-byte hash check says false because Git checkout converted eight LF endings to CRLF; parsed JSON equality is true, and `generated_at` remains the original value. This is not a selection change.

## Leak probes

The complete independent helper and results remain in clone `.audit/probe.py` and `.audit/probe-results.json`. The helper does not refit a classifier. Its fold-calibrator reconstruction is the limitation in AUD-03.

Actual completion output:

```text
PROBE COMPLETE {"pairs": 30, "max_prediction_delta": 0.0,
"max_served_delta": 0.0, "duration_pairs": 10, "max_metric_delta": 0,
"gap_violations": 0, "report_checks": 10}
```

### Thirty-pair saved-model and served-prediction reconstruction

Seed `20261012` selects 15 validation and 15 test raion/hour pairs, including outage and recovery hours. At every t, I dropped records with `started_at >= t` and nulled `finished_at > t`. I rebuilt features for all 119 units, preserving national/oblast/neighbour inputs, then compared all 23 feature columns against full-record evaluation with zero relative tolerance and absolute tolerance 1e-6.

For each selected raion I applied the saved M1 booster with saved feature order and categorical vocabularies. Test probabilities use the saved all-val calibration files. Validation uses the opposite-month folds rebuilt from saved-model raw probabilities and eligible validation labels, retaining raw seam rows exactly as production does. I compared those outputs with `onset_M1.parquet`, then applied cumulative maximum and compared with `forecasts.parquet`.

Every feature difference, every stored probability difference, and every served probability difference is zero. Each maximum below is across all three horizons; displayed probabilities are rounded to six decimals. These checks establish record-prefix invariance with a frozen model, not chronological validity of the validation fitting protocol.

| # | Split | Raion | t, UTC | Rebuilt M1 = stored, P1 / P3 / P12 | Served P1 / P3 / P12 | Max Δ model / served |
|---|---|---|---|---|---|---|
| 1 | val | zakarpatska/rakhivskyi | 2026-06-26T18:00:00Z | 0.008963 / 0.032741 / 0.132045 | 0.008963 / 0.032741 / 0.132045 | 0 / 0 |
| 2 | val | sumska/shostkynskyi | 2026-07-30T14:00:00Z | 0.131814 / 0.621047 / 1.000000 | 0.131814 / 0.621047 / 1.000000 | 0 / 0 |
| 3 | val | odeska/rozdilnianskyi | 2026-07-07T08:00:00Z | 0.083333 / 0.156250 / 0.527139 | 0.083333 / 0.156250 / 0.527139 | 0 / 0 |
| 4 | val | khmelnytska/khmelnytskyi | 2026-07-19T00:00:00Z | 0.347339 / 0.902778 / 0.844545 | 0.347339 / 0.902778 / 0.902778 | 0 / 0 |
| 5 | val | zakarpatska/mukachivskyi | 2026-06-09T02:00:00Z | 0.000000 / 0.000000 / 0.012953 | 0.000000 / 0.000000 / 0.012953 | 0 / 0 |
| 6 | val | volynska/kamin-kashyrskyi | 2026-07-15T21:00:00Z | 0.025999 / 0.096797 / 0.528426 | 0.025999 / 0.096797 / 0.528426 | 0 / 0 |
| 7 | val | kirovohradska/novoukrainskyi | 2026-06-19T22:00:00Z | 0.060765 / 0.132541 / 0.363309 | 0.060765 / 0.132541 / 0.363309 | 0 / 0 |
| 8 | val | kirovohradska/kropyvnytskyi | 2026-06-06T07:00:00Z | 0.192108 / 0.368781 / 0.685787 | 0.192108 / 0.368781 / 0.685787 | 0 / 0 |
| 9 | val | zhytomyrska/zhytomyrskyi | 2026-07-02T03:00:00Z | 0.119492 / 0.328437 / 0.527139 | 0.119492 / 0.328437 / 0.527139 | 0 / 0 |
| 10 | val | kharkivska/krasnohradskyi | 2026-06-29T03:00:00Z | 0.060765 / 0.373626 / 0.939569 | 0.060765 / 0.373626 / 0.939569 | 0 / 0 |
| 11 | val | rivnenska/sarnenskyi | 2026-07-01T18:00:00Z | 0.074492 / 0.374237 / 0.528426 | 0.074492 / 0.374237 / 0.528426 | 0 / 0 |
| 12 | val | kyivska/buchanskyi | 2026-07-25T22:00:00Z | 0.038987 / 0.154028 / 0.527139 | 0.038987 / 0.154028 / 0.527139 | 0 / 0 |
| 13 | val | odeska/bilhorod-dnistrovskyi | 2026-07-10T20:00:00Z | 0.194289 / 0.502724 / 0.882699 | 0.194289 / 0.502724 / 0.882699 | 0 / 0 |
| 14 | val | odeska/bilhorod-dnistrovskyi | 2026-07-16T20:00:00Z | 0.252064 / 0.601023 / 0.899523 | 0.252064 / 0.601023 / 0.899523 | 0 / 0 |
| 15 | val | cherkaska/umanskyi | 2026-06-30T22:00:00Z | 0.098014 / 0.206348 / 0.592484 | 0.098014 / 0.206348 / 0.592484 | 0 / 0 |
| 16 | test | rivnenska/varaskyi | 2026-08-03T17:00:00Z | 0.013164 / 0.024263 / 0.133211 | 0.013164 / 0.024263 / 0.133211 | 0 / 0 |
| 17 | test | kyivska/bilotserkivskyi | 2026-08-20T05:00:00Z | 0.055730 / 0.111204 / 0.361758 | 0.055730 / 0.111204 / 0.361758 | 0 / 0 |
| 18 | test | kyivska/brovarskyi | 2026-09-06T15:00:00Z | 0.055730 / 0.193269 / 0.608782 | 0.055730 / 0.193269 / 0.608782 | 0 / 0 |
| 19 | test | zaporizka/vasylivskyi | 2026-09-01T04:00:00Z | 0.033403 / 0.290750 / 0.668917 | 0.033403 / 0.290750 / 0.668917 | 0 / 0 |
| 20 | test | rivnenska/rivnenskyi | 2026-08-24T19:00:00Z | 0.013164 / 0.048198 / 0.157616 | 0.013164 / 0.048198 / 0.157616 | 0 / 0 |
| 21 | test | ternopilska/ternopilskyi | 2026-08-04T00:00:00Z | 0.004012 / 0.000602 / 0.096188 | 0.004012 / 0.004012 / 0.096188 | 0 / 0 |
| 22 | test | kharkivska/chuhuivskyi | 2026-08-17T06:00:00Z | 0.117605 / 0.465133 / 0.984854 | 0.117605 / 0.465133 / 0.984854 | 0 / 0 |
| 23 | test | donetska/mariupolskyi | 2026-08-22T02:00:00Z | 0.042849 / 0.165803 / 0.665571 | 0.042849 / 0.165803 / 0.665571 | 0 / 0 |
| 24 | test | khersonska/khersonskyi | 2026-09-02T00:00:00Z | 0.080252 / 0.118110 / 0.511513 | 0.080252 / 0.118110 / 0.511513 | 0 / 0 |
| 25 | test | odeska/bolhradskyi | 2026-08-02T15:00:00Z | 0.203670 / 0.505184 / 0.899507 | 0.203670 / 0.505184 / 0.899507 | 0 / 0 |
| 26 | test | zhytomyrska/berdychivskyi | 2026-08-03T15:00:00Z | 0.022423 / 0.078625 / 0.252010 | 0.022423 / 0.078625 / 0.252010 | 0 / 0 |
| 27 | test | khersonska/skadovskyi | 2026-08-05T15:00:00Z | 0.080386 / 0.290750 / 0.665571 | 0.080386 / 0.290750 / 0.665571 | 0 / 0 |
| 28 | test | lvivska/zolochivskyi | 2026-09-02T17:00:00Z | 0.013164 / 0.023118 / 0.437097 | 0.013164 / 0.023118 / 0.437097 | 0 / 0 |
| 29 | test | odeska/odeskyi | 2026-08-27T03:00:00Z | 0.076623 / 0.424911 / 0.928761 | 0.076623 / 0.424911 / 0.928761 | 0 / 0 |
| 30 | test | ternopilska/kremenetskyi | 2026-09-01T14:00:00Z | 0.006680 / 0.021995 / 0.133211 | 0.006680 / 0.021995 / 0.133211 | 0 / 0 |

At the same 30 times, full versus truncated `precompute.active_state` returned identical `active` and `active_since` values for all 119 units. The real leakage test additionally exercised 50 random times, the snapshot, exact start/end boundaries, a real merge-gap timestamp, duplicates, and rejection of its planted future-duration feature; the full 49-test suite passed.

### Calibration and early stopping

| H | June fit rows | July fit rows | Raw seam rows | All-val serving fit rows | Max Δ reconstructed cross-fit versus stored val | Max Δ saved versus rebuilt serving calibrator |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 85,680 | 88,536 | 0 | 174,216 | 0 | 0 |
| 3 | 85,442 | 88,298 | 238 | 173,978 | 0 | 0 |
| 12 | 84,371 | 87,227 | 1,309 | 172,907 | 0 | 0 |

Calibration fit-label windows end no later than July 1 for June fits and August 1 for July/all-val fits. No test labels are included. The negative-control perturbation in AUD-01 demonstrates why the July-to-June direction remains a future dependency despite successful cross-fitting.

M1 early stopping uses only the inner training holdout from `2026-04-06T00:00Z`; `onset.py:175` passes only that dataset as `valid_sets`. Selected regime-window evidence:

| H | Inner train n | Inner holdout n | Refit n | Best iteration | Max inner-train label end | Max refit label end |
|---|---:|---:|---:|---:|---|---|
| 1 | 671,160 | 159,936 | 831,096 | 114 | 2026-04-06T00:00Z | 2026-06-01T00:00Z |
| 3 | 670,922 | 159,698 | 830,858 | 124 | 2026-04-06T00:00Z | 2026-06-01T00:00Z |
| 12 | 669,851 | 158,627 | 829,787 | 92 | 2026-04-06T00:00Z | 2026-06-01T00:00Z |

The exact split-end label convention follows AGENTS.md. D1 deliberately uses validation for stopping/candidate selection, as documented, and does not use test targets for fitting.

### Selection timing and validation-only gate

Main committed fragment contents record selection at `2026-10-12T09:46:44.475218Z`, then test scoring at `2026-10-12T09:48:02.029497Z`, approximately 77.55 seconds later. Both were committed in `405cd17`. The selection note cites validation Brier and the two-of-three gate; it contains no test-score criterion.

Current onset training selects windows from its validation score dictionary, and does not write selection.json. Precompute reads the frozen selection fragment and never chooses from test scores. The clone retained the exact selection content before/after rebuilding and final scoring. These contents, code paths, and recorded timestamps corroborate the reported sequence. They cannot independently prove that nobody viewed outcomes before the original freeze; no such evidence was found.

I instrumented the actual `backtest.run(False)` in the clone, redirecting only report/figure output to `.audit/gate-report/` and `.audit/gate-figures/` so the final rebuild metrics remained intact. The reader raises if test features, test predictions, or test-start duration targets are requested. I also planted an invalid stale test fragment. Result:

```text
Wrote scores_val.json: 18 onset and 2 duration scores
Validated metrics.json: 18 onset, 2 duration scores
test scores in merged gate report = 0
```

Observed reads were validation features only, onset predictions filtered to split=val, duration predictions filtered to split=val, and 18,293 episode targets with start predicates `>= VAL_START` and `< TEST_START`. No test feature file or test target row was requested; the invalid stale fragment was ignored. The focused split tests independently cover the gating/error paths.

### Gap policy

I independently checked every eligible test key against both the label-window overlap and the requested `[2026-08-29T06:00Z, 2026-09-09T14:00Z)` exclusion.

| H | Total test rows | Scored rows | Excluded rows | Last scored t, UTC | Forbidden-window violations |
|---|---:|---:|---:|---|---:|
| 1 | 104,958 | 80,563 | 24,395 | 2026-08-29T04:00Z | 0 |
| 3 | 104,958 | 80,325 | 24,633 | 2026-08-29T02:00Z | 0 |
| 12 | 104,958 | 79,254 | 25,704 | 2026-08-28T17:00Z | 0 |

Duration scoring uses B3's separate episode-overlap/censoring/split-contained-end mask. It does not apply the onset-feature recovery rule, consistent with the current contract.

### Other causal checks

B0's saved raion and oblast tables were reconstructed from eligible TRAIN rows and matched exactly. Selected train counts are 831,096 / 830,858 / 829,787 for H=1/3/12. The latest permitted target end is June 1, never a test time. Code review confirms training-only global/coarser fallback rates, with window/shrinkage decisions using validation.

Ten independently sampled duration onsets were reconstructed using the duration contract's inclusive starts `<= s` and masked ends `> s`. All 16 features, including recent merged-prefix duration history and simultaneous starting level, match:

| Raion | Onset s, UTC | All 16 features, full versus inclusive as-of |
|---|---|---|
| chernihivska/prylutskyi | 2026-06-29T21:07:27Z | equal |
| poltavska/myrhorodskyi | 2026-07-02T06:01:13Z | equal |
| odeska/bilhorod-dnistrovskyi | 2026-07-22T20:58:45Z | equal |
| zaporizka/melitopolskyi | 2026-06-01T06:09:06Z | equal |
| chernihivska/chernihivskyi | 2026-07-27T17:31:00Z | equal |
| chernihivska/chernihivskyi | 2026-08-27T12:35:24Z | equal |
| donetska/kramatorskyi | 2026-07-02T16:14:47Z | equal |
| sumska/okhtyrskyi | 2026-08-07T20:20:08Z | equal |
| dnipropetrovska/kamianskyi | 2026-08-05T00:54:38Z | equal |
| poltavska/poltavskyi | 2026-08-07T07:40:16Z | equal |

Onset and duration dynamic features read raw records; full episodes supply targets/keys. Record-start counts deduplicate exact coverage records and then count distinct start times. The recent-duration code corrects provisional prefix ends inside future merge extensions. The record-prefix tests cover this merge-gap trap.

The regime cutoff and geographic universe are intentionally fixed metadata. The cutoff is retrospective, not an online detector, as the report states. This audit does not reinterpret that accepted contract choice as a new feature leak.

Latest contains 119 predictions, all onset labels and actual-onset fields null. No active forecast row lacks duration quantiles in val, test, or latest. Test/latest onset feature reads explicitly exclude y columns. Precompute attaches actual outcomes only after predictions and state are computed; no reverse path from actual-onset columns into model inputs was found.

## Served-score distinction

These audit calculations use the already frozen predictions and fixed scoring masks. They did not choose, refit, or change a model, and did not rerun `backtest --final`.

| Split | H | Scored rows | Changed probabilities | Max probability change | Original Brier | Served Brier |
|---|---|---|---|---|---|---|
| val | 1 | 174216 | 0 | 0.000000 | 0.074394121 | 0.074394121 |
| val | 3 | 173978 | 9516 | 0.315284 | 0.135454889 | 0.135463955 |
| val | 12 | 172907 | 5078 | 0.530686 | 0.140450312 | 0.140656544 |
| test | 1 | 80563 | 0 | 0.000000 | 0.082083377 | 0.082083377 |
| test | 3 | 80325 | 5508 | 0.230110 | 0.142332186 | 0.142363441 |
| test | 12 | 79254 | 1837 | 0.363272 | 0.131844750 | 0.132029069 |

This quantifies AUD-02. H=1 is unchanged. Aggregate Brier changes are small, but individual changes can be large; the serving adjustment is not merely floating-point rounding.

## Report and README numbers

Ten main-report spot checks:

| Model / split / H | Metric | metrics.json value | Report text | Result |
|---|---|---|---|---|
| M1 / test / 3h | brier | 0.1423321855799064 | 0.142332 | match |
| M1 / test / 3h | n | 80325 | 80,325 | match |
| M1 / test / 3h | bss | 0.13842857784045004 | 13.843% | match |
| M1-full / test / 3h | bss | 0.14058886390288095 | 14.059% | match |
| B0 / test / 3h | brier | 0.16520068089439094 | 0.165201 | match |
| M1 / val / 12h | bss | -0.005985235530920008 | -0.599% | match |
| D0 / val | pinball_q10 | 12.297950991129122 | 12.297951 | match |
| D1 / val | mae_q50_min | 94.56317448707082 | 94.563174 | match |
| D1 / test | mae_q50_min | 90.61074377279994 | 90.610744 | match |
| D1 / test | coverage_80 | 0.759772435309231 | 75.98% | match |

All four README headlines match: selected M1 test H=3 BSS 13.843%, Brier 0.142332, unselected M1-full BSS 14.059%, and D1 test coverage 75.98%. All 40 generated table rows, beyond these spot checks, equal the main report. An initial byte/text equality check differed only because command output used CRLF; comparison by lines gives true.

## Browser verification

| Mode | Viewports | Executions | Retries / skips | Exit | Actual output |
|---|---|---:|---|---:|---|
| Live, clone FastAPI | 1440×900 and 390×844 | 24/24 passed | 0 / 0 | 0 | `24 passed (20.3s)` |
| Mock | 1440×900 and 390×844 | 24/24 passed | 0 / 0 | 0 | `24 passed (17.0s)` |

These are the unchanged 12 workflows in both viewports. They verify all forecast polygons, no-data polygons, horizon paint changes, time scrubbing/stepping/playback, selected-raion values, history interaction, sorted list, every displayed onset/duration score, model selection, latest mode, deep-link reloads, and layout. Error capture remained enabled. There were no test failures, retries, or skipped workflows, and the earlier ResizeObserver error did not recur in these runs.

Artifacts stay under clone `web/test-results/report/{live,mock}/` and `web/test-results/artifacts/{live,mock}/`. I opened the four live full-page screenshots, rather than relying only on assertions:

- `artifacts/live/dashboard-clicking-a-polyg-8930a-replay-outcomes-and-history-desktop/map.png`
- `artifacts/live/dashboard-clicking-a-polyg-8930a-replay-outcomes-and-history-mobile/map.png`
- `artifacts/live/dashboard-map-selected-pan-af504-wport-and-deep-links-reload-desktop/report.png`
- `artifacts/live/dashboard-map-selected-pan-af504-wport-and-deep-links-reload-mobile/report.png`

They show the requested black UI, intact Ukraine geometry, amber probability scale, no-data regions, responsive stacked mobile panel, and report tables with their own horizontal scroll regions. There is no horizontal page overflow at either size. Reliability labels remain separate from the model curves. The corrected P50 mean absolute error wording is visible.

Manual T3 checks used the clone's IPv6 frontend and real backend:

| Case | Observation |
|---|---|
| Active alert after predicted median | Kryvorizkyi at August 1 03:00Z shows 0.12 / 0.51 / 0.91 and "The predicted median end passed 12 min ago. The alert is still active." |
| Inside outage | August 30 12:00Z shows the explicit outage/unscored notice, unknown alert status, three unknown outcomes, zero actual-onset classes, zero active dots, and a history gap band. Selection remains white, AUD-05. |
| Exact recovery boundary | September 2 14:00Z switches to "History recovering after outage. Forecasts here are not scored." |
| Before outage, horizon crosses gap | August 29 03:00Z, H=12, correctly shows unknown 3h/12h outcomes, but no unscored notice, AUD-04. |
| Latest | September 7 05:00Z displays 08:00 Kyiv, recovery notice, and the missing-running-alert snapshot limitation. Replay outcomes are hidden. |

Additional inspected screenshots are `web/test-results/audit-outage-desktop.png` and `audit-outage-mobile.png`; `audit-pre-outage-mobile.png` records the pre-gap hour. E2E screenshots have the exact CSS viewports and full-page dimensions. T3 exported its desktop screenshot at 1280×800 despite a 1440×900 CSS viewport and its mobile screenshot at double density; the e2e captures establish the requested layout dimensions.

## Shutdown and ownership

Sent Ctrl+C to the audit Vite session and audit FastAPI session. Uvicorn reported clean application shutdown. The audit browser tab was closed. A listener query afterwards found neither port 8000, port 5187, nor the clone's IPv6 port 5173. Only the pre-existing listener process on `127.0.0.1:5173` remained. It was not stopped or changed.

The clone is retained with source, rebuilt data, fitted artifacts, exact metrics, raw command logs, probe code/results, traces, reports, and screenshots. The main repository's only audit write is this file. No commits, branch changes, fixes, or dependency additions were made.

## Delivered

`docs/audit.md` in the main repository. Clone-only evidence and helper paths are listed above.

## Verified

Literal README rebuild through serve and dev; exact source SHA-256; 36 onset and four duration score rows with maximum absolute numeric difference 0; 30 record-prefix/saved-model/served probes with maximum difference 0; 10 inclusive duration probes; validation-only I/O gate; zero forbidden scored gap rows; ten report spot checks and all headline/table values; 49 passing Python tests; passing Ruff, frontend check/build; 24 live and 24 mock browser executions; desktop/mobile screenshot inspection; server shutdown.

## Decisions/assumptions

Verdict applies to the retrospective test replay with its disclosed source limitations. Validation cross-fitting is accepted as the implemented diagnostic protocol, but not as a chronological forecast experiment. Validation probe fold fits were reconstructed because they were not persisted. Exact reproduction was tested on the existing build machine with cached tools, not a blank OS.

## Open issues

AUD-01 through AUD-06 remain for their owners. No fixes were applied.

## Contract change requests

Lead clarification requested for the scope of the blanket as-of claim: record-prefix feature causality versus chronological model/calibrator fitting, especially validation outputs. The inclusive duration-onset rule is already explicit and needs no new contract change.

## Dependencies added

None.

## Post-audit verification

Rechecked on 2026-10-12 after A3's phase 3 display changes. The original findings above remain the audit record. This follow-up updates the status of AUD-04 and AUD-05 for the current UI.

**AUD-04:** I checked `2026-08-29T03:00Z` in the T3 browser at 1440×900 and 390×844, selecting Kryvorizkyi and switching H=1/3/12. H=1 has no unscored notice, because its window ends before the outage. H=3 and H=12 each show "The [3 or 12] h forecast window reaches into the source outage. This forecast is not scored." The API-backed panel retains `1 h Yes`, `3 h Unknown`, and `12 h Unknown` at every horizon. Known active status and all 73 active dots remain visible. The warnings agree with an independent comparison against `/api/meta.data_gaps`. Neither viewport has horizontal overflow. AUD-04 is resolved for this case.

**AUD-05:** At `2026-08-01T01:00Z`, H=3, I selected Boryspilskyi beside Cherkaskyi in the browser at 390 px. The adjacency file confirms they share a boundary. The API gives Boryspilskyi `actual_onset_3=false` and Cherkaskyi `actual_onset_3=true`. Boryspilskyi has a violet `#bda4ff` selection ring and black base stroke; Cherkaskyi has the white `#f0efec` onset stroke. Selecting Cherkaskyi keeps its white onset stroke and adds the separate violet ring. The two legend keys are distinct, and the page stays within 390 px. The automated outline workflow also passes at both suite viewports. AUD-05 is resolved for this case.

`web/tests/dashboard.spec.ts` now owns its probability display oracle, with no import from `format.ts`: values below 0.01 use `<0.01`, values above 0.99 use `>0.99`, and the closed interval between those thresholds uses two decimals. Map ARIA, panel, tooltip, list, history table/readout, and history ARIA expectations follow that rule. Score expectations retain their original formatting.

The added API-driven extreme workflow selected real Dnistrovskyi `p_1=0` and Iziumskyi `p_12=1` at `2026-08-01T02:00Z`. Both wordings passed in map ARIA, tooltip, panel, history ARIA/readout/table, and list at both viewports. Mock data has no extremes at that hour, so each missing bound produces an explicit coverage annotation and console log. Those subchecks are disclosed rather than counted through a vacuous expectation.

Real Playwright summaries, with 14 workflows at 1440×900 and 390×844, zero retries, zero failed executions, and zero whole-test skips:

| Mode | Command from `web/` | Actual summary | Exit |
|---|---|---|---:|
| Mock | `bunx playwright test` | `28 passed (18.7s)` | 0 |
| Live | `$env:E2E_TARGET='live'; bunx playwright test` | `28 passed (21.9s)` | 0 |

Reports and traces are retained under `web/test-results/report/{mock,live}/` and `web/test-results/artifacts/{mock,live}/`. Independently inspected screenshots are [the H=12 pre-outage notice](<local-browser-artifacts>/browser-screenshot-127-0-0-1-mv553a63-7375b491.png) and [mobile selection beside actual onset](<local-browser-artifacts>/browser-screenshot-127-0-0-1-mv554oye-f71cbb6a.png).

Strict TypeScript checking of the test files and Playwright configuration passed, exit 0. I stopped the FastAPI server started with `uv run airalert serve --port 8000` and the manual Vite server on 5188; FastAPI reported "Application shutdown complete". Both Playwright Vite processes on 5187 were also removed by their test runners. The final listener check found none on 8000, 5187, or 5188. The pre-existing listener process on 5173 remains untouched. No product files, fixtures, models, scores, contracts, or dependency manifests were changed. No commits or branch changes were made.
