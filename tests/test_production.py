"""Exercise the actual hourly API, observation cutoff and immutable scoring workflow."""

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from airalert import config, production
from airalert.data import Snapshot
from airalert.features import FeatureContext
from airalert.models import baseline, onset
from airalert.schemas import ForecastRow, ProductionModel


def _inputs(t: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, FeatureContext]:
    raions = pd.DataFrame({"raion_id": ["a", "b"], "oblast_id": ["x", "x"]})
    context = FeatureContext(raions, {"a": ["b"], "b": ["a"]},
                             raions.assign(lat=[49., 50.], lon=[30., 31.]),
                             t - pd.Timedelta(days=300))
    records = pd.DataFrame({
        "raion_id": ["a", "b", "a"], "oblast_id": "x", "level": "raion", "source": "csv",
        "started_at": [t - pd.Timedelta(hours=2), t - pd.Timedelta(hours=1), t],
        "finished_at": [t + pd.Timedelta(hours=3), t - pd.Timedelta(minutes=20),
                         t + pd.Timedelta(hours=1)],
    })
    observations = pd.DataFrame({
        "raion_id": ["b", "a", "a"], "oblast_id": "x", "level": "raion", "source": "siren",
        "started_at": [t - pd.Timedelta(minutes=10), t - pd.Timedelta(minutes=5),
                       t - pd.Timedelta(minutes=3)], "finished_at": pd.NaT,
        "observed_at": [t - pd.Timedelta(minutes=2), t, t + pd.Timedelta(minutes=1)],
        "origin_uid": ["1", "2", "3"], "origin_event_id": ["1:air", "2:air", "3:air"],
        "hazard_type": "air_raid",
    })
    for column in ("started_at", "finished_at", "observed_at"):
        observations[column] = pd.to_datetime(observations[column], utc=True).astype(
            "datetime64[us, UTC]")
    return records, observations, context


