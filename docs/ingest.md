# v2 ingest report, I1 round 3

## Persistent local alerts

A live community/hromada origin active for more than 72 hours is excluded
from forecast records, every record-derived feature, active/active-since and
remaining-duration inputs. Exactly 72 hours still counts. District and oblast
origins have no age exclusion. `PERSISTENT_LOCAL_ALERT_H` in `config.py` is the
single threshold. Canonical CSV/archive
history and labels are unchanged. The online view also excludes unresolved
persistent hromada continuations already present in canonical archive data.
This avoids treating a permanently alerted
frontline community as seven months of raion-wide coverage, a state absent
from the ended-only training history.

The live map still displays persistent origins. Separately for red and yellow,
its since time is the earliest active district/oblast layer start if there is
one; community starts are used only when that layer has no broader origin.

Successful origin snapshots received strictly before the forecast hour govern
live availability. An absent origin closes at the first successful absent
receipt, including after downtime. Failed polls do not clear alerts. Continuous
inexact siren level times are live onset proxies; a color change does not add
a start, and a reappearance after an absent poll is a new spell. These proxies
never become onset labels. Receipts before the reconciled snapshot boundary
are superseded by canonical history. New successful observation files have
receipt sidecars, including empty polls; legacy empty files use write time.
Archive-censored coverage closes when a successful poll has no eligible origin
at its raion and source level. A community alert cannot keep an absent district
or oblast origin open.

### Archive completeness and publication, Y1

V2-03 adds a check before writing a day's normalized partition. Count unique
national air-raid origin event IDs whose intervals overlap that Kyiv day,
including alerts continuing from earlier days and excluded permanent origins.
Mirrored copies and repeated event IDs do not increase this count. Require at
least **100 records** and **20% of the median of the preceding 14 reconciled
days**. Use all available preceding days during bootstrap; the absolute floor
still applies when there is no baseline. Accepted counts and check inputs are
saved in `ledger.day_checks`. Legacy ledgers derive the baseline from their
decoded raw responses, or durable partitions when the raw cache is absent.
Those partitions omit the excluded permanent origins, so that fallback is
slightly more permissive.

These are conservative outage thresholds. The 54 cached sample days inspected
for Y1 have a minimum of 374 unique national air-raid records; the latest
14-day median is 567. Across the latest 60 complete days, the minimum is 219
raion episode starts on August 15. A genuinely quiet national day is
essentially impossible in these data. The check catches empty and severely
truncated responses while allowing a large real decrease in daily activity.
It is not a claim to detect every missing alert.

Where successful minute observations exist for the day, every observed raion
and receipt time must also have archive air-raid coverage. Use `observed_at`,
not the inexact live onset proxy, and allow 120 seconds around archive starts
and finishes to match the reviewed source timing tolerance. Broad archive
coverage can satisfy a local observation; colors are not completeness labels.
Receipt coverage uses sorted interval searches rather than a row-by-row scan.
A contradiction fails the day even when its national count clears both floors.

An `IncompleteArchiveError` records the whole Kyiv day in `ledger.gaps`, with
UTC bounds and an explicit reason. `/api/system.coverage_gaps` exposes those
bounds. No candidate partition or canonical records are published, the
watermark stays at the last accepted day, and catch-up stops before later
days. The next catch-up retries the failed day with a fresh upstream response,
so a suspicious but syntactically valid cached response cannot prevent repair.

V2-04 uses one `canonical_publication` lock for minute and nightly writers.
It covers each shared file's read, merge, staging, replacement and rollback,
including hazards and severity transitions. It combines a process-local lock
with an OS file lock at `data/processed/.canonical.lock`, so a separate CLI
also participates. Large records/episodes staging and model fits happen
outside this lock. Atomic file replacement still protects readers; the lock
also prevents one writer from erasing another writer's newer row.

V2-06 decodes and validates compact archive events before caching a response.
Cache hits are validated too; an invalid legacy cache is bypassed and fetched
again. HTTP/JSON/schema-invalid responses do not replace a valid raw cache.
The normal two-second request spacing and descriptive User-Agent still apply.

### Failed secondary polls, Y1

