"""Optional supported API. Bearer credentials stay in memory and off stdout."""

import os
from pathlib import Path

import httpx

from airalert.sources import Event, Level, array, mapping, timestamp
from airalert.sources.siren import USER_AGENT


def token_from_env(dotenv: Path) -> str | None:
    """Read only the requested key from a local .env, without executing its contents."""
    token = os.environ.get("ALERTS_IN_UA_TOKEN")
    if token:
        return token
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.removeprefix("export ").partition("=")
            if separator and key.strip() == "ALERTS_IN_UA_TOKEN":
                return value.strip().strip("\"'") or None
    return None


def fetch_alerts(token: str) -> tuple[object, bytes]:
    response = httpx.get(
        "https://api.alerts.in.ua/v1/alerts/active.json", timeout=40,
        headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json(), response.content


def decode(payload: object) -> list[Event]:
    root = mapping(payload)
    rows = root.get("alerts", root.get("raw", root.get("sample_rows")))
    result: list[Event] = []
    for raw in array(rows):
        row = mapping(raw)
        start, updated = timestamp(row["started_at"]), timestamp(row["updated_at"])
        if start is None or updated is None:
            raise ValueError("Supported API alert needs start and update timestamps")
        levels: list[Level] = []
        severity = row.get("alert_level", "unknown")
        if severity not in {"red", "yellow", "unknown"}:
            raise ValueError("Unknown API severity")
        # Current scalar color has no documented change time. Do not backdate
        # a later escalation to started_at in the transition artifact.
        levels.append(Level(severity, start, time_known=False))
        for item in array(row.get("threats", [])):
            threat = mapping(item)
            color, since = threat.get("level"), timestamp(threat.get("started_at"))
            if color not in {"red", "yellow"} or since is None:
                raise ValueError("Malformed API threat")
            levels.append(Level(color, since))
        uid = str(row["location_uid"])
        result.append(Event(
            str(row["id"]), uid, uid, str(row["alert_type"]), tuple(levels),
            start, timestamp(row.get("finished_at")), updated, "aiu_api",
            "alert_level" in row, location_name=str(row.get("location_title", "")),
            location_type=str(row["location_type"]),
            oblast_name=str(row.get("location_oblast", "")),
            parent_name=str(row.get("location_raion", "")),
        ))
    return result
