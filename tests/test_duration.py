"""Guard duration leakage, split boundaries, and finite ordered predictions."""

import numpy as np
import pandas as pd
import pytest
from numpy.typing import NDArray

from airalert.config import DATA_GAPS, TEST_END_OFFSET_H, TEST_START, TRAIN_START, VAL_START
from airalert.data import load_episodes, load_raions, load_records, load_snapshot
from airalert.models.duration import (
    EmpiricalDuration,
    build_start_features,
    duration_minutes,
    episode_split,
    episode_split_mask,
)


def _synthetic() -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    origin = pd.Timestamp(TRAIN_START)
    raions = pd.DataFrame({"raion_id": ["a/one", "a/two", "b/three"], "oblast_id": ["a", "a", "b"]})
    records = pd.DataFrame(
        [
            ("a/one", "a", "raion", 0, 10),
            ("a/one", "a", "raion", 0, 10),
            ("a/one", "a", "raion", 0, 12),
            ("a/one", "a", "raion", 13, 25),
            ("a/one", "a", "raion", 60, 80),
            ("a/two", "a", "hromada", 11, 11),
            ("a/two", "a", "raion", 15, 120),
            ("b/three", "b", "oblast", 8, None),
        ],
        columns=["raion_id", "oblast_id", "level", "started_at", "finished_at"],
    )
    for column in ("started_at", "finished_at"):
        records[column] = (origin + pd.to_timedelta(records[column], unit="min")).astype(
            "datetime64[us, UTC]"
        )
    return records, raions, origin


def test_start_features_equal_asof_prefix_including_merge_gap() -> None:
    records, raions, origin = _synthetic()
    for minute in (5, 11, 12, 13, 16, 27, 81):
        start = origin + pd.Timedelta(minutes=minute)
        rows = pd.DataFrame({"raion_id": ["a/one"], "oblast_id": ["a"], "start": [start]})
        truncated = records.loc[records.started_at.le(start)].copy()
        truncated.loc[truncated.finished_at.gt(start), "finished_at"] = pd.NaT
        full = build_start_features(records, rows, raions, origin)
        asof = build_start_features(truncated, rows, raions, origin)
        pd.testing.assert_frame_equal(full, asof)
        if minute == 12:
            assert full.recent_median_duration_min.iloc[0] == 12
            assert full.minutes_since_previous_end.iloc[0] == 0
        if minute == 13:
            assert full.source_level.iloc[0] == "raion"
            assert full.national_starts_15m.iloc[0] == 3
            assert full.national_active.iloc[0] == 2


def test_real_start_features_equal_asof_prefix() -> None:
    records = load_records()
    episodes = load_episodes()
    raions = load_raions()
    switch = load_snapshot().regime_switch
    sample = episodes.loc[
        episodes.start.ge(VAL_START) & episodes.start.lt(TEST_START)
    ].sample(5, random_state=42)
    for row in sample.itertuples(index=False):
        query = pd.DataFrame(
            {"raion_id": [row.raion_id], "oblast_id": [row.oblast_id], "start": [row.start]}
        )
        truncated = records.loc[records.started_at.le(row.start)].copy()
        truncated.loc[truncated.finished_at.gt(row.start), "finished_at"] = pd.NaT
        pd.testing.assert_frame_equal(
            build_start_features(records, query, raions, switch),
            build_start_features(truncated, query, raions, switch),
        )


def test_record_duplicates_do_not_change_features() -> None:
    records, raions, origin = _synthetic()
    query = pd.DataFrame(
        {"raion_id": ["a/one"], "oblast_id": ["a"],
         "start": [origin + pd.Timedelta(minutes=16)]}
    )
    pd.testing.assert_frame_equal(
        build_start_features(records, query, raions, origin),
        build_start_features(records.drop_duplicates(), query, raions, origin),
    )


