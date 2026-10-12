"""Test freshness, calendar scheduling, failure recovery and serialized publication offline."""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from airalert.live import LiveCache
from airalert.scheduler import Scheduler, next_midnight, worker_call
from airalert.schemas import ForecastResponse, ForecastRow, ProductionModel

NOW = datetime(2026, 10, 24, 20, 59, 59, tzinfo=UTC)
KEY = "shape-1"


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def raw_state(now: datetime) -> dict[str, object]:
    return {
        "fetched_at": now.isoformat(), "stale": False, "sources": {"siren": "observed"},
        "polygons": [{
            "display_key": KEY, "red": {"active": True, "since": NOW.isoformat()},
            "yellow": {"active": True, "since": NOW.isoformat()},
            "coverage": "inherited", "other_hazards": [{"type": "artillery"}],
        }],
    }


def base_forecast() -> ForecastResponse:
    return ForecastResponse(
        at=NOW.replace(minute=0, second=0), mode="latest", onset_model="B0", duration_model=None,
        rows=[ForecastRow(
            raion_id="one", p_1=0.1, p_3=0.2, p_12=0.5, active=False,
            active_since=None, end_p10=None, end_p50=None, end_p90=None,
            actual_onset_1=None, actual_onset_3=None, actual_onset_12=None,
        )],
    )


@pytest.fixture
def scheduler(tmp_path: Path) -> Scheduler:
    geo = {"features": [{"properties": {
        "display_key": KEY, "raion_id": "one", "oblast_id": "oblast", "name": "From GeoJSON",
    }}]}
    return Scheduler(
        live=LiveCache(geo, tmp_path / "state.json"), root=tmp_path,
        fallback=base_forecast, reload=lambda: None, coverage=lambda: [],
        poll=raw_state, catch_up=lambda now: None, features=lambda: None,
        clock=Clock(), jitter=lambda: 0,
    )


@pytest.mark.parametrize("failure", ["exception", "stale_return"])
def test_failed_poll_retains_state_and_recovers(scheduler: Scheduler, failure: str) -> None:
    async def scenario() -> None:
        await scheduler.poll_once()
        successful = scheduler.live.get(scheduler.clock())
        assert successful.units[0].name == "From GeoJSON"
        assert successful.units[0].red_since and successful.units[0].yellow_since
        assert successful.units[0].other_hazards == ["artillery"]

        def failing(now: datetime) -> dict[str, object]:
            if failure == "exception":
                raise ConnectionError("feed unavailable")
            value = raw_state(now)
            value["stale"] = True
            value["polygons"][0]["red"] = None
            return value

        scheduler.poll = failing
        scheduler.clock.now += timedelta(seconds=60)
        await scheduler.poll_once()
        failed = scheduler.live.get(scheduler.clock())
        assert failed.stale and failed.units == successful.units
        assert failed.fetched_at == successful.fetched_at
        scheduler.poll = raw_state
        await scheduler.poll_once()
        recovered = scheduler.live.get(scheduler.clock())
        assert not recovered.stale and recovered.fetched_at > successful.fetched_at
        assert not scheduler.live.get(scheduler.clock() + timedelta(minutes=3)).stale
        assert scheduler.live.get(scheduler.clock() + timedelta(minutes=3, microseconds=1)).stale

    asyncio.run(scenario())


def test_secondary_failure_retains_alert_in_api_state_and_recovers(scheduler: Scheduler) -> None:
    """One API stale flag means incomplete secondary polls keep the whole snapshot."""
    async def scenario() -> None:
        await scheduler.poll_once()
        before = scheduler.live.get(scheduler.clock())

        def secondary_failed(now: datetime) -> dict[str, object]:
            value = raw_state(before.fetched_at)
            value.update({"stale": True, "sources": {"siren": "observed", "ubilling": "failed"}})
            return value

        scheduler.poll = secondary_failed
        scheduler.clock.now += timedelta(minutes=1)
        await scheduler.poll_once()
        failed = scheduler.live.get(scheduler.clock())
        assert failed.stale and failed.units == before.units
        assert failed.fetched_at == before.fetched_at and scheduler.status().live_stale
        assert failed.model_dump(mode="json")["units"][0]["red_since"]
        scheduler.poll = raw_state
        await scheduler.poll_once()
        assert not scheduler.live.get(scheduler.clock()).stale

    asyncio.run(scenario())


