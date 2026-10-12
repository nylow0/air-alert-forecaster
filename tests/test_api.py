"""Exercise synthetic precompute through the real API startup and HTTP contracts."""

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from airalert import api, config
from airalert.api import create_app
from airalert.backtest import assign_split
from airalert.data import merge_episodes
from airalert.precompute import (
    Paths,
    active_state,
    forecast_times,
    run,
    select_models,
    validate_forecasts,
)
from airalert.schemas import (
    ForecastResponse,
    HistoryResponse,
    LiveState,
    Meta,
    NewsFeed,
    Report,
    SystemStatus,
)

RAION = "kharkivska/kharkivskyi"
OTHER = "kyiv-city/kyiv-city"
OPTIONAL_MODULE = api._optional_module


@pytest.fixture(autouse=True)
def offline_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contract tests neither run network workers nor load optional local settings/auth."""
    monkeypatch.setenv("AIRALERT_DISABLE_SCHEDULER", "1")
    monkeypatch.setattr(api, "_optional_module", lambda name: None)


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Paths, pd.Timestamp]:
    """Two raions, a merge gap, an exact-hour start, a censored alert, and an outage."""
    clock = pd.Timestamp(config.DATA_GAPS[0][0]).floor("D").as_unit("us")
    monkeypatch.setattr(config, "TRAIN_START", (clock - pd.Timedelta(days=1)).to_pydatetime())
    monkeypatch.setattr(config, "VAL_START", clock.to_pydatetime())
    monkeypatch.setattr(config, "TEST_START", (clock + pd.Timedelta(hours=1)).to_pydatetime())
    snapshot = pd.Timestamp(config.DATA_GAPS[0][1]) + pd.Timedelta(
        hours=config.TEST_END_OFFSET_H + 2,
    )
    snapshot = snapshot.as_unit("us")
    paths = Paths(
        records=tmp_path / "records.parquet", episodes=tmp_path / "episodes.parquet",
        raions=tmp_path / "raions.csv", snapshot=tmp_path / "snapshot.json",
        preds=tmp_path / "preds", features=tmp_path / "features",
        selection=tmp_path / "selection.json", forecasts=tmp_path / "forecasts.parquet",
        geojson=tmp_path / "raions.geojson", metrics=tmp_path / "metrics.json",
        live=tmp_path / "live",
    )
    paths.preds.mkdir()
    paths.features.mkdir()
    paths.snapshot.write_text(json.dumps({
        "snapshot": snapshot.isoformat(), "csv_sha256": "synthetic",
        "regime_switch": clock.date().isoformat(),
    }), encoding="utf-8")
    pd.DataFrame({
        "raion_id": [RAION, OTHER], "oblast_id": ["kharkivska", "kyiv-city"],
        "oblast_name": ["Kharkivska", "Kyiv City"], "raion_name": ["Kharkivskyi", "Kyiv City"],
    }).to_csv(paths.raions, index=False)
    records = pd.DataFrame([
        (RAION, "kharkivska", "raion", clock + pd.Timedelta(minutes=5),
         clock + pd.Timedelta(minutes=58)),
        (RAION, "kharkivska", "raion", clock + pd.Timedelta(minutes=62),
         clock + pd.Timedelta(minutes=90)),
        (RAION, "kharkivska", "hromada", clock + pd.Timedelta(minutes=91),
         clock + pd.Timedelta(hours=3)),
        (OTHER, "kyiv-city", "oblast", clock + pd.Timedelta(hours=2),
         clock + pd.Timedelta(minutes=165)),
        (OTHER, "kyiv-city", "oblast", clock + pd.Timedelta(hours=4), pd.NaT),
    ], columns=["raion_id", "oblast_id", "level", "started_at", "finished_at"])
    for column in ("started_at", "finished_at"):
        records[column] = pd.to_datetime(records[column], utc=True).astype("datetime64[us, UTC]")
    records.to_parquet(paths.records, index=False)
    episodes = merge_episodes(records)
    episodes.to_parquet(paths.episodes, index=False)
    predictions = pd.MultiIndex.from_product(
        [forecast_times(snapshot), [RAION, OTHER]], names=["t", "raion_id"],
    ).to_frame(index=False)
    predictions["split"] = assign_split(predictions.t, snapshot=snapshot)
    predictions["p_1"] = np.where(predictions.raion_id.eq(RAION), -0.2, 0.9)
    predictions["p_3"] = np.where(predictions.raion_id.eq(RAION), 0.8, 1.2)
    predictions["p_12"] = 0.4
    predictions.to_parquet(paths.preds / "onset_B0.parquet", index=False)
    durations = episodes[["raion_id", "start"]].copy()
    durations["split"] = "val"
    durations[["q10_min", "q50_min", "q90_min"]] = [60.0, 120.0, 240.0]
    durations.to_parquet(paths.preds / "duration_D0.parquet", index=False)
    paths.geojson.write_text(json.dumps({"type": "FeatureCollection", "features": []}),
                             encoding="utf-8")
    return paths, clock


def test_precompute_to_api_contract(inputs: tuple[Paths, pd.Timestamp]) -> None:
    paths, clock = inputs
    run(paths)
    schema = pq.read_schema(paths.forecasts)
    assert all(str(schema.field(column).type) == "timestamp[us, tz=UTC]"
               for column in ("t", "active_since", "end_p10", "end_p50", "end_p90"))
    with TestClient(create_app(paths)) as client:
        meta = Meta.model_validate(client.get("/api/meta").json())
        assert meta.raion_count == 2
        assert meta.onset_model == "B0" and meta.duration_model == "D0"
        assert [(gap.start, gap.end) for gap in meta.data_gaps] == list(config.DATA_GAPS)
        assert client.get("/api/meta").json()["snapshot"].endswith("Z")
        latest = ForecastResponse.model_validate(client.get("/api/forecast").json())
        assert latest.mode == "latest" and latest.at == meta.latest_at
        assert all(row.actual_onset_1 is None and row.actual_onset_3 is None
                   and row.actual_onset_12 is None for row in latest.rows)
        for when in forecast_times(pd.Timestamp(meta.snapshot)):
            response = client.get("/api/forecast", params={"at": when.isoformat()})
            assert response.status_code == 200, response.text
            forecast = ForecastResponse.model_validate(response.json())
            assert {row.raion_id for row in forecast.rows} == {RAION, OTHER}
            assert len(forecast.rows) == 2
            for row in forecast.rows:
                assert 0 <= row.p_1 <= row.p_3 <= row.p_12 <= 1
                if row.active:
                    assert row.active_since is not None and row.active_since < forecast.at
                    assert row.end_p10 is not None and row.end_p50 is not None
                    assert row.end_p90 is not None
                    assert row.active_since <= row.end_p10 <= row.end_p50 <= row.end_p90
                else:
                    assert row.active_since is None
                    assert row.end_p10 is None and row.end_p50 is None and row.end_p90 is None
        gap = client.get("/api/forecast", params={"at": config.DATA_GAPS[0][0].isoformat()}).json()
        assert all(row[f"actual_onset_{h}"] is None for row in gap["rows"]
                   for h in config.HORIZONS)
        # A window ending exactly at the outage start is also unknown.
        boundary = client.get("/api/forecast", params={"at": (clock + pd.Timedelta(hours=5))
                                                     .isoformat()}).json()
        assert all(row["actual_onset_1"] is None for row in boundary["rows"])
        history = client.get(f"/api/raion/{RAION}/history", params={
            "from": clock.isoformat(), "to": (clock + pd.Timedelta(hours=3)).isoformat(),
        })
        assert history.status_code == 200, history.text
        parsed = HistoryResponse.model_validate(history.json())
        assert parsed.raion_id == RAION and len(parsed.series) == 4
        assert len(parsed.episodes) == 1
        assert parsed.episodes[0].start == clock + pd.Timedelta(minutes=5)
        assert client.get("/api/raions").json() == {"type": "FeatureCollection", "features": []}
        missing = client.get("/api/report")
        assert missing.status_code == 404 and "not available yet" in missing.json()["detail"]
        report = Report(generated_at=meta.snapshot, notes=["synthetic report"])
        paths.metrics.write_text(report.model_dump_json(), encoding="utf-8")
        assert client.get("/api/report").json() == report.model_dump(mode="json")


def test_activity_uses_asof_records(inputs: tuple[Paths, pd.Timestamp]) -> None:
    paths, clock = inputs
    records = pd.read_parquet(paths.records)
    for hour in range(5):
        when = clock + pd.Timedelta(hours=hour)
        rows = pd.DataFrame({"t": [when, when], "raion_id": [RAION, OTHER]})
        asof = records.loc[records.started_at.lt(when)].copy()
        asof.loc[asof.finished_at.gt(when), "finished_at"] = pd.NaT
        pd.testing.assert_frame_equal(active_state(records, rows), active_state(asof, rows))
    run(paths)
    forecasts = pd.read_parquet(paths.forecasts).set_index(["t", "raion_id"])
    # The full episode covers 01:00, but the as-of source coverage does not.
    assert not forecasts.loc[(clock + pd.Timedelta(hours=1), RAION), "active"]
    assert forecasts.loc[(clock + pd.Timedelta(hours=2), RAION), "active_since"] == (
        clock + pd.Timedelta(minutes=5)
    )
    assert not forecasts.loc[(clock + pd.Timedelta(hours=2), OTHER), "active"]
    assert not forecasts.loc[(clock + pd.Timedelta(hours=3), RAION), "active"]
    assert forecasts.loc[(clock + pd.Timedelta(hours=5), OTHER), "active"]
    assert forecasts.loc[(clock, RAION), "actual_onset_1"]
    assert not forecasts.loc[(clock + pd.Timedelta(hours=1), RAION), "actual_onset_1"]
    assert forecasts.loc[(clock + pd.Timedelta(hours=1), OTHER), "actual_onset_1"]


def test_feature_labels_and_selected_models(inputs: tuple[Paths, pd.Timestamp]) -> None:
    paths, clock = inputs
    predictions = pd.read_parquet(paths.preds / "onset_B0.parquet")
    predictions.to_parquet(paths.preds / "onset_M1.parquet", index=False)
    assert select_models(paths).onset == "M1"
    for split in ("val", "test"):
        features = predictions.loc[predictions.split.eq(split), ["t", "raion_id"]].copy()
        for horizon in config.HORIZONS:
            features[f"y_{horizon}"] = pd.array(np.zeros(len(features)), dtype="Int8")
        features.to_parquet(paths.features / f"{split}.parquet", index=False)
    paths.selection.write_text(Report(generated_at=None, selection={
        "onset": "B0", "duration": None, "note": "selected on validation",
    }).model_dump_json(), encoding="utf-8")
    run(paths)
    rows = pd.read_parquet(paths.forecasts)
    assert not rows.loc[rows.t.eq(clock), "actual_onset_1"].any()
    assert rows[["end_p10", "end_p50", "end_p90"]].isna().all().all()
    # Changing selection after publication must not misreport the stored forecasts.
    paths.selection.write_text(Report(generated_at=None, selection={
        "onset": "M1", "duration": "D0", "note": None,
    }).model_dump_json(), encoding="utf-8")
    with TestClient(create_app(paths)) as client:
        assert client.get("/api/meta").json()["onset_model"] == "B0"
        assert client.get("/api/forecast").json()["duration_model"] is None


def test_request_validation_and_startup(inputs: tuple[Paths, pd.Timestamp]) -> None:
    paths, clock = inputs
    run(paths)
    with TestClient(create_app(paths)) as client:
        for at, status in ((clock - pd.Timedelta(hours=1), 404),
                           (clock + pd.Timedelta(minutes=1), 422),
                           (clock.tz_localize(None), 422)):
            assert client.get("/api/forecast", params={"at": at.isoformat()}).status_code == status
        assert client.get("/api/forecast", params={"at": "invalid"}).status_code == 422
        assert client.get("/api/forecast", params={
            "at": clock.tz_convert("Europe/Kyiv").isoformat(),
        }).json()["at"] == clock.isoformat().replace("+00:00", "Z")
        for start, end in ((clock, clock), (clock, clock + pd.Timedelta(days=32)),
                           (clock - pd.Timedelta(hours=1), clock + pd.Timedelta(hours=1))):
            assert client.get(f"/api/raion/{RAION}/history", params={
                "from": start.isoformat(), "to": end.isoformat(),
            }).status_code == 422
        assert client.get("/api/raion/unknown/id/history", params={
            "from": clock.isoformat(), "to": (clock + pd.Timedelta(hours=1)).isoformat(),
        }).status_code == 404
    with (
        pytest.raises(FileNotFoundError, match="Required serving artifact missing"),
        TestClient(create_app(replace(paths, episodes=paths.episodes.with_name("missing")))),
    ):
        pass
    rows = pd.read_parquet(paths.forecasts)
    rows = pd.concat([rows, rows.iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate forecast"):
        validate_forecasts(rows, pd.read_csv(paths.raions), pd.Timestamp(
            json.loads(paths.snapshot.read_text(encoding="utf-8"))["snapshot"],
        ))


def test_incomplete_prediction_inputs_fail(inputs: tuple[Paths, pd.Timestamp]) -> None:
    paths, _ = inputs
    predictions = pd.read_parquet(paths.preds / "onset_B0.parquet")
    predictions.iloc[1:].to_parquet(paths.preds / "onset_B0.parquet", index=False)
    with pytest.raises(ValueError, match="every universe raion exactly once"):
        run(paths)
    predictions.to_parquet(paths.preds / "onset_B0.parquet", index=False)
    durations = pd.read_parquet(paths.preds / "duration_D0.parquet")
    durations["q90_min"] = 1.0
    durations.to_parquet(paths.preds / "duration_D0.parquet", index=False)
    with pytest.raises(ValueError, match="quantiles must be ordered"):
        run(paths)


def test_live_display_and_missing_optional_modules(inputs: tuple[Paths, pd.Timestamp]) -> None:
    paths, _ = inputs
    run(paths)
    paths = replace(paths, geojson=config.RAIONS_GEOJSON_PATH)
    with TestClient(create_app(paths)) as client:
        live = LiveState.model_validate(client.get("/api/live").json())
        assert len(live.units) == 139 and live.stale
        geo = client.get("/api/raions").json()
        keys = {feature["properties"]["display_key"] for feature in geo["features"]}
        assert len(keys) == 139 and keys == {unit.display_key for unit in live.units}
        assert {unit.display_key: unit.name for unit in live.units} == {
            feature["properties"]["display_key"]: feature["properties"]["name"]
            for feature in geo["features"]
        }
        system = SystemStatus.model_validate(client.get("/api/system").json())
        assert system.production.onset == "B0" and system.production.trained_at is None
        feed = NewsFeed.model_validate(client.get("/api/news").json())
        assert feed.status.state == "off" and feed.status.message and not feed.adjustments
        assert client.get("/api/forecast").status_code == 200


def test_forecast_uses_cached_hour(inputs: tuple[Paths, pd.Timestamp]) -> None:
    paths, _ = inputs
    run(paths)
    with TestClient(create_app(paths)) as client:
        scheduler = client.app.state.scheduler
        cached = scheduler.latest.model_copy(update={
            "at": scheduler.latest.at + pd.Timedelta(days=2), "onset_model": "production",
        })
        scheduler.latest = cached
        forecast = ForecastResponse.model_validate(client.get("/api/forecast").json())
        meta = Meta.model_validate(client.get("/api/meta").json())
        assert forecast == cached and forecast.mode == "latest"
        assert meta.latest_at == cached.at and meta.onset_model == "production"
        response = client.get(f"/api/raion/{RAION}/history", params={
            "from": (cached.at - pd.Timedelta(days=7)).isoformat(), "to": cached.at.isoformat(),
        })
        assert response.status_code == 200, response.text
        history = HistoryResponse.model_validate(response.json())
        assert history.series[-1].t == cached.at


def test_new_snapshot_keeps_honest_stored_fallback(inputs: tuple[Paths, pd.Timestamp]) -> None:
    paths, _ = inputs
    run(paths)
    original = json.loads(paths.snapshot.read_text(encoding="utf-8"))
    old_hour = pd.Timestamp(original["snapshot"])
    original["snapshot"] = (old_hour + pd.Timedelta(days=2)).isoformat()
    paths.snapshot.write_text(json.dumps(original), encoding="utf-8")
    with TestClient(create_app(paths)) as client:
        meta = Meta.model_validate(client.get("/api/meta").json())
        forecast = ForecastResponse.model_validate(client.get("/api/forecast").json())
        assert meta.snapshot > meta.latest_at == forecast.at == old_hour
        system = SystemStatus.model_validate(client.get("/api/system").json())
        assert system.data_through == meta.snapshot
        assert system.production.data_through == forecast.at


@pytest.mark.parametrize("worker_fails", [False, True])
def test_optional_routers_and_worker_lifecycle(
    inputs: tuple[Paths, pd.Timestamp], monkeypatch: pytest.MonkeyPatch, worker_fails: bool,
) -> None:
    paths, _ = inputs
    run(paths)
    news_router, news_worker, production = (
        ModuleType("news.router"), ModuleType("news.worker"), ModuleType("production"),
    )
    router = APIRouter()
    fixture = NewsFeed.model_validate_json((config.FIXTURES_DIR / "news_off.json").read_text())

    @router.get("/api/news")
    def news() -> NewsFeed:
        return fixture

    @router.get("/api/settings")
    def settings() -> dict[str, bool]:
        return {"included": True}

    news_router.router = router
    calls: list[str] = []

    async def start(provider: Callable[[], ForecastResponse | None]) -> None:
        calls.append("start")
        assert provider() is not None
        if worker_fails:
            raise ImportError("optional worker dependency unavailable")

    async def stop() -> None:
        calls.append("stop")

    news_worker.start, news_worker.stop = start, stop
    production.router = APIRouter()

    @production.router.post("/api/production/control")
    def control() -> dict[str, bool]:
        return {"ok": True}

    modules = {"airalert.news.router": news_router, "airalert.news.worker": news_worker,
               "airalert.production": production}
    monkeypatch.setattr(api, "_optional_module", modules.get)
    when = pd.Timestamp(json.loads(paths.snapshot.read_text())["snapshot"]).to_pydatetime()
    with TestClient(create_app(paths, clock=lambda: when), base_url="http://localhost",
                    client=("127.0.0.1", 12345)) as client:
        assert client.get("/api/settings").json() == {"included": True}
        feed = NewsFeed.model_validate(client.get("/api/news").json())
        assert feed.status.state == "off"
        if worker_fails:
            assert "could not start" in feed.status.message
        else:
            assert feed == fixture
        assert client.post("/api/production/control", headers={
            "origin": "http://localhost",
        }).status_code == 200
        assert client.post("/api/production/control", headers={
            "origin": "https://untrusted.example",
        }).status_code == 403
    assert calls == ["start", "stop"]


def test_broken_optional_imports_keep_server_available(
    inputs: tuple[Paths, pd.Timestamp], monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = inputs
    run(paths)
    original_import = api.importlib.import_module

    def import_module(name: str, package: str | None = None) -> ModuleType:
        if name in {"airalert.news.router", "airalert.production"}:
            raise RuntimeError("optional import failed")
        return original_import(name, package)

    monkeypatch.setattr(api.importlib, "import_module", import_module)
    monkeypatch.setattr(api, "_optional_module", OPTIONAL_MODULE)
    with TestClient(create_app(paths)) as client:
        assert client.get("/api/forecast").status_code == 200
        feed = NewsFeed.model_validate(client.get("/api/news").json())
        assert feed.status.state == "off" and feed.status.message
