"""Cross-module publication checks and opt-in probes of the real local server."""

import argparse
import asyncio
import json
import logging
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
import uvicorn

from airalert import config
from airalert.live import LiveCache
from airalert.scheduler import Scheduler
from airalert.schemas import (
    AuthStatus,
    ForecastResponse,
    HistoryResponse,
    LiveState,
    Meta,
    NewsFeed,
    ProductionModel,
    Report,
    SystemStatus,
)


def test_cold_start_fits_without_blocking_or_serving_a_snapshot(tmp_path: Path) -> None:
    """A current ledger cannot hide missing models; HTTP consumers get no dated fallback."""
    now = datetime(2026, 10, 12, 17, tzinfo=UTC)
    fallback = ForecastResponse(at=now - timedelta(days=1), mode="latest",
                                onset_model="v1", duration_model=None, rows=[])
    calls: list[str] = []
    ready = False

    class Production:
        def status(self) -> ProductionModel:
            return ProductionModel(onset="selected" if ready else "untrained", duration=None,
                                   trained_at=now if ready else None, data_through=None)

        def forecast(self, clock: datetime) -> pd.DataFrame:
            if not ready:
                raise FileNotFoundError("not fitted")
            return pd.DataFrame()

        def refit(self, clock: datetime) -> ProductionModel:
            nonlocal ready
            calls.append("refit")
            ready = True
            return self.status()

        def score_recent(self, clock: datetime) -> None:
            calls.append("score")

    async def scenario() -> None:
        scheduler = Scheduler(
            live=LiveCache({"features": []}, tmp_path / "state.json"), root=tmp_path,
            fallback=lambda: fallback, reload=lambda: calls.append("reload"), coverage=lambda: [],
            poll=lambda clock: {"polygons": [], "fetched_at": now.isoformat()},
            catch_up=lambda clock: calls.append("catch_up"),
            features=lambda: calls.append("features"), production=Production(), clock=lambda: now,
            refit_duration=lambda clock: calls.append("duration"),
            rebuild=lambda: calls.append("rebuild"),
        )
        scheduler.ledger = {"watermark": "2026-10-11"}

        async def refresh(clock: datetime) -> None:
            calls.append("forecast")

        scheduler._refresh = refresh
        assert scheduler.latest is None
        await scheduler.start()
        try:
            assert scheduler.model.onset == "fitting" and scheduler.base_forecast() is None
            job = next(task for task in scheduler._tasks if task.get_name() == "airalert-catch-up")
            await asyncio.wait_for(job, 5)
            steps = ["catch_up", "features", "refit", "duration", "score", "rebuild", "reload"]
            assert [call for call in calls if call != "forecast"] == steps
            assert scheduler.nightly["ok"] is True
        finally:
            await scheduler.stop()

    asyncio.run(scenario())


def test_complete_ledger_and_pipeline_do_not_repeat_on_restart(tmp_path: Path) -> None:
    now = datetime(2026, 10, 12, 17, tzinfo=UTC)
    (tmp_path / "ledger.json").write_text(json.dumps({"watermark": "2026-10-11"}))
    (tmp_path / "nightly.json").write_text(json.dumps({"ok": True}))
    base = ForecastResponse(at=now, mode="latest", onset_model="selected",
                            duration_model=None, rows=[])

    async def scenario() -> None:
        scheduler = Scheduler(
            live=LiveCache({"features": []}, tmp_path / "state.json"), root=tmp_path,
            fallback=lambda: base, reload=lambda: None, coverage=lambda: [],
            poll=lambda clock: {"polygons": [], "fetched_at": now.isoformat()},
            catch_up=lambda clock: None, features=lambda: None, clock=lambda: now,
        )
        await scheduler.start()
        try:
            assert all(task.get_name() != "airalert-catch-up" for task in scheduler._tasks)
        finally:
            await scheduler.stop()

    asyncio.run(scenario())