V2-05 supersedes the earlier secondary-failure policy below. A failure in
siren, enabled `aiu_api`, or ubilling retains the **entire last complete live
snapshot**, including Sevastopol's inherited red layer, both colors, source
contributions and the last successful `fetched_at`. It marks the snapshot and
polygons stale, and records the failed source in local `state.sources`.
The existing API exposes the top-level stale flag; no per-unit API field or
schema change is needed. If no complete snapshot exists, coverage is unknown
and stale until all enabled sources succeed.

This intentionally treats the display as one complete receipt. A fresh
primary response cannot certify the absent secondary source's units as fresh.
Failed attempts produce no successful observation receipt and cannot close
live origins in forecast inputs. Raw responses already received may remain
archived. The next complete poll clears stale and can apply a source-confirmed
all-clear. A disabled optional API is not a failure.

**DATA_GAPS recommendation:** empty `config.DATA_GAPS`. The accepted archive
fills the August 29 to September 2 outage with plausible daily activity, all
44 complete Kyiv days through October 11 published successfully, and the
ledger has no gaps. I1 did not edit the lead-owned config.

**New snapshot:** `2026-10-11T21:00:00Z`, midnight at the start of October 12
in Europe/Kyiv. Sources are `csv` and `aiu_web`; cutover is
`2026-08-29T06:00:00Z`; reconciled through `2026-10-11`; coverage gaps are
empty.

`data/processed/snapshot.json` now contains:

```json
{
  "snapshot": "2026-10-11T21:00:00Z",
  "csv_sha256": "b396f87696243069eec0219880e87dcd642b20beb0a7bae0d0e1f61e1ed20ec4",
  "regime_switch": "2025-08-14",
  "sources": [
    "csv",
    "aiu_web"
  ],
  "cutover": "2026-08-29T06:00:00Z",
  "coverage": {
    "reconciled_through": "2026-10-11",
    "gaps": [],
    "archive_gate_passed": true,
    "csv_authoritative_before": "2026-08-29T06:00:00Z",
    "archive_through_exclusive": "2026-10-11T21:00:00Z"
  }
}
```

I1, 2026-10-12. The lead accepted the archive and lowered only the daily gate
requirement from 92% to 88%. The 95% aggregate requirements remain.
The real backfill, full Python suite, feature CLI and live poll have run.

## Delivered

- `src/airalert/ingest.py`: the daily recall/precision floor and recorded
  validation threshold are 88%; aggregate recall/precision stay at 95%.
- `tests/test_ingest.py`: the existing gate test now checks acceptance at
  exactly 88%, rejection below it, separate precision, and count-weighted
  aggregate failures.
- `docs/ingest.md`: this final coverage and verification report, replacing
  the blocked round-2 handoff.
- `data/live/`: updated validation, 44 accepted normalized Kyiv-day
  partitions, ledger, raw responses and a fresh successful live state.
  `round3-before.json`, `round3-after.json` and
  `round3-verification.json` preserve the measured handoff evidence.
- `data/processed/records.parquet`, `episodes.parquet`,
  `snapshot.json`, `hazards.parquet` and `severity_transitions.parquet`:
  reconciled publication with separate hazards and live transition evidence.
- `data/processed/features/{train,val,test,latest}.parquet`: rebuilt through
  the new snapshot by the requested feature CLI.

No new edits to source adapters, `data.py`, config, CLI, models, frontend
or frozen v1 reports were needed in round 3. Previous rounds' source adapter
and `data.py` changes remain present. No commits or git state changes.

## Verified

### Commands and results

