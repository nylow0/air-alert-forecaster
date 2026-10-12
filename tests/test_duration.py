"""Guard duration leakage, split boundaries, and finite ordered predictions."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from numpy.typing import NDArray

from airalert.config import TEST_END_OFFSET_H, TEST_START, TRAIN_START, VAL_START
from airalert.data import load_episodes, load_raions, load_records, load_snapshot
from airalert.models.duration import (
    CHECKPOINTS,
    ConformalDuration,
    EmpiricalDuration,
    _eligible_window,
    _fit_fixed,
    _save_bundle,
    build_landmark_features,
    build_start_features,
    duration_minutes,
    episode_split,
    episode_split_mask,
    landmark_rows,
    predict_remaining,
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


def test_duration_splits_exclude_crossing_ends_censoring_and_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airalert.models import duration

    # The real source gap can be repaired; this boundary test needs a synthetic outage.
    gap_start = pd.Timestamp(TEST_START) + pd.Timedelta(days=10)
    gap_end = gap_start + pd.Timedelta(days=4)
    monkeypatch.setattr(duration, "DATA_GAPS", ((gap_start, gap_end),))
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


def test_checkpoint_features_equal_prefix_with_late_observations() -> None:
    records, raions, origin = _synthetic()
    records["observed_at"] = pd.Series(pd.NaT, index=records.index, dtype="datetime64[us, UTC]")
    # This simultaneous event was received later; it must not enter onset context.
    records.loc[records.raion_id.eq("b/three"), "observed_at"] = (
        origin + pd.Timedelta(minutes=20)
    )
    for minute in (0, 12, 13, 16, 20, 27, 81):
        checkpoint = origin + pd.Timedelta(minutes=minute)
        rows = pd.DataFrame({"raion_id": ["a/one"], "start": [origin],
                             "checkpoint": [checkpoint]})
        truncated = records.loc[
            records.started_at.le(checkpoint)
            & (records.observed_at.isna() | records.observed_at.le(checkpoint))
        ].copy()
        truncated.loc[truncated.finished_at.gt(checkpoint), "finished_at"] = pd.NaT
        full = build_landmark_features(records, rows, raions, origin)
        pd.testing.assert_frame_equal(
            full, build_landmark_features(truncated, rows, raions, origin)
        )
        assert full.elapsed_min.iloc[0] == minute
        assert full.national_active.iloc[0] == 1
        if minute == 12:
            assert full.now_national_active.iloc[0] == 0
        if minute == 20:
            assert full.now_national_active.iloc[0] == 2


def test_real_checkpoint_features_equal_prefix() -> None:
    records, episodes, raions = load_records(), load_episodes(), load_raions()
    switch = load_snapshot().regime_switch
    long_episodes = episodes.loc[
        episodes.start.ge(TEST_START)
        & ((episodes.end - episodes.start).dt.total_seconds() > 480 * 60)
    ].sample(3, random_state=42)
    for row, elapsed in zip(long_episodes.itertuples(index=False), (15, 120, 480), strict=True):
        checkpoint = row.start + pd.Timedelta(minutes=elapsed)
        query = pd.DataFrame({"raion_id": [row.raion_id], "start": [row.start],
                              "checkpoint": [checkpoint]})
        truncated = records.loc[records.started_at.le(checkpoint)].copy()
        truncated.loc[truncated.finished_at.gt(checkpoint), "finished_at"] = pd.NaT
        pd.testing.assert_frame_equal(
            build_landmark_features(records, query, raions, switch),
            build_landmark_features(truncated, query, raions, switch),
        )


def test_conformal_uses_only_earlier_ended_targets() -> None:
    origin = pd.Timestamp(TRAIN_START)
    cal_start, eval_start = origin + pd.Timedelta(days=10), origin + pd.Timedelta(days=20)
    starts = [cal_start] * 5 + [eval_start, cal_start, origin]
    ends = [cal_start + pd.Timedelta(minutes=i) for i in (10, 20, 30, 40, 50)]
    ends += [eval_start + pd.Timedelta(minutes=1), eval_start, cal_start]
    episodes = pd.DataFrame({"start": starts, "end": ends, "censored": False})
    actual = np.array([10., 20., 30., 40., 50., 1e9, 1e9, 1e9])
    raw = np.tile([15., 25., 35.], (len(episodes), 1))
    kwargs = {"model_fit_end": cal_start, "calibration_start": cal_start,
              "evaluation_start": eval_start}
    fitted = ConformalDuration.fit(episodes, actual, raw, **kwargs)
    earlier_only = ConformalDuration.fit(episodes.iloc[:5], actual[:5], raw[:5], **kwargs)
    assert fitted == earlier_only
    assert fitted.n == 5
    assert fitted.offset_min == 15
    assert pd.Timestamp(fitted.last_target_end) < eval_start
    calibrated = fitted.predict(raw[:5])
    assert np.mean((actual[:5] >= calibrated[:, 0]) & (actual[:5] <= calibrated[:, 2])) >= .8
    with pytest.raises(ValueError, match="precede"):
        ConformalDuration.fit(episodes, actual, raw, **{**kwargs, "model_fit_end": eval_start})


def test_landmarks_exclude_merge_gap_and_handle_zero_and_long_episodes() -> None:
    records, raions, origin = _synthetic()
    records.loc[records.raion_id.eq("a/one"), "finished_at"] = (
        origin + pd.Timedelta(minutes=14)
    )
    # Full-data merging can bridge 14 -> 16, but at minute 15 coverage is inactive.
    record = records.iloc[[0]].copy()
    record.started_at = origin + pd.Timedelta(minutes=16)
    record.finished_at = origin + pd.Timedelta(days=90)
    records = pd.concat([records.loc[records.started_at.le(origin)], record], ignore_index=True)
    zero = record.assign(raion_id="a/two", started_at=origin, finished_at=origin)
    records = pd.concat([records, zero], ignore_index=True)
    episodes = pd.DataFrame({"raion_id": ["a/one", "a/two"], "start": [origin, origin],
                             "end": [origin + pd.Timedelta(days=90), origin]})
    rows = landmark_rows(episodes, records)
    assert len(rows.loc[rows.raion_id.eq("a/two")]) == 1
    assert rows.loc[rows.raion_id.eq("a/two"), "remaining_min"].iloc[0] == 1
    assert not ((rows.raion_id == "a/one") & (rows.elapsed_min == 15)).any()
    assert set(rows.elapsed_min) == set(CHECKPOINTS) - {15}
    assert np.isfinite(np.log(rows.remaining_min)).all()
    features = build_landmark_features(records, rows, raions, origin)
    assert features.elapsed_min.max() == 480


def test_remaining_hook_loads_real_artifacts_and_returns_future_ordered_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airalert.models import duration

    records, raions, origin = _synthetic()
    rows = pd.DataFrame({"raion_id": ["a/one"] * 100, "start": [origin] * 100,
                         "checkpoint": [origin + pd.Timedelta(minutes=120)] * 100})
    features = build_landmark_features(records, rows, raions, origin)
    models = _fit_fixed(features, np.linspace(1, 90 * 24 * 60, len(rows)))
    calibration = ConformalDuration(10., 0., 100, origin.isoformat(),
                                    origin.isoformat(), origin.isoformat())
    destination = tmp_path / "generation"
    _save_bundle(destination, models, {str(e): calibration for e in CHECKPOINTS},
                 {"snapshot": origin.isoformat(), "regime_switch": origin.isoformat()})
    models[1].save_model(str(destination / "median_all.txt"))
    (tmp_path / "current.json").write_text('{"generation":"generation"}', encoding="utf-8")
    monkeypatch.setattr(duration, "PRODUCTION_DIR", tmp_path)
    monkeypatch.setattr(duration, "_production_records", lambda now: records)
    monkeypatch.setattr(duration, "load_raions", lambda: raions)
    now = origin + pd.Timedelta(minutes=1000)
    active = pd.DataFrame({"raion_id": ["a/one"], "start": [origin]})
    result = predict_remaining(active, now)
    ends = result[["end_p10", "end_p50", "end_p90"]]
    assert (ends > now).all().all()
    assert (ends.end_p10 <= ends.end_p50).all()
    assert (ends.end_p50 <= ends.end_p90).all()
    assert all(str(dtype) == "datetime64[us, UTC]" for dtype in ends.dtypes)
    assert list(predict_remaining(active.iloc[:0], now)) == list(result)
    with pytest.raises(ValueError, match="timezone-aware"):
        predict_remaining(active, now.tz_localize(None))
    with pytest.raises(ValueError, match="no later"):
        predict_remaining(active.assign(start=now + pd.Timedelta(minutes=1)), now)


def test_v2_episode_windows_do_not_cross_calibration_or_evaluation() -> None:
    origin = pd.Timestamp(TRAIN_START)
    boundary = origin + pd.Timedelta(days=1)
    episodes = pd.DataFrame({"start": [origin, origin, boundary],
                             "end": [boundary - pd.Timedelta(minutes=1), boundary,
                                     boundary + pd.Timedelta(minutes=1)], "censored": False})
    mask = _eligible_window(episodes, origin, boundary, observed_through=boundary, gaps=())
    assert mask.tolist() == [True, False, False]


@pytest.mark.parametrize("model_id", ["D0", "D1"])
def test_lightgbm_prediction_order_with_crossing_log_outputs(model_id: str) -> None:
    # Quantile ordering is verified on actual fitted artifacts in the B3 CLI run.
    # This test guards both the postprocessing and the zero-minute output floor.
    from airalert.models.duration import _predict_boosters

    class ConstantBooster:
        def __init__(self, value: float) -> None:
            self.value = value

        def predict(
            self, features: pd.DataFrame, *, num_threads: int = 4,
        ) -> NDArray[np.float64]:
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
