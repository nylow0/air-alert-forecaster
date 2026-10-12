"""Guard resumable bootstrap and real cold production fits with frozen evaluation."""

import json
from dataclasses import replace
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from airalert import api, cli, config, data, precompute, production, setup
from airalert.features import FeatureContext
from airalert.models import duration, onset


def test_setup_skips_finished_steps_resumes_failure_and_force_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    first, second = tmp_path / "records.parquet", tmp_path / "models.json"
    calls = {"records": 0, "models": 0}
    fail = True

    def records() -> None:
        calls["records"] += 1
        first.write_bytes(b"complete records")

    def models() -> None:
        nonlocal fail
        calls["models"] += 1
        if fail:
            fail = False
            raise RuntimeError("interrupted fit")
        second.write_text("{}", encoding="utf-8")

    steps = [
        setup.Step("records", "Build records", records, lambda: [source], lambda: [first]),
        setup.Step("models", "Fit models", models, lambda: [first], lambda: [second]),
    ]
    monkeypatch.setattr(setup, "build_steps", lambda now: steps)
    with pytest.raises(RuntimeError, match="interrupted"):
        setup.run()
    assert json.loads((tmp_path / "live/setup.json").read_text())["steps"]["models"][
        "status"] == "error"
    setup.run()
    assert calls == {"records": 1, "models": 2}
    setup.run()
    assert calls == {"records": 1, "models": 2}
    assert "skipped, complete" in capsys.readouterr().out
    # A changed input and a lost output must both invalidate durable checkpoints.
    source.write_text('{"new": true}', encoding="utf-8")
    setup.run()
    assert calls == {"records": 2, "models": 3}
    second.unlink()
    setup.run()
    assert calls == {"records": 2, "models": 4}
    setup.run(force=True)
    assert calls == {"records": 3, "models": 5}