| Actual command or inspection | Actual result |
|---|---|
| `uv run --no-sync python -m airalert.ingest validate --days 2026-08-20 2026-08-21 2026-08-22 2026-08-23 2026-08-24 2026-08-25 2026-08-26 2026-08-27 2026-09-03 2026-09-04 2026-09-05 2026-09-06` | Exit 0; `{"archive_gate_passed": true}`. Measured agreement is unchanged; the new daily floor passes. |
| `uv run airalert backfill` | Exit 0. Ledger watermark `2026-10-11`, 44 accepted consecutive days, `gaps: []`; no failed reconcile day. The CLI itself prints no output. |
| `uv run pytest` | `106 passed, 1 warning in 91.33s (0:01:31)`. The warning is the existing Starlette/httpx deprecation. |
| `uv run airalert features` | Exit 0. Train 4,346,832; val 174,216; test 203,966; latest 119. `Elapsed: 16.69s`. |
| `uv run airalert live-once` | Exit 0. Persisted successful state at `2026-10-12T16:46:57.340822Z`; 139 polygons; `stale=false`. The CLI itself prints no output. |
| `uv run --no-sync ruff check src/airalert/data.py src/airalert/ingest.py src/airalert/sources tests/test_ingest.py --output-format concise` | `All checks passed!` |
| `git diff --check -- src/airalert/data.py` | Exit 0; only the repository's LF-to-CRLF notice. |
| Canonical Parquet and snapshot inspection | Exactly seven contracted record columns, UTC microsecond start/end timestamps, 119 raions, zero negative durations, zero starts at or after snapshot, zero known ends after snapshot. |
| Cutover check against a fresh expansion of the saved CSV | All 873,696 pre-cutover CSV starts and their geographic keys are identical; all post-cutover records are archive-sourced. Zero source/cutover violations. |
| Feature Parquet inspection | Counts and hourly bounds below agree with the CLI; 24 feature columns per split; every latest label is null. |
| Final report/artifact consistency check | Snapshot JSON equals the published file; all 53 daily-count rows match the saved evidence; ledger and Parquet row counts match; all six rule-9 sections are present. |
| Privacy and whitespace check over the two changed Python files and this report | Zero email, local-user-path or literal Bearer-token pattern matches; zero trailing-whitespace lines. |

Network requests use the adapter's locked minimum two-second start spacing
and descriptive `AirAlertForecaster-Ingest/2.0` User-Agent. Twenty validation
context days were cached already. Backfill fetched the other 34 days,
September 8 through October 11, and gzip-cached their raw responses.
The shortest observed interval between those new cache completions was
4.45 seconds. Cache completion times are supporting evidence, not the rate
limiter's clock.

### Counts and coverage

| Canonical artifact | Before round 3 | After round 3 |
|---|---:|---:|
| Expanded records | 875,633 | 894,563 |
| Merged episodes | 431,094 | 447,999 |
| Forecast universe | 119 | 119 |
| Snapshot | 2026-09-07T05:00:00Z | 2026-10-11T21:00:00Z |
| Adopted cutover | None | 2026-08-29T06:00:00Z |
| Ledger watermark | None | 2026-10-11 |
| Accepted archive days | 0 | 44 |

Records increased by 18,930 and episodes by 16,905. The final records contain
873,696 CSV rows and 20,867 normalized archive rows. Thirty episodes are
censored at the reconciled boundary. No incomplete October 12 archive day
was adopted.

| Interval or use | Source | Published coverage |
|---|---|---|
| History before 2026-08-29T06:00:00Z | CSV | CSV starts remain authoritative, including exact duplicates. Reviewed overlapping archive records can correct continuing CSV ends. |
| [2026-08-29T06:00:00Z, 2026-10-11T21:00:00Z) | `aiu_web` | Air-raid records and merged target episodes; all complete Kyiv days through October 11 accepted. |
| Current live observations | `siren` | Minute-poll display state, observed-at feature inputs and explicit level-created transition times; they do not advance the historical snapshot. |
| Optional supported live source | `aiu_api` | Disabled in this actual poll because no token is configured. |
| Sevastopol display only | ubilling default feed | Available in the real poll; inherited permanent red alert, excluded from forecast records and labels. |

There are no failed days and no remaining ledger coverage gaps. Snapshot
coverage is reconciled historical coverage, not a claim that every upstream
alert is perfect or that current live alerts are represented in the archive.

### Outage plausibility

These are project-merged episode starts across the fixed 119-raion universe,
attributed to Europe/Kyiv calendar dates. A broad alert contributes one start
for each covered raion. They are not counts of distinct national incidents.

| Kyiv day | Before, CSV snapshot | After backfill | Raions with starts | Oblasts with starts |
|---|---:|---:|---:|---:|
| 2026-08-27 | 536 | 536 | 91 | 20 |
| 2026-08-28 | 462 | 462 | 87 | 20 |
| 2026-08-29 | 162 | 435 | 81 | 16 |
| 2026-08-30 | 0 | 387 | 80 | 19 |
| 2026-08-31 | 0 | 360 | 82 | 19 |
| 2026-09-01 | 0 | 380 | 82 | 20 |
| 2026-09-02 | 158 | 419 | 92 | 21 |
| 2026-09-03 | 428 | 432 | 96 | 21 |
| 2026-09-04 | 386 | 385 | 75 | 17 |