def test_failed_archive_day_is_visible_in_system_coverage(scheduler: Scheduler) -> None:
    scheduler.ledger = {"watermark": "2026-10-24", "gaps": [{
        "day": "2026-10-25", "start": "2026-10-24T21:00:00Z", "end": "2026-10-25T22:00:00Z",
        "error_type": "IncompleteArchiveError", "reason": "Archive completeness: 0 records",
    }]}
    status = scheduler.status()
    assert status.model_dump(mode="json")["ledger_watermark"] == "2026-10-24"
    assert status.coverage_gaps[0].start == datetime(2026, 10, 24, 21, tzinfo=UTC)
    assert status.coverage_gaps[0].end == datetime(2026, 10, 25, 22, tzinfo=UTC)


@pytest.mark.parametrize("fail_first", [False, True])
def test_kyiv_midnight_once_across_dst_and_failure(scheduler: Scheduler, fail_first: bool) -> None:
    async def scenario() -> None:
        calls: list[datetime] = []
        sleeps: list[float] = []

        def catch_up(now: datetime) -> None:
            calls.append(now)
            if fail_first and len(calls) == 1:
                raise RuntimeError("archive unavailable")

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) == 3:
                raise asyncio.CancelledError
            scheduler.clock.now += timedelta(seconds=seconds)

        scheduler.catch_up, scheduler.sleep = catch_up, sleep
        with pytest.raises(asyncio.CancelledError):
            await scheduler._nightly_loop()
        assert calls == [
            datetime(2026, 10, 24, 21, tzinfo=UTC),
            datetime(2026, 10, 25, 22, tzinfo=UTC),
        ]
        assert sleeps == [1, 25 * 3600, 24 * 3600]
        persisted = json.loads((scheduler.root / "nightly.json").read_text())
        assert persisted["ok"] is True and persisted["steps"]["forecasts"]["status"] == "ok"
        assert next_midnight(calls[-1]) == datetime(2026, 10, 26, 22, tzinfo=UTC)

    asyncio.run(scenario())


@pytest.mark.parametrize("watermark,expected", [(None, True), ("2026-10-22", True),
                                               ("2026-10-23", False)])
def test_startup_catch_up(scheduler: Scheduler, watermark: str | None, expected: bool) -> None:
    async def scenario() -> None:
        scheduler.ledger = {} if watermark is None else {"watermark": watermark}
        loop = asyncio.get_running_loop()
        finished, sleeping = asyncio.Event(), asyncio.Event()
        calls: list[datetime] = []

        def catch_up(now: datetime) -> None:
            calls.append(now)
            loop.call_soon_threadsafe(finished.set)

        async def sleep(seconds: float) -> None:
            sleeping.set()
            await asyncio.Event().wait()

        scheduler.catch_up, scheduler.sleep = catch_up, sleep
        await scheduler.start()
        try:
            if expected:
                await asyncio.wait_for(finished.wait(), 2)
            else:
                await asyncio.wait_for(sleeping.wait(), 2)
            assert bool(calls) is expected
        finally:
            await scheduler.stop()

    asyncio.run(scenario())


def test_pipeline_records_failed_step_and_retries(scheduler: Scheduler) -> None:
    async def scenario() -> None:
        calls: list[str] = []

        def broken_features() -> None:
            raise ValueError("feature rebuild failed")

        scheduler.features = broken_features
        await scheduler.run_pipeline("startup")
        saved = json.loads((scheduler.root / "nightly.json").read_text())
        assert saved["ok"] is False and saved["started"] and saved["finished"]
        assert saved["error_class"] == "ValueError"
        assert saved["error_message"] == "feature rebuild failed"
        assert saved["steps"]["catch_up"]["status"] == "ok"
        assert saved["steps"]["features"]["status"] == "error"
        assert saved["steps"]["forecasts"]["status"] == "skipped"
        scheduler.features = lambda: calls.append("features")
        scheduler.reload = lambda: calls.append("reload")
        await scheduler.run_pipeline()
        assert scheduler.nightly["ok"] is True and calls == ["features", "reload"]
        await scheduler.poll_once()
        assert not scheduler.live.get(scheduler.clock()).stale

    asyncio.run(scenario())


