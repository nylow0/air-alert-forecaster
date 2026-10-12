"""Translate ingest's independent alert layers into a complete display-unit cache."""

import csv
import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

from airalert.schemas import LiveState, LiveUnit


def display_geography(geojson: dict[str, object], crosswalk: Path) -> dict[str, object]:
    """Match forecast IDs first; excluded shapes use the crosswalk's original shape name."""
    result = deepcopy(geojson)
    with crosswalk.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
        keys = {
            (row["oblast_id"], row["shape_name"]): row["shape_id"]
            for row in rows
        }
        raions = {row["raion_id"]: row["shape_id"] for row in rows if row["raion_id"]}
    for feature in result["features"]:
        properties = feature["properties"]
        properties["display_key"] = (
            raions[properties["raion_id"]] if properties["raion_id"] else
            keys[(properties["oblast_id"], properties["name"])]
        )
    return result


class LiveCache:
    """Publish complete snapshots; failed polls never erase active layers."""

    def __init__(self, geojson: dict[str, object], state_path: Path) -> None:
        self._units = [
            LiveUnit(
                display_key=feature["properties"]["display_key"],
                raion_id=feature["properties"]["raion_id"],
                oblast_id=feature["properties"]["oblast_id"],
                name=feature["properties"]["name"], red_since=None, yellow_since=None,
                other_hazards=[], coverage="unknown",
            )
            for feature in geojson["features"]
        ]
        if len({unit.display_key for unit in self._units}) != len(self._units):
            raise ValueError("Display keys must be unique")
        self._state = LiveState(fetched_at=None, source="siren", stale=True, units=self._units)
        if state_path.is_file():
            try:
                self.publish(json.loads(state_path.read_text(encoding="utf-8")))
            except (ValueError, KeyError, TypeError):
                # A damaged disk cache must not prevent the next live poll.
                self.fail()

    def publish(self, raw: dict[str, object]) -> None:
        if raw.get("stale") and self._state.fetched_at is not None:
            # Ingest may return a failed poll instead of raising. Retain our last
            # successful snapshot, or recover its preserved disk state at startup.
            self.fail()
            return
        polygons = {row["display_key"]: row for row in raw["polygons"]}
        if set(polygons) != {unit.display_key for unit in self._units}:
            raise ValueError("Live poll must cover every display polygon exactly once")
        if len(polygons) != len(raw["polygons"]):
            raise ValueError("Live poll contains duplicate display polygons")
        units: list[LiveUnit] = []
        for template in self._units:
            row = polygons[template.display_key]
            red, yellow = row.get("red"), row.get("yellow")
            units.append(LiveUnit(
                display_key=template.display_key, raion_id=template.raion_id,
                oblast_id=template.oblast_id, name=template.name,
                red_since=red["since"] if red and red["active"] else None,
                yellow_since=yellow["since"] if yellow and yellow["active"] else None,
                other_hazards=sorted({
                    hazard if isinstance(hazard, str) else hazard["type"]
                    for hazard in row.get("other_hazards", [])
                }),
                coverage=row["coverage"],
            ))
        sources = raw.get("sources", {})
        source = "+".join(
            name for name, status in sources.items()
            if status in {"observed", "reported_permanent_alert"}
        ) or "siren"
        state = LiveState(
            fetched_at=raw.get("fetched_at"), source=source,
            stale=bool(raw.get("stale", False)), units=units,
        )
        if state.fetched_at is None and not state.stale:
            raise ValueError("Successful polls require fetched_at")
        self._state = state

    def fail(self) -> None:
        self._state = self._state.model_copy(update={"stale": True})

    def get(self, now: datetime) -> LiveState:
        stale = (
            self._state.stale or self._state.fetched_at is None
            or now - self._state.fetched_at > timedelta(minutes=3)
        )
        return self._state.model_copy(update={"stale": stale})