The five repaired days have 360 to 435 starts per day across 80 to 92 raions
and 16 to 21 oblasts. Their mean is 396.2 starts/day, compared with 453.75
across the four neighbouring days shown. Variation of this size also occurs
outside the outage. August 28 has 462 starts, the mixed cutover day has 435,
and the first full archive day has 387. There is no empty run or obvious
jump at the cutover. I consider the outage plausibly filled and recommend
emptying `config.DATA_GAPS` for v2. The accepted source imperfections below
remain; these counts cannot prove perfect per-alert completeness.

### Every daily episode-start count

All 53 Kyiv calendar days from August 20 through yesterday, October 11:

| Kyiv day | Episode starts | Record authority |
|---|---:|---|
| 2026-08-20 | 348 | CSV |
| 2026-08-21 | 239 | CSV |
| 2026-08-22 | 363 | CSV |
| 2026-08-23 | 281 | CSV |
| 2026-08-24 | 375 | CSV |
| 2026-08-25 | 372 | CSV |
| 2026-08-26 | 336 | CSV |
| 2026-08-27 | 536 | CSV |
| 2026-08-28 | 462 | CSV |
| 2026-08-29 | 435 | CSV before 09:00 Kyiv; archive after |
| 2026-08-30 | 387 | Archive |
| 2026-08-31 | 360 | Archive |
| 2026-09-01 | 380 | Archive |
| 2026-09-02 | 419 | Archive |
| 2026-09-03 | 432 | Archive |
| 2026-09-04 | 385 | Archive |
| 2026-09-05 | 391 | Archive |
| 2026-09-06 | 328 | Archive |
| 2026-09-07 | 342 | Archive |
| 2026-09-08 | 469 | Archive |
| 2026-09-09 | 378 | Archive |
| 2026-09-10 | 393 | Archive |
| 2026-09-11 | 352 | Archive |
| 2026-09-12 | 428 | Archive |
| 2026-09-13 | 313 | Archive |
| 2026-09-14 | 279 | Archive |
| 2026-09-15 | 508 | Archive |
| 2026-09-16 | 386 | Archive |
| 2026-09-17 | 557 | Archive |
| 2026-09-18 | 412 | Archive |
| 2026-09-19 | 441 | Archive |
| 2026-09-20 | 533 | Archive |
| 2026-09-21 | 452 | Archive |
| 2026-09-22 | 508 | Archive |
| 2026-09-23 | 551 | Archive |
| 2026-09-24 | 450 | Archive |
| 2026-09-25 | 533 | Archive |
| 2026-09-26 | 429 | Archive |
| 2026-09-27 | 368 | Archive |
| 2026-09-28 | 431 | Archive |
| 2026-09-29 | 476 | Archive |
| 2026-09-30 | 384 | Archive |
| 2026-10-01 | 372 | Archive |
| 2026-10-02 | 379 | Archive |
| 2026-10-03 | 347 | Archive |
| 2026-10-04 | 372 | Archive |
| 2026-10-05 | 407 | Archive |
| 2026-10-06 | 357 | Archive |
| 2026-10-07 | 469 | Archive |
| 2026-10-08 | 443 | Archive |
| 2026-10-09 | 549 | Archive |
| 2026-10-10 | 615 | Archive |
| 2026-10-11 | 604 | Archive |

Later context can change episode unions when a previously open alert receives
a real end. The published hybrid counts on September 3 to 6 need not equal
the archive-only validation counts, which used the August 19 to September 7
context. The validation evidence itself was retained unchanged.

### Severity

The first explicit colour in the adopted records is yellow, starting at
`2026-09-07T06:09:12Z`, or 09:09:12 Kyiv on September 7. No retained record
explicitly reports red through October 11. This is the first observed colour
date in this backfill, not a verified global provider rollout date.

