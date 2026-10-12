"""Read-only R3 source probes. Print small evidence; never persist HTTP bodies or secrets."""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CSV_URL = (
    "https://raw.githubusercontent.com/Vadimkin/"
    "ukrainian-air-raid-sirens-dataset/main/datasets/official_data_en.csv"
)


def get(url: str) -> httpx.Response:
    """Only public HTTPS/HTTP GETs, without cookies or authorization."""
    if re.search(r"token|api_key|apikey|authorization", urlsplit(url).query, re.I):
        raise ValueError("Credential-bearing query rejected")
    return httpx.get(
        url,
        timeout=35,
        follow_redirects=True,
        headers={"User-Agent": "AirAlertForecaster-R3-research/1.0"},
    )


def text_only(body: str) -> str:
    """Keep readable source text without scripts, tags or repeated whitespace."""
    body = re.sub(r"<(script|style)\b.*?</\1>", "", body, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()


def redact(value: str) -> str:
    """Remove possible embedded credentials before source excerpts reach stdout."""
    value = re.sub(
        r"(?i)(token|api_key|apikey|api_hash|authorization)(\s*[=:]\s*[\"']?)[^\s\"'&,;]+",
        r"\1\2[REDACTED]",
        value,
    )
    return value


def inspect(url: str, terms: list[str], limit: int) -> dict[str, object]:
    """Find short literal-context excerpts in first-party HTML, JS or collector code."""
    response = get(url)
    body = text_only(response.text) if "text/html" in response.headers.get("content-type", "") \
        else response.text
    matches: dict[str, list[str]] = {}
    for term in terms:
        positions = list(re.finditer(re.escape(term), body, re.I))[:8]
        matches[term] = [
            redact(body[max(0, match.start() - 140) : match.end() + limit])
            for match in positions
        ]
    return {"url": url, "status": response.status_code, "matches": matches}


def sample(url: str) -> dict[str, object]:
    """Summarize an entire JSON feed and retain representative real rows."""
    response = get(url)
    result: dict[str, object] = {
        "url": url,
        "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": response.status_code,
        "bytes": len(response.content),
        "headers": {
            key: value
            for key, value in response.headers.items()
            if key in {"date", "last-modified", "cache-control", "etag"}
        },
    }
    if response.status_code != 200:
        result["error_body"] = text_only(response.text)[:300]
        return result
    data = response.json()
    if isinstance(data, dict):
        result["keys"] = list(data)
        result["metadata"] = {
            key: value for key, value in data.items()
            if key in {"source", "cachedat", "last_update", "meta", "disclaimer"}
        }
        rows = data.get("raw", data.get("alerts", data.get("states", [])))
    else:
        rows = data
    if not isinstance(rows, list):
        result["data"] = rows
        return result
    result["row_count"] = len(rows)
    if rows and "activeAlerts" in rows[0]:
        entries = [item for row in rows for item in row.get("activeAlerts", [])]
        unique = {
            (item["regionId"], item["type"], item["lastUpdate"]): item for item in entries
        }
        result["unique_origin_alerts"] = len(unique)
        result["origin_type_counts"] = dict(Counter(item["type"] for item in unique.values()))
        result["origin_level_counts"] = dict(Counter(
            level.get("alertLevel", "missing") for item in unique.values()
            for level in item.get("activeAlertLevels", [])
        ))
        result["region_type_counts"] = dict(Counter(row["regionType"] for row in rows))
        special = [row for row in rows if re.search(
            "Луган|Крим|Севаст|Чорноб|Чорнобиль", row["regionName"], re.I
        )]
        mixed = [row for row in rows if any(len({
            level.get("alertLevel") for level in item.get("activeAlertLevels", [])
        }) > 1 for item in row["activeAlerts"])]
        yellow = [row for row in rows if any(
            level.get("alertLevel") == "Yellow" for item in row["activeAlerts"]
            for level in item.get("activeAlertLevels", [])
        )]
        result["sample_rows"] = special + mixed[:1] + yellow[:1]
    elif rows and "alert_type" in rows[0]:
        result["type_counts"] = dict(Counter(row["alert_type"] for row in rows))
        result["level_counts"] = dict(Counter(row.get("alert_level", "missing") for row in rows))
        special = [row for row in rows if re.search(
            "Луган|Крим|Севаст|Чорноб", row.get("location_title", ""), re.I
        )]
        yellow = [row for row in rows if row.get("alert_level") == "yellow"]
        conflicts = [row for row in rows if row.get("alert_level") == "yellow" and any(
            threat.get("level") == "red" for threat in row.get("threats", [])
        )]
        result["scalar_yellow_with_red_threat_count"] = len(conflicts)
        result["sample_rows"] = special + yellow[:1] + conflicts[:1]
    elif rows and "alert" in rows[0]:
        result["active_rows"] = [row for row in rows if row["alert"]]
        result["sample_rows"] = rows[:2]
    else:
        result["sample_rows"] = rows[:4]
    return result


def mapping_probe() -> dict[str, object]:
    """Join public source hierarchy to dataset names, without editing the crosswalk."""
    from airalert.ids import raion_id

    en = pd.read_csv(ROOT / "data/raw/official_data_en_2026-10-12.csv").fillna("")
    response = get(CSV_URL.replace("_en.csv", "_uk.csv"))
    response.raise_for_status()
    uk = pd.read_csv(io.BytesIO(response.content)).fillna("")
    if len(en) != len(uk) or not en[["started_at", "finished_at", "level"]].equals(
        uk[["started_at", "finished_at", "level"]]
    ):
        raise ValueError("Dataset language editions do not align")
    pairs = pd.DataFrame({
        "oblast_uk": uk["oblast"], "raion_uk": uk["raion"],
        "oblast_en": en["oblast"], "raion_en": en["raion"],
    }).drop_duplicates()
    targets: dict[tuple[str, str], str] = {}
    for row in pairs.itertuples(index=False):
        targets[(str(row.oblast_uk), str(row.raion_uk))] = raion_id(
            str(row.oblast_en), str(row.raion_en)
        )
    universe = set(pd.read_csv(ROOT / "data/processed/raions.csv")["raion_id"])
    tree = get("https://siren.pp.ua/api/v3/regions").json()
    flat: list[dict[str, object]] = []

    def walk(node: dict[str, object], oblast: str, district: str) -> None:
        name = str(node["regionName"])
        kind = node["regionType"]
        if kind == "State":
            oblast = name
        elif kind == "District":
            district = name
        target = targets.get((oblast, district))
        if target is None and district == "Новомосковський район":
            target = targets.get((oblast, "Самарівський район"))
        if target is None and district == "Новоград-Волинський район":
            target = targets.get((oblast, "Звягельський район"))
        if target is None and district == "Шептицький район":
            target = targets.get((oblast, "Червоноградський район"))
        flat.append({"source_uid": str(node["regionId"]), "name": name, "type": kind,
                     "oblast": oblast, "district": district,
                     "forecast_raion_id": target if target in universe else None})
        for child in node.get("regionChildIds", []):  # type: ignore[union-attr]
            walk(child, oblast, district)

    for node in tree["states"]:
        walk(node, "", "")
    mapped = {str(row["forecast_raion_id"]) for row in flat if row["forecast_raion_id"]}
    special = [row for row in flat if row["type"] != "Community" and (re.search(
        "Луган|Крим|Севаст|Чорноб|Вишгород|Криворіз|Самарів|Новомосков|Звягел|Новоград",
        str(row["name"]), re.I
    ) or row["oblast"] == "Луганська область")]
    return {
        "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": "https://siren.pp.ua/api/v3/regions",
        "language_rows_aligned": True,
        "top_nodes": len(tree["states"]), "node_count": len(flat),
        "type_counts": dict(Counter(str(row["type"]) for row in flat)),
        "forecast_universe_count": len(universe), "mapped_forecast_units": len(mapped),
        "unmapped_forecast_units": sorted(universe - mapped),
        "special_source_nodes": special,
        "sample_nodes": flat[:3],
        "unmatched_map_polygons": pd.read_csv(ROOT / "data/geo/crosswalk.csv").fillna("")
        .query("raion_id == ''").to_dict("records"),
    }


def fetch(url: str, limit: int) -> dict[str, object]:
    started = time.monotonic()
    result: dict[str, object] = {
        "url": url,
        "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    try:
        response = get(url)
        body = response.text
        result.update(
            status=response.status_code,
            final_url=str(response.url),
            elapsed_s=round(time.monotonic() - started, 3),
            bytes=len(response.content),
            sha256=hashlib.sha256(response.content).hexdigest(),
            headers={
                key: value
                for key, value in response.headers.items()
                if key in {"date", "last-modified", "etag", "content-type", "cache-control"}
            },
        )
        try:
            data = response.json()
            result["json"] = data if len(body) <= limit else {"excerpt": body[:limit]}
            if isinstance(data, list):
                result["item_count"] = len(data)
            if isinstance(data, dict):
                result["keys"] = list(data)[:35]
        except ValueError:
            result["text"] = text_only(body)[:limit]
            result["links"] = list(
                dict.fromkeys(
                    urljoin(str(response.url), item)
                    for item in re.findall(r'(?:href|src)=["\']([^"\']+)', body)
                    if not item.startswith(("data:", "mailto:", "#"))
                )
            )[:55]
            if "githubusercontent.com" in url:
                result["text"] = body[:limit]
    except (httpx.HTTPError, ValueError) as exc:
        result["error"] = str(exc)
    return result


def csv_probe(day: str) -> dict[str, object]:
    """Compare the live upstream CSV to the saved snapshot without saving a download."""
    response = get(CSV_URL)
    response.raise_for_status()
    frame = pd.read_csv(io.BytesIO(response.content))
    saved = pd.read_csv(ROOT / "data/raw/official_data_en_2026-10-12.csv")
    start = pd.Timestamp(day, tz="UTC")
    end = start + pd.Timedelta(days=1)
    times = pd.to_datetime(frame["started_at"], utc=True)
    subset = frame.loc[(times >= start) & (times < end)].drop_duplicates()
    saved_times = pd.to_datetime(saved["started_at"], utc=True)
    old = saved.loc[(saved_times >= start) & (saved_times < end)].drop_duplicates()
    keys = list(frame.columns)
    new_set = set(map(tuple, subset[keys].fillna("").to_numpy()))
    old_set = set(map(tuple, old[keys].fillna("").to_numpy()))
    gap_start = pd.Timestamp("2026-08-29T05:55:00Z")
    gap_end = pd.Timestamp("2026-09-02T13:48:00Z")
    return {
        "url": CSV_URL,
        "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": response.status_code,
        "bytes": len(response.content),
        "sha256": hashlib.sha256(response.content).hexdigest(),
        "saved_sha256": hashlib.sha256(
            (ROOT / "data/raw/official_data_en_2026-10-12.csv").read_bytes()
        ).hexdigest(),
        "rows": len(frame),
        "unique_rows": len(frame.drop_duplicates()),
        "columns": list(frame.columns),
        "max_started_at": times.max().isoformat(),
        "max_finished_at": pd.to_datetime(frame["finished_at"], utc=True).max().isoformat(),
        "starts_inside_known_gap": int(((times > gap_start) & (times < gap_end)).sum()),
        "day_utc": day,
        "day_raw_rows": int(((times >= start) & (times < end)).sum()),
        "day_unique_rows": len(subset),
        "saved_day_unique_rows": len(old),
        "exact_common_rows": len(new_set & old_set),
        "live_only": len(new_set - old_set),
        "saved_only": len(old_set - new_set),
        "day_level_counts": subset["level"].value_counts().to_dict(),
        "day_sample": subset.head(4).fillna("").to_dict("records"),
    }


def telegram(url: str) -> dict[str, object]:
    response = get(url)
    body = response.text
    blocks = re.split(r'<div class="tgme_widget_message_wrap', body)[1:]
    messages: list[dict[str, object]] = []
    for block in blocks:
        identifier = re.search(r'data-post="([^\"]+)"', block)
        timestamp = re.search(r'<time[^>]+datetime="([^\"]+)"', block)
        content = re.search(
            r'<div class="tgme_widget_message_text[^\"]*"[^>]*>(.*?)</div>', block, re.S
        )
        if identifier and timestamp:
            messages.append(
                {
                    "id": identifier[1],
                    "published_at": timestamp[1],
                    "text": text_only(content[1]) if content else "",
                    "edited": bool(re.search(r">edited\s*<", block)),
                }
            )
    return {
        "url": url,
        "status": response.status_code,
        "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "message_count": len(messages),
        "messages": messages,
    }


def telegram_day(day: str) -> dict[str, object]:
    """Bounded public-preview overlap measurement; no synthetic ends or ML ingestion."""
    en = pd.read_csv(ROOT / "data/raw/official_data_en_2026-10-12.csv").fillna("")
    uk_response = get(CSV_URL.replace("_en.csv", "_uk.csv"))
    uk_response.raise_for_status()
    uk = pd.read_csv(io.BytesIO(uk_response.content)).fillna("")
    aligned = len(en) == len(uk) and en[["started_at", "finished_at", "level"]].equals(
        uk[["started_at", "finished_at", "level"]]
    )
    if not aligned:
        raise ValueError("English/Ukrainian row alignment changed; do not guess mappings")
    start = pd.Timestamp(day, tz="UTC")
    end = start + pd.Timedelta(days=1)
    times = pd.to_datetime(en["started_at"], utc=True)
    target = en.loc[(times >= start) & (times < end)].drop_duplicates()
    latest_end = pd.to_datetime(target["finished_at"], utc=True).max()
    tags: dict[str, set[tuple[str, str, str, str]]] = {}
    for index in target.index:
        source_row = uk.loc[index]
        name = source_row[str(source_row["level"])]
        tag = "#" + re.sub(r"[-.'’]", "", str(name).lower()).replace(" ", "_")
        row = en.loc[index]
        location = tuple(str(row[key]) for key in ["oblast", "raion", "hromada", "level"])
        tags.setdefault(tag, set()).add(location)
    cutoff = latest_end + pd.Timedelta(minutes=2)
    request_count = 0

    def page(cursor: int) -> list[dict[str, object]]:
        nonlocal request_count
        time.sleep(1)
        result = telegram(f"https://t.me/s/air_alert_ua?before={cursor}")
        request_count += 1
        if result["status"] != 200:
            raise ValueError(f"Preview HTTP {result['status']}")
        return result["messages"]  # type: ignore[return-value]

    low, high = 1, 338381
    for _ in range(20):
        if high - low <= 20:
            break
        midpoint = (low + high) // 2
        messages = page(midpoint)
        if not messages:
            low = midpoint
            continue
        newest = max(pd.Timestamp(str(message["published_at"])) for message in messages)
        if newest < cutoff:
            low = midpoint
        else:
            high = midpoint
    cursor = min(338381, high + 25)
    downloaded: dict[int, dict[str, object]] = {}
    exhausted = False
    for page_index in range(130):
        messages = page(cursor)
        if not messages:
            exhausted = True
            break
        identifiers = [int(str(message["id"]).split("/")[-1]) for message in messages]
        for identifier, message in zip(identifiers, messages, strict=True):
            downloaded[identifier] = message
        new_cursor = min(identifiers)
        oldest = min(pd.Timestamp(str(message["published_at"])) for message in messages)
        if oldest < start:
            exhausted = True
            break
        if new_cursor >= cursor:
            raise ValueError("Telegram cursor failed to advance")
        cursor = new_cursor
        if page_index % 20 == 19:
            print(json.dumps({"progress": "telegram_day", "requests": request_count,
                              "oldest": oldest.isoformat()}), flush=True)
    if not exhausted:
        raise ValueError("Bounded 130-page day probe exhausted before start boundary")
    active: dict[str, str] = {}
    pairs: set[tuple[str, ...]] = set()
    duplicate_starts = 0
    threat_messages = 0
    orphan_ends = 0
    ambiguous_tags = {tag: list(locations) for tag, locations in tags.items() if len(locations) > 1}
    for identifier in sorted(downloaded):
        message = downloaded[identifier]
        timestamp = pd.Timestamp(str(message["published_at"]))
        text = str(message["text"])
        first = re.sub(r"^[🔴🟢🟡🟠\s0-9:]+", "", text)
        is_start = first.startswith("Повітряна тривога")
        is_end = first.startswith("Відбій тривоги")
        if "Загроза" in text and not is_start and not is_end:
            threat_messages += 1
        if not is_start and not is_end:
            continue
        message_tags = re.findall(r"#[\w’'\-]+", text.lower())
        for tag in message_tags:
            if tag not in tags or len(tags[tag]) != 1:
                continue
            if is_start and start <= timestamp < end:
                if tag in active:
                    duplicate_starts += 1
                    # Keep the first observed start; escalation notices do not restart it.
                    continue
                active[tag] = timestamp.isoformat()
            if is_end:
                if tag in active:
                    location = next(iter(tags[tag]))
                    pairs.add((*location, active.pop(tag), timestamp.isoformat()))
                else:
                    orphan_ends += 1
    expected = {tuple(str(row[key]) for key in ["oblast", "raion", "hromada", "level"])
                + (pd.Timestamp(str(row["started_at"])).isoformat(),
                   pd.Timestamp(str(row["finished_at"])).isoformat())
                for row in target.to_dict("records")}
    matched = pairs & expected
    missing = expected - pairs
    extra = pairs - expected
    return {
        "day_utc": day,
        "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "english_ukrainian_rows_aligned": aligned,
        "requests": request_count,
        "downloaded_messages": len(downloaded),
        "first_message": downloaded[min(downloaded)],
        "last_message": downloaded[max(downloaded)],
        "expected_csv_unique_events": len(expected),
        "reconstructed_pairs": len(pairs),
        "exact_pairs": len(matched),
        "pair_recall": len(matched) / len(expected),
        "pair_precision": len(matched) / len(pairs) if pairs else None,
        "csv_only": len(missing),
        "telegram_only": len(extra),
        "unclosed_day_starts": len(active),
        "duplicate_start_messages": duplicate_starts,
        "non_onset_threat_messages": threat_messages,
        "orphan_end_messages_for_sample_locations": orphan_ends,
        "ambiguous_location_tags": ambiguous_tags,
        "missing_sample": sorted(missing)[:6],
        "extra_sample": sorted(extra)[:6],
    }


def validate_samples() -> dict[str, object]:
    """Check saved research artifacts for valid JSON and credential fields/URLs."""
    paths = sorted((ROOT / "docs/research/samples").glob("r3_*.json"))
    violations: list[str] = []
    forbidden = {"authorization", "api_key", "api_token", "api_hash", "api_session_string"}

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in forbidden:
                    violations.append(str(key))
                if key in {"url", "final_url"} and isinstance(item, str):
                    names = {name.lower() for name in parse_qs(urlsplit(item).query)}
                    if names.intersection({"token", "api_key", "apikey"}):
                        violations.append("credential-bearing URL")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for path in paths:
        walk(json.loads(path.read_text(encoding="utf-8")))
    if violations:
        raise ValueError(f"Credential scan failed: {violations}")
    report = ROOT / "docs/research/live-sources.md"
    linked_samples = re.findall(
        r"\]\((samples/[^)]+)\)", report.read_text(encoding="utf-8")
    )
    broken = [target for target in linked_samples if not (report.parent / target).is_file()]
    if broken:
        raise ValueError(f"Broken sample links: {broken}")
    return {"json_samples_parsed": len(paths), "forbidden_credential_fields_or_urls": 0,
            "total_sample_bytes": sum(path.stat().st_size for path in paths),
            "broken_local_sample_links": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    fetch_parser = sub.add_parser("fetch")
    fetch_parser.add_argument("urls", nargs="+")
    fetch_parser.add_argument("--limit", type=int, default=2500)
    csv_parser = sub.add_parser("csv")
    csv_parser.add_argument("--day", default="2026-09-05")
    telegram_parser = sub.add_parser("telegram")
    telegram_parser.add_argument("urls", nargs="+")
    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("url")
    inspect_parser.add_argument("terms", nargs="+")
    inspect_parser.add_argument("--limit", type=int, default=250)
    sample_parser = sub.add_parser("sample")
    sample_parser.add_argument("urls", nargs="+")
    day_parser = sub.add_parser("telegram-day")
    day_parser.add_argument("--day", default="2026-09-05")
    sub.add_parser("mapping")
    sub.add_parser("validate-samples")
    args = parser.parse_args()
    if args.mode == "fetch":
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda url: fetch(url, args.limit), args.urls))
    elif args.mode == "csv":
        results = [csv_probe(args.day)]
    elif args.mode == "inspect":
        results = [inspect(args.url, args.terms, args.limit)]
    elif args.mode == "sample":
        results = []
        for url in args.urls:
            results.append(sample(url))
            # Shared-IP mirrors can reject even a two-request burst.
            time.sleep(1)
    elif args.mode == "telegram-day":
        results = [telegram_day(args.day)]
    elif args.mode == "mapping":
        results = [mapping_probe()]
    elif args.mode == "validate-samples":
        results = [validate_samples()]
    else:
        results = [telegram(url) for url in args.urls]
    sys.stdout.reconfigure(encoding="utf-8")
    print(redact(json.dumps(results, ensure_ascii=False)))


if __name__ == "__main__":
    main()
