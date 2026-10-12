# Live alerts and historical backfill

R3 research for project owner. Tested on 2026-10-12, principally 15:15-15:26 UTC, or 18:15-18:26 Kyiv. This is a source recommendation and contract proposal. No production data, models, API or dashboard were changed. No credentials were requested, used or saved.

## Recommendation

Use the supported **alerts.in.ua API** as the primary live adapter, with one server-side request every 60 seconds. It exposes location identifiers, hazard types, ongoing starts, red/yellow levels and individual threats. Obtain its token through the [official request form](https://alerts.in.ua/api-request). The actual no-token request returned 401. Its normalized payload was independently observed through the documented Ubilling raw proxy, rather than claimed from an authenticated request.

Use **siren.pp.ua's UkraineAlarm mirror** as the immediate no-token fallback. It returned current district/community alerts with explicit red and yellow levels, including both levels at one location. Add the supported official UkraineAlarm API ahead of that mirror if project owner obtains its separate key. Ubilling's explicitly selected raw `aiu` and `ual` feeds are further fallbacks. These services share upstream inputs, so this is transport redundancy, not independent confirmation of events.

For the first historical repair, request an **authorized alerts.in.ua archive export or approved date-range access**, covering September 7 through today, plus the August 29 to September 2 outage and overlap before both boundaries. Its publicly visible map archive demonstrably contains September 7 and October 11 data. However, that response explicitly says the API is private. The supported history endpoint documents only a rolling month. A token alone therefore does not establish complete access to the 35-day gap. Do not promise full backfill before the export's coverage has been checked.

Thereafter, retain a durable event archive and reconcile it nightly with supported history. One-minute current-state snapshots alone can miss a short alert completely. Last-25 history and rolling-month history cannot guarantee recovery after arbitrary downtime. That guarantee requires an agreed long-retention archive, an export service, or a collector that continues independently while this server is down.

## What yellow actually means

The owner is right about the national change. The current [Cabinet resolution 1092, amended September 15](https://zakon.rada.gov.ua/laws/show/1092-2026-%D0%BF) defines yellow as a drone threat. Red covers missile threats, combined missile/drone threats, and mass drone threats. Both are levels of the air-raid warning signal. The amendment specifies a one-minute red siren and a 30-second yellow siren, with yellow differentiated by district. This is a threat classification, not a forecast probability.

[alerts.in.ua's September 6 explanation](https://blog.alerts.in.ua/p/alert-levels) says its map and API support the new levels. The [official UkraineAlarm map code](https://map.ukrainealarm.com/js/mapCore.js?v=236) checks whether an AIR alert has any Red level; otherwise it assigns its yellow map class. The [official map](https://map.ukrainealarm.com/) separately displays air alerts and other hazard types.

Three distinctions matter:

- The older alerts.in.ua IoT code `P` means partial district/community coverage within an oblast. It does not encode yellow. `A/P/N` loses the red/yellow distinction. [API documentation](https://devs.alerts.in.ua/)
- A message containing `загроза` is not enough to classify severity or even a new onset. The older Telegram channel contains red drone threat notices and orange informational notices within an alert. Read explicit level fields or a documented message template.
- Yellow remains an active alert. It must never become "quiet" or "safe" in the model or map. Operational rules depend on the institution and applicable rules; the UI should describe the reported level rather than give a general permission to continue normal activity.

Actual simultaneous levels were observed in UkraineAlarm origin UID `56`, Pokrovskyi district. Its AIR event contained a Red missile entry and a Yellow drone entry. Separate layers can both contain this area. If one fill is needed, red takes precedence, while the tooltip preserves both threats. See [the real sample](samples/r3_siren_active.json).

The AIU proxy also returned two records whose scalar level was yellow while a nested threat was red. Sample alert `262473`, Pokrovska hromada UID `353`, had a red missile threat and an implausible `location_oblast_uid: 353`. Preserve and flag this disagreement. Resolve parents through a checked hierarchy, and do not discard nested red evidence. See [the real sample](samples/r3_ubilling_aiu_raw.json).

## Ranked live sources

Ranks reflect suitability for this project's detailed map. Latency claims below distinguish published settings from observations. HTTP response time is not end-to-end alert latency.

| Rank and source | Authentication and tested access | Granularity and levels | Freshness and limits | Terms and limitations |
|---|---|---|---|---|
| 1. [alerts.in.ua supported API](https://devs.alerts.in.ua/) | Personal free token; request form. No-token `v1/alerts/active.json` returned 401 JSON. Use Bearer header server-side. | Oblast, raion, hromada and city locations. `alert_type`, `location_type`, `location_uid`, `alert_level`, optional `threats[]`. Raw proxy verified both colors. | Documented soft 8-10 requests/min/IP, hard 12; history 2/min. One request/min is within the limit. `If-Modified-Since` supported. No independently measured event-delay SLA. | Public services must proxy; token must not reach browser code. Docs disclaim critical-infrastructure use and reliability. No broad dataset/ML license was verified. |
| 2. [Official UkraineAlarm API](https://api.ukrainealarm.com/) | Apply with name, company, phone, email, project link and purpose; accept offered contract. Official SDK uses API key in `Authorization`. Direct requests returned 403 Cloudflare HTML, so authenticated behavior was not tested. | State, District, Community hierarchy. Mirror and first-party map expose `activeAlerts[].activeAlertLevels[]`. AIR, ARTILLERY, URBAN_FIGHTS, CHEMICAL, NUCLEAR; SDK also lists UNKNOWN and INFO. | `/alerts/status` provides change index. Numeric rate limit not verified. Official web feed observed with 10-second cache header and a Last-Modified 25 seconds before fetch. | Official application data entry point, not a distinct independent source. Offer PDF was blocked from this environment; owner must verify approved usage, rate allowance and archive access. |
| 3. [siren.pp.ua](https://github.com/PaulAnnekov/uasiren) | No auth in actual `/api/v3/alerts`, `/regions`, `/alerts/status` and history requests; all 200. | Preserves UkraineAlarm district/community hierarchy and level arrays. Inherited district events appear inside community objects too. | First-party wrapper documents 50 requests/min/IP. Live headers `max-age=1`. GET completed in about 0.16-0.30 seconds in representative probes; origin-to-user latency unmeasured. | Unofficial public wrapper. No service availability commitment or repository license found. Dependence on official upstream remains. |
| 4. [Ubilling raw proxy](https://wiki.ubilling.net.ua/doku.php?id=aerialalertsapi) | No auth. Explicit `?source=aiu&raw` and `?source=ual&raw` returned 200. | Raw `aiu` preserves AIU location/type/level/threat fields; raw `ual` preserves official level arrays. Normalized `states` loses detail and colors. | Docs say 3-second server cache, 2 requests/sec/host. Actual responses advertise `max-age=60`; `ual` cache timestamp lagged fetch by about 198 seconds. An initial concurrent burst produced 429, then sequential fetch succeeded. | Provider describes informational/statistical use and advises official sources. Default source may change. Always pin source; never interpret a successful response as proof of fresh data. |
| 5. [Official application web-map backend](https://map.ukrainealarm.com/) | Actual `/api/v2/data/mapUpdate` returned 200 without auth. Older `/api/data/getAlerts` returned 401. JS normally attaches a page bearer value, which was neither extracted nor saved. | Detailed official alerts, levels, reasons, locations and recent state history. | `mapUpdate` had 10-second cache header; no published third-party rate contract verified. | Discovered from first-party JS, not a supported replacement for an approved API key. Technical public access does not establish a stable integration contract. |
| 6. [Public JSON behind alerts.in.ua](https://alerts.in.ua/) | `v3/alerts/active.json` returned 200 without auth. Browser actually fetched `.mp`; JS lists JSON adapter. `v4/alerts/active.json` returned 404. | Compact keys and map dictionaries; colors and geography are decoded by map code. Not the supported v1 schema. | Live JSON `max-age=1`; Last-Modified 6 seconds before the saved sample. | Both response and browser console explicitly warn that this API is private and may change. Research evidence only; obtain approved public access. |
| 7. [Public Markdown situation report](https://alerts.in.ua/llm) | No auth; 200, redirect to `api.alerts.in.ua/v3/alerts/active.md`. Explicitly advertised by [llms.txt](https://alerts.in.ua/llms.txt). | Human-readable district/oblast descriptions, red/yellow totals and threats. Not a stable typed events API. | Sample reported October 12 18:15:19 Kyiv, 54 areas, 17 red and 37 yellow. Published page `max-age=1`. | Useful independent rendering check and status fallback text. Avoid turning narrative summaries into training labels. |
| 8. [alerts.com.ua](https://github.com/and3rson/raid) | Docs require `X-API-Key`, requested from `a@dun.ai` or `@andunai`; actual `/api/states` succeeded without a key. Homepage and `/api/history` returned 404. | 24 oblasts plus Kyiv city, boolean status only. No red/yellow field or detailed district coverage. | Docs claim usually up to 2-second delay, 10 req/sec/IP, 100/sec/key, history 1/min. Actual payload was current-stamped but only Luhansk active while other feeds had dozens of active districts. `max-age=14400`. | First-party docs permit general/commercial use subject to their restrictions. Current behavior is unsuitable for detailed fallback or repair. Does not expose Crimea, Sevastopol or Chornobyl. |

Source links for older official models are [Alerts API paths](https://github.com/UkraineAlarm/UkraineAlarm-javascript/blob/master/docs/AlertsApi.md), [hazard enum](https://github.com/UkraineAlarm/UkraineAlarm-javascript/blob/master/docs/AlertType.md), and [region model](https://github.com/UkraineAlarm/UkraineAlarm-javascript/blob/master/docs/AlertRegionModel.md). The older model documents omit the newer `activeAlertLevels`, which actual live JSON supplies. Preserve unknown additional fields in the raw archive.

## Endpoint evidence

All paths here were discovered in first-party docs, website code or browser traffic, then requested. There was no endpoint guessing presented as a functioning API. Samples are small selections of actual responses with timestamps and no credentials.

| Actual GET | Result on October 12 | Evidence and scope |
|---|---:|---|
| `https://api.alerts.in.ua/v1/alerts/active.json` | 401 | Token required JSON. Supported field behavior verified through raw proxy, not an authenticated direct call. |
| `https://api.alerts.in.ua/v1/regions/16/alerts/month_ago.json` | 401 | Supported rolling-month history requires token. |
| `https://api.alerts.in.ua/v1/iot/active_air_raid_alerts_by_oblast.json` | 401 | Token required; documentation's A/P/N is geographic coverage. |
| `https://api.ukrainealarm.com/api/v3/alerts` | 403 | Cloudflare HTML, not evidence of the API's credential-error semantics. |
| `https://api.ukrainealarm.com/api/v3/regions` | 403 | Same transport block. |
| `https://api.ukrainealarm.com/api/v3/alerts/regionHistory?regionId=16` | 403 | Same transport block. |
| `https://siren.pp.ua/api/v3/alerts` | 200 | Saved sample had 50 outer objects and 54 distinct origin/type/update tuples; arrays contained Red and Yellow. [Sample](samples/r3_siren_active.json) |
| `https://siren.pp.ua/api/v3/regions` | 200 | 29 top nodes, 122 District nodes and 1,455 Community nodes; all 119 forecast units mapped with a reviewed alias. [Mapping](samples/r3_mapping.json) |
| `https://siren.pp.ua/api/v3/alerts/status` | 200 | `lastActionIndex: 639274152421921943`; treat as a 64-bit integer/string, not a JavaScript safe integer. |
| `https://siren.pp.ua/api/v3/alerts/16` and `/9999` | 200 | Targeted permanent Red AIR for Luhansk and Crimea. |
| `https://siren.pp.ua/api/v3/alerts/regionHistory?regionId=122` | 200 | 20 alerts, oldest October 10 17:31:26Z, newest October 12 14:13:51Z. Current row uses `isContinue:true` and year-0001 end sentinel. [Sample](samples/r3_siren_history_122.json) |
| `https://ubilling.net.ua/aerialalerts/?source=aiu&raw` | 200 | 54 records, 47 air-raid, 6 artillery, 1 urban-fights in saved sample. Two scalar-yellow/nested-red conflicts. [Sample](samples/r3_ubilling_aiu_raw.json) |
| `https://ubilling.net.ua/aerialalerts/?source=ual&raw` | 200 | 54 outer records in a different cached snapshot. [Sample](samples/r3_ubilling_ual_raw.json) |
| `https://ubilling.net.ua/aerialalerts/` | 200 | Normalized coarse default; Sevastopol true, many end/start ages at epoch sentinel. [Sample](samples/r3_ubilling_default.json) |
| `https://alerts.com.ua/api/states` | 200 | 25 boolean region rows; only Luhansk true. [Sample](samples/r3_alerts_com_states.json) |
| `https://alerts.com.ua/api/history` | 404 | Documented dump path currently unavailable without auth; no history downloaded. |
| `https://map.ukrainealarm.com/api/v2/data/mapUpdate` | 200 | Official map response had 54 outer alerts in saved request, with level arrays. [Sample](samples/r3_official_map_update.json) |
| `https://map.ukrainealarm.com/api/data/getAlerts` | 401 | Legacy map endpoint requires page authorization. |
| `https://api.alerts.in.ua/v3/alerts/active.json` | 200 | Compact private map response; 59 records at saved request. [Sample](samples/r3_alerts_web_compact.json) |
| `https://api.alerts.in.ua/v3/alerts/day/1655.json` | 200 | 553 compact records for September 5 Kyiv day, including earlier continuing alerts. Browser independently requested `/1655.mp` after selecting that calendar date. |
| `https://api.alerts.in.ua/v3/alerts/day/1657.json` | 200 | September 7 Kyiv day, 518 compact records. [Sample](samples/r3_web_day_1657.json) |
| `https://api.alerts.in.ua/v3/alerts/day/1691.json` | 200 | October 11 Kyiv day, 803 compact records. [Sample](samples/r3_web_day_1691.json) |
| `https://alerts.in.ua/llm` | 200 | Current Markdown, explicit red/yellow counts. [Sample](samples/r3_public_sitrep.json) |

Counts differ between requests because alerts change and mirrors cache different snapshots. They are not counts of unique raion episodes. A record may cover many polygons, and multiple outer objects may repeat one originating alert. [Failure samples](samples/r3_endpoint_failures.json) include the real 429 and Cloudflare responses without client identifiers or secrets.

## History and September-to-today repair

### Fresh CSV check

The current [GitHub English file](https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/main/datasets/official_data_en.csv) returned HTTP 200, 31,025,633 bytes and 299,275 rows. SHA-256 still equals `b396f87696243069eec0219880e87dcd642b20beb0a7bae0d0e1f61e1ed20ec4`, exactly the saved file. Max start is `2026-09-07T05:28:22Z`; max finish is `2026-09-07T05:39:33Z`. No starts were found strictly inside the known August 29 to September 2 source gap. [Measured summary](samples/r3_csv_check.json)

The repository tree had an October 12 update and commit `d10c80fa696bab42822b9f05b423fc8a30e79d20`, but its official CSV and collector cursor remained frozen. A recent repository commit does not mean recent official observations.

### Supported history limits

AIU documents `GET /v1/regions/{uid}/alerts/month_ago.json` and only that rolling period. On October 12 this cannot be assumed to reach September 7; the approximate missing earliest portion is September 7-11. A 119-region sweep has a minimum request-budget duration of 59.5 minutes at 2/min, before hromadas or retries. Do not assume an oblast request includes all descendant events; test completeness after token access. A nightly all-region sweep is plausible, but arbitrary downtime recovery is not established.

The [official UkraineAlarm SDK](https://github.com/UkraineAlarm/UkraineAlarm-javascript/blob/master/docs/AlertsApi.md) describes region history as the last 25 alerts and provides no documented date-range or pagination parameter. The actual mirror returned 20 in the Chuhuivskyi sample, spanning less than two days. Thus even a several-day outage in a busy district can exceed retention. That sample also had no severity fields on historical rows, despite the live endpoint's level arrays. Never manufacture past color histories from today's color.

The private AIU day archive reaches beyond the CSV cutoff, as the tested September 7 and October 11 pages show. It uses a numeric war-day index and **Kyiv calendar days**, not UTC days. The browser normalized the September 5 archive into dates ending at `2026-09-05T20:56:01Z`, and its replay began September 4 at 21:00Z. Archive entries can be continuing or censored at the day's boundary. Neither 553 raw entries nor 481 browser-filtered entries can be compared directly with 381 UTC-start CSV events. This is evidence to request a supported export, not a promise that every intervening date or level transition is complete.

### Telegram and the collector

The requested [official `@air_alert_ua` preview](https://t.me/s/air_alert_ua) returned 20 messages but its newest was `338380`, `2026-09-07T06:09:04Z`. A second request with `?before=338401` returned the same end of history. The [oldest preview](https://t.me/s/air_alert_ua?before=21) reached channel creation on March 14, 2022 and substantive alerts on March 15. Technical historical reach is good; current September-to-October reach is absent in this test. [Reach sample](samples/r3_telegram_reach.json)

The first-party [collector](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset/blob/main/processors/official_channel_processor.py) reads this channel with Telethon, `reverse=True` and `min_id` from a persisted cursor. It matches hashtags using `states.json`, retains open alerts in a pickle, and appends only completed records to CSV. The [entry point](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset/blob/main/process.py) uses Telegram application credentials and an authorized session. The [workflow](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset/blob/main/.github/workflows/update_datasets.yml) schedules midnight **UTC**, not midnight Kyiv. Its published cursor was `338380`, exactly the preview's newest message.

Running the same collector is technically possible with its dependencies and private Telegram credentials/session, but does not cure a stopped channel. Reuse its geography dictionary and cursor idea, not its parser unchanged. It treats any red emoji in the first line as activation, including red threat updates. It also writes an unmarked synthetic one-hour end if another activation arrives more than three hours after an unclosed alert. These are observable code paths, not a claim that every mismatched record uses that fallback. Never synthesize end times in the new ingest.

The volunteer [єТривога, `@UkraineAlarmSignal`](https://t.me/s/UkraineAlarmSignal) does continue through today, and the tested preview contained explicit yellow, red, red-clear and all-clear messages. Its own description identifies volunteers, not the official app operator. Mixed messages can clear red while yellow remains active. It is a possible manually reviewed repair candidate, not a verified replacement for official labels. No full gap or agreement study was run for it.

[Telegram's content-licensing terms](https://telegram.org/tos/content-licensing) restrict collection and use of platform data for ML training, validation, benchmarking and deployment, with a specific consent exception. Credentials or public preview access do not supply that consent. The small probes here measure access and agreement; they were not added to training. For bulk training backfill, obtain a licensing basis or a provider-authorized non-Telegram export.

### Measured overlap on September 5

`uv run python scripts/r3_probe.py telegram-day --day 2026-09-05` fetched 85 bounded, paced preview pages/searches and 1,399 messages. Coverage ran from September 4 23:37:15Z through September 7 01:26:10Z, covering the relevant ends for starts on September 5. English/Ukrainian CSV row alignment was checked before building location aliases.

| Measure | Actual result |
|---|---:|
| Distinct CSV events starting on September 5 UTC | 381, comprising 361 raion, 16 hromada and 4 oblast |
| Strict-template reconstructed start/end pairs for sample-day locations | 371 |
| Exact location, start and end matches | 359 |
| Exact-pair recall against CSV | 94.23% |
| Exact-pair precision against CSV | 96.77% |
| CSV-only pairs / Telegram-only pairs | 22 / 12 |
| Non-onset threat messages encountered | 137 |
| Unclosed sample-day starts after the covered end window | 0 |

This was a conservative parser, not the upstream parser and not an independent ground-truth audit. It used full onset/all-clear wording and UTC publication times including seconds. Its mapping vocabulary was restricted to locations present in that CSV day; unrepresented places cannot contribute measured precision. It did not expand old district-name aliases, so Samarivskyi versus Novomoskovskyi accounts for part of the missing coverage. Remaining disagreements require message-level review, especially threat updates and hromada states. The result supports reconstruction feasibility but rejects an automatic exact-equivalence claim. [Numeric evidence and examples](samples/r3_telegram_day_20260905.json)

### Additional archive checked

[MyKyiv's own history page](https://my-kiev.com/alerts/history/) advertises a 90-day journal from May 2, 2026, collected from UkraineAlarm every five seconds. Actual `/alerts/api/history/14` returned 200 with Kyiv-oblast events, but repeated starts/shared ends and unmeasured rows; `/alerts/api/history/122` returned an empty Chuhuivskyi history. The provider explicitly aggregates to oblast waves. It cannot be expanded into true raion labels. Its September-to-today interval is a coarse comparison candidate, not a detailed backfill recommendation; usage/ML license and full coverage were not verified.

## Mapping plan

Keep the existing English-derived IDs from `airalert.ids`, the fixed `raions.csv` forecast universe, and the geography crosswalk. Do not slug Ukrainian strings: `ids.slug` removes non-ASCII characters, so it cannot transliterate them. Also do not trust API English names as exact dataset names: `Luhansk Oblast` and the dataset's `Luhanska oblast` differ.

The probe aligned the downloaded Ukrainian edition to the saved English CSV using every row's start, end and level, then joined official source parent/name pairs to existing IDs. The real hierarchy mapped 118/119 units initially. Adding the reviewed `Шептицький район` alias for dataset `Червоноградський район` mapped **119/119**. That rename is supported by [resolution 3984-IX](https://zakon.rada.gov.ua/laws/show/3984-20). Source spelling/renaming changes must update aliases, not forecast IDs. [Full mapping evidence](samples/r3_mapping.json)

| Source location | Existing target and expansion |
|---|---|
| UkraineAlarm district `46`, Криворізький район, Dnipropetrovska parent | `dnipropetrovska/kryvorizkyi` |
| UkraineAlarm district `43`, Самарівський район; old Новомосковський messages | `dnipropetrovska/samarivskyi` |
| UkraineAlarm district `60`, Звягельський район; old Новоград-Волинський messages | `zhytomyrska/zviahelskyi` |
| Шептицький район, Lvivska parent | `lvivska/chervonohradskyi`, retaining frozen dataset ID |
| UkraineAlarm district `74`, Вишгородський район | `kyivska/vyshhorodskyi`; does not prove the exclusion zone has an independent status |
| Hromada/city UID | Join through the source hierarchy to its parent raion. Preserve the original UID and geographic level for partial-coverage disclosure. |
| Oblast UID | Expand across its existing forecast units and all applicable display-only polygons. Never infer an entire oblast alert from one child alert. |
| Kyiv city | Explicit alias to `kyiv-city/kyiv-city`. Keep it distinct from Kyivska oblast. |

Use `(provider, location_uid)` as the external key. In particular, Crimea is AIU `29` but UkraineAlarm `9999`; Sevastopol is documented by AIU as `30`; Kyiv city is AIU `31`. Treat integer/string UID variation as a canonical string. AIU's public `locations.json?v=5` had 174 title/type entries but no UIDs; use its documented UID directory and an approved hierarchy/export to complete its mapping, rather than list position. Common numerical IDs in two services are not proof of a shared namespace.

For UkraineAlarm responses, the outer location is the subscriber/covered region and nested `activeAlerts[].regionId` is the originating event region. Preserve both. Deduplicate originating events before expanding to raions, or a district alert inherited by its communities will inflate starts. The sample's top-level Vovchanska community `1313` contained AIR originating in district `122` as well as its own artillery and street-fighting alerts.

### Crimea, Luhansk, Sevastopol and Chornobyl

All 20 unmatched polygons already exist in the 139-feature map. Give each an independent display key tied to its crosswalk `shape_id` or a reviewed stable geography key, with nullable forecast ID. They can carry current status without inventing trained predictions or adding permanent-alert negatives to the 119-unit model.

| Area | Actual source evidence | Proposed display handling |
|---|---|---|
| Crimea's ten raion polygons | UkraineAlarm `9999` and AIU `29` reported continuing Red AIR from `2022-12-10T22:22:00Z`. No Crimea district children in official tree. | Show the oblast-level reported alert on all ten polygons, explicitly marked inherited oblast coverage; forecast unavailable. |
| Luhansk's eight raion polygons | Both feeds reported continuing Red AIR. Official start `2022-04-04T16:45:00Z`; AIU start `16:45:39Z`. | Inherit the observed oblast alert across all eight polygons, including raions missing from source tree. Retain provider-specific start precision. |
| Luhansk district nodes | Official `84` Sievierodonetskyi, `85` Svativskyi, `86` Starobilskyi, `87` Shchastynskyi. No separate nodes for Alchevskyi, Dovzhanskyi, Luhanskyi or Rovenkivskyi. | Use direct child evidence when present, otherwise explicitly inherited oblast evidence. Display IDs can follow existing polygon names; forecast IDs stay null. |
| Sevastopol polygon | No separate node in tested official tree or active raw arrays. AIU documents UID `30` but no live row was observed. Ubilling default said true since `2022-12-11 00:22:00`, without severity or timezone. | Show "reported permanent alert, secondary source" with provenance if accepting that source; severity unknown. A verified independent red/yellow status is unresolved. Absence in active arrays does not establish quiet. |
| Chornobyl exclusion-zone polygon `22097711B27688590731099` | No matching forecast unit and no independent node in tested detailed source hierarchy/active arrays. The map names it `Chornobylska zona vidchuzhennia`, not a source raion. | Preserve and label the zone. Apply a verified Kyivska oblast-wide alert when present. Otherwise show independent status unavailable. Any inheritance from Vyshhorodskyi `74` requires confirmation of alert jurisdiction; do not silently equate them. |

Crimea polygon names are Bakhchysaraiskyi, Bilohirskyi, Dzhankoiskyi, Yevpatoriiskyi, Kerchynskyi, Kurmanskyi, Perekopskyi, Simferopolskyi, Feodosiiskyi and Yaltynskyi. Luhansk polygon names and exact shape IDs are retained in [the sample crosswalk selection](samples/r3_mapping.json). This plan shows every area, but research did not establish an independently observed detailed status for Sevastopol or the exclusion zone.

## Proposed records extension

Keep `level` for geographic scope. Add severity separately; a hromada alert can be red or yellow. Preserve an immutable source event identity before expanding coverage. This is a lead-owned contract change proposal, not an edit to `records.parquet`.

| Field | Proposed representation and purpose |
|---|---|
| Existing keys and timestamps | Retain `raion_id`, `oblast_id`, `level`, `started_at`, nullable `finished_at` for forecast records. Display-only status uses its own geography key. |
| `alert_type` | `air_raid`, `artillery_shelling`, `urban_fights`, `chemical`, `nuclear`; retain unknown/INFO raw types separately instead of coercing them to air raid. Map official AIR/ARTILLERY names explicitly. |
| `severity` | `red`, `yellow`, `unknown`; retain source `alert_level`/`alertLevel`. Historical CSV has unknown severity. Color is a presentation of severity, not another independent label. |
| `source` | Origin provider such as `ukrainealarm`, `alerts_in_ua`, `vadimkin_official_csv`; distinguish transport such as siren or Ubilling in `source_transport`. |
| `source_alert_id` | Provider ID where supplied. Where absent, use a documented composite origin UID/type/start identity, without assuming `lastUpdate` is always the original onset. |
| `source_location_uid`, `source_location_type`, parent UIDs | String external keys; preserve both origin and covered location. Validate the hierarchy and aliases. |
| `observed_at`, `source_updated_at` | UTC receipt time and source modification time. Archive both; old event time does not prove old publication availability. |
| `threats` / separate threat interval table | Preserve each reported threat/level/reason/start, including simultaneous levels. Append timestamped changes instead of replacing historical severity. |
| `status_quality`, `coverage_scope` | Evidence states such as observed, inherited, stale, conflicting or unknown; full-unit versus some hromadas. Keep occupancy/forecast availability separate from alert status. |

Store immutable response/change observations and derive event intervals from them. A severity escalation is a change inside an air-alert episode, not necessarily a new onset. Red and yellow layers can overlap. For existing onset/duration models, use the union of air-raid intervals across severity, with deduplication before counting. Other hazard types must not silently enter those targets. Year-0001 end sentinels and `isContinue:true` become null ends, not ancient completed events.

Retain the strict event-time as-of rule, plus observed-availability versions for genuine live forecasts. For a forecast at `t`, no record starting at or after `t`, later end, later severity transition or response received later than the decision time may enter its online features. Retrospective archive backfill cannot retroactively certify availability of historical forecasts. A current mutable severity column alone would leak later upgrades into replay.

## Nightly ingest and recovery design

1. Poll centrally every 60 seconds and archive successful responses before serving the shared live map cache. Requesting once per browser would multiply provider traffic. On errors, retain last observation with its timestamp and an explicit stale/unknown state; do not clear alerts because a fetch failed. Reconcile disappearance against a complete successful snapshot and recent history, preserving an interval of uncertainty when the end time is not supplied.
2. Trigger at **00:00 in `Europe/Kyiv`**, using an IANA-aware scheduler, not a fixed UTC cron. October 12 is UTC+3, so that midnight is 21:00Z on the preceding UTC date; winter conversion differs. Compute the previous local day's `[midnight, midnight)` UTC boundaries. DST days can contain 23 or 25 hours.
3. Persist a last-successfully-reconciled local-day watermark. On restart, process every missing complete local day through yesterday, in order, then refresh today's partial live state. The watermark advances only after validated ingest and atomic publication, not after a request starts or a response merely returns 200.
4. Re-fetch overlapping dates and alerts that started before the cutoff but remain open. Upsert by origin identity and preserve corrections. Do not restrict ingest to starts after the old snapshot, and do not force an end at midnight. Maintain a coverage ledger; fetch errors, unmapped locations and retention shortfalls leave explicit gaps.
5. Rebuild affected canonical records/episodes and training-feature tails, respecting the 168-hour longest lookback and future label windows. New rows enter supervised training only when their full 1/3/12-hour outcomes are observable. Duration targets require a real end and no censoring. Carry ongoing records into new prediction features even when no completed duration target exists.
6. Publish refreshed prediction artifacts with their own forecast time, data coverage and model version. Nightly retraining, if chosen by the lead, must use only matured labels. Keep the frozen August report and held-out evaluation unchanged; a new source/regime requires a new future evaluation period. The live-status clock and forecast clock must remain visible separately.
7. At startup, verify that the available history actually reaches the watermark. If more was missed than retention allows, use the approved archive/export. Otherwise mark the unrecovered interval unknown and stop claiming complete backfill. Neither rolling-month nor recent-20/25 history meets an unlimited recovery guarantee alone.

If the outage archive is repaired, the lead must decide whether and how to version the existing gap masks and regenerate research artifacts. This research leaves them unchanged.

## Contract change requests

- Add a distinct live-status response and freshness metadata; current `ForecastRow.active` is tied to a forecast hour and cannot represent every minute or display-only polygons cleanly.
- Extend origin records and add observed severity/threat history, with a documented air-only target filter and deduplication rules across mirrors.
- Add stable display IDs for null-forecast polygons and provenance-aware inherited coverage. Confirm Sevastopol status and exclusion-zone alert jurisdiction before claiming direct coverage.
- Define archival retention/export access, nightly watermarks and source coverage masks. Preserve the existing frozen evaluation rather than silently moving its snapshot.

## Delivered

- `docs/research/live-sources.md`, this ranked source study, recommendation, schema proposal, mapping and operating plan.
- `docs/research/samples/r3_*.json`, small real response selections and measured summaries.
- `scripts/r3_probe.py`, read-only repeatable GET, CSV, hierarchy and bounded Telegram overlap probes. It prints results; sample files were written separately. Dependencies added: none.

## Verified

Real commands, not compilation-only claims:

| Command | Actual output |
|---|---|
| `uv run python scripts/r3_probe.py csv --day 2026-09-05` | HTTP 200; 299275 rows; SHA equal to saved CSV; max finish September 7 05:39:33Z; 381/381 exact overlap with saved September 5 rows. |
| `uv run python scripts/r3_probe.py sample https://siren.pp.ua/api/v3/alerts 'https://ubilling.net.ua/aerialalerts/?source=aiu&raw' 'https://ubilling.net.ua/aerialalerts/?source=ual&raw' https://alerts.com.ua/api/states https://api.alerts.in.ua/v3/alerts/active.json` | All five sequential GETs returned 200; saved source samples expose real level arrays, conflicts and compact/private disclaimer. |
| `uv run python scripts/r3_probe.py mapping` | `forecast_universe_count:119`, `mapped_forecast_units:119`, `unmapped_forecast_units:[]`; 1606 source hierarchy nodes. |
| `uv run python scripts/r3_probe.py telegram-day --day 2026-09-05` | 85 requests, 1399 messages, 371 pairs, 359 exact, recall 0.9422572178, precision 0.9676549865; no unclosed sample-day starts. |
| `uv run python scripts/r3_probe.py sample https://api.alerts.in.ua/v3/alerts/day/1657.json https://api.alerts.in.ua/v3/alerts/day/1691.json https://map.ukrainealarm.com/api/v2/data/mapUpdate 'https://siren.pp.ua/api/v3/alerts/regionHistory?regionId=122'` | All 200; archive row counts 518 and 803; official map 54 outer rows; history sample 20 alerts. |
| `uv run ruff check scripts/r3_probe.py` | `All checks passed!` |
| `uv run python scripts/r3_probe.py validate-samples` | 16 JSON samples parsed; 0 forbidden credential fields or credential-bearing URLs; 42832 total sample bytes; 0 broken local sample links. |

The real alerts.in.ua page was opened in the collaborative browser. Selecting history and September 5 produced `/v3/alerts/day/1655.mp` requests with HTTP 200, and the visible map legend explicitly separated red and yellow air alerts. Authenticated supported APIs, a nightly production job and a one-minute application poller were not implemented or verified by this research task.

## What the owner must provide

- An alerts.in.ua project token via a **private secret input to the implementing lead**, after using its official request form. Never put it in this report, samples, a URL, frontend bundle or committed file.
- Provider-approved detailed archive/export access covering the missing period and outage, with confirmation of retention, hromada inclusion, level-transition availability and permission for training/prediction use. The ordinary rolling-month token route alone is insufficient.
- An optional separate official UkraineAlarm key and approved rate allowance for a supported authoritative fallback. The unofficial mirrors already worked without credentials.
- If Telegram is selected despite the limitations, a valid content-use basis and private `api_id`, `api_hash` plus an authorized session; app credentials alone do not log in a user. R3 does not request these.
- Confirmation of Sevastopol's current-source coverage and Chornobyl-zone alert jurisdiction from the relevant provider/operator. Until then, show their evidence and uncertainty explicitly.

## Open risks

Complete September-to-today backfill remains conditional on authorized archive access. The current CSV and official Telegram preview are stopped, recent official history is short, and tested long history is private. No examined source guarantees unlimited downtime recovery.

Yellow is a new reporting regime that begins near the old snapshot. Historical colors are unavailable in the CSV and were absent from the tested official history rows. There is no basis to retrospectively label all old drone notices yellow. Simultaneous levels, scalar/threat contradictions, partial coverage and mutable severity all need explicit handling.

Sevastopol has secondary-source permanent-alert evidence but no independently verified current detailed level. Chornobyl has no independent source unit. They can be shown today; claiming directly observed red/yellow status for both would exceed the evidence.

Sources share upstream systems, have no verified alert-delay SLA here, and can return fresh HTTP envelopes around stale or incomplete content. Aliases, inherited events, sentinel dates and source revisions require validation. A 94.23% one-day Telegram overlap is a parser measurement, not certification of complete history or official ground truth.