| Source | Red | Yellow | Unknown | Total |
|---|---:|---:|---:|---:|
| CSV | 0 | 0 | 873,696 | 873,696 |
| Archive | 0 | 10,876 | 9,991 | 20,867 |
| Total | 0 | 10,876 | 883,687 | 894,563 |

An omitted archive level stays unknown. It is not inferred as red from the
map's default rendering. Explicit `al=0` decodes as red and `al=1` as
yellow. Scalar archive colours do not establish when a severity transition
occurred, so the historical rows do not manufacture transition timestamps.

### Downstream features

The requested `airalert features` CLI builds the existing main feature splits.
Its test table now extends to the new snapshot minus the configured 12 hours.
It does not invoke M2's separate `build_v2()` builder. I1 changed no main or
`V2_*` split constant, and did not score test data or regenerate frozen v1
metrics.

| Split | Rows | First forecast UTC | Last forecast UTC |
|---|---:|---|---|
| Train | 4,346,832 | 2022-04-01T00:00:00Z | 2026-05-31T23:00:00Z |
| Validation | 174,216 | 2026-06-01T00:00:00Z | 2026-07-31T23:00:00Z |
| Test | 203,966 | 2026-08-01T00:00:00Z | 2026-10-11T09:00:00Z |
| Latest | 119 | 2026-10-11T21:00:00Z | 2026-10-11T21:00:00Z |

Build time was 16.69 seconds. All four files have 24 `f_*` columns.
The full suite includes the focused ingest gate, leakage, live, API and split
tests. Production model fitting, v2 scoring and browser verification belong
to the downstream agents.

### Actual live handling

The successful poll at `2026-10-12T16:46:57.340822Z` has 139 display
polygons, 37 active red and 28 active yellow polygons, with 119 observed, 19 inherited
and one unknown. Red and yellow counts may overlap. Sources report
`siren=observed`, `aiu_api=disabled`,
`ubilling=reported_permanent_alert`; `stale=false`.

- Sevastopol, shape `22097711B55202998529821`, has no forecast raion ID.
  The real secondary feed supplied inherited red since
  `2022-12-10T22:22:00Z`; yellow is unknown. Its offset-free reported time
  `2022-12-11 00:22:00` is interpreted in Europe/Kyiv under the lead policy.
- Chornobyl, shape `22097711B27688590731099`, has no forecast raion ID.
  Both layers and coverage are unknown. The captured primary response has
  no Kyivska oblast-wide origin alert, UID 14. Raion alerts in Kyivska do
  not paint this zone. The existing test also checks inheritance when a
  genuine Kyivska oblast-wide alert exists.

The historical snapshot stayed `2026-10-11T21:00:00Z` after live polling.
The secondary Sevastopol evidence never enters records, labels, transitions
or the live feature observation store.

## Decisions/assumptions

### Binding lead decisions, 2026-10-12

1. **Round-3 archive acceptance.** The lead accepts aggregate episode recall
   96.55%, precision 97.82%, median matched duration difference zero and P90
   absolute difference four seconds. The archive/CSV record-start ratio is
   0.973137. One weaker day out of twelve is a documented imperfection.
   The daily recall/precision floor becomes 88%; both aggregate floors
   remain 95%. August 20's 89.66% recall now passes. This supersedes the
   blocked round-2 decision.
2. Adopt cutover `2026-08-29T06:00:00Z`. CSV starts are authoritative before
   it; the accepted archive repairs the outage and supplies complete Kyiv
   days through yesterday.
3. Within one raion, archive air-raid starts in an inclusive 120-second window
   collapse to the earliest start, latest known end or null if any member is
   open, and finest level. Windows anchor at the earliest start; 121 seconds
   does not join by transitive chaining. Origin-event upserts precede
   normalization, and normalization runs across day boundaries.
4. The gate compares project-merged episode starts one-to-one within a raion
   at inclusive 120-second tolerance. Both sources retain boundary context.
   Recall is attributed to the CSV start day and precision to the archive
   start day. No matching tolerance or aggregate requirement was relaxed.
5. Use explicit archive colours only. Missing levels remain unknown.
   Daily scalar colours are not historical transition times or proof of
   as-of availability. Live inputs retain their `observed_at` clock.