def test_duration_splits_exclude_crossing_ends_censoring_and_gaps() -> None:
    gap_start, gap_end = map(pd.Timestamp, DATA_GAPS[0])
    snapshot = gap_end + pd.Timedelta(days=7)
    test_end = snapshot - pd.Timedelta(hours=TEST_END_OFFSET_H)
    starts = pd.to_datetime(
        [
            pd.Timestamp(VAL_START) - pd.Timedelta(hours=2),
            pd.Timestamp(VAL_START) - pd.Timedelta(hours=2),
            pd.Timestamp(TEST_START) - pd.Timedelta(hours=2),
            pd.Timestamp(TEST_START) - pd.Timedelta(hours=2),
            pd.Timestamp(TEST_START),
            pd.Timestamp(TEST_START),
            gap_start - pd.Timedelta(hours=1), gap_start,
            gap_end, test_end, test_end + pd.Timedelta(microseconds=1),
        ], utc=True,
    )
    ends = pd.to_datetime(
        [
            pd.Timestamp(VAL_START) - pd.Timedelta(hours=1), pd.Timestamp(VAL_START),
            pd.Timestamp(TEST_START) - pd.Timedelta(hours=1), pd.Timestamp(TEST_START),
            pd.Timestamp(TEST_START) + pd.Timedelta(hours=1), pd.NaT,
            gap_start + pd.Timedelta(hours=1), gap_start,
            gap_end + pd.Timedelta(hours=1), test_end + pd.Timedelta(hours=1),
            test_end + pd.Timedelta(hours=1),
        ], utc=True,
    )
    episodes = pd.DataFrame({"start": starts, "end": ends, "censored": ends.isna()})
    assert np.flatnonzero(episode_split_mask(episodes, "train", snapshot)).tolist() == [0]
    assert np.flatnonzero(episode_split_mask(episodes, "val", snapshot)).tolist() == [2]
    assert np.flatnonzero(episode_split_mask(episodes, "test", snapshot)).tolist() == [4, 8, 9]
    assert episode_split(episodes, snapshot).iloc[10] == "latest"


def test_empirical_quantiles_are_ordered_with_zero_length_target_and_fallback() -> None:
    start = pd.Timestamp(TRAIN_START)
    episodes = pd.DataFrame(
        {"start": [start] * 60, "end": start + pd.to_timedelta(np.arange(60), unit="min")}
    )
    minutes = duration_minutes(episodes)
    assert minutes[0] == 1
    assert np.isfinite(np.log(minutes)).all()
    features = pd.DataFrame(
        {"oblast_id": ["a"] * 60, "hour_bucket": [0] * 30 + [1] * 30, "raion_regime": [1] * 60}
    )
    model = EmpiricalDuration.fit(features, minutes)
    queries = pd.DataFrame(
        {"oblast_id": ["a", "a", "missing"], "hour_bucket": [0, 3, 3], "raion_regime": [1, 1, 0]}
    )
    predictions = model.predict(queries)
    assert np.isfinite(predictions).all()
    assert (predictions >= 1).all()
    assert (np.diff(predictions, axis=1) >= 0).all()
    np.testing.assert_allclose(predictions[0], np.quantile(minutes[:30], (0.1, 0.5, 0.9)))
    np.testing.assert_allclose(
        predictions[1:], np.tile(np.quantile(minutes, (0.1, 0.5, 0.9)), (2, 1))
    )


@pytest.mark.parametrize("model_id", ["D0", "D1"])
def test_lightgbm_prediction_order_with_crossing_log_outputs(model_id: str) -> None:
    # Quantile ordering is verified on actual fitted artifacts in the B3 CLI run.
    # This test guards both the postprocessing and the zero-minute output floor.
    from airalert.models.duration import _predict_boosters

    class ConstantBooster:
        def __init__(self, value: float) -> None:
            self.value = value

        def predict(self, features: pd.DataFrame) -> NDArray[np.float64]:
            return np.full(len(features), self.value)

    if model_id == "D0":
        model = EmpiricalDuration(np.array([3.0, 1.0, 2.0]), [])
        predictions = model.predict(pd.DataFrame(index=range(2)))
    else:
        predictions = _predict_boosters(
            [ConstantBooster(2.0), ConstantBooster(-10.0), ConstantBooster(1.0)],
            pd.DataFrame(index=range(2)),
        )
    assert np.isfinite(predictions).all()
    assert (predictions >= 1).all()
    assert (np.diff(predictions, axis=1) >= 0).all()
