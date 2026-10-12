"""Reproduce the audit's overwrite probes using only isolated reports and tiny data."""

import builtins
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import pytest

from airalert import backtest, config, metrics, production
from airalert.data import Snapshot
from airalert.features import FeatureContext
from airalert.models import duration, onset
from airalert.schemas import OnsetScore, Report


def _saved_files(directory: Path) -> dict[str, bytes]:
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


def _frozen_layout(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    reports = root / "reports" / "v2"
    (reports / "metrics").mkdir(parents=True)
    for name, path in (("V2_REPORTS_DIR", reports),
                       ("V2_METRICS_DIR", reports / "metrics"),
                       ("V2_METRICS_PATH", reports / "metrics.json"),
                       ("V2_SELECTION_PATH", reports / "selection.json"),
                       ("V2_ARTIFACTS_DIR", root / "artifacts" / "v2"),
                       ("V2_PREDS_DIR", root / "data" / "preds"),
                       ("V2_FEATURES_DIR", root / "data" / "features"),
                       ("SNAPSHOT_PATH", root / "data" / "snapshot.json")):
        monkeypatch.setattr(config, name, path)
    report = Report(generated_at=datetime.now(UTC), onset=[OnsetScore(
        model="selected", horizon=3, split=split, row_set="served", n=2,
        base_rate=0.5, brier=0.1, bss=0.2, logloss=0.3, pr_auc=0.8,
        reliability=[], by_oblast={}, by_state={},
    ) for split in ("val", "test")])
    (reports / "metrics.json").write_text(report.model_dump_json(), encoding="utf-8")
    for name in ("selection.json", "bootstrap.json", "duration_scores.json",
                 "metrics/selection.json", "metrics/scores_val.json", "metrics/scores_test2.json"):
        (reports / name).write_text("{}\n", encoding="utf-8")
    (reports / "test2_claim.json").write_text('{"status":"finished"}', encoding="utf-8")
    return reports


@pytest.mark.parametrize("module,flags", [
    ("onset", ["--score-only"]), ("duration", []), ("duration", ["--final"]),
])
def test_audit_cli_probes_refuse_fresh_clone_and_preserve_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: str, flags: list[str],
) -> None:
    reports = _frozen_layout(tmp_path, monkeypatch)
    before = _saved_files(reports)
    # A source-only clone has committed reports but no ignored data or D2 marker.
    shutil.copytree(config.REPO_ROOT / "src" / "airalert", tmp_path / "src" / "airalert",
                    ignore=shutil.ignore_patterns("__pycache__"))
    assert not (tmp_path / "artifacts" / "D2" / "test2_scored.json").exists()
    environment = {**os.environ, "PYTHONPATH": str(tmp_path / "src"),
                   "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-m", f"airalert.models.{module}", *flags],
                            cwd=tmp_path, env=environment, capture_output=True, text=True,
                            timeout=45, check=False)
    assert result.returncode != 0
    assert "v2 evaluation is frozen" in result.stderr
    assert "--out" in result.stderr
    assert _saved_files(reports) == before
    assert len(Report.model_validate_json((reports / "metrics.json").read_text()).onset) == 2


@pytest.mark.parametrize("marker", ["test2_claim.json", "duration_test2_claim.json"])
def test_all_public_writers_refuse_either_committed_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: str,
) -> None:
    reports = _frozen_layout(tmp_path, monkeypatch)
    (reports / "test2_claim.json").rename(reports / marker)
    before = _saved_files(reports)
    for target in ("metrics.json", "metrics/scores_val.json", "metrics/new_fragment.json",
                   "selection.json", "bootstrap.json", "duration_scores.json", "source_shift.json"):
        with pytest.raises(RuntimeError, match="frozen"):
            backtest.write_v2_json(reports / target, {})
    with pytest.raises(RuntimeError, match="frozen"):
        metrics.merge_reports(directory=reports / "metrics", destination=reports / "metrics.json",
                              include_test=False)
    for call in (backtest.run_v2, onset.train_v2, onset.refresh_v2_validation,
                 onset.wait_for_backfill, duration.evaluate_v2, duration.wait_for_backfill):
        with pytest.raises(RuntimeError, match="frozen"):
            call()
    with pytest.raises(RuntimeError, match="frozen"):
        onset.select_v2({}, {}, elapsed_seconds=0.)
    assert _saved_files(reports) == before
    with pytest.raises(ValueError, match="outside"):
        backtest.run_v2(out=reports / "nested-experiment")