6. Sevastopol may show the secondary source's reported permanent alert as
   inherited red. Y1's V2-05 repair supersedes the former unknown-on-secondary-
   failure behavior: any enabled source failure now preserves the complete
   prior snapshot as stale. Chornobyl inherits only Kyivska oblast-wide
   air-raid alerts.

The normalization window and thresholds remain local to I1's code as the
previous ownership workaround. Only the lead can move constants into config.

### Gate evidence and accepted imperfections

The cached twelve-day validation rerun yielded:

| Episode gate | Measured | Required | Result |
|---|---:|---:|---|
| Aggregate recall | 4,220 / 4,371 = 96.55% | >= 95% | Pass |
| Aggregate precision | 4,214 / 4,308 = 97.82% | >= 95% | Pass |
| Lowest daily recall, August 20 | 312 / 348 = 89.66% | >= 88% | Pass |
| Lowest daily precision, August 27 | 524 / 540 = 97.04% | >= 88% | Pass |
| Archive adoption | Lead accepted | Passing updated gate | Published |

Six Vinnytsia matches straddle Kyiv midnight by 80 or 81 seconds, explaining
why matched CSV and archive totals differ. All 4,220 matched CSV episodes
have known ends in both sources. Signed and absolute median duration
differences are zero; P90 absolute difference is four seconds. These duration
statistics describe matched episodes only.

Normalized record starts are 4,818 archive versus 4,951 CSV, a ratio of
0.973137, about 2.7% fewer archive starts. The episode-start ratio is a
different quantity, 4,308 / 4,371. The gate does not hide this lower raw
start count behind the episode agreement.

August 20 has 36 unmatched CSV episode starts:

| Oblast or city | Misses | CSV episode source levels |
|---|---:|---|
| Cherkaska | 4 | 4 raion |
| Kharkivska | 16 | 15 raion, 1 hromada |
| Kirovohradska | 1 | 1 raion |
| Kyiv city | 1 | 1 oblast |
| Kyivska | 1 | 1 raion |
| Mykolaivska | 1 | 1 raion |
| Odeska | 2 | 2 raion |
| Poltavska | 2 | 2 raion |
| Sumska | 4 | 4 raion |
| Zhytomyrska | 4 | 4 raion |
| Total | 36 | 34 raion, 1 hromada, 1 oblast |

Twenty-five misses cluster just after Kyiv midnight. Twenty of those have
archive episode starts 8.5 to 9.9 minutes earlier; the remaining five have
offsets of about 4.5 to 40.5 minutes. For example, the four Cherkaska starts
differ by 544 seconds, and Kyiv city by 593 seconds. Their common timing
suggests a shared boundary/continuity discrepancy, but does not establish
an upstream incident's cause.

Ten other August 20 misses are Kharkivskyi starts absorbed by an unresolved
Lypetska community record, origin UID 1284, event 185413, open since
`2026-03-10T14:01:38Z`. One further miss is Kupianskyi continuity, with the
nearest archive start 2,593 seconds away. Therefore this is not one proven,
isolated source hiccup. It combines the shared midnight cluster with a
long-running unresolved record and one further continuity difference.

Across all twelve days there are 151 CSV-only and 94 archive-only episode
starts. The Lypetska continuation absorbs 86 CSV starts in the validation
context. Local continuity also differs in Oleksandriiskyi, Nikopolskyi and
Pokrovskyi. I1 did not synthesize missing ends, discard open origins or widen
the approved tolerance.

Full per-raion daily counts, disagreements, durations and CSV fingerprint
remain in `data/live/validation.json`; it retains the round-1 raw totals too.

### Nightly catch-up

`catch_up(now)` requires an aware datetime and resumes from the ledger
watermark plus one day through yesterday in Europe/Kyiv. The same function
is ready for startup and midnight scheduling. It processes missing complete
days in order and refuses skipped or incomplete days. It uses semantically
validated raw caches when available, checks per-day completeness before
publication, and later daily partitions can update an earlier origin event's end.

Before reconciliation, the accepted episode gate and current CSV hash must
still agree. With a ledger already present, its chosen cutover remains in
force even after the lead empties `DATA_GAPS`; the config fallback is used
only when initializing a new ledger.

