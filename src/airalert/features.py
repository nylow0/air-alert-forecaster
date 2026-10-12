"""Vectorized features from raw coverage records, never merged target episodes."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter

import numpy as np
import pandas as pd

from airalert.config import (
    ADJACENCY_PATH,
    CENTROIDS_PATH,
    FEATURES_DIR,
    HORIZONS,
    KYIV_TZ,
    REPO_ROOT,
    TEST_END_OFFSET_H,
    TEST_START,
    TRAIN_START,
    VAL_START,
)
from airalert.data import load_episodes, load_raions, load_records, load_snapshot
from airalert.labels import build_labels, utc_times


@dataclass(frozen=True)
class FeatureContext:
    """Fixed geography and saved regime metadata, independent of record activity."""

    raions: pd.DataFrame
    adjacency: dict[str, list[str]]
    centroids: pd.DataFrame
    regime_switch: pd.Timestamp


def load_feature_context() -> FeatureContext:
    """Load static inputs once when evaluating many as-of views."""
    return FeatureContext(
        raions=load_raions(),
        adjacency=json.loads(ADJACENCY_PATH.read_text(encoding="utf-8")),
        centroids=pd.read_csv(CENTROIDS_PATH),
        regime_switch=load_snapshot().regime_switch,
    )


def _active_records(group: pd.DataFrame, clock: np.ndarray) -> np.ndarray:
    """Coverage is active only when start < t and end > t or end is unknown.

    Zero-length records never contribute coverage or subtract unseen starts.
    """
    starts = group.started_at.array.asi8
    ends = group.finished_at.array.asi8
    positive = group.finished_at.isna().to_numpy() | (ends > starts)
    return (
        np.searchsorted(np.sort(starts[positive]), clock, side="left")
        - np.searchsorted(np.sort(ends[positive & group.finished_at.notna()]), clock, side="right")
    ) > 0


def _minutes_since(values: np.ndarray, clock: np.ndarray, *, side: str) -> np.ndarray:
    """Return NaN without history; only search ends already known at the clock."""
    positions = np.searchsorted(values, clock, side=side) - 1
    result = np.full(len(clock), np.nan, dtype=np.float32)
    valid = positions >= 0
    result[valid] = (clock[valid] - values[positions[valid]]) / 60_000_000
    return result


def _alert_minutes(group: pd.DataFrame, clock: np.ndarray) -> np.ndarray:
    """Integrate actual coverage over [t-24h, t), without closing short gaps.

    Full interval ends can define the union, because each integral stops at t.
    Future starts cannot change coverage before their start, and replacing an
    end > t by an open end cannot change either integral at or before t.
    """
    ordered = group.sort_values("started_at", kind="stable")
    starts = ordered.started_at.array.asi8
    ends = ordered.finished_at.array.asi8.copy()
    ends[ordered.finished_at.isna()] = np.iinfo(np.int64).max
    positive = ends > starts
    starts, ends = starts[positive], ends[positive]
    if not len(starts):
        return np.zeros(len(clock), dtype=np.float32)
    running_end = np.maximum.accumulate(ends)
    boundaries = np.flatnonzero(np.r_[True, starts[1:] > running_end[:-1]])
    union_starts = starts[boundaries]
    union_ends = np.maximum.reduceat(ends, boundaries)
    # Sum integer microseconds so subtracting two integrals keeps exact boundaries.
    prefix = np.r_[np.int64(0), np.cumsum(union_ends - union_starts)]

    def covered_until(query: np.ndarray) -> np.ndarray:
        positions = np.searchsorted(union_starts, query, side="left") - 1
        valid = positions >= 0
        result = np.zeros(len(query), dtype=np.int64)
        selected = positions[valid]
        result[valid] = prefix[selected] + (
            np.minimum(query[valid], union_ends[selected]) - union_starts[selected]
        )
        return result

    minutes = (covered_until(clock) - covered_until(clock - 86_400_000_000)) / 60_000_000
    return minutes.astype(np.float32)


def build_features(
    records: pd.DataFrame,
    times: Iterable[datetime],
    *,
    context: FeatureContext | None = None,
) -> pd.DataFrame:
    """Evaluate every fixed-universe raion at arbitrary aware forecast timestamps.

    Starts use [t-window, t). Ends are known at end <= t only for records that
    started < t. A supplied context avoids repeated static reads in leakage
    tests or duration inference; records always remain the only dynamic input.
    """
    context = load_feature_context() if context is None else context
    clock = utc_times(times)
    raions = context.raions
    ids = raions.raion_id.to_numpy()
    if raions.raion_id.duplicated().any():
        raise ValueError("The raion universe must contain unique IDs")
    records = records.drop_duplicates(["raion_id", "level", "started_at", "finished_at"])
    records = records.copy()
    records["started_at"] = utc_times(records.started_at)
    records["finished_at"] = pd.to_datetime(records.finished_at, utc=True).astype(
        "datetime64[us, UTC]"
    )
    if records.finished_at.lt(records.started_at).any():
        raise ValueError("Record ends cannot precede starts")
    if not records.raion_id.isin(ids).all():
        raise ValueError("Records contain raions outside the fixed universe")
    groups = {
        key: group for key, group in records.groupby("raion_id", sort=False, observed=True)
    }
    shape = (len(ids), len(clock))
    names = (
        "f_active", "f_minutes_since_start", "f_minutes_since_end",
        "f_starts_1h", "f_starts_6h", "f_starts_24h", "f_starts_168h",
        "f_alert_minutes_24h",
    )
    arrays = {name: np.zeros(shape, dtype=np.float32) for name in names}
    arrays["f_minutes_since_start"].fill(np.nan)
    arrays["f_minutes_since_end"].fill(np.nan)
    recent = {minutes: np.zeros(shape, dtype=np.float32) for minutes in (15, 30, 60)}
    for index, raion_id in enumerate(ids):
        if raion_id not in groups:
            continue
        group = groups[raion_id]
        starts = np.unique(group.started_at.array.asi8)
        before = np.searchsorted(starts, clock.asi8, side="left")
        arrays["f_active"][index] = _active_records(group, clock.asi8)
        arrays["f_minutes_since_start"][index] = _minutes_since(
            starts, clock.asi8, side="left"
        )
        ended = group.loc[group.finished_at.notna()]
        positive = ended.finished_at.gt(ended.started_at)
        known_ends = np.sort(ended.loc[positive, "finished_at"].array.asi8)
        zero_ends = np.sort(ended.loc[~positive, "finished_at"].array.asi8)
        # A zero-length record at t has not started yet; its end is also unseen.
        arrays["f_minutes_since_end"][index] = np.fmin(
            _minutes_since(known_ends, clock.asi8, side="right"),
            _minutes_since(zero_ends, clock.asi8, side="left"),
        )
        for hours in (1, 6, 24, 168):
            arrays[f"f_starts_{hours}h"][index] = before - np.searchsorted(
                starts, clock.asi8 - hours * 3_600_000_000, side="left"
            )
        for minutes in recent:
            recent[minutes][index] = before - np.searchsorted(
                starts, clock.asi8 - minutes * 60_000_000, side="left"
            )
        arrays["f_alert_minutes_24h"][index] = _alert_minutes(group, clock.asi8)

    active = arrays["f_active"]
    index_by_id = {raion_id: index for index, raion_id in enumerate(ids)}
    neighbor_active = np.zeros(shape, dtype=np.float32)
    neighbor_starts = {minutes: np.zeros(shape, dtype=np.float32) for minutes in (30, 60)}
    for index, raion_id in enumerate(ids):
        neighbors = context.adjacency.get(raion_id)
        if neighbors is None or any(key not in index_by_id for key in neighbors):
            raise ValueError(f"Incomplete adjacency for {raion_id}")
        positions = [index_by_id[key] for key in set(neighbors) if key != raion_id]
        if positions:
            neighbor_active[index] = active[positions].mean(axis=0)
            for minutes in neighbor_starts:
                neighbor_starts[minutes][index] = recent[minutes][positions].sum(axis=0)
    arrays["f_adjacent_active_share"] = neighbor_active
    for minutes, values in neighbor_starts.items():
        arrays[f"f_adjacent_starts_{minutes}m"] = values
    oblast_share = np.zeros(shape, dtype=np.float32)
    for oblast_id in raions.oblast_id.unique():
        positions = np.flatnonzero(raions.oblast_id.eq(oblast_id).to_numpy())
        oblast_share[positions] = active[positions].mean(axis=0)
    arrays["f_oblast_active_share"] = oblast_share
    frame = pd.DataFrame({
        "t": np.tile(clock, len(ids)),
        "raion_id": np.repeat(ids, len(clock)),
        "oblast_id": np.repeat(raions.oblast_id.to_numpy(), len(clock)),
    })
    frame["t"] = pd.DatetimeIndex(frame.t).tz_convert("UTC").as_unit("us")
    for name, values in arrays.items():
        frame[name] = values.ravel()
    national = {
        "f_country_active": active.sum(axis=0),
        "f_country_started_15m": (recent[15] > 0).sum(axis=0),
        "f_country_started_60m": (recent[60] > 0).sum(axis=0),
    }
    local = clock.tz_convert(KYIV_TZ)
    hour = local.hour.to_numpy() + local.minute.to_numpy() / 60 + (
        local.second.to_numpy() + local.microsecond.to_numpy() / 1_000_000
    ) / 3_600
    calendar = {
        "f_hour": hour,
        "f_hour_sin": np.sin(2 * np.pi * hour / 24),
        "f_hour_cos": np.cos(2 * np.pi * hour / 24),
        "f_weekday": local.dayofweek.to_numpy(),
        "f_weekend": local.dayofweek.to_numpy() >= 5,
        "f_regime_raion": clock >= context.regime_switch,
    }
    for name, values in (national | calendar).items():
        frame[name] = np.tile(np.asarray(values, dtype=np.float32), len(ids))
    centroids = context.centroids.set_index("raion_id").reindex(ids)
    if centroids[["lat", "lon"]].isna().any().any():
        raise ValueError("Every universe raion needs a centroid")
    for coordinate in ("lat", "lon"):
        frame[f"f_{coordinate}"] = np.repeat(
            centroids[coordinate].to_numpy(dtype=np.float32), len(clock)
        )
    return frame


def _early_features(
    records: pd.DataFrame, times: Iterable[datetime], raions: pd.DataFrame
) -> pd.DataFrame:
    clock = utc_times(times)
    frame = pd.DataFrame({
        "t": np.tile(clock, len(raions)),
        "raion_id": np.repeat(raions.raion_id.to_numpy(), len(clock)),
        "oblast_id": np.repeat(raions.oblast_id.to_numpy(), len(clock)),
    })
    frame["t"] = pd.DatetimeIndex(frame.t).tz_convert("UTC").as_unit("us")
    active = np.zeros((len(raions), len(clock)), dtype=np.float32)
    groups = {key: group for key, group in records.groupby("raion_id", sort=False)}
    for index, raion_id in enumerate(raions.raion_id):
        if raion_id in groups:
            active[index] = _active_records(groups[raion_id], clock.asi8)
    frame["f_active"] = active.ravel()
    return frame


def build(*, early: bool = False) -> None:
    """Write all hourly rows without embargo or outage masking; B2 applies those."""
    began = perf_counter()
    records = load_records().drop_duplicates(
        ["raion_id", "level", "started_at", "finished_at"]
    )
    episodes, raions, metadata = load_episodes(), load_raions(), load_snapshot()
    clocks = {
        "train": pd.date_range(TRAIN_START, VAL_START, freq="h", inclusive="left"),
        "val": pd.date_range(VAL_START, TEST_START, freq="h", inclusive="left"),
        "test": pd.date_range(
            TEST_START, metadata.snapshot - pd.Timedelta(hours=TEST_END_OFFSET_H), freq="h"
        ),
        "latest": pd.DatetimeIndex([metadata.snapshot]),
    }
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    context = None if early else load_feature_context()
    columns: list[str] = []
    for split, times in clocks.items():
        frame = (
            _early_features(records, times, raions) if early
            else build_features(records, times, context=context)
        )
        labels = build_labels(episodes, times, metadata.snapshot, raions=raions)
        for horizon in HORIZONS:
            frame[f"y_{horizon}"] = labels[f"y_{horizon}"]
        # Readers may switch to these files as soon as the early marker appears.
        # Replace each complete file atomically so B2 never sees a partial footer.
        staging = FEATURES_DIR / f".{split}.parquet.tmp"
        frame.to_parquet(staging, index=False)
        staging.replace(FEATURES_DIR / f"{split}.parquet")
        rates = "; ".join(
            f"y_{h}={frame[f'y_{h}'].mean():.6%}" if frame[f"y_{h}"].notna().any()
            else f"y_{h}=null" for h in HORIZONS
        )
        print(f"{split}: {len(frame):,} rows; {rates}", flush=True)
        columns = frame.columns.tolist()
    if early:
        marker = REPO_ROOT / ".lead" / "B1_LABELS_READY"
        marker.write_text(
            f"{pd.Timestamp.now(tz='UTC').isoformat()}\ncolumns: {', '.join(columns)}\n",
            encoding="utf-8",
        )
    print(f"Elapsed: {perf_counter() - began:.2f}s", flush=True)