def test_duration_uses_onsets_live_closures_and_preserves_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent successful poll closes stale archive coverage in both model contexts."""
    from airalert import production
    from airalert.models import duration

    now = pd.Timestamp("2026-10-12T18:00:00Z")
    records = pd.DataFrame({"raion_id": ["a"], "oblast_id": ["x"], "level": ["raion"],
                            "started_at": [now - pd.Timedelta(days=1)],
                            "finished_at": pd.Series([pd.NaT], dtype="datetime64[us, UTC]"),
                            "source": ["aiu_web"]})
    observed = pd.DataFrame({
        "raion_id": ["b"], "oblast_id": ["x"], "level": ["raion"],
        "source": ["siren"], "hazard_type": ["air_raid"],
        "started_at": [now - pd.Timedelta(hours=2)],
        "finished_at": pd.Series([pd.NaT], dtype="datetime64[us, UTC]"),
        "observed_at": [now - pd.Timedelta(minutes=30)],
        "origin_uid": [1], "origin_event_id": [2],
    })
    monkeypatch.setattr(duration, "load_records", lambda: records)
    monkeypatch.setattr(production, "load_observations", lambda when: observed)
    view = duration._production_records(now).set_index("raion_id")
    assert view.loc["a", "finished_at"] == observed.observed_at.iloc[0]
    assert pd.isna(view.loc["a", "observed_at"])
    assert view.loc["b", "observed_at"] == observed.observed_at.iloc[0]
    assert view.loc["b", "observed_at"] > view.loc["b", "started_at"]


def probe_http(client: httpx.Client) -> dict[str, object]:
    """Validate real responses and compare replay/now probabilities with their producer files."""
    from airalert.precompute import active_state

    meta = Meta.model_validate(client.get("/api/meta").raise_for_status().json())
    system = SystemStatus.model_validate(client.get("/api/system").raise_for_status().json())
    current = ForecastResponse.model_validate(client.get("/api/forecast").raise_for_status().json())
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    assert current.at == meta.latest_at == hour and len(current.rows) == 119
    assert current.onset_model == "selected" and current.duration_model == "D2"
    issued = pd.read_parquet(config.V2_FORECASTS_DIR / f"{hour.strftime('%Y%m%dT%HZ')}.parquet")
    served = pd.DataFrame([row.model_dump() for row in current.rows]).set_index("raion_id")
    pd.testing.assert_frame_equal(served[["p_1", "p_3", "p_12"]].sort_index(),
                                  issued.set_index("raion_id")[["p_1", "p_3", "p_12"]]
                                  .sort_index(), check_dtype=False)
    assert meta.data_gaps == [] and meta.replay.start == config.V2_VAL_START
    selected = pd.read_parquet(config.V2_PREDS_DIR / "onset_selected.parquet")
    records = pd.read_parquet(config.RECORDS_PATH)
    checks = []
    for when in (pd.Timestamp(config.V2_VAL_START), pd.Timestamp(config.V2_TEST_START),
                 pd.Timestamp(config.V1_DATA_GAPS[0][0]), pd.Timestamp(meta.splits["test"].end)):
        response = client.get("/api/forecast", params={"at": when.isoformat()}).raise_for_status()
        replay = ForecastResponse.model_validate(response.json())
        frame = pd.DataFrame([row.model_dump() for row in replay.rows]).assign(t=when)
        expected = selected.loc[selected.t.eq(when)].set_index("raion_id")
        expected[["p_1", "p_3", "p_12"]] = np.maximum.accumulate(
            expected[["p_1", "p_3", "p_12"]].to_numpy(), axis=1)
        pd.testing.assert_frame_equal(frame.set_index("raion_id")[["p_1", "p_3", "p_12"]]
                                      .sort_index(), expected[["p_1", "p_3", "p_12"]]
                                      .sort_index(), check_dtype=False)
        prefix = records.loc[records.started_at.lt(when)].copy()
        prefix.loc[prefix.finished_at.gt(when), "finished_at"] = pd.NaT
        state = active_state(prefix, frame)
        assert frame.active.tolist() == state.active.tolist()
        assert frame.active_since.tolist() == state.active_since.tolist()
        active = frame.loc[frame.active]
        assert active[["end_p10", "end_p50", "end_p90"]].notna().all().all()
        assert active.end_p10.gt(when).all()
        split = "val" if when < config.V2_TEST_START else "test2"
        labels = pd.read_parquet(config.V2_FEATURES_DIR / f"{split}.parquet",
                                 filters=[("t", "==", when.to_pydatetime())])
        labels = labels.set_index("raion_id")
        for row in replay.rows:
            for horizon in config.HORIZONS:
                value = labels.loc[row.raion_id, f"y_{horizon}"]
                assert getattr(row, f"actual_onset_{horizon}") == (
                    None if pd.isna(value) else bool(value))
        checks.append({"at": replay.at.isoformat(), "rows": len(frame),
                       "active": len(active), "future_ordered_ends": True})
    raion = current.rows[0].raion_id
    histories = []
    for end in (meta.replay.start + timedelta(days=7), meta.splits["test"].end, current.at):
        start = max(meta.replay.start, end - timedelta(days=7))
        response = client.get(f"/api/raion/{raion}/history", params={
            "from": start.isoformat(), "to": end.isoformat()}).raise_for_status()
        history = HistoryResponse.model_validate(response.json())
        histories.append({"start": start.isoformat(), "end": end.isoformat(),
                          "points": len(history.series), "episodes": len(history.episodes)})
    report = Report.model_validate(client.get("/api/report").raise_for_status().json())
    v1 = client.get("/api/report", params={"version": "v1"}).raise_for_status().json()
    assert v1 == Report.model_validate_json(config.METRICS_PATH.read_text()).model_dump(mode="json")
    assert len(report.onset) == 156 and len(report.duration) == 92
    assert report.selection.onset == "selected" and report.selection.duration == "D2"
    assert any("Rolling live track record" in note for note in report.notes)
    live = LiveState.model_validate(client.get("/api/live").raise_for_status().json())
    assert len(live.units) == 139
    assert system.data_through == meta.snapshot and system.coverage_gaps == []
    auth = AuthStatus.model_validate(client.get("/api/auth/status").raise_for_status().json())
    return {"current": {"at": current.at.isoformat(), "rows": len(current.rows),
                         "active": sum(row.active for row in current.rows),
                         "onset": current.onset_model, "duration": current.duration_model},
            "replay_checks": checks, "history_checks": histories,
            "meta": meta.model_dump(mode="json"), "system": system.model_dump(mode="json"),
            "report": {"onset": len(report.onset), "duration": len(report.duration),
                       "v1_matches": True, "rolling_notes": True},
            "live": {"units": len(live.units), "fetched_at": live.fetched_at.isoformat(),
                     "stale": live.stale},
            "auth": {"codex_installed": auth.codex_installed, "logged_in": auth.logged_in,
                     "login_in_progress": auth.login_in_progress,
                     "available_models": len(auth.available_models)}}


def _news_counts() -> dict[str, object]:
    with sqlite3.connect(config.DATA_DIR / "news" / "news.sqlite") as connection:
        return {"runs": connection.execute("SELECT count(*) FROM runs").fetchone()[0],
                "messages": connection.execute("SELECT count(*) FROM messages").fetchone()[0],
                "last_poll": connection.execute(
                    "SELECT max(last_poll) FROM channels").fetchone()[0]}


def probe_news(client: httpx.Client) -> dict[str, object]:
    """One authorized real on-cycle; always restore off, including failure/timeout exits."""
    settings = client.get("/api/settings").raise_for_status().json()
    settings["news_enabled"] = False
    client.put("/api/settings", json=settings).raise_for_status()
    time.sleep(2)
    before = _news_counts()
    time.sleep(65)
    assert _news_counts() == before
    off = client.get("/api/news").raise_for_status().json()
    assert off["status"]["state"] == "off"
    started = time.monotonic()
    transitions = []
    result: dict[str, object] = {"off_counts": before, "off_65s_unchanged": True}
    try:
        settings["news_enabled"] = True
        response = client.put("/api/settings", json=settings).raise_for_status()
        assert response.json()["news_enabled"]
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            feed = NewsFeed.model_validate(client.get("/api/news").raise_for_status().json())
            state = feed.status.state
            if not transitions or transitions[-1]["state"] != state:
                transitions.append({"state": state, "seconds": round(time.monotonic()-started, 2)})
                print(f"news {state}: events={len(feed.events)} "
                      f"adjustments={len(feed.adjustments)}",
                      flush=True)
            if feed.status.last_extraction_at and (
                feed.status.last_evaluation_at or not feed.events):
                result["feed"] = feed.model_dump(mode="json")
                break
            if state in {"needs_codex", "needs_login", "quota_limited"}:
                raise RuntimeError(f"News cannot run: {state}")
            time.sleep(5)
        else:
            result["feed"] = feed.model_dump(mode="json")
            result["timeout"] = True
        result["transitions"] = transitions
        result["on_counts"] = _news_counts()
    finally:
        settings["news_enabled"] = False
        client.put("/api/settings", json=settings).raise_for_status()
        time.sleep(3)
        after = _news_counts()
        time.sleep(65)
        assert _news_counts() == after
        assert client.get("/api/news").json()["status"]["state"] == "off"
        result["off_again_65s_unchanged"] = True
    return result


def serve_startup_probe(root: Path, *, cold: bool) -> None:
    """Isolate the ledger/cache and optionally model outputs from the real service."""
    from airalert import api, features, ingest, production
    from airalert.models import duration
    from airalert.precompute import Paths

    source = config.DATA_DIR / "live"
    root.mkdir(parents=True, exist_ok=True)
    ledger = json.loads((source / "ledger.json").read_text())
    if not cold:
        ledger["watermark"] = (datetime.now(UTC).date() - timedelta(days=2)).isoformat()
    (root / "ledger.json").write_text(json.dumps(ledger))
    shutil.copy2(config.FORECASTS_PATH, root / "forecasts.parquet")
    shutil.copy2(source / "state.json", root / "state.json")
    original_optional = api._optional_module
    api._optional_module = lambda name: None if name.startswith("airalert.news") \
        else original_optional(name)

    def catch_up(now: datetime, *, root: Path) -> dict[str, object]:
        copied = json.loads((root / "ledger.json").read_text())
        copied["watermark"] = ledger["watermark"] if cold else json.loads(
            (source / "ledger.json").read_text())["watermark"]
        (root / "ledger.json").write_text(json.dumps(copied))
        return {"simulation": "temporary ledger only", "watermark": copied["watermark"]}

    ingest.catch_up = catch_up
    # These probes verify startup decisions, not another archive/feature publication.
    features.build_v2 = lambda: None
    if cold:
        config.V2_PRODUCTION_DIR = root / "production"
        config.V2_FORECASTS_DIR = root / "issued"
        duration.PRODUCTION_DIR = config.V2_PRODUCTION_DIR / "duration"
    else:
        production.refit = lambda now: production.status()
        duration.refit_production = lambda now: None
        production.score_recent = lambda now: {"simulation": "scoring retained"}
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    paths = Paths(live=root, forecasts=root / "forecasts.parquet",
                  issued=config.V2_FORECASTS_DIR)
    uvicorn.run(api.create_app(paths), host="127.0.0.1", port=8001, access_log=False)


def probe_startup() -> dict[str, object]:
    """Read running HTTP servers with a copied stale ledger and genuinely absent models."""
    directory = config.DATA_DIR / "live" / "x1"
    ledger_path = config.DATA_DIR / "live" / "ledger.json"
    original = ledger_path.read_bytes()
    results = {}
    for cold in (False, True):
        name = "cold" if cold else "stale"
        root = directory / f"{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
        root.mkdir(parents=True)
        command = [sys.executable, str(Path(__file__).resolve()), "startup-server",
                   "--root", str(root)]
        if cold:
            command.append("--cold")
        began = time.monotonic()
        with (root / "server.log").open("w", encoding="utf-8") as log:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                initial = None
                with httpx.Client(base_url="http://127.0.0.1:8001", timeout=10) as client:
                    deadline = time.monotonic() + 420
                    while time.monotonic() < deadline:
                        assert child.poll() is None, "Isolated server exited; inspect its local log"
                        try:
                            status = SystemStatus.model_validate(client.get("/api/system")
                                                                 .raise_for_status().json())
                            if initial is None:
                                initial = status.model_dump(mode="json")
                                latency = time.monotonic() - began
                                print(f"startup {name}: responsive after {latency:.2f}s; "
                                      f"model={status.production.onset}", flush=True)
                            if status.last_nightly_ok is not None:
                                assert status.last_nightly_ok is True
                                break
                        except httpx.ConnectError:
                            pass
                        time.sleep(1)
                    else:
                        raise TimeoutError("Isolated startup pipeline did not finish")
                    nightly = json.loads((root / "nightly.json").read_text())
                    assert nightly["reason"] == "startup"
                    if cold:
                        assert initial["production"]["onset"] == "fitting"
                        forecast = ForecastResponse.model_validate(client.get("/api/forecast")
                                                                   .raise_for_status().json())
                        assert forecast.at == datetime.now(UTC).replace(
                            minute=0, second=0, microsecond=0)
                        assert forecast.duration_model == "D2" and len(forecast.rows) == 119
                    results[name] = {"initial": initial, "ready_seconds": round(latency, 3),
                                     "completed_seconds": round(time.monotonic() - began, 3),
                                     "nightly": nightly, "final": status.model_dump(mode="json")}
                    print(f"startup {name}: pipeline ok; real_refit={cold}", flush=True)
            finally:
                child.terminate()
                child.wait(timeout=15)
        assert ledger_path.read_bytes() == original
    results["canonical_ledger_unchanged"] = True
    return results


def probe_lifecycle(client: httpx.Client) -> dict[str, object]:
    """Observe successive real polls and the next real hourly publication, with bounded waits."""
    from airalert import production
    from airalert.scheduler import next_midnight

    first = client.get("/api/forecast").raise_for_status().json()
    before = client.get("/api/system").raise_for_status().json()
    nightly = json.loads((config.DATA_DIR / "live" / "nightly.json").read_text())
    assert before["last_nightly_at"] == pd.Timestamp(nightly["started"]).isoformat().replace(
        "+00:00", "Z")
    assert before["production"]["trained_at"] == nightly["steps"]["refit"]["result"]["trained_at"]
    samples = []
    for _ in range(2):
        live = LiveState.model_validate(client.get("/api/live").raise_for_status().json())
        status = SystemStatus.model_validate(client.get("/api/system").raise_for_status().json())
        assert len(live.units) == 139 and not live.stale and not status.live_stale
        assert status.live_fetched_at == live.fetched_at
        assert abs((datetime.now(UTC) - status.now).total_seconds()) < 5
        assert status.next_nightly_at == next_midnight(status.now)
        assert status.production == production.status()
        assert status.last_nightly_ok is True and status.coverage_gaps == []
        samples.append({"fetched_at": live.fetched_at.isoformat(), "units": len(live.units),
                        "red": sum(u.red_since is not None for u in live.units),
                        "yellow": sum(u.yellow_since is not None for u in live.units)})
        if len(samples) == 1:
            time.sleep(65)
    assert samples[1]["fetched_at"] > samples[0]["fetched_at"]
    print(f"successive real polls: {samples}", flush=True)
    target = pd.Timestamp(first["at"]) + pd.Timedelta(hours=1)
    deadline = target.to_pydatetime() + timedelta(minutes=2)
    while datetime.now(UTC) < deadline:
        response = client.get("/api/forecast")
        if response.status_code == 200 and pd.Timestamp(response.json()["at"]) == target:
            forecast = ForecastResponse.model_validate(response.json())
            meta = Meta.model_validate(client.get("/api/meta").raise_for_status().json())
            assert meta.replay.end == meta.latest_at == forecast.at
            frame = pd.read_parquet(config.FORECASTS_PATH, filters=[("t", "==", forecast.at)])
            assert len(frame) == len(forecast.rows) == 119
            assert all(row.end_p10 > forecast.at for row in forecast.rows if row.active)
            result = {"restart_reused_training": True, "nightly_started": before["last_nightly_at"],
                      "trained_at": before["production"]["trained_at"], "live_samples": samples,
                      "next_hour": {"at": forecast.at.isoformat(), "stored_rows": len(frame),
                                    "replay_end": meta.replay.end.isoformat(),
                                    "active": sum(row.active for row in forecast.rows)}}
            print(f"real hourly publication: {result['next_hour']}", flush=True)
            return result
        time.sleep(2)
    raise TimeoutError("Next real hourly forecast was not published within two minutes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", choices=["http", "news", "startup", "startup-server", "lifecycle"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--cold", action="store_true")
    args = parser.parse_args()
    if args.probe == "startup-server":
        serve_startup_probe(args.root, cold=args.cold)
        raise SystemExit(0)
    if args.probe == "startup":
        evidence = probe_startup()
    else:
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=45) as client:
            if args.probe == "http":
                evidence = probe_http(client)
            elif args.probe == "lifecycle":
                evidence = probe_lifecycle(client)
            else:
                evidence = probe_news(client)
    directory = config.DATA_DIR / "live" / "x1"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{args.probe}.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    if args.probe == "http":
        print(json.dumps({key: evidence[key] for key in (
            "current", "replay_checks", "history_checks", "report", "live", "auth")}), flush=True)
    elif args.probe == "startup":
        print(json.dumps({name: {key: evidence[name][key] for key in (
            "ready_seconds", "completed_seconds")} for name in ("stale", "cold")}), flush=True)
    else:
        print(json.dumps({key: value for key, value in evidence.items() if key != "feed"}),
              flush=True)