Each day is written as a durable partition, origin-upserted with all accepted
partitions, normalized and published with rebuilt records, episodes, hazards,
transitions and snapshot. Known ends beyond the reconciled boundary are
masked. Staged files replace the canonical artifacts; snapshot and ledger
commit after data publication, with rollback on a caught publication error.
The watermark advances only after publication succeeds.

On fetch, decode, geography or publication failure, the day is recorded in
`ledger.gaps` with its Kyiv-day UTC bounds and error type. Catch-up stops,
leaves the watermark at the last successful day and resumes that failed day
on the next call. There is no successful-looking jump over a failed day.
Schema-invalid caches are bypassed automatically. A failed completeness check
forces a fresh fetch on the next catch-up; manual cache deletion is unnecessary.

I1 owns this ingestion step. S1's scheduler must invoke the downstream
feature rebuild, production refit, forecasts and rolling scoring after
catch-up; these operations are not performed inside `catch_up`.
`live_state(now)` is independent. Any enabled source failure keeps the previous
polygons and successful fetched time, marks them stale and never clears alerts.

## Open issues

- The lead must remove the repaired static `DATA_GAPS` entry or integrate
  data-driven coverage. The current static mask can still exclude repaired
  rows until that integration.
- The public day archive is a private, versioned upstream schema. Unknown
  hazards, levels, compact timestamps or geography fail the day rather than
  silently lose data. The accepted 2.7% lower normalized record-start count
  and August 20 discrepancies remain documented source imperfections.
- Alerts that begin and end between 60-second live polls may be missed by the
  live observation store. Historical nightly reconciliation can recover
  archive events but cannot invent the missed observation availability.
- The supported authenticated API remains untested in a real token-enabled
  poll. No token was configured, read into a tracked artifact or logged.
- Archive scalar colours have incomplete coverage and no exact transition
  history. No explicit red records were present in the adopted interval.
  Original siren starts can be inexact; downstream live features must respect
  `observed_at` and `start_is_exact`.
- The unresolved Lypetska end and other continuity differences remain as
  supplied upstream. No production model, v2 score report or rolling live
  track record was regenerated by I1.

## Contract change requests

- **Empty `config.DATA_GAPS` for the repaired outage.** The lead-authorized
  source gate now passes, every outage day published, counts are plausible
  and final ledger gaps are empty. Keep frozen v1 reports unchanged.
- Preserve live `observed_at` and nullable original-onset semantics in the
  S1/M2 handoff. Sevastopol's secondary feed is display evidence only.
- Move the approved 120-second normalization/matching window and gate
  thresholds to lead-owned config during integration. This is unchanged
  from the previous ownership request.
- The main feature CLI still calls `build()`. If the intended CLI should
  build isolated v2 tables, the lead can route it to M2's supplied
  `build_v2()` or expose a separate command. I1 verified the requested
  command as it currently exists and changed no split constants.

## Dependencies added

None.

## Y1 ingest robustness report

### Delivered

#### V2-03, archive completeness

`src/airalert/ingest.py` rejects empty, severely truncated and live-contradictory
archive days before partition publication. Rejections preserve the watermark,
write an explicit day gap, stop later requests and force a fresh retry next time.
`ledger.day_checks` stores accepted volume and live-coverage evidence.
The guarding test is
`tests/test_ingest.py::test_archive_completeness_blocks_bad_days_and_retries`,
with empty, low-relative-volume, missing-live-raion and missing-live-interval
cases. `tests/test_live.py::test_failed_archive_day_is_visible_in_system_coverage`
checks the existing API status representation of the gap.

#### V2-04, shared canonical publication

`src/airalert/sources/storage.py` supplies one thread/OS publication lock.
`src/airalert/ingest.py` holds it across shared reads, merges, replacements and
nightly rollback. `src/airalert/scheduler.py` documents this boundary while
retaining independent poll and fit scheduling. The guarding test is
`tests/test_ingest.py::test_poll_rows_survive_interleaved_nightly_publication`,
run for both hazards and severity transitions. It pauses the real nightly
shared-file read, invokes the actual poll writers, resumes publication and
checks that both live rows survive.

#### V2-05, failed secondary polls

