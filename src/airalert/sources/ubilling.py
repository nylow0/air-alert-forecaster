"""Secondary display evidence for Sevastopol only, never forecast records or labels."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from airalert.config import KYIV_TZ
from airalert.sources import mapping
from airalert.sources.siren import USER_AGENT


def fetch_alerts() -> tuple[object, bytes]:
    response = httpx.get("https://ubilling.net.ua/aerialalerts/", timeout=15,
                         headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.json(), response.content


def sevastopol_since(payload: object) -> datetime | None:
    """Interpret the default feed's offset-free reported time in Europe/Kyiv.

    The lead accepts red permanent-alert display from this secondary source.
    It supplies neither severity nor hazard classification for model use.
    """
    root = mapping(payload)
    if root.get("source") != "Vadym Klymenko API (default)":
        raise ValueError("Expected ubilling's default source")
    row = mapping(mapping(root["states"])["Севастополь"])
    if not isinstance(row.get("alertnow"), bool):
        raise ValueError("Sevastopol status must be a boolean")
    if not row["alertnow"]:
        return None
    changed = datetime.fromisoformat(str(row["changed"]))
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=ZoneInfo(KYIV_TZ))
    return changed.astimezone(UTC)