def test_score_only_experiments_write_only_explicit_or_default_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = _frozen_layout(tmp_path, monkeypatch)
    before = _saved_files(reports)
    config.V2_PREDS_DIR.mkdir(parents=True)
    config.V2_FEATURES_DIR.mkdir(parents=True)
    t = pd.Timestamp(config.V2_VAL_START)
    rows = pd.DataFrame({"t": [t, t + pd.Timedelta(hours=1)], "raion_id": "a/r",
                         "oblast_id": "a", "f_active": [0., 1.], "f_source_archive": 0.})
    predictions = rows[["t", "raion_id"]].assign(split="val")
    for horizon in config.HORIZONS:
        rows[f"y_{horizon}"] = [0, 1]
        predictions[f"p_{horizon}"] = [0.2, 0.8]
    rows.to_parquet(config.V2_FEATURES_DIR / "val.parquet", index=False)
    predictions.to_parquet(config.V2_PREDS_DIR / "onset_B0-raw.parquet", index=False)
    monkeypatch.setattr(config, "DATA_GAPS", ())
    monkeypatch.setattr(backtest, "load_snapshot", lambda: Snapshot(
        t + pd.Timedelta(days=100), "test", t - pd.Timedelta(days=300)))
    for arguments, destination in (
        (["--score-only", "--out", str(tmp_path / "experiment")], tmp_path / "experiment"),
        (["--score-only", "--out"], config.V2_ARTIFACTS_DIR / "experiments"),
    ):
        onset.main(arguments)
        saved = Report.model_validate_json((destination / "metrics.json").read_text())
        assert len(saved.onset) == 6 and all(score.split == "val" for score in saved.onset)
    assert _saved_files(reports) == before


def test_concurrent_claim_has_exactly_one_winner_and_finished_claim_cannot_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "V2_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(config, "V2_ARTIFACTS_DIR", tmp_path / "artifacts")
    barrier = threading.Barrier(2)

    def attempt() -> metrics.V2EvaluationClaim | None:
        barrier.wait(timeout=10)
        try:
            return metrics.claim_v2_evaluation(tmp_path, "duration", snapshot="2026-10-11T21:00Z")
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: attempt(), range(2)))
    winners = [claim for claim in results if claim is not None]
    assert len(winners) == 1
    claim = winners[0]
    assert json.loads(claim.path.read_bytes())["status"] == "started"
    with pytest.raises(RuntimeError, match="frozen"):
        backtest.write_v2_json(tmp_path / "duration_scores.json", {})
    with pytest.raises(RuntimeError, match="frozen"):
        backtest.write_v2_json(tmp_path / "selection.json", {}, claim=claim)
    backtest.write_v2_json(tmp_path / "duration_scores.json", {"scored": True}, claim=claim)
    claim.finish()
    assert json.loads(claim.path.read_bytes())["status"] == "finished"
    before = _saved_files(tmp_path)
    with pytest.raises(RuntimeError, match="frozen"):
        backtest.write_v2_json(tmp_path / "duration_scores.json", {}, claim=claim)
    assert _saved_files(tmp_path) == before


def test_committed_publication_and_claim_creation_cannot_interleave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "V2_REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(config, "V2_ARTIFACTS_DIR", tmp_path / "artifacts")
    publishing, release = threading.Event(), threading.Event()
    target = config.V2_REPORTS_DIR / "duration_scores.json"

    def publish() -> None:
        with metrics.v2_report_write(target):
            publishing.set()
            assert release.wait(timeout=10)
            assert not metrics.v2_freeze_marker()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{"validation":true}', encoding="utf-8")

    def claim() -> metrics.V2EvaluationClaim:
        return metrics.claim_v2_evaluation(config.V2_REPORTS_DIR, "duration",
                                           snapshot="2026-10-11T21:00Z")

    with ThreadPoolExecutor(max_workers=2) as workers:
        writer = workers.submit(publish)
        assert publishing.wait(timeout=10)
        claimant = workers.submit(claim)
        try:
            # A live writer holds publication for this bounded contention probe.
            with pytest.raises(TimeoutError):
                claimant.result(timeout=0.2)
        finally:
            release.set()
        writer.result(timeout=15)
        owned = claimant.result(timeout=15)
    assert json.loads(target.read_bytes()) == {"validation": True}
    backtest.write_v2_json(target, {"test2": True}, claim=owned)
    owned.finish()
    with pytest.raises(RuntimeError, match="frozen"):
        backtest.write_v2_json(target, {"validation": True})
    assert json.loads(target.read_bytes()) == {"test2": True}


