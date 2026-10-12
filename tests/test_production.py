"""Exercise the actual hourly API, observation cutoff and immutable scoring workflow."""

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from airalert import config, ingest, production
from airalert.data import Snapshot
from airalert.features import FeatureContext
from airalert.models import baseline, duration, onset
from airalert.schemas import ForecastRow, ProductionModel
from airalert.sources import Event, Level
from airalert.sources.locations import Location


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


def test_persistent_community_is_displayed_but_excluded_from_forecast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the actual forecast producer and display aggregation on the same origins."""
    t = pd.Timestamp("2026-10-12T18:00:00Z")
    records, _, context = _install(tmp_path, monkeypatch, t)
    community_start = t - pd.Timedelta(days=210)
    district_start = t - pd.Timedelta(minutes=30)
    events = [Event(
        uid + ":air", uid, uid, "air_raid", (Level("red", start.to_pydatetime()),),
        start.to_pydatetime(), None, start.to_pydatetime(), "siren", start_is_exact=False,
    ) for uid, start in (("community", community_start), ("district", district_start))]

    class Geography:
        by_uid = {uid: Location(uid, uid, "x", ("a",), level)
                  for uid, level in (("community", "hromada"), ("district", "raion"))}

        def resolve(self, uid: str) -> Location:
            return self.by_uid[uid]

    geography = Geography()
    crosswalk = pd.DataFrame({"raion_id": ["a"], "oblast_id": ["x"], "shape_id": ["shape"]})
    monkeypatch.setattr(production, "load_records", lambda: records.iloc[:0])
    monkeypatch.setattr(duration, "load_records", lambda: records.iloc[:0])
    for origins, expected in ((events, district_start), (events[:1], None)):
        observed = ingest.event_records(origins, geography)
        observed["observed_at"] = t - pd.Timedelta(seconds=30)
        monkeypatch.setattr(production, "load_observations",
                            lambda time, observed=observed: observed)
        features, state, view = production.forecast_inputs(
            records.iloc[:0], observed, t, context=context)
        served = production.forecast(t.to_pydatetime(), persist=False).set_index("raion_id")
        assert bool(served.loc["a", "active"]) == (expected is not None)
        assert bool(features.loc[features.raion_id.eq("a"), "f_active"].iloc[0]) == (
            expected is not None)
        assert not view.level.eq("hromada").any()
        remaining_inputs = duration._production_records(t)
        pd.testing.assert_frame_equal(view, remaining_inputs.drop(columns="observed_at"))
        if expected is not None:
            assert served.loc["a", "active_since"] == expected
            assert state.active_since.iloc[0] == expected
        else:
            assert pd.isna(served.loc["a", "active_since"])
        polygon = ingest.polygon_state(origins, geography, crosswalk, t.to_pydatetime())[0]
        assert polygon["red"] == {"active": True, "since": (
            district_start if expected is not None else community_start
        ).isoformat().replace("+00:00", "Z")}


def test_live_spells_close_on_absence_and_inexact_color_changes_do_not_add_starts() -> None:
    t = pd.Timestamp("2026-10-12T18:00:00Z")
    records, observed, context = _inputs(t)
    first = observed.iloc[:1].copy()
    first["started_at"] = t - pd.Timedelta(hours=1)
    first["observed_at"] = t - pd.Timedelta(minutes=50)
    first["start_is_exact"] = False
    escalation = first.copy()
    escalation["started_at"] = t - pd.Timedelta(minutes=40)
    escalation["observed_at"] = t - pd.Timedelta(minutes=30)
    absence = first.copy()
    absence["raion_id"] = None
    absence["hazard_type"] = "poll"
    absence["observed_at"] = t - pd.Timedelta(minutes=20)
    restarted = first.copy()
    restarted["started_at"] = t - pd.Timedelta(minutes=10)
    restarted["observed_at"] = t - pd.Timedelta(minutes=5)
    observations = pd.concat([first, escalation, absence, restarted], ignore_index=True)
    _, _, continuous = production.forecast_inputs(
        records.iloc[:0], observations, t - pd.Timedelta(minutes=25), context=context)
    assert len(continuous) == 1
    assert continuous.started_at.iloc[0] == first.started_at.iloc[0]
    assert continuous.finished_at.isna().all()
    # Downtime and the first absent successful poll end the old spell, even if
    # the source reuses the same origin/event ID for a later alert.
    _, state, quiet = production.forecast_inputs(
        records.iloc[:0], observations, t - pd.Timedelta(minutes=15), context=context)
    assert not state.active.any()
    assert quiet.finished_at.iloc[0] == absence.observed_at.iloc[0]
    features, state, resumed = production.forecast_inputs(
        records.iloc[:0], observations, t, context=context)
    assert len(resumed) == 2
    assert state.active_since.iloc[1] == restarted.started_at.iloc[0]
    assert features.loc[features.raion_id.eq("b"), "f_starts_1h"].iloc[0] == 2


def test_persistent_threshold_is_strict_and_broad_origins_are_retained() -> None:
    t = pd.Timestamp("2026-10-12T18:00:00Z")
    records, observed, _ = _inputs(t)
    observed = observed.iloc[:1].copy()
    observed["started_at"] = t - pd.Timedelta(hours=ingest.PERSISTENT_LOCAL_ALERT_H)
    observed["level"] = "hromada"
    assert len(production.as_of_records(records.iloc[:0], observed, t)) == 1
    assert production.as_of_records(records.iloc[:0], observed, t + pd.Timedelta(
        microseconds=1)).empty
    observed["started_at"] = t - pd.Timedelta(days=210)
    for level in ("raion", "oblast"):
        observed["level"] = level
        assert len(production.as_of_records(records.iloc[:0], observed, t)) == 1


def test_reconciliation_supersedes_old_receipts_and_empty_poll_receipt_closes_stale_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = pd.Timestamp("2026-10-12T18:00:00Z")
    records, observed, context = _inputs(t)
    boundary = t - pd.Timedelta(hours=3)
    monkeypatch.setattr(config, "V2_LIVE_OBSERVATIONS_DIR", tmp_path)
    monkeypatch.setattr(production, "load_snapshot", lambda: Snapshot(
        boundary, "test", context.regime_switch))
    old = observed.iloc[:1].copy()
    old["observed_at"] = boundary - pd.Timedelta(seconds=1)
    old.to_parquet(tmp_path / "old.parquet", index=False)
    assert production.load_observations(t).empty
    live = observed.iloc[:1].copy()
    live.to_parquet(tmp_path / "live.parquet", index=False)
    ended = live.iloc[:0]
    ended.to_parquet(tmp_path / "empty.parquet", index=False)
    receipt = t - pd.Timedelta(seconds=30)
    (tmp_path / "empty.json").write_text(json.dumps({"observed_at": receipt.isoformat()}))
    loaded = production.load_observations(t)
    assert len(loaded) == 2
    view = production.as_of_records(records.iloc[:0], loaded, t)
    assert view.finished_at.iloc[0] == receipt
    # Exact-hour receipts are unavailable, including a completely empty poll.
    (tmp_path / "empty.json").write_text(json.dumps({"observed_at": t.isoformat()}))
    view = production.as_of_records(records.iloc[:0], production.load_observations(t), t)
    assert view.finished_at.isna().all()


def test_censored_archive_community_cannot_extend_current_live_since() -> None:
    """The real archive can retain an unresolved persistent local continuation."""
    t = pd.Timestamp("2026-10-12T19:00:00Z")
    records, observed, context = _inputs(t)
    archive = records.iloc[:1].copy()
    archive["started_at"] = t - pd.Timedelta(days=25)
    archive["finished_at"] = pd.Series(pd.NaT, index=archive.index, dtype="datetime64[us, UTC]")
    archive["level"] = "hromada"
    archive["source"] = "aiu_web"
    live = observed.iloc[:1].copy()
    live["raion_id"] = "a"
    live["level"] = "hromada"
    live["started_at"] = t - pd.Timedelta(hours=34)
    _, state, view = production.forecast_inputs(archive, live, t, context=context)
    assert state.active.iloc[0]
    assert state.active_since.iloc[0] == live.started_at.iloc[0]
    assert not view.source.eq("aiu_web").any()
    assert archive.finished_at.isna().all()


def test_policy_repair_preserves_first_issuance_and_replay_uses_serving_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airalert.precompute import Paths, _issued_frames

    t = pd.Timestamp("2026-10-12T19:00:00Z")
    records, observed, _ = _install(tmp_path, monkeypatch, t)
    first = production.forecast(t.to_pydatetime())
    original_path = next(config.V2_FORECASTS_DIR.glob("*.parquet"))
    # Simulate a pre-policy issuance, with its original probabilities and clock.
    legacy = pd.read_parquet(original_path).drop(columns="live_input_policy")
    legacy.to_parquet(original_path, index=False)
    original_bytes = original_path.read_bytes()
    monkeypatch.setattr(production, "load_records", lambda: records.iloc[:0])
    monkeypatch.setattr(production, "load_observations", lambda time: observed.iloc[:0])
    repaired = production.forecast((t + pd.Timedelta(minutes=10)).to_pydatetime())
    assert first.active.any() and not repaired.active.any()
    assert original_path.read_bytes() == original_bytes
    repair_path = original_path.parent / "corrections" / production.LIVE_INPUT_POLICY / (
        original_path.name)
    repair_bytes = repair_path.read_bytes()
    monkeypatch.setattr(production, "load_records", lambda: records)
    repeated = production.forecast((t + pd.Timedelta(minutes=20)).to_pydatetime())
    pd.testing.assert_frame_equal(repeated, repaired)
    assert repair_path.read_bytes() == repair_bytes
    assert original_path.read_bytes() == original_bytes
    frames = _issued_frames(Paths(issued=config.V2_FORECASTS_DIR))
    assert len(frames) == 1
    pd.testing.assert_frame_equal(frames[0], repaired)
