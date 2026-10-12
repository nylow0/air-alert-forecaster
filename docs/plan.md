# Air Alert Forecaster: the build plan

Published at https://a5f99090aaae9dcaad8479b273d99a69.llm-plans.com/ (text extracted from the HTML; the published page is the original).
Where this file and AGENTS.md disagree, AGENTS.md wins: it records the architect decisions made after the plan.


Air Alert Forecaster: build plan for a lead agent and its subagents

Plan / 2026-10-12 / KSE pet project

Air Alert Forecaster

A Python system that predicts air raid alerts in Ukraine for each raion. It gives the probability that an alert starts within the next 1, 3 or 12 hours, and estimates how long an alert will last once it starts. A black-themed Svelte dashboard replays the test period on a map. Every forecast uses only data published before the forecast time. Text signals from Telegram come in stage 2, after the alerts-only system works.

13hwall-clock budget

~130raions on the map

1·3·12honset horizons

13subagent briefs

ReaderLead agent that spawns and integrates subagents

Owner: project owner (decisions), lead agent (integration)

Hard ruleA forecast made at time t may only use data from strictly before t

Workspace. (currently empty)

01 Decisions
02 Verified facts
03 Targets & split
04 As-of rule
05 System
06 Models
07 Dashboard
08 Agent waves
09 Text research
10 Tests & done
11 Out of scope

01 / Settled with project owner

Decisions locked before any code

Don't reopen these. Raise anything that conflicts with them with the lead agent, and the lead agent takes it to project owner.

Language
English, Python 3.12 via uv
The code, report and UI copy are in English. Ukrainian appears only inside text-signal keyword lists.

Targets
(a) onset probability + (d) duration
(a) P(an alert starts in raion r within (t, t+H]) for H ∈ {1, 3, 12} h. (d) The P10/P50/P90 duration of an alert, predicted when it starts.

Spatial unit
Raion, with an oblast fallback gate
Raion is workable here. Before December 2025, an oblast-wide alert expands to every raion in that oblast. Text features stay at the national or oblast level and are copied to each raion, so text never has to be resolved at raion level. If more than 5% of dataset raions can't be matched to map boundaries at gate G1, switch the unit to oblast.

Alert source
Vadimkin official dataset
official_data_en.csv from Vadimkin/ukrainian-air-raid-sirens-dataset. We save a dated copy so every run uses the same data.

Text data
Stage 2, after the core works
Research starts in wave 1 so we know early whether text is feasible. We integrate it only after gate G4.

Shape
Python backend + Svelte dashboard
FastAPI serves forecasts computed in advance. The frontend uses project owner's usual stack: Svelte 5, Vite, TypeScript, Tailwind 4 and bun, with a full-black theme and Playwright end-to-end tests.

02 / Checked on 2026-10-12

Verified facts the plan depends on

Rows with an amber edge change how the code has to work.

CSV schema
oblast, raion, hromada, level, started_at, finished_at, source. level is one of oblast | raion | hromada. All times are UTC. The file is about 31 MB, starts 2022-03-15 and is updated daily (latest commit 2026-10-12).

Regime change
Since December 2025, alerts have mostly been issued per raion. Before that, they usually covered the whole oblast. The data therefore has two regimes. Agent A1 finds the exact switch date from the data. Models get a regime feature, and the test period falls entirely in the raion regime.

Missing permanent alerts
Luhansk oblast (since 2022-04-04) and Crimea (since 2022-12-10) have been under permanent alerts that the dataset doesn't list. Leave both out of the raion set.

Boundaries trap
geoBoundaries gbOpen/UKR/ADM2 has the 2006 raions (495 units), which is the wrong set. Use gbHumanitarian/UKR/ADM2 (OCHA COD-AB, 2022, 139 units, CC BY 3.0 IGO). Its polygons have about 4,600 vertices each on average, so simplify them before they reach the browser.

Volunteer dataset
volunteer_data_en.csv (eTryvoga) starts 2022-02-25 and is oblast-level only. Some records have naive=True, where the end time is a made-up start + 30 min. We don't use it for labels.

Tooling
uv 0.11 is installed and there's no system Python, so uv manages the interpreter. project owner's other projects use bun, Svelte 5, Tailwind 4, @fontsource-variable fonts and Playwright.

03 / What exactly is predicted

Targets, forecast clock and time split

Target (a) / onset

yH(r, t) = 1 if a merged alert episode in raion r starts in (t, t+H]

Forecasts are made every hour on the hour (UTC). Calendar features use Europe/Kyiv local time, so daylight saving is handled.