@pytest.mark.parametrize("kind", ["onset", "duration"])
def test_interrupted_final_claim_blocks_silent_rescoring_before_any_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["onset", "duration"],
) -> None:
    reports = _frozen_layout(tmp_path, monkeypatch)
    (reports / "test2_claim.json").unlink()
    snapshot = Snapshot(pd.Timestamp("2026-10-11T21:00Z"), "test",
                        pd.Timestamp("2025-08-14T00:00Z"))
    monkeypatch.setattr(config, "DATA_GAPS", ())

    def crash(*args: object, **kwargs: object) -> None:
        marker = metrics.v2_freeze_marker()
        assert marker is not None and json.loads(marker.read_bytes())["status"] == "started"
        raise RuntimeError("interrupted while reading targets")

    if kind == "onset":
        monkeypatch.setattr(backtest, "load_snapshot", lambda: snapshot)
        monkeypatch.setattr(backtest, "v2_prediction_hashes", lambda directory=None: {})
        monkeypatch.setattr(backtest, "v2_score_predictions", crash)
        (reports / "selection.json").write_text(json.dumps({
            "snapshot": snapshot.snapshot.isoformat(), "data_gaps": [], "prediction_sha256": {},
        }), encoding="utf-8")
        evaluate = backtest.run_v2
    else:
        monkeypatch.setattr(duration, "load_snapshot", lambda: snapshot)
        monkeypatch.setattr(duration, "ARTIFACTS_DIR", tmp_path / "artifacts")
        directory = duration.ARTIFACTS_DIR / "D2"
        directory.mkdir(parents=True)
        (directory / "evaluation_val.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(duration, "load_episodes", crash)
        evaluate = duration.evaluate_v2
    with pytest.raises(RuntimeError, match="interrupted"):
        evaluate(final=True)
    before = _saved_files(reports)
    with pytest.raises(RuntimeError, match="started claim may be unfinished"):
        evaluate(final=True)
    assert _saved_files(reports) == before


def _duration_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Snapshot]:
    starts = pd.to_datetime([f"2026-{month:02d}-{day:02d}T00:00Z"
                             for month in (5, 7, 8, 9) for day in (2, 5, 12, 20)], utc=True)
    episodes = pd.DataFrame({"raion_id": "a/r", "oblast_id": "a", "start": starts,
                             "end": starts + pd.to_timedelta(np.arange(16) + 900, unit="m"),
                             "censored": False, "source_level": "raion"})
    records = episodes.rename(columns={"start": "started_at", "end": "finished_at",
                                       "source_level": "level"}).drop(columns="censored")
    raions = pd.DataFrame({"raion_id": ["a/r"], "oblast_id": ["a"]})
    snapshot = Snapshot(pd.Timestamp("2026-10-11T21:00Z"), "test",
                        pd.Timestamp("2025-08-14T00:00Z"))
    return episodes, records, raions, snapshot


