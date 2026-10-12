"""Compare full-data features with reconstructed as-of views, including edge traps."""

from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest

from airalert.config import (
    MERGE_GAP_MIN,
    RECORDS_PATH,
    TEST_END_OFFSET_H,
    TEST_START,
    TRAIN_START,
    VAL_START,
)
from airalert.data import load_records, load_snapshot
from airalert.features import FeatureContext, build_features, load_feature_context
from airalert.labels import utc_times


def _as_of(records: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    view = records.loc[records.started_at < t].copy()
    view.loc[view.finished_at > t, "finished_at"] = pd.NaT
    return view


def _assert_same_features(full: pd.DataFrame, view: pd.DataFrame) -> None:
    """Compare keys and every feature, so newly added columns join the guard."""
    keys = ["raion_id", "t"]
    full = full.sort_values(keys).reset_index(drop=True)
    view = view.sort_values(keys).reset_index(drop=True)
    pd.testing.assert_frame_equal(full[keys], view[keys])
    columns = [column for column in full if column.startswith("f_")]
    assert columns == [column for column in view if column.startswith("f_")]
    for column in columns:
        np.testing.assert_allclose(
            full[column], view[column], rtol=0, atol=1e-6, equal_nan=True,
            err_msg=f"As-of mismatch in {column}",
        )


def _check_at(
    builder: Callable[[pd.DataFrame, pd.DatetimeIndex], pd.DataFrame],
    records: pd.DataFrame,
    t: pd.Timestamp,
) -> None:
    times = pd.DatetimeIndex([t])
    _assert_same_features(builder(records, times), builder(_as_of(records, t), times))


def _synthetic() -> tuple[pd.DataFrame, FeatureContext, pd.Timestamp]:
    t = pd.Timestamp("2026-06-10T12:00:00Z")
    minutes = pd.Timedelta(minutes=1)
    rows = [
        ("a", "x", "oblast", t - 60 * minutes, t - 10 * minutes),
        ("a", "x", "oblast", t - 60 * minutes, t - 10 * minutes),
        ("a", "x", "raion", t - 60 * minutes, t - 20 * minutes),
        ("a", "x", "hromada", t - 30 * minutes, t - 10 * minutes),
        ("a", "x", "raion", t - 8 * minutes, t + 60 * minutes),
        ("b", "x", "raion", t - 20 * minutes, t),
        ("b", "x", "hromada", t, t),
        ("c", "y", "raion", t - 15 * minutes, pd.NaT),
        ("c", "y", "hromada", t, t + 15 * minutes),
        ("c", "y", "raion", t, t),
    ]
    records = pd.DataFrame(
        rows, columns=["raion_id", "oblast_id", "level", "started_at", "finished_at"]
    )
    context = FeatureContext(
        raions=pd.DataFrame({"raion_id": ["a", "b", "c"], "oblast_id": ["x", "x", "y"]}),
        adjacency={"a": ["b", "c"], "b": ["a"], "c": ["a"]},
        centroids=pd.DataFrame({"raion_id": ["a", "b", "c"], "lat": [50., 49., 48.],
                                "lon": [30., 31., 32.]}),
        regime_switch=t - pd.Timedelta(days=1),
    )
    return records, context, t


def test_as_of_boundaries_and_feature_meaning() -> None:
    """Overlap, duplicate starts, zero lengths, open ends and a two-minute gap."""
    records, context, t = _synthetic()

    def builder(frame: pd.DataFrame, times: pd.DatetimeIndex) -> pd.DataFrame:
        return build_features(frame, times, context=context)

    for clock in (t, t + pd.Timedelta(microseconds=1), t - pd.Timedelta(minutes=9)):
        _check_at(builder, records, clock)
    frame = builder(records, pd.DatetimeIndex([t])).set_index("raion_id")
    assert frame.f_active.to_dict() == {"a": 1, "b": 0, "c": 1}
    assert frame.f_starts_1h.to_dict() == {"a": 3, "b": 1, "c": 1}
    assert frame.f_alert_minutes_24h.to_dict() == {"a": 58, "b": 20, "c": 15}
    assert frame.f_minutes_since_start.to_dict() == {"a": 8, "b": 20, "c": 15}
    assert frame.f_minutes_since_end["b"] == 0
    assert pd.isna(frame.f_minutes_since_end["c"])
    assert frame.f_adjacent_active_share["a"] == 0.5
    assert frame.f_adjacent_starts_30m["a"] == 2
    assert frame.f_oblast_active_share["a"] == 0.5
    assert (frame.f_country_active == 2).all()
    assert (frame.f_country_started_15m == 2).all()
    assert frame.loc["a", "f_hour"] == 15
    assert all(dtype == np.dtype("float32") for dtype in frame.filter(like="f_").dtypes)
    gap = builder(records, pd.DatetimeIndex([t - pd.Timedelta(minutes=9)]))
    assert gap.set_index("raion_id").loc["a", "f_active"] == 0


def _real_adversarial_times(records: pd.DataFrame) -> list[pd.Timestamp]:
    usable = records.loc[
        records.started_at.ge(VAL_START) & records.finished_at.gt(records.started_at)
    ]
    record = usable.iloc[0]
    times = [record.started_at, record.started_at + pd.Timedelta(microseconds=1),
             record.finished_at]
    for _, group in records.groupby("raion_id", sort=False):
        group = group.sort_values("started_at").drop_duplicates(
            ["started_at", "finished_at"]
        )
        starts = group.started_at.array.asi8
        ends = group.finished_at.array.asi8.copy()
        ends[group.finished_at.isna()] = np.iinfo(np.int64).max
        running_ends = np.maximum.accumulate(ends)
        gaps = starts[1:] - running_ends[:-1]
        candidates = np.flatnonzero(
            (gaps > 0) & (gaps < MERGE_GAP_MIN * 60_000_000)
            & (starts[1:] >= utc_times([TRAIN_START]).asi8[0])
        )
        if len(candidates):
            index = candidates[0]
            times.append(pd.Timestamp(
                int(running_ends[index] + gaps[index] // 2), unit="us", tz="UTC"
            ))
            return times
    raise AssertionError("Expected a real short merge gap to exercise the trap")


@pytest.mark.skipif(not RECORDS_PATH.exists(), reason="Real records not built yet")
def test_real_records_as_of_50_times_and_adversarial_edges() -> None:
    """Stratify across both regimes and every historical split with a fixed seed."""
    records = load_records()
    metadata = load_snapshot()
    context = load_feature_context()
    rng = np.random.default_rng(20261012)
    edges = [pd.Timestamp(TRAIN_START), metadata.regime_switch, pd.Timestamp(VAL_START),
             pd.Timestamp(TEST_START),
             metadata.snapshot - pd.Timedelta(hours=TEST_END_OFFSET_H)]
    times: list[pd.Timestamp] = []
    for start, end, size in zip(edges[:-1], edges[1:], (10, 10, 15, 15), strict=True):
        hours = int((end - start) / pd.Timedelta(hours=1))
        times.extend(start + pd.Timedelta(hours=int(offset))
                     for offset in rng.choice(hours, size=size, replace=False))
    times.extend(_real_adversarial_times(records))
    times.append(metadata.snapshot)
    full = build_features(records, pd.DatetimeIndex(times), context=context)
    for t in times:
        view = build_features(_as_of(records, t), [t], context=context)
        _assert_same_features(full.loc[full.t == t], view)


def test_checker_rejects_planted_future_end_leak() -> None:
    """The same checker must reject a feature that reads a running alert's end."""
    t = pd.Timestamp("2026-06-10T12:00:00Z")
    records = pd.DataFrame({
        "raion_id": ["a"], "started_at": [t - pd.Timedelta(minutes=10)],
        "finished_at": [t + pd.Timedelta(minutes=20)],
    })

    def leaky(frame: pd.DataFrame, times: pd.DatetimeIndex) -> pd.DataFrame:
        duration = (frame.finished_at - frame.started_at).dt.total_seconds().sum() / 60
        return pd.DataFrame({"raion_id": ["a"], "t": times, "f_leak": [duration]})

    with pytest.raises(AssertionError, match="As-of mismatch in f_leak"):
        _check_at(leaky, records, t)