Episode: the union of all records that cover raion r. An oblast record covers every raion in the oblast, and a hromada record covers its parent raion. Intervals that overlap, or are separated by less than MERGE_GAP_MIN = 5, are merged into one episode.

One model per horizon. At serving time we enforce monotonicity: P(1h) ≤ P(3h) ≤ P(12h).

Target (d) / duration

log(minutes) for an episode, predicted when it starts (time s)

Outputs P10, P50 and P90. Scored with pinball loss, MAE of the median and how often the true duration falls inside the 80% interval.

Features may use only records with started_at ≤ s. How many raions light up in the minutes after s is future data.

Leave out of training any episode that hadn't ended by the snapshot time (right-censored).

Time split (forecast times)

TRAIN · oblast regime

raion regime

VAL

TEST

2022-04-01~2025-12 regime switch2026-06-012026-08-01snapshot − 12h

fit
tuning + isotonic calibration
evaluated once, at the end
embargo: drop training forecast times within H of the next split, because their labels look into it

Start at 2022-04-01 so every feature has a full 168 h of history. Agent C1 also compares training on the full history (with the regime flag) against training on the raion regime only, and keeps whichever scores better on validation.

04 / The as-of contract

Nothing from time t or later

Every feature function takes (data, t) and must return the same result when it's given the data as it stood at t. One test enforces this for both alert and text features (see section 10). These are the concrete ways to break the rule:

Using the end time of a running alert
If an episode is still running at t, its finished_at isn't known yet. Treat it as active with an unknown end. Clip "alert minutes in the last 24h" at t.

Overlapping labels across the split
A training forecast at val_start − 2h with H=3 has a label that looks into validation. The embargo removes these rows.

Climatology computed on all rows
Base rates per raion and hour, and any target encoding, are computed on the training window only. Validation and test only read them.

Raion universe chosen with hindsight
The raion list is fixed by geography and the exclusions, not by "raions that had alerts in the test period".

Edited text messages
Use each message's publish time and require date < t. Drop messages edited more than 10 minutes after publishing, because the edit may add hindsight.

Settings tuned on the test set
The test period is scored once, with backtest --final. Every tuning decision uses validation.

05 / Architecture

One pipeline, one thin API, one dashboard

The API only reads results that were computed in advance, so the UI never waits for a model. Each stage is a uv run airalert <cmd> subcommand that writes Parquet or JSON for the next stage.

fetchSaves a dated copy of the CSV, with its sha256, to data/raw/

episodesExpands records to raions and merges them. Writes episodes.parquet

geoWrites simplified raions.geojson, a name crosswalk and an adjacency list

featuresAs-of features and labels, one row per hourly forecast time and raion

train · backtestBaselines, logistic regression, LightGBM, the duration model. Writes metrics.json

precomputeForecasts for every hour in validation and test, plus the latest hour

serve · webFastAPI reads the Parquet files. The Svelte map replays them

Stage 2 branch: text ingest → text features (the same as-of contract, attached to oblasts or the whole country, then copied to raions) joins at features. A comparison run scores alerts-only and alerts+text models on exactly the same rows.

kse/
├─ pyproject.toml          # uv, py3.12, package "airalert"
├─ src/airalert/
│  ├─ cli.py               # one subcommand per stage
│  ├─ schemas.py           # pydantic API models (the source of truth)
│  ├─ data.py  geo.py  features.py  labels.py
│  ├─ models/ baseline.py onset.py duration.py
│  ├─ backtest.py metrics.py precompute.py api.py
│  └─ text/ ingest.py features.py     # stage 2
├─ tests/                  # focused tests, see section 10
├─ data/ raw/ processed/ geo/ text/   # git-ignored except geo
├─ reports/ metrics.json  figures/
├─ docs/ research/  report.md
└─ web/                    # Svelte 5 + Vite + TS + Tailwind 4, bun
└─ src/lib/types.ts     # hand-copied from schemas.py

API contract (lead writes it in wave 0)

GET /api/meta: snapshot date, split ranges, horizons, raion count

GET /api/raions: simplified GeoJSON whose features carry raion_id, oblast and name

GET /api/forecast?at=ISO: for each raion, p[1|3|12], active, active_since, end_p10/p50/p90, and in replay mode only, actual_onset[1|3|12]

GET /api/raion/{id}/history?from&to: probability series plus the real episodes

GET /api/report: metrics.json as-is

raion_id is <oblast-slug>/<raion-slug>, built from the dataset's English names, for example cherkaska/umanskyi. The map side gets matched to these ids, not the other way round.

06 / Models & metrics

Build up from baselines, and report the result honestly

