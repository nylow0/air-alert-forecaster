"""Onset targets from full-data episodes, kept separate from as-of features."""

from collections.abc import Iterable
from datetime import datetime

import numpy as np
import pandas as pd

from airalert.config import HORIZONS
from airalert.data import load_raions


def utc_times(times: Iterable[datetime]) -> pd.DatetimeIndex:
    """Require aware timestamps and retain their order at UTC microsecond precision."""
    result = pd.DatetimeIndex(times)
    if result.empty and result.tz is None:
        return result.tz_localize("UTC").as_unit("us")
    if result.tz is None or result.hasnans:
        raise ValueError("Forecast times must be timezone-aware and non-null")
    return result.tz_convert("UTC").as_unit("us")


def build_labels(
    episodes: pd.DataFrame,
    times: Iterable[datetime],
    snapshot: datetime,
    *,
    raions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Label every fixed-universe raion at each t with a start in (t, t+H].

    Targets use merged full-data episodes. The optional universe lets synthetic
    tests supply geography without deriving it from observed episode activity.
    """
    clock = utc_times(times)
    edge = utc_times([snapshot]).asi8[0]
    universe = load_raions() if raions is None else raions
    ids = universe.raion_id.to_numpy()
    if universe.raion_id.duplicated().any():
        raise ValueError("The raion universe must contain unique IDs")
    result = pd.DataFrame({
        "t": np.tile(clock, len(ids)),
        "raion_id": np.repeat(ids, len(clock)),
    })
    result["t"] = pd.DatetimeIndex(result.t).tz_convert("UTC").as_unit("us")
    starts = episodes[["raion_id", "start"]].copy()
    starts["start"] = utc_times(starts.start)
    grouped = {
        raion_id: np.sort(group.start.array.asi8)
        for raion_id, group in starts.groupby("raion_id", sort=False, observed=True)
    }
    for horizon in HORIZONS:
        ends = clock.asi8 + horizon * 3_600_000_000
        values = np.zeros((len(ids), len(clock)), dtype=np.int8)
        for index, raion_id in enumerate(ids):
            episode_starts = grouped.get(raion_id, np.empty(0, dtype=np.int64))
            values[index] = (
                np.searchsorted(episode_starts, ends, side="right")
                > np.searchsorted(episode_starts, clock.asi8, side="right")
            )
        labels = pd.array(values.ravel(), dtype="Int8")
        labels[np.tile(ends > edge, len(ids))] = pd.NA
        result[f"y_{horizon}"] = labels
    return result
