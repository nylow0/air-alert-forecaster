# Text sources for earlier alert warning

R1 research, checked on 2026-10-12. This is a source/access study, not a model evaluation.

## Recommendation

The single best technical candidate is [monitor, `@war_monitor`](https://t.me/war_monitor). It publishes aviation activity, bomber flights, MiG-31K takeoffs and drone headings in Ukrainian. A bomber report in the saved sample preceded alert starts in several previously quiet regions by about 22–31 minutes. An earlier aviation-activity report preceded those starts by about 105–115 minutes. These are examples from one attack, not typical lead times or proof that text improves on alert-history features.

Its no-login preview is deep enough. [The oldest-page probe](https://t.me/s/war_monitor?before=21) returned channel creation at `2022-04-25T20:08:58Z` and substantive posts by 2022-04-30; `?before=1` returned no messages. Full UTC-day samples from December 2025 downloaded successfully. Start the proposed six-month R2 study at **2025-12-01 UTC**, within the raion-regime window. Older visible history reaches April 2022, but I did not download or establish uninterrupted coverage of every intervening day.

Technically, project owner need not provide anything for the preview route. Fetch `https://t.me/s/war_monitor`, parse message IDs and publish times, then request `?before=<oldest_visible_id>` until the chosen start date. Keep requests at about one per second and stop on an error or a cursor that does not advance. This route is sufficient for reach and text samples, and can support a conservative dataset that excludes every message visibly marked edited. It is **not sufficient to apply the plan's precise 10-minute edit rule**, because the preview does not give the last-edit timestamp or old text versions.

There is a separate permission constraint. Telegram's current [content-licensing terms](https://telegram.org/tos/content-licensing) restrict collecting and using platform data for ML development, validation and benchmarking, and describe an exception based on explicit, informed, continuing consent from all relevant users for the specific content/context. [API terms §1.5](https://core.telegram.org/api/terms) also cover ML use. Public visibility, free access and `api_id`/`api_hash` do not establish that permission. My recommendation is **conditional go for `@war_monitor` as a source; no-go for bulk ML ingestion until the relevant consent/licensing basis is established**. This is the requested terms-of-service finding, not a legal determination.

If permission is established and exact edit metadata is needed, project owner must supply a Telegram application `api_id` and `api_hash` from [my.telegram.org](https://core.telegram.org/api/obtaining_api_id), plus an authorized user session. Initial user login needs an account login code and, if enabled, its two-step-verification password. App credentials alone do not authenticate a user. No credentials were requested or used in this research.

## Ranked candidates and the five criteria

Scores are ordinal evidence scores, not model-quality scores. `2` means the criterion is demonstrated or technically well supported; `1` means partial evidence or an unresolved condition; `0` means unsuitable, blocked or absent. H = history reach, T = offset-bearing publish timestamps, A = access/cost, P = permission/terms, L = evidence of pre-onset information. Ranking prioritizes early-warning content and provenance; adding scores would hide the permission constraint.

| Rank and verified source | H | T | A | P | L | Measured preview reach or archive limit | Confidence |
|---|---:|---:|---:|---:|---:|---|---|
| 1. [monitor, `@war_monitor`](https://t.me/s/war_monitor) | 2 | 2 | 2 | 0 | 2 | Earliest visible #1, 2022-04-25 20:08:58Z; substantive #18, 2022-04-30; latest checked #47555, 2026-10-11. Two complete December UTC days obtained. | High access confidence; medium signal confidence, one attack aligned. Unofficial, underlying observations unverified. |
| 2. [Повітряні Сили ЗС України / Air Force of the Armed Forces of Ukraine, `@kpszsu`](https://t.me/s/kpszsu) | 2 | 2 | 2 | 0 | 1 | Earliest #1, 2019-02-11 17:55:59Z; latest initial probe #85501, 2026-10-12. Complete 2025-12-26 obtained. | High identity/access confidence; sample MiG and ballistic warnings followed local alert onset. |
| 3. [monitoring, `@monitorwarr`](https://t.me/s/monitorwarr) | 2 | 2 | 2 | 0 | 1 | Earliest #1, 2022-06-27 11:00:27Z; latest #42406, 2026-10-12. Actual 2023 flight and launch posts retrieved. | High access confidence; medium content confidence; no representative timing study. |
| 4. [Николаевский Ванёк, `@vanek_nikolaev`](https://t.me/s/vanek_nikolaev) | 2 | 2 | 2 | 0 | 1 | Earliest #1, 2022-04-17 00:39:18Z; substantive #20 that day; latest #43340, 2026-10-12. | High handle/access confidence; Russian-language tracking observed; pre-alert lead not measured. |
| 5. [Київська обласна військова адміністрація, `@kyivoda`](https://t.me/s/kyivoda) | 2 | 2 | 2 | 0 | 0 | Earliest #1, 2019-10-31 17:29:44Z; latest #61036, 2026-10-12. | High identity/access confidence. Recent preview heavily repeats raion alerts and all-clears. |
| 6. [Олег Кіпер / Одеська ОДА (ОВА), `@odeskaODA`](https://t.me/s/odeskaODA) | 2 | 2 | 2 | 0 | 1 | Earliest #1, 2022-03-01 04:47:08Z; latest #21731, 2026-10-12. | High identity/access confidence; regional warning/aftermath mix, lead unknown. |
| 7. [Олександр Ганжа / Дніпропетровська ОДА (ОВА), `@dnipropetrovskaODA`](https://t.me/s/dnipropetrovskaODA) | 2 | 2 | 2 | 0 | 0 | Earliest #1, 2022-03-01 05:16:52Z; latest #32556, 2026-10-12. | High handle/access confidence; preview mainly aftermath and administrative news, no demonstrated early lead. |
| 8. [Ukrinform English RSS](https://www.ukrinform.net/rss/block-lastnews), no Telegram handle required | 0 | 2 | 2 | 1 | 0 | HTTP 200, 30 items only, spanning 2026-10-11 19:52 to 2026-10-12 11:05 in `+0300`; no historical paging advertised in this feed. | High feed/timestamp confidence; no useful archive or demonstrated early lead. |
| 9. [ISW daily campaign assessments](https://understandingwar.org/research/russia-ukraine/russian-offensive-campaign-assessment-december-27-2025/), no channel handle asserted | 1 | 0 | 0 | 1 | 0 | Historical assessment dated 2025-12-27 was search-indexed. Direct article, `/feed/` and archive requests returned HTTP 403. Downloadable RSS reach unverified. | Low access confidence. Daily retrospective analysis is a poor fit for imminent launch/onset timing. |

For every tested Telegram message with a visible message footer, the publish time was an ISO datetime with an explicit UTC offset. This does not imply every historical attachment is text-readable or every original post still exists. H=2 records demonstrated early reach and current access, not an exhaustive completeness audit.

The monitoring channels identify themselves as aviation monitoring/OSINT. Their names do not make them official. The Air Force's [own social-links page](https://sites.google.com/view/uaairforce) links to `t.me/kpszsu`; its [own channel announcement](https://t.me/kpszsu/2303) also identifies that handle. Kyiv OVA's [government website](https://koda.gov.ua/) and channel description link the official administration. Government sources identify the [Odesa channel](https://izmail-rda.od.gov.ua/aktualni-novyny-oblasti-u-dva-klika/) and [Dnipropetrovsk channel](https://decentralization.gov.ua/news/14609?page=202). The old label "Serhii Lysak" is not the title currently returned by `@dnipropetrovskaODA`; its measured title is Oleksandr Hanzha. Keep stable handles separate from changing titles.

Two plausible-looking handles failed verification. `@monitoring_ua` currently returned a channel titled `✙` with only a 2016 creation message. `@nikolaev_vanek` returned two December 2022 service posts, rather than the active verified `@vanek_nikolaev`. Neither is an acceptable substitute based on its name.

### Access, cost, terms and limits

All seven ranked Telegram channels returned public previews without cookies, login, credentials, subscription or payment. No published numerical web-preview quota was established. The probe enforces at least 1.05 seconds between request starts per client; the rate is our courtesy limit, not a service guarantee. Stop on HTTP 429/403, honor any retry instructions, and do not bypass restrictions. Channels can disable previews or remove posts later.

The [Telegram API is offered without an API usage fee](https://core.telegram.org/api/obtaining_api_id). [Telethon's `iter_messages`](https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.messages.MessageMethods.iter_messages) can page public channel history with `offset_date`/`offset_id`, expose `date` and `edit_date`, and preserve message IDs. Use an authorized user session, not a Bot API token, for a general historical backfill. Telethon documents approximate, variable history flood waits and a `wait_time` setting; it does not promise a universal safe rate. Handle [FloodWaitError](https://docs.telethon.dev/en/stable/concepts/errors.html) by waiting the server-specified duration, and checkpoint rather than restart a long download. Telethon was documented, not authenticated or tested here.

P=0 for Telegram candidates reflects the current platform ML restriction, not an assertion about an individual channel's copyright status. The consent exception needs to cover the intended research and downstream use, not just reading a channel. No source-specific ML permission was found or requested. Ukrinform's [article footer](https://www.ukrinform.net/amp/rubric-ato/4074111-air-force-shows-how-it-repelled-russias-massive-attack.html) specifies attribution requirements, but that is not a verified blanket ML license. ISW permissions and rate limits remain unassessed because direct access was blocked.

## Measured paging and timestamp behavior

The machine-readable request log is [r1_access_2026-10-12.json](samples/r1_access_2026-10-12.json). For each candidate in that log, the test requested the latest preview, `?before=21`, and `?before=1`. The last request returned zero messages. IDs may be deleted, so counts on an old page are often below 20. The earliest #1 rows are channel-creation service messages, not takeoff reports.

Sequential paging on the selected source actually moved backward:

| Request | Visible IDs | UTC date range | HTTP request time |
|---|---|---|---:|
| `https://t.me/s/war_monitor` | 47536–47555 | 2026-10-10 04:49:56 to 2026-10-11 10:02:55 | 0.104 s |
| `?before=47536` | 47516–47535 | 2026-10-09 06:26:43 to 14:03:24 | 0.106 s |
| `?before=47516` | 47496–47515 | 2026-10-08 14:21:23 to 2026-10-09 06:24:21 | 0.102 s |

These are HTTP latencies, excluding the courtesy wait. Sustained extraction is limited to about 20 visible messages per 1.05-second request interval, and sometimes fewer for deleted posts or media groups. The selected-source 2025-12-27 extraction used 19 requests, including ID bisection and both day boundaries, with median HTTP latency 0.132 s. The 2025-12-26 extraction used 15 requests with median 0.130 s. Around 47,555 message IDs at 20 per page would require roughly 2,378 pages and at least about 42 minutes at this rate; that is a rough lower-bound estimate, not a measured full-history runtime or a completeness claim.

The publish field is the footer's `<time datetime="2023-02-02T13:07:42+00:00" class="time">`, verified on `kpszsu/2303`. Normalize the explicit offset to UTC and serialize with `Z`. Do not parse the displayed `13:07` or clock times inside the prose. On the selected attack, `war_monitor/36062` describes a situation "станом на 00:50" but was published at **22:58:24Z**, later than that stated observation time. Availability begins at publication, never at a takeoff time claimed retrospectively inside the text.

Edited messages are visibly marked `edited` inside `.tgme_widget_message_meta`. The Air Force sample has 11 marked edits among 104 messages; both selected-source sample days have zero visible edit markers. The inspected edited markup retains only the publish `<time>`; no last-edit UTC field was exposed. [Telegram's MTProto message schema](https://core.telegram.org/constructor/message) does provide `edit_date`, described as the last edit date.

The preview supplies the current text, not the original version. Excluding marked edits removes direct exposure to revised text but cannot prove that edit markers are never hidden or that deleted messages were absent at the historical forecast time. Even an API backfill supplies current text and the last edit, not a complete version history. If using edited current text after authorization, require both publication and the last edit to precede forecast time, as well as the plan's edit-delay cutoff. An edit within 10 minutes can still add future information at a forecast between publication and the edit. A prospectively captured version archive would give stronger evidence of what was available at each historical time.

## One-day samples and real alert alignment

Saved primary sample: [r1_war_monitor_2025-12-27.jsonl](samples/r1_war_monitor_2025-12-27.jsonl). It has all 87 visible messages on that UTC day, IDs 36090–36176, from `00:00:43Z` through `22:01:02Z`, no ID gaps and no visible edits. It includes overnight tracking and the following evening; it is not limited to favorable messages.

The attack began on the preceding UTC day, so I also saved [r1_war_monitor_2025-12-26.jsonl](samples/r1_war_monitor_2025-12-26.jsonl), all 34 visible messages, IDs 36056–36089, with no gaps or visible edits. The official comparator is [r1_kpszsu_2025-12-26.jsonl](samples/r1_kpszsu_2025-12-26.jsonl), 104 visible messages, IDs 50771–50874, with 11 visible edits. Every JSONL record has exactly `id`, `date_utc`, `edited` and `text`. `edited` is a boolean marker, not an edit timestamp.

The selected day corresponds to a [large combined attack reported by Ukrinform](https://www.ukrinform.net/amp/rubric-ato/4074111-air-force-shows-how-it-repelled-russias-massive-attack.html). Alert alignment uses the [official dataset](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset), read from A1's existing local copy `data/raw/official_data_en_2026-10-12.csv`; R1 did not alter it. The 31,025,633-byte file has 299,275 rows and SHA-256 `b396f87696243069eec0219880e87dcd642b20beb0a7bae0d0e1f61e1ed20ec4`. Its maximum `started_at` is **2026-09-07T05:28:22Z**, despite the copy's October filename. Do not infer dataset freshness from that filename. All aligned events are in December 2025, before validation/test, and no model or test-set metrics were computed.

The detailed output is [r1_alignment_2025-12-26_27.json](samples/r1_alignment_2025-12-26_27.json). For a previously quiet region, the lead is its first subsequent raw alert start minus publication. For an already-active region, I report the existing start and do not call a later new record warning of the attack already under way. In December's raion regime, "first in oblast" means the earliest covered raion/hromada start, not a fictitious oblast-wide alert. Bomber and MiG events are national signals; they do not name the Ukrainian destinations below.

| Message / UTC publication | Region or named destination | Relevant alert start UTC | Measured timing and interpretation |
|---|---|---|---|
| [36060](https://t.me/war_monitor/36060), Dec 26 21:34:56, strategic-aviation communications | Kyivska oblast; national-signal example | Dec 26 23:20:33, Brovarskyi raion | **105.62 min before**. No active raw record anywhere in Kyivska at publication. Activity report, not confirmed takeoff. |
| [36062](https://t.me/war_monitor/36062), Dec 26 22:58:24, Tu-95/Tu-160 airborne | Kyivska oblast; national-signal example | Dec 26 23:20:33 | **22.15 min before**, region quiet at publication. |
| Same bomber message | Kyiv City; separate unit | Dec 26 23:27:53 | **29.48 min before**, city quiet at publication. |
| Same bomber message | Lvivska oblast; national-signal example | Dec 26 23:29:31, Sambirskyi raion | **31.12 min before**, region quiet at publication. |
| [36073](https://t.me/war_monitor/36073), Dec 26 23:45:24, MiG-31K takeoff | Kyiv City; national signal | Existing start Dec 26 23:27:53 | **17.52 min after onset**. No additional warning of that onset. |
| [36066](https://t.me/war_monitor/36066), Dec 26 23:29:11, ballistic heading to Kyivska | Kyivska oblast, explicitly named | Existing start Dec 26 23:20:33 | **8.63 min after onset**. |
| [36097](https://t.me/war_monitor/36097), Dec 27 00:19:17, six drones heading to Kirovohradska | Kirovohradska oblast, explicitly named | Existing starts Dec 26 23:20:11 | **59.10 min after onset**. The next new raw start at 01:47:42 is 88.42 minutes later, but calling that pre-attack warning would be misleading. |
| [36103](https://t.me/war_monitor/36103), Dec 27 02:05:26, drone groups toward north Kyivska | Kyivska oblast, explicitly named | Existing start Dec 26 23:20:33 | **164.88 min after onset**. |
| [36172](https://t.me/war_monitor/36172), Dec 27 19:08:54, drones toward Odesa port | Odeskyi raion, explicitly named city | Existing start Dec 27 18:57:48 | **11.10 min after onset**. |

The official channel's [MiG post 50864](https://t.me/kpszsu/50864) arrived at `2025-12-26T23:48:41Z`, 3 min 17 s after the monitoring post, but both followed Kyiv City's alert. Its [ballistic warning 50853](https://t.me/kpszsu/50853), `23:28:34Z`, followed Kyiv City's start by 41 seconds. This supports keeping the official source as a comparator, not assuming it supplies extra warning.

Hourly forecasting matters. The bomber post at 22:58:24Z can enter the 23:00Z forecast, only 20 min 33 s before Kyivska's start. The aviation-activity post at 21:34:56Z can enter the 22:00Z forecast, 80 min 33 s before that start. A short MiG warning may occur wholly between forecast ticks. R2 must measure usable lead from the next forecast tick as well as message-to-alert lead.

## Language and concrete keyword examples for R2

The chosen source, Air Force and `@monitorwarr` are mainly Ukrainian. `@vanek_nikolaev` is mainly Russian and uses slang such as `мопеды` for drones. Preserve original UTF-8 text; normalize case, apostrophes, Unicode hyphens and `і`/`и` variants deliberately. Do not silently translate place names or infer that a Russian airfield's oblast is a Ukrainian target. The excerpts below are real messages with original wording and UTC publish times; ellipses mean omitted surrounding text.

### Strategic bombers airborne or taking off

| UTC publication | Original-language excerpt | Source |
|---|---|---|
| 2025-12-06 02:45:00Z | "Станом на 04:40 маємо 3х борти Ту-95 в межах центральних регіонів рф." | [35463](https://t.me/war_monitor/35463) |
| 2025-12-26 22:58:24Z | "4х борти стратавіа Ту-95мс з ае \"Бєлая\", Іркутської області" | [36062](https://t.me/war_monitor/36062) |
| 2026-01-20 01:56:57Z | "По Ту-95: 4 борти з Оленьї." | [36778](https://t.me/war_monitor/36778) |
| 2026-02-07 00:07:27Z | "4х борти Ту-95мс з аеродрому Оленья" | [37354](https://t.me/war_monitor/37354) |
| 2023-06-03 20:31:12Z | "Попередньо активність стратавіа, 6х Ту-95мс у межах Західного регіону." | [monitorwarr/11784](https://t.me/monitorwarr/11784) |

Require aircraft names with an action/context such as `зліт`, `виліт`, `у повітрі`, `борти`, `стратавіа`. Separate confirmed flight, preliminary flight, communications/activity and launch. Explicit `тренувальний рейс`, `перебазування`, `не активна`, `без вильотів`, `не злітали` are different states. For example, [monitorwarr/6703](https://t.me/monitorwarr/6703), `2023-01-13T09:20:02Z`, says "Тренувальний рейс" and is visibly edited. An aircraft-name-only rule also matches overnight summaries, aircraft destroyed by Ukraine, and fundraising/news posts.

### MiG-31K takeoff

| UTC publication | Original-language excerpt | Source |
|---|---|---|
| 2025-12-13 00:23:32Z | "Зліт МіГ-31К ВПС рф." | [35683](https://t.me/war_monitor/35683) |
| 2025-12-13 00:24:47Z | "3 борти МіГ-31К у повітрі." | [35684](https://t.me/war_monitor/35684) |
| 2025-12-23 05:13:45Z | "Зліт МіГ-31К ВПС рф." | [35990](https://t.me/war_monitor/35990) |
| 2025-12-26 23:45:24Z | "Зліт МіГ-31К ВПС рф." | [36073](https://t.me/war_monitor/36073) |

Support `МіГ-31К`, `МиГ-31К`, `Миг-31К`, spacing/hyphen variants and Russian `взлёт`/`взлет`. Treat `Відбій загрози МіГ-31К` as all-clear, not takeoff. De-duplicate repeated takeoff/status updates before estimating event frequencies.

### Drone groups and heading

| UTC publication | Original-language excerpt | Source |
|---|---|---|
| 2025-12-27 00:19:17Z | "6х БпЛА через Миколаївський та Баштанський райони у напрямку Кіровоградщини." | [36097](https://t.me/war_monitor/36097) |
| 2025-12-27 00:49:04Z | "Миколаївщина: ... 5х БпЛА у напрямку Кіровоградщини." | [36098](https://t.me/war_monitor/36098) |
| 2025-12-27 02:05:26Z | "Декілька груп БпЛА через Чернігівщину тримають курс на північ Київщини." | [36103](https://t.me/war_monitor/36103) |
| 2025-12-27 02:39:16Z | "Нові групи БпЛА з Херсонщини на Криворіжжя Дніпропетровської області та Миколаївщину." | [36107](https://t.me/war_monitor/36107) |
| 2025-12-27 04:29:19Z | "25х+ БпЛА через Миколаївщину у напрямку Черкащини." | [36124](https://t.me/war_monitor/36124) |

Useful terms include `БпЛА`, `БПЛА`, `шахед`, `Shahed`, `груп`, `пуски`, `у напрямку`, `курсом на`, `прямують`, plus Russian `БПЛА`, `шахед`, `мопед`, `группа`, `курс`, `в направлении`. Extract origin/transit and destination separately. In 36097, Mykolaiv is transit and Kirovohrad is the destination. A message can have several destinations; its header place is not always the threatened future destination. Keep city-to-oblast mapping explicit, including Kyiv City versus Kyivska oblast.

### Ballistic threat or launch

| UTC publication | Original-language excerpt | Source |
|---|---|---|
| 2025-12-26 23:28:31Z | "Загроза балістики з Північного Сходу ... Імовірний пуск ракет Іскандер" | [36065](https://t.me/war_monitor/36065) |
| 2025-12-26 23:29:11Z | "Балістика вектор Київщина" | [36066](https://t.me/war_monitor/36066) |
| 2025-12-27 00:12:18Z | "Київ / Бровари — спуск балістики!" | [36095](https://t.me/war_monitor/36095) |
| 2025-12-26 23:28:34Z | "Загроза застосування балістичного озброєння з брянська." | [kpszsu/50853](https://t.me/kpszsu/50853) |

Use `баліст`, Russian `баллист`, `Іскандер`, `Кинджал`, `загроза`, `пуск`, `вихід`, `спуск` and Russian `угроза`, `пуск`. Threat, detected launch and imminent descent are distinct phases. `з Брянська` describes an origin outside Ukraine, not an oblast assignment. `Кинджал` in a standard description of the MiG's weapon does not imply a separate missile-launch event.

### Retrospective summary and all-clear

| UTC publication | Original-language excerpt | Source |
|---|---|---|
| 2025-12-26 01:06:45Z | "Одеса — чисто." | [36058](https://t.me/war_monitor/36058) |
| 2025-12-27 09:09:48Z | "В ніч на та вранці 27.12.25 ворог завдав комбінованого ракетно-дронового удару по Україні." | [36166](https://t.me/war_monitor/36166) |
| 2025-12-27 12:02:16Z | "У ніч на 27.12.25, згідно зі звітом Повітряних Сил, Силами оборони було знищено" | [36171](https://t.me/war_monitor/36171) |

Exclude `зведення`, `у ніч на`, `збито`, `знищено`, `подавлено`, `відбій`, `чисто` from immediate launch tags unless the message also clearly contains a separate new event. A retrospective summary may be usable as a past-event feature after its own publication, never as a reconstructed earlier warning.

## Sources to exclude and remaining risks

- [`@air_alert_ua`, "Повітряна Тривога"](https://t.me/s/air_alert_ua) is an official alert-notification channel, not a primary pre-alert flight source. The measured page contains alert/all-clear templates and threat notices during active alerts. Its preview reaches 2022-03-14, but much of the text repeats the target dataset. Exclude it from the proposed incremental text feature set. Kyiv OVA alert templates need the same filtering. Mixed channels should retain genuinely new observation posts only.
- Flight information is sometimes preliminary, wrong, retracted or merely training. The samples establish that early aviation text exists, not its precision or causal connection to the later attack.
- The alert-history model may already see active neighboring regions. Earlier publication relative to one destination is not evidence of added information beyond those features. R2 must compare matched rows and conditioning on the same quiet/active state, then measure lift and false positives across at least six months.
- Preview reach is not archival completeness. Deleted IDs, current edited text, hidden edit markers, media-only observations, forwarded quotations and changed handles can distort a historical backtest. The parser reads the outer message's publish time; it does not move forwarded/replied-to content back to the quoted author's date.
- Generic geography words can misassign Russia's airfields to Ukrainian targets. Separate national aviation events, Ukrainian transit places and Ukrainian destinations. The chosen source works at national/oblast level, as planned; this probe does not propose new raion-resolution contracts.
- The local alert snapshot is older than the acquisition filename. R2 should pin both message cache and alert CSV by hashes and report their actual timestamp coverage. Do not fill later alerts with assumed negatives.
- The consent/licensing basis and exact edit handling remain the stage-2 blockers. No six-month ingestion, model fitting, credentials, account login or external messages were attempted.

## Reproduction and verification

The read-only probe is [scripts/r1_probe.py](../../scripts/r1_probe.py). It uses the current `selectolax.lexbor` parser. The deprecated `selectolax.parser.HTMLParser` backend failed in this environment, so the probe does not depend on it. It emits JSON to stdout and writes no download files; the checked-in samples and logs are the captured results. Temporary dependencies are provided by `uv --no-project` and do not modify the shared dependency files.

```powershell
uv run --no-project --with httpx --with selectolax python scripts/r1_probe.py pages --channel war_monitor --before 21 --before 1 --sequential 2
uv run --no-project --with httpx --with selectolax python scripts/r1_probe.py day --channel war_monitor --date 2025-12-27
uv run --no-project --with httpx --with selectolax python scripts/r1_probe.py day --channel war_monitor --date 2025-12-26
uv run --no-project --with httpx --with selectolax python scripts/r1_probe.py day --channel kpszsu --date 2025-12-26
uv run --no-project --with httpx --with selectolax python scripts/r1_probe.py validate --sample docs/research/samples/r1_war_monitor_2025-12-27.jsonl
uv run --no-project --with httpx --with selectolax python scripts/r1_probe.py align --sample docs/research/samples/r1_war_monitor_2025-12-26.jsonl --csv data/raw/official_data_en_2026-10-12.csv --ids 36060,36062,36065,36066,36073 --oblast 'Kyiv City' --oblast 'Kyivska oblast' --oblast 'Cherkaska oblast' --oblast 'Lvivska oblast' --oblast 'Khmelnytska oblast' --oblast 'Zhytomyrska oblast' --oblast 'Kirovohradska oblast'
uv run --no-project --with httpx --with selectolax python scripts/r1_probe.py align --sample docs/research/samples/r1_war_monitor_2025-12-27.jsonl --csv data/raw/official_data_en_2026-10-12.csv --ids 36097,36098,36103,36107,36124,36156,36172 --oblast 'Kirovohradska oblast' --oblast 'Kyivska oblast' --oblast 'Dnipropetrovska oblast' --oblast 'Cherkaska oblast' --oblast 'Zhytomyrska oblast' --oblast 'Odeska oblast'
uv run --no-project --with ruff ruff check scripts/r1_probe.py
```

Actual validation output for the primary sample was `messages=87`, `days=[2025-12-27]`, `edited=0`, `first_id=36090`, `last_id=36176`, `id_gaps=0`. Supplementary validation returned 34 messages and 0 edits for monitor on December 26, and 104 messages and 11 edits for the official comparator. Ruff returned `All checks passed!`. The access and alignment commands above were run against real public previews and the actual local official CSV; they were not mocked.

Delivered: this document, the three UTC-day JSONL samples, the access log, the alignment evidence and `scripts/r1_probe.py`.

Verified: public old-page reach, sequential paging, UTC publish markup, visible edit markers, full-day boundaries/IDs, RSS timestamps, real alert alignment, sample validation and Ruff.

Decisions/assumptions: recommend `@war_monitor` for the conditional stage-2 study; start at December 2025; use actual publication times; timing examples do not estimate typical lift or justify model selection.

Open issues: platform consent/licensing, no precise edit time in preview, historical deletions/versions, six-month signal frequency and incremental value, and the alert CSV's actual freshness.

Contract change requests: none. Any integration of edit availability or consent metadata belongs to the lead/T1 decision, not an R1 contract edit.

Dependencies added: none to the project. `httpx`, `selectolax` and `ruff` were used through temporary `uv --no-project` environments.