The headline number is the Brier skill score against climatology at H = 3 h on the test set. If LightGBM doesn't beat the baselines, the report says so and the dashboard serves whichever model scores best.

ModelTaskWhat it isOwner

B0 climatologyonsetTraining-window base rate per raion × local hour × weekday/weekend, smoothed toward the oblast rateB2

B1 persistence-awareonsetLogistic regression on about 10 handpicked features. It shows how much of the skill simple structure already explainsC1

M1 LightGBMonsetOne model across all raions, one per horizon, with raion_id and oblast as categorical features. Isotonic calibration on validationC1

D0 empiricaldurationDuration quantiles from training data, per oblast × hour bucket × regimeB3

D1 LightGBM quantiledurationThree quantile models on log-duration, using only features known at the start timeB3

Raion features
active now, minutes since the last start and the last end
alert starts in the last 1/6/24/168 h
alert minutes in the last 24 h (clipped at t)
centroid latitude and longitude (a stand-in for distance to the front)

Spread features
share of adjacent raions active now
alert starts in adjacent raions in the last 30/60 min
share of the oblast's raions active now

Country & calendar features
number of raions active nationally, and raions with a start in the last 15/60 min (an alert covering most of the country at once usually means missiles)
Kyiv local hour and weekday, the regime flag

Metrics: Brier score, Brier skill score vs B0, log-loss, PR-AUC and a 10-bin reliability diagram for each horizon. Break results down by oblast and by whether the raion was quiet or active at t. There are about 130 raions × 36k hourly forecast times ≈ 4.7M rows per horizon. Compute features as arrays, with searchsorted on each raion's sorted start and end times. Avoid Python loops over rows.

07 / Frontend

A replay map: what the model said, and what happened next

The dataset updates once a day, so a "live" forecast would be up to a day out of date. The honest demo is a replay of the test period. At each hour, the map shows the forecast as it was made then, plus outlines of the raions where an alert actually started afterwards. A "Latest" mode shows the forecast from the most recent snapshot.

Air Alert Forecaster
ReplayLatest
1h3h12h
MapModel report

Selected raion
Kharkivskyi raion · Kharkivska

1h0.34

3h0.61

12h0.88

If active: expected end in 42 min (P10–P90: 18–110 min)

7-day chart: predicted probability vs the real alerts

▶2026-09-14 03:00 Kyivtest period

Build notes for A3
Load the frontend-design and dataviz skills before building any UI.
Draw raion paths as SVG with d3-geo, using a projection fitted to Ukraine. No map tiles or tile server.
Use a pure #000 background, a single-colour sequential scale for probability, and outlines to mark real alert starts.
Use a font with Cyrillic support (Onest, as in NMT). Make the layout work down to 390 px wide.

Model report page
Brier skill score per horizon for B0, B1 and M1
Reliability diagrams
Duration coverage and pinball loss
The text-vs-no-text comparison, if stage 2 ships
All numbers come from /api/report. None are hardcoded.

08 / Execution

Agent waves, ownership and gates

Each agent gets this plan's URL, its brief, and the wave-0 contracts. Each agent owns specific paths, and only one agent writes to a given path. Contracts change only through the lead agent. The lead checks each wave's results by running the tests and opening the UI itself, rather than trusting what agents report.

0:00 – 0:45Wave 0: lead lays the foundations

L0 · leadScaffold + contracts
DoesSets up the uv project with ruff and pytest, creates web/ with bun create vite (svelte-ts) and Tailwind 4, and adds a .gitignore and git init. Writes schemas.py, types.ts, fixtures/forecast_sample.json, the raion_id rule, the episode Parquet schema, and an AGENTS.md listing who owns which paths.Done whenuv run pytest and bun run dev both start. The dated CSV copy is saved with its sha256.

0:45 – 3:00Wave 1: data, maps, UI shell and text sources in parallel

A1Data & episodes
Ownsdata.py, tests/test_episodes.pyDeliversepisodes.parquet (raion_id, oblast, start, end, source_level, censored), raions.csv (the raion list), and the exact date of the regime switch.Done whenTests pass. A short EDA in docs/eda.md covers alert starts per day over time, the hour-of-day profile, the mix of alert levels by month showing the switch, and median duration.
A2Geo & crosswalk
Ownsgeo.py, data/geo/DeliversgbHumanitarian ADM2 simplified to under 600 KB of GeoJSON, a crosswalk from map polygons to raion_id, adjacency.json (which raions share a border) and centroids.Done whenA coverage report lists every dataset raion that has no polygon, and a PNG shows the simplified map.
A3Frontend shell
Ownsweb/DeliversThe layout, horizon and mode switches, scrubber and raion panel, working against the fixture JSON. Switches to A2's GeoJSON as soon as it's ready.Done whenThe map draws from the fixture at 1440 and 390 px wide, and svelte-check passes.
R1 · researchText sources and access
Ownsdocs/research/text-sources.md onlyDeliversA ranked list of sources and a recommendation (see section 09). Includes a sample of one day's messages.Done whenIt names the best source, how to get its history and from what date, and what project owner needs to provide, if anything.