def test_duration_experiment_scores_tiny_cohorts_and_freezes_its_own_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = _frozen_layout(tmp_path, monkeypatch)
    before = _saved_files(reports)
    episodes, records, raions, snapshot = _duration_inputs()
    config.SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SNAPSHOT_PATH.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "DATA_GAPS", ())
    monkeypatch.setattr(duration, "ARTIFACTS_DIR", tmp_path / "artifacts")
    for name, value in (("load_snapshot", snapshot), ("load_episodes", episodes),
                        ("load_records", records), ("load_raions", raions)):
        monkeypatch.setattr(duration, name, lambda value=value: value)
    features = duration.build_start_features(records, episodes, raions, snapshot.regime_switch)
    models = duration._fit_fixed(features, duration.duration_minutes(episodes))
    baseline = duration.ARTIFACTS_DIR / "D1"
    baseline.mkdir(parents=True)
    for model, column in zip(models, duration.PREDICTION_COLUMNS, strict=True):
        model.save_model(str(baseline / f"{column}.txt"))
    out = tmp_path / "experiment"
    validation = duration.evaluate_v2(out=out)
    assert len(validation.duration) == 46
    final = duration.evaluate_v2(out=out, final=True)
    assert len(final.duration) == 92
    claim = json.loads((out / "duration_test2_claim.json").read_bytes())
    assert claim["status"] == "finished" and claim["snapshot"] == snapshot.snapshot.isoformat()
    saved = _saved_files(out)
    with pytest.raises(RuntimeError, match="frozen"):
        duration.evaluate_v2(out=out)
    assert _saved_files(out) == saved
    assert _saved_files(reports) == before


def test_production_refits_and_score_recent_never_open_frozen_files_for_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = _frozen_layout(tmp_path, monkeypatch)
    episodes, records, raions, snapshot = _duration_inputs()
    config.SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SNAPSHOT_PATH.write_text("{}", encoding="utf-8")
    choice = {"candidate": "B0-raw", "identity": "B0", "window": "full", "k": 1.,
              "calibrated": False, "validation_calibration": {
                  "apply_from": "2026-08-01T00:00Z",
                  "prefix_model_data_through": "2026-06-20T00:00Z", "rounds": 0}}
    config.V2_SELECTION_PATH.write_text(json.dumps({
        "horizons": {str(h): choice for h in config.HORIZONS},
    }), encoding="utf-8")
    before = _saved_files(reports)
    context = FeatureContext(raions, {"a/r": []}, raions.assign(lat=49., lon=30.),
                             snapshot.regime_switch)
    monkeypatch.setattr(config, "DATA_GAPS", ())
    monkeypatch.setattr(config, "V2_TRAIN_START", pd.Timestamp("2026-05-01T00:00Z"))
    monkeypatch.setattr(config, "V2_PRODUCTION_DIR", tmp_path / "artifacts" / "production")
    monkeypatch.setattr(config, "V2_FORECASTS_DIR", tmp_path / "issued")
    monkeypatch.setattr(duration, "PRODUCTION_DIR", config.V2_PRODUCTION_DIR / "duration")
    for module in (production, duration):
        for name, value in (("load_snapshot", snapshot), ("load_episodes", episodes),
                            ("load_records", records), ("load_raions", raions)):
            monkeypatch.setattr(module, name, lambda value=value: value)
    monkeypatch.setattr(production, "load_feature_context", lambda: context)
    writes: list[Path] = []
    real_open, real_io_open = builtins.open, io.open

    def guard(file: str | bytes | int | os.PathLike[str], mode: str = "r", *args: object,
              **kwargs: object) -> None:
        if isinstance(file, int) or not any(flag in mode for flag in "wax+"):
            return
        target = Path(os.fsdecode(file)).resolve()
        writes.append(target)
        if target.is_relative_to(reports):
            assert target.name in ("track_record.json", "track_record.json.tmp")

    def guarded_open(file: str | bytes | int | os.PathLike[str], mode: str = "r",
                     *args: object, **kwargs: object) -> io.IOBase:
        guard(file, mode)
        return real_open(file, mode, *args, **kwargs)

    def guarded_io_open(file: str | bytes | int | os.PathLike[str], mode: str = "r",
                        *args: object, **kwargs: object) -> io.IOBase:
        guard(file, mode)
        return real_io_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    now = snapshot.snapshot + pd.Timedelta(hours=1)
    production.refit(now.to_pydatetime())
    duration.refit_production(now)
    rolling = production.score_recent(now.to_pydatetime())
    assert (config.V2_PRODUCTION_DIR / "current.json").exists()
    assert (duration.PRODUCTION_DIR / "current.json").exists()
    assert all(score["n"] == 0 for score in rolling["windows"]["7"])
    assert reports / "track_record.json.tmp" in writes
    assert all(path.is_relative_to(config.V2_PRODUCTION_DIR)
               or path == reports / "track_record.json.tmp" for path in writes)
    after = _saved_files(reports)
    assert {name: value for name, value in after.items() if name != "track_record.json"} == before