def test_cold_production_fits_locked_settings_without_evaluation_or_report_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Train real LightGBM/calibrators, reload the generations and issue an actual payload."""
    locked = config.V2_SELECTION_PATH.read_bytes()
    reports = tmp_path / "reports/v2"
    reports.mkdir(parents=True)
    selection = reports / "selection.json"
    selection.write_bytes(locked)
    (reports / "test2_claim.json").write_text('{"frozen": true}', encoding="utf-8")
    (reports / "metrics.json").write_text('{"unchanged": true}', encoding="utf-8")
    before = {path.name: path.read_bytes() for path in reports.iterdir()}
    generation_root = tmp_path / "production"
    missing_evaluation = tmp_path / "evaluation-not-shipped"
    through = pd.Timestamp("2026-10-11T21:00:00Z")
    start = through - pd.Timedelta(days=160)
    raions = pd.DataFrame({"raion_id": ["x/a", "x/b"], "oblast_id": ["x", "x"]})
    starts = pd.date_range(start, through - pd.Timedelta(days=1), freq="12h")
    records = pd.DataFrame({
        "raion_id": np.tile(raions.raion_id, len(starts)), "oblast_id": "x",
        "level": "raion", "source": "csv", "severity": "unknown",
        "started_at": np.repeat(starts, 2),
    })
    records["finished_at"] = records.started_at + pd.to_timedelta(
        np.resize([600, 540, 30, 60], len(records)), unit="min")
    ongoing = records.iloc[[0]].assign(started_at=through - pd.Timedelta(hours=1),
                                      finished_at=pd.NaT)
    records = pd.concat([records, ongoing], ignore_index=True)
    for column in ("started_at", "finished_at"):
        records[column] = records[column].astype("datetime64[us, UTC]")
    episodes = data.merge_episodes(records)
    context = FeatureContext(raions, {"x/a": ["x/b"], "x/b": ["x/a"]},
                             raions.assign(lat=[49., 50.], lon=[30., 31.]), start,
                             raions[["raion_id"]].assign(area=[100., 200.]))
    snapshot = data.Snapshot(through, "synthetic", start)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({"snapshot": through.isoformat(),
        "coverage": {"archive_gate_passed": True, "gaps": []}}), encoding="utf-8")
    for name, value in (("V2_REPORTS_DIR", reports), ("V2_SELECTION_PATH", selection),
                        ("V2_ARTIFACTS_DIR", missing_evaluation),
                        ("V2_PRODUCTION_DIR", generation_root),
                        ("V2_FORECASTS_DIR", tmp_path / "issued"),
                        ("SNAPSHOT_PATH", snapshot_path), ("V2_TRAIN_START", start)):
        monkeypatch.setattr(config, name, value)
    for module in (production, duration):
        monkeypatch.setattr(module, "load_records", lambda: records)
        monkeypatch.setattr(module, "load_episodes", lambda: episodes)
        monkeypatch.setattr(module, "load_raions", lambda: raions)
        monkeypatch.setattr(module, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(duration, "ARTIFACTS_DIR", missing_evaluation)
    monkeypatch.setattr(duration, "PRODUCTION_DIR", generation_root / "duration")
    monkeypatch.setattr(production, "load_feature_context", lambda: context)
    monkeypatch.setattr(production, "load_observations", lambda t: pd.DataFrame())
    now = through + pd.Timedelta(days=1)
    status = production.refit(now.to_pydatetime())
    duration.refit_production(now)
    assert status.data_through == through.to_pydatetime()
    generation = production._generation()
    choices = json.loads(locked)["horizons"]
    metadata = json.loads((generation / "metadata.json").read_text())
    for horizon in config.HORIZONS:
        choice = choices[str(horizon)]
        model = joblib.load(generation / f"model_{horizon}h.joblib")
        assert isinstance(model, onset.HorizonModel)
        assert model.window == choice["window"]
        assert model.rounds == choice["validation_calibration"]["rounds"]
        assert any(name.startswith("f_geo_") for name in model.features) == (horizon != 12)
        bounds = metadata["horizons"][str(horizon)]
        assert pd.Timestamp(bounds["max_label_end"]) <= through
        assert pd.Timestamp(bounds["prefix_model_data_through"]) <= pd.Timestamp(bounds["min_t"])
        calibrator = joblib.load(generation / f"calibrator_{horizon}h.joblib")
        assert np.isfinite(calibrator.predict([0.1, 0.9])).all()
    frame = production.forecast(now.to_pydatetime())
    assert len(frame) == 2
    assert (np.diff(frame[["p_1", "p_3", "p_12"]], axis=1) >= 0).all()
    assert frame[["actual_onset_1", "actual_onset_3", "actual_onset_12"]].isna().all().all()
    assert frame.active.any()
    assert (frame.loc[frame.active, ["end_p10", "end_p50", "end_p90"]] > now).all().all()
    assert {path.name: path.read_bytes() for path in reports.iterdir()} == before
    assert not missing_evaluation.exists()


def test_serve_on_unprepared_clone_has_one_line_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(config, "FORECASTS_PATH", tmp_path / "not-built.parquet")
    monkeypatch.setattr("sys.argv", ["airalert", "serve", "--port", "8011"])
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == ["App data is missing. Run uv run airalert setup first."]


def test_cold_replay_uses_only_issued_hours_and_nightly_can_extend_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = replace(precompute.Paths(), forecasts=tmp_path / "forecasts.parquet",
                    snapshot=tmp_path / "snapshot.json", raions=tmp_path / "raions.csv",
                    episodes=tmp_path / "episodes.parquet", issued=tmp_path / "issued",
                    preds=tmp_path / "preds")
    paths.issued.mkdir()
    t = pd.Timestamp("2026-10-12T18:00:00Z")
    paths.snapshot.write_text(json.dumps({"snapshot": (t - pd.Timedelta(days=1)).isoformat(),
                                          "csv_sha256": "synthetic",
                                          "regime_switch": "2025-08-14"}), encoding="utf-8")
    raions = pd.DataFrame({"raion_id": ["x/a", "x/b"], "oblast_id": "x"})
    raions.to_csv(paths.raions, index=False)
    pd.DataFrame({"raion_id": pd.Series(dtype=str), "start": pd.Series(
        dtype="datetime64[us, UTC]"), "end": pd.Series(
        dtype="datetime64[us, UTC]")}).to_parquet(paths.episodes, index=False)
    frame = raions[["raion_id"]].assign(t=t, p_1=.1, p_3=.2, p_12=.4, active=False)
    for column in ("active_since", "end_p10", "end_p50", "end_p90"):
        frame[column] = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[us, UTC]")
    for horizon in config.HORIZONS:
        frame[f"actual_onset_{horizon}"] = None
    frame.to_parquet(paths.issued / "first.parquet", index=False)
    # The historical adapter must never be needed for an issued-only cache.
    def forbidden(*args: object) -> None:
        raise AssertionError("Backward evaluation reconstruction was called")

    monkeypatch.setattr(duration, "predict_replay", forbidden)
    result = precompute.run_v2(paths)
    assert result["historical"] == "issued only"
    assert pd.read_parquet(paths.forecasts).t.min() == t
    assert api._load(paths).meta.replay.start == t.to_pydatetime()
    frame.assign(t=t + pd.Timedelta(hours=2)).to_parquet(paths.issued / "next.parquet", index=False)
    # Nightly reconciliation advances provenance while issued probabilities stay frozen.
    saved = json.loads(paths.snapshot.read_text())
    saved.update({"snapshot": (t + pd.Timedelta(hours=1)).isoformat(), "csv_sha256": "new-csv"})
    paths.snapshot.write_text(json.dumps(saved), encoding="utf-8")
    result = precompute.run_v2(paths)
    assert result["issued_hours"] == 2
    assert sorted(pd.read_parquet(paths.forecasts).t.unique()) == [t, t + pd.Timedelta(hours=2)]
    replay = api._load(paths).meta.replay
    assert replay.start == t.to_pydatetime()
    assert replay.end == (t + pd.Timedelta(hours=2)).to_pydatetime()
