"""Typed adapters shared by the reconciler and the minute live collector."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

Severity = Literal["red", "yellow", "unknown"]
Source = Literal["aiu_web", "siren", "aiu_api"]


@dataclass(frozen=True)
class Level:
    severity: Severity
    since: datetime
    time_known: bool = True


@dataclass(frozen=True)
class Event:
    """Origin identity survives expanded/inherited copies and severity changes."""

    event_id: str
    origin_uid: str
    covered_uid: str
    hazard: str
    levels: tuple[Level, ...]
    started_at: datetime
    finished_at: datetime | None
    updated_at: datetime
    source: Source
    severity_explicit: bool = True
    parent_uid: str | None = None
    location_name: str = ""
    location_type: str = ""
    oblast_name: str = ""
    parent_name: str = ""
    start_is_exact: bool = True
    upstream_source: int | None = None


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("Expected a JSON object")
    return value


def array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array")
    return value


def timestamp(value: object) -> datetime | None:
    """Year-0001 history ends mean ongoing, never a real end timestamp."""
    if value is None or str(value).startswith("0001-"):
        return None
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Source timestamp must include its UTC offset")
    return result.astimezone(UTC)
