"""UkraineAlarm mirror. Keep origin and covered UIDs distinct for inherited copies."""

from datetime import datetime
from pathlib import Path

import httpx

from airalert.sources import Event, Level, array, mapping, timestamp
from airalert.sources.storage import cache_response

BASE_URL = "https://siren.pp.ua/api/v3"
USER_AGENT = "AirAlertForecaster-Ingest/2.0 (research; server-side collector)"


def fetch_regions(cache_path: Path) -> object:
    if cache_path.exists():
        import gzip
        import json
        return json.loads(gzip.decompress(cache_path.read_bytes()))
    response = httpx.get(f"{BASE_URL}/regions", timeout=40, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    payload = response.json()
    if not array(mapping(payload)["states"]):
        raise ValueError("Empty source hierarchy")
    cache_response(cache_path, response.content)
    return payload


def fetch_alerts() -> tuple[object, bytes]:
    response = httpx.get(f"{BASE_URL}/alerts", timeout=40, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    payload = response.json()
    array(payload)
    return payload, response.content


def decode(payload: object) -> list[Event]:
    events: list[Event] = []
    for value in array(payload):
        outer = mapping(value)
        for item in array(outer["activeAlerts"]):
            alert = mapping(item)
            updated = timestamp(alert.get("lastUpdate"))
            if updated is None:
                raise ValueError("Live alert lacks a valid source update time")
            levels: list[Level] = []
            for raw in array(alert.get("activeAlertLevels", [])):
                level = mapping(raw)
                severity = str(level["alertLevel"]).lower()
                since = timestamp(level.get("createdAt"))
                if severity not in {"red", "yellow"} or since is None:
                    raise ValueError("Unsupported live severity or missing level start")
                levels.append(Level("red" if severity == "red" else "yellow", since))
            started = timestamp(alert.get("startedAt")) or min(
                (level.since for level in levels), default=updated
            )
            ended = None if alert.get("isContinue", True) else timestamp(alert.get("endDate"))
            origin = str(alert["regionId"])
            hazard = str(alert["type"]).lower()
            hazard = {"air": "air_raid", "artillery": "artillery_shelling"}.get(hazard, hazard)
            events.append(Event(
                f"{origin}:{hazard}", origin, str(outer["regionId"]), hazard,
                tuple(levels), started, ended, updated, "siren",
                start_is_exact=alert.get("startedAt") is not None,
            ))
    return events


def decode_history_end(row: dict[str, object]) -> datetime | None:
    """Recent history explicitly carries isContinue and year-0001 sentinels."""
    return None if row.get("isContinue") else timestamp(row.get("endDate"))
