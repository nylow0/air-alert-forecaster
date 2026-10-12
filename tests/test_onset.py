"""Guard the training clock, forward calibration and actual Parquet publication."""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from airalert import config
from airalert.models.onset import (
    INNER_WEEKS,
    calibrate,
    calibration_masks,
    fit_calibrator,
    training_masks,
    validate_predictions,
    write_predictions,
)


@pytest.mark.parametrize("horizon", config.HORIZONS)
def test_training_excludes_future_embargo_and_corrupted_lookbacks(
    horizon: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    val_start = pd.Timestamp(config.VAL_START)
    holdout_start = val_start - pd.Timedelta(weeks=INNER_WEEKS)
    gap_start = holdout_start - pd.Timedelta(days=10)
    gap_end = gap_start + pd.Timedelta(hours=2)
    monkeypatch.setattr(config, "DATA_GAPS", ((gap_start, gap_end),))
    times = pd.DatetimeIndex([
        pd.Timestamp(config.TRAIN_START) - pd.Timedelta(hours=1),
        holdout_start - pd.Timedelta(hours=horizon),
        holdout_start - pd.Timedelta(minutes=30), holdout_start,
        val_start - pd.Timedelta(hours=horizon), val_start - pd.Timedelta(minutes=30),
        val_start, pd.Timestamp(config.TEST_START), gap_start,
        gap_start - pd.Timedelta(hours=horizon), gap_end + pd.Timedelta(hours=24),
        gap_end + pd.Timedelta(hours=config.DATA_GAP_FEATURE_RECOVERY_H),
    ])
    snapshot = pd.Timestamp(config.TEST_START) + pd.Timedelta(days=40)
    frame = pd.DataFrame({"t": times, f"y_{horizon}": 0})
    refit, inner_train, holdout = training_masks(frame, horizon, snapshot=snapshot)
    assert set(np.flatnonzero(refit)) == {1, 2, 3, 4, 11}
    assert set(np.flatnonzero(inner_train)) == {1, 11}
    assert set(np.flatnonzero(holdout)) == {3, 4}
    assert not (inner_train & holdout).any()
    for mask in (refit, inner_train, holdout):
        assert frame.loc[mask, "t"].lt(val_start).all()
        assert (frame.loc[mask, "t"] + pd.Timedelta(hours=horizon)).le(val_start).all()
    assert (frame.loc[inner_train, "t"] + pd.Timedelta(hours=horizon)).le(holdout_start).all()
    regime_refit, _, _ = training_masks(frame, horizon, regime_start=holdout_start,
                                       snapshot=snapshot)
    assert frame.loc[regime_refit, "t"].ge(holdout_start).all()
    frame.loc[3, f"y_{horizon}"] = np.nan
    assert not training_masks(frame, horizon, snapshot=snapshot)[0].iloc[3]


def test_validation_calibration_moves_forward_and_keeps_first_month_raw() -> None:
    start = pd.Timestamp(config.VAL_START)
    seam = start + pd.DateOffset(months=1)
    snapshot = pd.Timestamp(config.TEST_START) + pd.Timedelta(days=40)
    times = pd.DatetimeIndex([
        start, start + pd.Timedelta(hours=1), seam - pd.Timedelta(hours=1),
        seam, seam + pd.Timedelta(hours=1),
    ])
    frame = pd.DataFrame({"t": times, "y_3": [0, 1, 1, 0, 0]})
    raw = np.array([0.1, 0.9, 0.7, 0.1, 0.9])
    first, second, embargo = calibration_masks(frame, 3, snapshot=snapshot)
    assert first.to_list() == [True, True, False, False, False]
    assert second.to_list() == [False, False, False, True, True]
    assert embargo.to_list() == [False, False, True, False, False]
    predicted, _, _ = calibrate(frame, raw, 3, snapshot=snapshot)
    changed = frame.copy()
    changed.loc[first, "y_3"] = 1 - changed.loc[first, "y_3"]
    altered, _, _ = calibrate(changed, raw, 3, snapshot=snapshot)
    np.testing.assert_array_equal(predicted[:3], altered[:3])
    assert not np.array_equal(predicted[second], altered[second])
    assert predicted[embargo][0] == raw[embargo][0]
    np.testing.assert_array_equal(predicted[:3], raw[:3])


def test_calibrator_chronology_maturity_and_saved_reproduction(tmp_path: Path) -> None:
    boundary = pd.Timestamp(config.V2_VAL_START)
    rows = pd.DataFrame({"t": [boundary - pd.Timedelta(days=2),
                               boundary - pd.Timedelta(hours=12)], "y_12": [0, 1]})
    fitted, provenance = fit_calibrator(rows, np.array([0.2, 0.7]), 12, boundary)
    assert pd.Timestamp(provenance["max_t"]) < pd.Timestamp(provenance["apply_from"])
    assert pd.Timestamp(provenance["max_label_end"]) <= boundary
    path = tmp_path / "calibrator.joblib"
    joblib.dump(fitted, path)
    np.testing.assert_array_equal(fitted.predict([0.3, 0.8]),
                                  joblib.load(path).predict([0.3, 0.8]))
    crossing = rows.copy()
    crossing.loc[1, "t"] = boundary - pd.Timedelta(hours=11)
    with pytest.raises(ValueError, match="labels cross"):
        fit_calibrator(crossing, np.array([0.2, 0.7]), 12, boundary)
    crossing.loc[1, "t"] = boundary
    with pytest.raises(ValueError, match="strictly precede"):
        fit_calibrator(crossing, np.array([0.2, 0.7]), 12, boundary)


def test_written_predictions_cover_keys_and_reject_invalid_probabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "PREDS_DIR", tmp_path)
    snapshot = pd.Timestamp(config.TEST_START) + pd.Timedelta(days=40)
    outputs = {
        split: pd.DataFrame({"t": [time, time], "raion_id": ["a/x", "b/y"]})
        for split, time in (("val", pd.Timestamp(config.VAL_START)),
                            ("test", pd.Timestamp(config.TEST_START)), ("latest", snapshot))
    }
    frames = {split: rows.assign(split=split, p_1=0.0, p_3=0.3, p_12=1.0)
              for split, rows in outputs.items()}
    path = write_predictions("M1", frames, outputs, snapshot)
    written = pd.read_parquet(path)
    validate_predictions(written, outputs, snapshot)
    assert len(written) == 6
    assert str(written.t.dtype) == "datetime64[us, UTC]"
    with pytest.raises(ValueError, match="cover feature keys exactly"):
        validate_predictions(written.iloc[:-1], outputs, snapshot)
    invalid = written.copy()
    invalid.loc[0, "p_3"] = 1.01
    with pytest.raises(ValueError, match="finite and in"):
        validate_predictions(invalid, outputs, snapshot)
    invalid.loc[0, "p_3"] = np.nan
    with pytest.raises(ValueError, match="finite and in"):
        validate_predictions(invalid, outputs, snapshot)