`src/airalert/ingest.py` retains the last complete snapshot on failure in
ubilling, enabled `aiu_api` or siren. Sevastopol's inherited red layer and
distinct authenticated-source contributions stay present, with top-level
stale and the old successful clock. No failed attempt becomes an absence
receipt. The rewritten guarding test is
`tests/test_ingest.py::test_failed_poll_keeps_layers_and_successful_clock`,
parameterized over all three sources.
`tests/test_live.py::test_secondary_failure_retains_alert_in_api_state_and_recovers`
checks API serialization, system staleness and recovery. No schema change or
`live.py` change was necessary.

#### V2-06, invalid archive cache recovery

`src/airalert/sources/aiu_web.py` validates compact events before caching and
validates existing cache hits. Legacy invalid hits trigger an upstream retry;
new invalid responses are not cached. The guarding test is
`tests/test_ingest.py::test_schema_invalid_archive_is_retried_after_upstream_recovers`,
with both a fresh invalid HTTP 200 and a preexisting bad cache. A corrected
second response is requested and accepted normally in both cases.

Matching operating guidance is updated in `docs/ingest.md` and
`docs/operations.md`. Y1 did not edit Y2's backtest, metrics or model files,
schemas, dependencies, frozen reports or git state.

### Verified

Before the repairs, `uv run --no-sync pytest tests/test_ingest.py -q --tb=line`
reproduced all four audit symptoms: empty/truncated/live-contradictory days
advanced through October 26, interleaved nightly publication erased a live
hazard, ubilling removed Sevastopol's red layer, and schema-invalid raw caches
blocked normal upstream retry. The original run had 10 failures and 10 passes,
including the new source-status assertion for primary failure.

The first full run of `uv run --no-sync pytest -q --tb=short` passed 160 tests
in 90.75 seconds. The final shared-tree run, including subsequent concurrent
Y2 tests and the additional receipt-interval case, returned **162 passed,
1 warning in 84.16s**. The warning is the existing FastAPI/Starlette test-client
deprecation. `uv run --no-sync ruff check .` returned **All checks passed!**
after Y2 resolved its transient line-length error. `git diff --check` also
passed. Y1 made no changes to Y2's files to obtain those results.

`uv run --no-sync airalert live-once` exited 0 and persisted a real successful
poll at `2026-10-12T19:30:55.749483Z`. Its state has 139 polygons, 52 red and
28 yellow, `stale=false`, `siren=observed`, `aiu_api=disabled` and
`ubilling=reported_permanent_alert`. It produced a receipt-tagged observation
file. The CLI itself emits no stdout on success; these counts were read back
from its actual `data/live/state.json`.

`uv run --no-sync airalert backfill` exited 0 as a no-op catch-up. The ledger
still has cutover `2026-08-29T06:00:00Z`, watermark `2026-10-11`, 44 accepted
days and `gaps=[]`. The historical snapshot remains
`2026-10-11T21:00:00Z`. `GET /api/system` on the existing server returned
HTTP 200 with the same watermark, empty coverage gaps and `live_stale=false`.
The server was left running and was not restarted.

### Decisions/assumptions

The national floor is 100 unique air-raid events overlapping the day; the
relative floor is 20% of the median of up to 14 previous accepted days.
Bootstrap always enforces the absolute floor. Live comparisons use expanded
raion interval coverage at receipt time with 120-second tolerance. These
checks detect implausible completeness and concrete live contradictions,
not every small omission. They do not turn live proxies into historical labels.

The existing API has only one stale flag, so any enabled secondary-source
failure preserves the complete display rather than advertising a partially
fresh snapshot. New successful observation receipts wait until all enabled
sources succeed. Disabled optional credentials do not block primary polling.

### Open issues

The real authenticated adapter remains unexercised because it is disabled in
this environment; its failure/retention path is covered with real-source-shaped
fixtures. The existing long-lived server still needs the lead's normal restart
to load this patch during integration. Multi-file publication remains atomic
per file, so forced process termination can still require startup recovery.

### Contract change requests

Resolved during final integration. `ARCHIVE_MIN_NATIONAL_RECORDS`,
`ARCHIVE_BASELINE_DAYS`, `ARCHIVE_MIN_BASELINE_FRACTION` and
`ARCHIVE_LIVE_TOLERANCE_SECONDS` now live in `config.py`. Ingest imports them;
their values and behavior are unchanged. No API schema change is requested.

### Dependencies added

None. The cross-process publication lock uses the standard library.
