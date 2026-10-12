"""Public-map day archive, decoded from 08634fab.js and locations.json v5.

The endpoint is private/versioned upstream; fail on unknown schema or locations.
War-day 1 is 2022-02-24; its calendar label is Kyiv-local, including DST days.
"""

import gzip
import json
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import httpx

from airalert.config import KYIV_TZ
from airalert.sources import Event, Level, array, mapping
from airalert.sources.siren import USER_AGENT
from airalert.sources.storage import cache_response

_REQUEST_LOCK = Lock()
_LAST_REQUEST = 0.0
_EPOCH = 0x61C06A00  # First-party _0x1544f5 timestamp decoder's base, 2021-12-20 UTC.
HAZARDS = {
    0: "air_raid", 1: "artillery_shelling", 2: "urban_fights", 3: "warning",
    4: "chemical", 5: "nuclear", 6: "info", 90: "potential_threat", 99: "occupation",
}


def war_day(day: date) -> int:
    return (day - date(2022, 2, 24)).days + 1


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Construct each local midnight independently so DST days can have 25 hours."""
    zone = ZoneInfo(KYIV_TZ)
    return (
        datetime.combine(day, datetime.min.time(), zone).astimezone(UTC),
        datetime.combine(day + timedelta(days=1), datetime.min.time(), zone).astimezone(UTC),
    )


def fetch_json(url: str, path: Path, *, refresh: bool = False) -> object:
    global _LAST_REQUEST
    if path.exists() and not refresh:
        return json.loads(gzip.decompress(path.read_bytes()))
    with _REQUEST_LOCK:
        time.sleep(max(0.0, 2.0 - (time.monotonic() - _LAST_REQUEST)))
        _LAST_REQUEST = time.monotonic()
        response = httpx.get(url, timeout=40, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        payload = response.json()
        cache_response(path, response.content)
        return payload


def fetch_day(day: date, raw_dir: Path, *, refresh: bool = False) -> object:
    return fetch_json(
        f"https://api.alerts.in.ua/v3/alerts/day/{war_day(day)}.json",
        raw_dir / f"{day.isoformat()}.json.gz", refresh=refresh,
    )


def fetch_dictionary(raw_dir: Path) -> list[object]:
    return array(fetch_json(
        "https://cdn.alerts.in.ua/assets/locations.json?v=5", raw_dir / "locations-v5.json.gz"
    ))


def compact_time(value: object) -> datetime | None:
    if value is None or value == 0:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError("Compact timestamp must be a number")
    return datetime.fromtimestamp(value if value > _EPOCH else value + _EPOCH, UTC)


def decode(payload: object, dictionary: list[object]) -> list[Event]:
    root = mapping(payload)
    if mapping(root["meta"]).get("type") != "compact":
        raise ValueError("Archive schema changed, expected compact")
    events: list[Event] = []
    for value in array(root["alerts"]):
        row = mapping(value)
        if row.get("cnt", "ua") not in {"ua", None}:
            continue
        start, end, update = (compact_time(row.get(key)) for key in ("s", "f", "u"))
        if start is None or update is None or end is not None and end < start:
            raise ValueError("Archive alert requires a valid start/update and nonnegative duration")
        hazard_code = row.get("at", 0)
        if hazard_code not in HAZARDS:
            raise ValueError(f"Unrecognized archive hazard {hazard_code}")
        location_name = str(row.get("n", ""))
        if "ni" in row:
            index = row["ni"]
            if not isinstance(index, int) or not 0 <= index < len(dictionary):
                raise ValueError("Archive dictionary index out of range")
            location_name = str(mapping(dictionary[index])["title"])
        oblast_name = ""
        if "loi" in row:
            index = row["loi"]
            if not isinstance(index, int) or not 0 <= index < len(dictionary):
                raise ValueError("Archive oblast dictionary index out of range")
            oblast_name = str(mapping(dictionary[index])["title"])
        origin = row.get("luid")
        if origin is None:
            origin = "name:" + location_name
        explicit = "al" in row or "alert_level" in row
        severity = row.get("alert_level", (
            "yellow" if row.get("al") == 1 else "red" if "al" in row else "unknown"
        ))
        if row.get("al", 0) not in {0, 1}:
            raise ValueError("Unrecognized archive severity")
        if severity not in {"red", "yellow", "unknown"}:
            raise ValueError("Unrecognized archive severity")
        events.append(Event(
            str(row["i"]), str(origin), str(origin), HAZARDS[hazard_code],
            (Level(severity, start, time_known=False),), start, end, update, "aiu_web",
            explicit,
            str(row["lruid"]) if "lruid" in row else None, location_name,
            {"o": "oblast", "r": "raion", "h": "hromada", "c": "hromada"}.get(
                str(row.get("t")), "unknown"
            ), oblast_name, upstream_source=int(row.get("src", 0)),
        ))
    return events
