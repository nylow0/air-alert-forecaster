"""Guard temporal boundaries, held-out isolation, and identical-row BSS with tiny data."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from airalert import backtest, config, metrics
from airalert.data import Snapshot
from airalert.models import baseline, duration
from airalert.schemas import Report, Selection


def _frame(times: list[pd.Timestamp]) -> pd.DataFrame:
    result = pd.DataFrame({"t": pd.to_datetime(times, utc=True)})
    for horizon in config.HORIZONS:
        result[f"y_{horizon}"] = pd.Series([0] * len(times), dtype="Int8")
    return result


@pytest.mark.parametrize("horizon", config.HORIZONS)
def test_embargo_keeps_exact_boundary_and_drops_crossing(horizon: int) -> None:
    delta = pd.Timedelta(hours=horizon)
    val, test = pd.Timestamp(config.VAL_START), pd.Timestamp(config.TEST_START)
    rows = _frame([val - delta - pd.Timedelta(hours=1), val - delta,
                   val - delta + pd.Timedelta(minutes=1), test - delta,
                   test - delta + pd.Timedelta(minutes=1)])
    snapshot = test + pd.Timedelta(days=7)
    assert backtest.trainable_mask(rows, horizon, snapshot=snapshot).tolist() == [
        True, True, False, False, False
    ]
    assert backtest.scorable_mask(rows, horizon, snapshot=snapshot).tolist() == [
        False, False, False, True, False
    ]


@pytest.mark.parametrize("horizon", config.HORIZONS)
def test_gap_label_and_recovery_edges(horizon: int, monkeypatch: pytest.MonkeyPatch) -> None:
    start = pd.Timestamp(config.VAL_START) - pd.Timedelta(days=10)
    end = start + pd.Timedelta(hours=4)
    monkeypatch.setattr(config, "DATA_GAPS", ((start, end),))
    delta = pd.Timedelta(hours=horizon)
    recovery_end = end + pd.Timedelta(hours=config.DATA_GAP_FEATURE_RECOVERY_H)
    times = [start - delta - pd.Timedelta(microseconds=1), start - delta,
             start, end, recovery_end - pd.Timedelta(microseconds=1), recovery_end]
    rows = _frame(times)
    expected = [True, False, False, False, False, True]
    snapshot = pd.Timestamp(config.TEST_START) + pd.Timedelta(days=7)
    assert backtest.data_gap_mask(rows, horizon).tolist() == expected
    assert backtest.trainable_mask(rows, horizon, snapshot=snapshot).tolist() == expected
    monkeypatch.setattr(config, "VAL_START", start - pd.Timedelta(days=2))
    assert backtest.scorable_mask(rows, horizon, snapshot=snapshot).tolist() == expected


def test_split_snapshot_null_labels_and_explicit_final() -> None:
    snapshot = pd.Timestamp(config.TEST_START) + pd.Timedelta(days=5)
    end = snapshot - pd.Timedelta(hours=config.TEST_END_OFFSET_H)
    times = [pd.Timestamp(config.TRAIN_START) - pd.Timedelta(hours=1),
             pd.Timestamp(config.TRAIN_START), pd.Timestamp(config.VAL_START),
             pd.Timestamp(config.TEST_START), end, end + pd.Timedelta(hours=1), snapshot]
    rows = _frame(times)
    assert backtest.assign_split(rows.t, snapshot=snapshot).tolist() == [
        "none", "train", "val", "test", "test", "none", "latest"
    ]
    assert backtest.assign_split(snapshot, snapshot=snapshot) == "latest"
    assert backtest.scorable_mask(rows, 1, snapshot=snapshot).tolist() == [
        False, False, True, False, False, False, False
    ]
    assert backtest.scorable_mask(rows, 1, final=True, snapshot=snapshot).tolist() == [
        False, False, True, True, True, False, False
    ]
    rows.loc[2, "y_1"] = pd.NA
    assert not backtest.scorable_mask(rows, 1, snapshot=snapshot).any()
    with pytest.raises(ValueError, match="timezone-aware"):
        backtest.assign_split(pd.Timestamp(config.VAL_START).tz_localize(None), snapshot=snapshot)


def _sandbox(monkeypatch: pytest.MonkeyPatch, directory: Path) -> Snapshot:
    for name, child in (("FEATURES_DIR", "features"), ("PREDS_DIR", "preds"),
                        ("METRICS_DIR", "metrics"), ("FIGURES_DIR", "figures"),
                        ("ARTIFACTS_DIR", "artifacts")):
        path = directory / child
        path.mkdir()
        monkeypatch.setattr(config, name, path)
    for name, path in (("METRICS_PATH", directory / "metrics.json"),
                       ("SCORES_VAL_PATH", config.METRICS_DIR / "scores_val.json"),
                       ("SCORES_TEST_PATH", config.METRICS_DIR / "scores_test.json"),
                       ("SELECTION_PATH", config.METRICS_DIR / "selection.json"),
                       ("EPISODES_PATH", directory / "episodes.parquet")):
        monkeypatch.setattr(config, name, path)
    snapshot = Snapshot(pd.Timestamp(config.TEST_START) + pd.Timedelta(days=5),
                        "synthetic", pd.Timestamp(config.VAL_START) - pd.Timedelta(days=100))
    monkeypatch.setattr(backtest, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(baseline, "load_snapshot", lambda: snapshot)
    return snapshot


def _synthetic_onset(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    _sandbox(monkeypatch, directory)
    val = _frame([pd.Timestamp(config.VAL_START) + pd.Timedelta(hours=1),
                  pd.Timestamp(config.VAL_START) + pd.Timedelta(hours=2)])
    val = val.assign(raion_id="o/r", oblast_id="o", f_active=[0, 1])
    for horizon in config.HORIZONS:
        val[f"y_{horizon}"] = [1, 0]
    val.to_parquet(config.FEATURES_DIR / "val.parquet", index=False)
    predictions = val[["t", "raion_id"]].assign(split="val")
    # These deliberately bad test rows must never reach the default scorer.
    bad_test = pd.DataFrame({"t": [pd.Timestamp(config.TEST_START)],
                             "raion_id": ["unknown"], "split": ["test"]})
    for horizon in config.HORIZONS:
        predictions[f"p_{horizon}"] = [0.9, 0.8]
        bad_test[f"p_{horizon}"] = np.nan
    pd.concat([predictions, bad_test]).to_parquet(config.PREDS_DIR / "onset_B0.parquet",
                                                index=False)
    restricted = predictions.iloc[:1].copy()
    for horizon in config.HORIZONS:
        restricted[f"p_{horizon}"] = 0.8
    restricted.to_parquet(config.PREDS_DIR / "onset_M1+text@text-window.parquet", index=False)


def test_harness_never_reads_test_and_aligns_restricted_bss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _synthetic_onset(monkeypatch, tmp_path)
    # A previous final fragment must not leak into a validation-only report.
    config.SCORES_TEST_PATH.write_text("not even valid JSON", encoding="utf-8")
    read_parquet = pd.read_parquet
    observed: list[Path] = []

    def guarded_read(path: Path, *args: object, **kwargs: object) -> pd.DataFrame:
        observed.append(path)
        assert path.name != "test.parquet"
        if path.parent == config.PREDS_DIR:
            assert kwargs["filters"] == [("split", "==", "val")]
        return read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", guarded_read)
    backtest.run(False)
    report = Report.model_validate_json(config.METRICS_PATH.read_text(encoding="utf-8"))
    assert observed and all(score.split == "val" for score in report.onset)
    restricted = next(score for score in report.onset if score.model == "M1+text")
    assert restricted.row_set == "text-window" and restricted.n == 1
    assert restricted.bss == pytest.approx(-3)
    assert restricted.by_oblast["o"].bss == pytest.approx(-3)
    assert restricted.by_state["quiet"].bss == pytest.approx(-3)
    assert config.SCORES_TEST_PATH.read_text(encoding="utf-8") == "not even valid JSON"


def test_harness_rejects_undeclared_missing_predictions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _synthetic_onset(monkeypatch, tmp_path)
    path = config.PREDS_DIR / "onset_M1+text@text-window.parquet"
    pd.read_parquet(path).to_parquet(config.PREDS_DIR / "onset_B1.parquet", index=False)
    with pytest.raises(ValueError, match="missing forecast keys"):
        backtest.run(False)


def test_duration_minutes_zero_clip_and_array_metrics() -> None:
    start = pd.Timestamp(config.VAL_START) + pd.Timedelta(hours=1)
    rows = pd.DataFrame({"start": [start, start],
                         "end": [start, start + pd.Timedelta(minutes=10)],
                         "q10_min": [1, 2], "q50_min": [1, 8], "q90_min": [1, 12]})
    score = backtest.score_duration_frame("D0", "val", rows)
    assert score.n == 2 and score.mae_q50_min == 1 and score.coverage_80 == 1
    assert score.pinball_q10 == pytest.approx(0.4)
    assert score.pinball_q50 == pytest.approx(0.5)
    assert score.pinball_q90 == pytest.approx(0.1)
    assert metrics.brier_score([0, 1], [0.2, 0.8]) == pytest.approx(0.04)
    assert metrics.log_loss([0, 1], [0, 1]) == pytest.approx(-np.log(1 - 1e-6))
    assert metrics.pr_auc([0, 1], [0.2, 0.8]) == 1
    assert metrics.brier_skill_score([0, 1], [0, 1], [0, 1]) is None
    cells = metrics.reliability([0, 1], [0, 1])
    assert cells[0].count == cells[9].count == 1
    assert cells[1].mean_pred is None and cells[1].frac_pos is None
    with pytest.raises(ValueError, match="final=True"):
        backtest.score_duration_frame("D0", "test", rows)


def test_duration_parquet_path_exclusions_and_final_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _synthetic_onset(monkeypatch, tmp_path)
    start = pd.Timestamp(config.VAL_START) + pd.Timedelta(hours=1)
    gap = start + pd.Timedelta(hours=2)
    monkeypatch.setattr(duration, "DATA_GAPS", ((gap, gap + pd.Timedelta(minutes=15)),))
    test_start = pd.Timestamp(config.TEST_START)
    episodes = pd.DataFrame({
        "raion_id": ["o/r"] * 6,
        "start": [start, start + pd.Timedelta(hours=1), gap,
                  start + pd.Timedelta(hours=3), start + pd.Timedelta(hours=4), test_start],
        "end": [start, start + pd.Timedelta(hours=1, minutes=10),
                gap + pd.Timedelta(minutes=2), pd.NaT, test_start,
                test_start + pd.Timedelta(minutes=10)],
        "censored": [False, False, False, True, False, False],
    })
    episodes.to_parquet(config.EPISODES_PATH, index=False)
    predictions = episodes[["raion_id", "start"]].assign(
        split=["val"] * 5 + ["test"], q10_min=[1, 2, 1, 1, 1, np.nan],
        q50_min=[1, 8, 1, 1, 1, np.nan], q90_min=[1, 12, 1, 1, 1, np.nan],
    )
    path = config.PREDS_DIR / "duration_D0.parquet"
    predictions.to_parquet(path, index=False)
    backtest.run(False)
    report = Report.model_validate_json(config.METRICS_PATH.read_text())
    assert report.duration[0].n == 2
    assert report.duration[0].mae_q50_min == 1
    assert not config.SCORES_TEST_PATH.exists()
    assert "TEST SET SCORED" not in capsys.readouterr().out

    # All final-path data is synthetic; production held-out targets stay unopened.
    test_feature = _frame([test_start]).assign(raion_id="o/r", oblast_id="o", f_active=0)
    for horizon in config.HORIZONS:
        test_feature[f"y_{horizon}"] = 1
    test_feature.to_parquet(config.FEATURES_DIR / "test.parquet", index=False)
    baseline_path = config.PREDS_DIR / "onset_B0.parquet"
    baseline_predictions = pd.read_parquet(baseline_path)
    baseline_predictions.loc[baseline_predictions.split.eq("test"), "raion_id"] = "o/r"
    for horizon in config.HORIZONS:
        baseline_predictions.loc[baseline_predictions.split.eq("test"), f"p_{horizon}"] = 0.5
    baseline_predictions.to_parquet(baseline_path, index=False)
    predictions.loc[5, ["q10_min", "q50_min", "q90_min"]] = [2, 8, 12]
    predictions.to_parquet(path, index=False)
    selection = Report(generated_at=None, selection=Selection(onset="B0", duration="D0",
                                                             note="synthetic selection"))
    config.SELECTION_PATH.write_text(selection.model_dump_json(), encoding="utf-8")
    backtest.run(True)
    assert "TEST SET SCORED (--final explicitly enabled)" in capsys.readouterr().out
    final_report = Report.model_validate_json(config.SCORES_TEST_PATH.read_text())
    assert len(final_report.onset) == len(config.HORIZONS)
    assert final_report.duration[0].n == 1 and final_report.duration[0].mae_q50_min == 2
    merged = Report.model_validate_json(config.METRICS_PATH.read_text())
    assert merged.selection == selection.selection


def test_baseline_shrinkage_uses_training_only_and_predicts_without_test_labels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = _sandbox(monkeypatch, tmp_path)
    train_times = pd.date_range(snapshot.regime_switch + pd.Timedelta(days=1), periods=10,
                                freq="7D")
    training = _frame(list(train_times)).assign(raion_id="o/r", oblast_id="o")
    for horizon in config.HORIZONS:
        training[f"y_{horizon}"] = [1, 0] * 5
    training.to_parquet(config.FEATURES_DIR / "train.parquet", index=False)
    val = _frame([pd.Timestamp(config.VAL_START) + pd.Timedelta(hours=1)]).assign(
        raion_id="o/r", oblast_id="o"
    )
    val.to_parquet(config.FEATURES_DIR / "val.parquet", index=False)
    for split, time in (("test", pd.Timestamp(config.TEST_START)), ("latest", snapshot.snapshot)):
        # These files intentionally have no targets at all.
        pd.DataFrame({"t": [time], "raion_id": ["o/r"], "oblast_id": ["o"]}).to_parquet(
            config.FEATURES_DIR / f"{split}.parquet", index=False
        )
    baseline.train()
    output = pd.read_parquet(config.PREDS_DIR / "onset_B0.parquet")
    assert set(output.split) == {"val", "test", "latest"}
    assert (output[[f"p_{h}" for h in config.HORIZONS]] == 0.5).all().all()
    metadata = json.loads((config.ARTIFACTS_DIR / "B0" / "metadata.json").read_text())
    assert len(metadata["comparisons"]) == 2 * len(config.HORIZONS)
    rows = baseline._calendar(pd.DataFrame({
        "t": [train_times[0], train_times[0]], "raion_id": ["o/a", "o/b"],
        "oblast_id": ["o", "o"], "y_1": [0, 1],
    }))
    fitted = baseline.fit_rates(rows, 1)
    assert baseline.predict_rates(rows, fitted, 1).tolist() == [0.25, 0.75]