G1 · 3:00If more than 5% of dataset raions have no polygon, switch the unit to oblast (raion_id = oblast-slug). The rest of the plan stays the same. If R1 needs Telegram API credentials, ask project owner now using request_secret.

3:00 – 6:00Wave 2: features, harness, duration model

B1Features & labels
Ownsfeatures.py, labels.py, tests/test_leakage.py, tests/test_labels.pyDeliversLabels first, within about 45 min, because B2 needs them. Then the feature tables as Parquet, split by period.Done whenThe leakage test passes on 50 random forecast times, and the whole history builds in under 10 minutes.
B2Backtest harness + B0
Ownsbacktest.py, metrics.py, models/baseline.py, tests/test_split.pyDeliversSplit and embargo logic, the metrics, reliability figures and metrics.json for B0. A --final flag is the only way to score the test set.Done whenairalert backtest writes B0's scores for validation.
B3Duration model
Ownsmodels/duration.py, tests/test_duration.pyDeliversD0 and D1, with features known at the start time and censored episodes handled. Adds a duration section to metrics.json.Done whenCoverage, pinball loss and MAE are reported for validation, and D1 is compared against D0.
A3 (cont.)Frontend
DeliversThe raion panel, the history chart and the model report page, still using fixtures.

G2 · 6:00The leakage test passes and B0 has validation scores. If not, all agents stop adding features and fix this first.

6:00 – 8:30Wave 3: onset models, API, and the text signal study

C1Onset models
Ownsmodels/onset.py, reports/DeliversB1 and M1 for each horizon, calibration, and the full-history vs raion-regime-only comparison. Picks the model on validation and runs --final once.Done whenmetrics.json has test scores for every model and horizon.
C2API + precompute
Ownsapi.py, precompute.py, tests/test_api.pyDeliversWires the API to B0 forecasts first, then switches to whichever model C1 picks. Writes forecasts.parquet for validation, test and the latest hour.Done whenThe contract tests pass and the frontend works against the real API.
R2 · researchText signal study
Ownsdocs/research/text-signal.md, scripts/text_probe.pyDeliversA list of event types extracted from R1's source, and how much each one raises the chance of an alert starting, and how far ahead (see section 09).Done whenIt gives a go/no-go with numbers.
A3 (cont.)Frontend
DeliversMoves from the fixtures to the live API, and adds replay playback.

G3 · 8:30M1 should beat B0's Brier skill on validation for at least 2 of the 3 horizons. If it doesn't, serve the best model and write up why.

G4 · 9:00Text goes ahead only if R2 says go, the history covers at least the raion-regime training window, and access works without manual steps.

8:30 – 11:30Wave 4: verification, report, stage 2 text

D1 · verifierIndependent audit
Ownsweb/tests/ (Playwright), docs/audit.mdDeliversRuns everything from a fresh clone, using only the README commands. Actively tries to find leaks. Runs the end-to-end tests with screenshots at 1440 and 390 px, and checks that every number in the UI and report matches metrics.json.Done whenIt hands the lead a list of issues. It doesn't fix them itself.
D2Report writer
OwnsREADME.md, docs/report.mdDeliversProblem, data, the as-of rule, method, results with baselines, limitations (two regimes, a daily-updated dataset, missing permanent alerts) and how to run it.
T1 · if G4 = goText integration
Ownstext/, tests/test_text_leakage.pyDeliversA message cache in Parquet, as-of text features, and the alerts-only vs alerts+text comparison on identical rows.Done whenThe comparison table is in metrics.json and on the report page.

G5 · 11:30Feature freeze. The remaining 1.5 h is for D1's fixes, re-running the end-to-end tests and the final report numbers.

09 / Stage 2 research

How text could add warning time

Alert history already captures timing patterns and how alerts spread between regions. Text can only help if it reports something before the alerts begin, such as bombers taking off or drone groups launched toward a specific oblast. Text is matched to an oblast or to the whole country, and then copied to each raion. This is why choosing raion as the unit doesn't make text harder.

R1 brief · sources & access

Score each candidate source on: (1) whether its history can be downloaded back to at least December 2025, ideally to 2022; (2) a UTC publish time on every message; (3) how to access it and what that costs; (4) terms of service and rate limits; (5) how far ahead of alerts it typically posts. Check every channel name yourself, because the list below is a starting point, not facts.