def _install(
    root: Path, monkeypatch: pytest.MonkeyPatch, t: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, FeatureContext]:
    records, observed, context = _inputs(t)
    for name, path in (("V2_PRODUCTION_DIR", root / "production"),
                       ("V2_FORECASTS_DIR", root / "issued"),
                       ("V2_REPORTS_DIR", root / "reports")):
        monkeypatch.setattr(config, name, path)
    generation = config.V2_PRODUCTION_DIR / "test-generation"
    generation.mkdir(parents=True)
    (config.V2_PRODUCTION_DIR / "current.json").write_text(
        json.dumps({"generation": generation.name}), encoding="utf-8")
    model_status = ProductionModel(onset="selected", duration=None, trained_at=t.to_pydatetime(),
                                   data_through=(t - pd.Timedelta(days=2)).to_pydatetime())
    metadata = {"status": model_status.model_dump(mode="json"),
                "horizons": {str(h): {"calibrated": False} for h in config.HORIZONS}}
    (generation / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    training = pd.DataFrame({"t": [t - pd.Timedelta(days=4)] * 2,
                             "raion_id": ["a", "b"], "oblast_id": "x"})
    for horizon in config.HORIZONS:
        training[f"y_{horizon}"] = [0, 1]
        model = onset.HorizonModel("B0", baseline.fit_rates(baseline._calendar(training), horizon),
                                   [], {}, "full", k=1.)
        joblib.dump(model, generation / f"model_{horizon}h.joblib")
        joblib.dump(model, generation / f"b0_{horizon}h.joblib")
    monkeypatch.setattr(production, "load_records", lambda: records)
    monkeypatch.setattr(production, "load_observations", lambda time: observed)
    monkeypatch.setattr(production, "load_feature_context", lambda: context)
    monkeypatch.setattr(production, "load_raions", lambda: context.raions)
    monkeypatch.setattr(production, "load_snapshot", lambda: Snapshot(
        t + pd.Timedelta(days=2), "test", context.regime_switch))
    return records, observed, context


def test_forecast_matches_truncated_inputs_at_several_clocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = pd.Timestamp("2026-10-12T16:00:00Z")
    records, observed, context = _install(tmp_path, monkeypatch, t)
    for clock in (t, t + pd.Timedelta(hours=1), t + pd.Timedelta(hours=4)):
        full = production.forecast(clock.to_pydatetime(), persist=False)
        prefix = records.loc[records.started_at.lt(clock)].copy()
        prefix.loc[prefix.finished_at.gt(clock), "finished_at"] = pd.NaT
        prefix_observed = observed.loc[observed.observed_at.lt(clock)].copy()
        prefix_observed.loc[prefix_observed.finished_at.gt(clock), "finished_at"] = pd.NaT
        monkeypatch.setattr(production, "load_records", lambda prefix=prefix: prefix)
        monkeypatch.setattr(production, "load_observations",
                            lambda time, prefix_observed=prefix_observed: prefix_observed)
        truncated = production.forecast(clock.to_pydatetime(), persist=False)
        pd.testing.assert_frame_equal(full, truncated)
        full_features, _, _ = production.forecast_inputs(records, observed, clock, context=context)
        prefix_features, _, _ = production.forecast_inputs(prefix, prefix_observed, clock,
                                                           context=context)
        pd.testing.assert_frame_equal(full_features, prefix_features)
        assert np.all(np.diff(full[["p_1", "p_3", "p_12"]], axis=1) >= 0)
        for record in full.drop(columns="t").astype(object).where(
            full.drop(columns="t").notna(), None).to_dict(orient="records"):
            ForecastRow.model_validate(record)
        monkeypatch.setattr(production, "load_records", lambda: records)
        monkeypatch.setattr(production, "load_observations", lambda time: observed)
    # The successful pre-hour live poll observes b and proves a is quiet.
    assert production.forecast(t.to_pydatetime(), persist=False).active.tolist() == [False, True]


def test_first_forecast_is_immutable_and_recent_scoring_uses_stored_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A future synthetic clock avoids falsely scoring a backdated test issuance.
    t = pd.Timestamp.now(tz="UTC").ceil("h") + pd.Timedelta(hours=1)
    records, observed, _ = _install(tmp_path, monkeypatch, t)
    first = production.forecast((t + pd.Timedelta(seconds=10)).to_pydatetime())
    stored_path = next(config.V2_FORECASTS_DIR.glob("*.parquet"))
    original = stored_path.read_bytes()
    monkeypatch.setattr(production, "load_records", lambda: records.iloc[0:0])
    monkeypatch.setattr(production, "load_observations", lambda time: observed.iloc[0:0])
    repeated = production.forecast((t + pd.Timedelta(minutes=20)).to_pydatetime())
    pd.testing.assert_frame_equal(first, repeated)
    assert stored_path.read_bytes() == original
    episodes = pd.DataFrame({"raion_id": ["a"], "start": [t + pd.Timedelta(minutes=30)]})
    monkeypatch.setattr(production, "load_episodes", lambda: episodes)
    score = production.score_recent((t + pd.Timedelta(days=1)).to_pydatetime())
    assert all(row["n"] == 2 and row["bss"] == pytest.approx(0.)
               for row in score["windows"]["7"])
    assert (config.V2_REPORTS_DIR / "track_record.json").exists()
    stored = pd.read_parquet(stored_path)
    stored["issued_at"] = stored.t + pd.Timedelta(minutes=10)
    stored.to_parquet(stored_path, index=False)
    assert all(row["n"] == 0 for row in production.score_recent(
        (t + pd.Timedelta(days=1)).to_pydatetime())["windows"]["7"])


def test_observed_at_boundary_and_successful_absence_close_an_origin() -> None:
    t = pd.Timestamp("2026-10-12T16:00:00Z")
    records, observed, _ = _inputs(t)
    view = production.as_of_records(records.iloc[0:0], observed, t)
    assert view.raion_id.tolist() == ["b"]
    assert view.finished_at.isna().all()
    # A later successful poll containing another origin proves b disappeared.
    view = production.as_of_records(records.iloc[0:0], observed, t + pd.Timedelta(minutes=2))
    b = view.loc[view.raion_id.eq("b")].iloc[0]
    assert b.finished_at == t
    assert view.loc[view.raion_id.eq("a"), "finished_at"].isna().any()