def test_polling_and_refitting_are_independent_and_readers_keep_snapshot(
    scheduler: Scheduler,
) -> None:
    async def scenario() -> None:
        entered, release, reconciled = threading.Event(), threading.Event(), threading.Event()

        def poll(now: datetime) -> dict[str, object]:
            entered.set()
            assert release.wait(2)
            return raw_state(now)

        scheduler.poll = poll
        scheduler.catch_up = lambda now: reconciled.set()
        polling = asyncio.create_task(scheduler.poll_once())
        assert await worker_call(entered.wait, 2)
        pipeline = asyncio.create_task(scheduler.run_pipeline())
        await asyncio.sleep(0)
        assert await worker_call(reconciled.wait, 2)
        assert scheduler.poll_lock.locked()
        assert scheduler.latest == base_forecast()
        assert scheduler.live.get(scheduler.clock()).stale
        release.set()
        await asyncio.gather(polling, pipeline)
        assert reconciled.is_set() and not scheduler.live.get(scheduler.clock()).stale

    asyncio.run(scenario())


def test_production_hour_and_pipeline_order(scheduler: Scheduler) -> None:
    calls: list[str] = []

    class Model:
        def refit(self, now: datetime) -> ProductionModel:
            calls.append("refit")
            return self.status()

        def forecast(self, now: datetime) -> pd.DataFrame:
            calls.append("forecast")
            return pd.DataFrame([base_forecast().rows[0].model_dump()])

        def score_recent(self, now: datetime) -> None:
            calls.append("score")

        def status(self) -> ProductionModel:
            return ProductionModel(onset="M1-production", duration=None,
                                   trained_at=NOW, data_through=NOW)

    scheduler.production = Model()
    scheduler.catch_up = lambda now: calls.append("catch_up")
    scheduler.features = lambda: calls.append("features")
    scheduler.reload = lambda: calls.append("reload")
    asyncio.run(scheduler.run_pipeline())
    assert calls == ["catch_up", "features", "refit", "score", "reload", "forecast"]
    assert scheduler.latest.at == NOW.replace(minute=0, second=0)
    assert scheduler.latest.onset_model == "M1-production"
    assert scheduler.base_forecast() == scheduler.latest
    scheduler.clock.now += timedelta(hours=1)
    assert scheduler.base_forecast() is None


def test_hourly_loop_uses_hour_boundary(scheduler: Scheduler) -> None:
    async def scenario() -> None:
        calls: list[datetime] = []

        async def refresh() -> None:
            calls.append(scheduler.clock())

        async def sleep(seconds: float) -> None:
            if len(calls) == 2:
                raise asyncio.CancelledError
            assert seconds == 1
            scheduler.clock.now += timedelta(seconds=seconds)

        scheduler.refresh_forecast, scheduler.sleep = refresh, sleep
        with pytest.raises(asyncio.CancelledError):
            await scheduler._hourly_loop()
        assert calls == [NOW, datetime(2026, 10, 24, 21, tzinfo=UTC)]

    asyncio.run(scenario())


def test_shutdown_waits_for_thread_before_unlocking(scheduler: Scheduler) -> None:
    async def scenario() -> None:
        entered, release = threading.Event(), threading.Event()

        def poll(now: datetime) -> dict[str, object]:
            entered.set()
            assert release.wait(2)
            raise ConnectionError("failed while stopping")

        scheduler.poll = poll
        task = asyncio.create_task(scheduler.poll_once())
        assert await worker_call(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert scheduler.poll_lock.locked() and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not scheduler.poll_lock.locked()

    asyncio.run(scenario())