The Ukrainian Air Force's official Telegram channel: launch reports, tracking updates, overnight summaries

Monitoring channels that post strategic-bomber (Tu-95/Tu-160) and MiG-31K takeoffs, and the direction Shahed groups are heading

Telegram channels of oblast military administrations

ISW daily assessments and news RSS feeds. These probably lag too far behind to help at horizons of 12 h or less, so check that

Access options: Telethon with api_id/api_hash from my.telegram.org, which project owner provides through request_secret, or the public t.me/s/<channel> web preview with ?before= paging, which needs no login. Check whether the preview reaches far enough back.

R2 brief · signal study

Pull 6 or more months of messages from R1's chosen source. Tag each message with an event type using Ukrainian and Russian keyword rules (no LLM at this stage). Match place names to oblasts using the dataset's oblast names plus a list of major cities. For each event type, measure the onset rate in the next H hours after the event relative to the base rate, and the median time until alerts start.

Go if at least one event type raises the rate ≥ 1.5× at some horizon and appears often enough in the training window. Text history may only go back to around December 2025. In that case the comparison trains both models on that same window, so neither has an unfair advantage.

Event type to testExample keywordsApplies toExpected lead time

Strategic bombers take offТу-95, Ту-160, стратегічна авіація, злітwhole countryhours: mostly matters for 3 h and 12 h

MiG-31K takeoffМіГ-31К, злітwhole countryminutes: usually starts an alert right away

Drone groups and their headingшахед, БпЛА, група, курсом на <place>oblast named in the message30 min to 3 h: mostly matters for 1 h and 3 h

Ballistic missile threatбалістика, загроза балістичногоoblast or whole countryminutes

Overnight summary / all clearу ніч на, збито, відбійwhole countrylooks back: may still help predict the next night

Features for T1: counts of each event type in the last 1, 3, 6 and 12 h, nationally and for the mentioned oblast, plus the time since the last event of each type. The as-of test from section 10 applies to these too. Don't use the channel that mirrors official alerts. Its timestamps come before t, so it wouldn't leak, but it only repeats data we already have.

10 / Verification

Few tests, each guarding a real failure

TestWhat it provesOwner

test_leakageFor 50 random forecast times t, features built from the full data equal features built from the data as it stood at t. To rebuild that view: drop records with started_at ≥ t and set finished_at > t to null. This is the most important test.B1

test_episodesAn oblast record expands to all of its raions. A hromada record maps to its parent raion. Overlapping records, and records with gaps shorter than MERGE_GAP_MIN, merge into one episode.A1

test_labelsA small synthetic example with known alert starts gives exactly the expected labels for every H, including starts that land exactly on the edges of the window.B1

test_splitNo training forecast time falls within H of the validation start. The test set can only be scored with --final.B2

test_apiThe forecast covers every raion, probabilities are between 0 and 1, P(1h) ≤ P(3h) ≤ P(12h), and the duration quantiles are in order.C2

Playwright e2eOn the real API: the map draws all the raions, switching horizon changes the colours, the scrubber changes the forecast time, clicking a raion opens a panel with 3 probabilities, and the report page shows the numbers from metrics.json. Screenshots at 1440 and 390 px.D1

Definition of done

On a fresh clone, the README commands (uv run airalert fetch through serve, plus bun run dev) rebuild the data, models, report and a working dashboard.

The leakage tests and the end-to-end tests pass. D1's audit has no open leakage issues.

docs/report.md shows test-set results for all models next to the baselines, including any model that lost.

Stage 2: the text comparison is reported either way. If text was skipped, the report says why.

11 / Scope guard

Out of scope for 13 hours

cutHawkes process
A good fit for how alerts trigger more alerts across regions, but the libraries are weak on Windows and it would take a full wave. Put it in the report's future-work section.

cutHromada level
The data is too sparse and the boundary matching is unreliable. Hromada alerts are mapped to their parent raion.

stretchLive alerts.in.ua feed
Needs a token. Only add it if everything else is done before G5.

cutDeep sequence models
LightGBM on good as-of features is the stronger use of the time we have.

cutLLM message tagging
Keyword rules come first. Try an LLM only if R2 shows the keyword rules miss most of the signal.

cutElectron packaging
A local web app is enough for the deliverable.

What only project owner can do

Provide Telegram api_id/api_hash by about 3:00 if R1 picks Telethon. The lead requests them through request_secret, never in chat.

Accept or override the raion vs oblast decision at G1, and the text go/no-go at G4.

