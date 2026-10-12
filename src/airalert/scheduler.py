"""In-process UTC polling and Kyiv-calendar reconciliation with serialized publication."""

import asyncio
import json
import logging
import random
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from airalert import config
from airalert.live import LiveCache
from airalert.precompute import response_from_frame
from airalert.schemas import ForecastResponse, ProductionModel, Range, SystemStatus

logger = logging.getLogger("airalert.scheduler")


class Production(Protocol):
    def refit(self, now: datetime) -> ProductionModel: ...
    def forecast(self, now: datetime) -> pd.DataFrame: ...
    def score_recent(self, now: datetime) -> None: ...
    def status(self) -> ProductionModel: ...


def next_midnight(now: datetime) -> datetime:
    """Construct tomorrow's Kyiv midnight afresh; a DST day can have 23 or 25 hours."""
    zone = ZoneInfo(config.KYIV_TZ)
    tomorrow = now.astimezone(zone).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, zone).astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def error_message(error: Exception) -> str:
    """Keep useful pipeline errors without copying credentials or personal paths."""
    message = str(error).replace(str(Path.home()), "[home]")
    message = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [redacted]", message)
    message = re.sub(r"(?i)(token|authorization|api[_-]?key|password)([=: ]+)[^\s,;]+",
                     r"\1\2[redacted]", message)
    return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", message)[:1000]


async def worker_call[T](function: Callable[..., T], *args: object) -> T:
    """On shutdown, finish a running thread before releasing the publication lock."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        with suppress(Exception):
            await task
        raise cancelled


class Scheduler:
    """Readers use old complete caches while this lock protects writers and reloads."""

    def __init__(
        self, *, live: LiveCache, root: Path,
        fallback: Callable[[], ForecastResponse], reload: Callable[[], None],
        coverage: Callable[[], list[Range]],
        poll: Callable[[datetime], dict[str, object]],
        catch_up: Callable[[datetime], object], features: Callable[[], object],
        production: Production | None = None,
        data_through: Callable[[], datetime | None] | None = None,
        clock: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(-3, 3),
    ) -> None:
        self.live, self.root = live, root
        self.fallback, self.reload, self.coverage = fallback, reload, coverage
        self.poll, self.catch_up, self.features = poll, catch_up, features
        self.production = production
        self.data_through = data_through
        self.clock, self.sleep, self.jitter = clock, sleep, jitter
        self.lock = asyncio.Lock()
        self.latest = fallback()
        self.model = self._fallback_model()
        self.ledger = self._read("ledger.json")
        self.nightly = self._read("nightly.json")
        self._tasks: list[asyncio.Task[None]] = []
        self._last_nightly_day: date | None = None

    def _read(self, name: str) -> dict[str, object]:
        try:
            value = json.loads((self.root / name).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _fallback_model(self) -> ProductionModel:
        base = self.fallback()
        return ProductionModel(
            onset=base.onset_model, duration=base.duration_model,
            trained_at=None, data_through=base.at,
        )

    def needs_catch_up(self, now: datetime) -> bool:
        watermark = self.ledger.get("watermark")
        yesterday = now.astimezone(ZoneInfo(config.KYIV_TZ)).date() - timedelta(days=1)
        return watermark is None or date.fromisoformat(str(watermark)) < yesterday

    def base_forecast(self) -> ForecastResponse | None:
        hour = self.clock().astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        return self.latest if self.latest.at == hour else None

    async def poll_once(self) -> None:
        async with self.lock:
            try:
                raw = await worker_call(self.poll, self.clock())
                self.live.publish(raw)
                state = self.live.get(self.clock())
                if raw.get("stale"):
                    logger.warning("live poll returned stale state error=%s", raw.get("error_type"))
                logger.info(
                    "live poll fetched_at=%s units=%s red=%s yellow=%s stale=%s",
                    state.fetched_at.isoformat() if state.fetched_at else None, len(state.units),
                    sum(unit.red_since is not None for unit in state.units),
                    sum(unit.yellow_since is not None for unit in state.units), state.stale,
                )
            except Exception as error:
                self.live.fail()
                logger.warning("live poll failed %s: %s",
                               type(error).__name__, error_message(error))

    async def _refresh(self, now: datetime) -> None:
        if self.production is None:
            self.latest, self.model = self.fallback(), self._fallback_model()
            return
        frame = await worker_call(self.production.forecast, now)
        model = await worker_call(self.production.status)
        when = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        forecast = response_from_frame(frame, when, model.onset, model.duration, latest=True)
        if {row.raion_id for row in forecast.rows} != {
            row.raion_id for row in self.fallback().rows
        }:
            raise ValueError("Production forecast must cover the forecast universe")
        self.latest, self.model = forecast, model
        logger.info("hourly forecast at=%s rows=%s model=%s", when.isoformat(),
                    len(forecast.rows), model.onset)

    async def refresh_forecast(self) -> None:
        async with self.lock:
            try:
                await self._refresh(self.clock())
            except Exception as error:
                # Preserve the last published forecast and its matching model identity.
                logger.warning("forecast refresh failed %s: %s",
                               type(error).__name__, error_message(error))

    def _persist(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        stage = self.root / "nightly.json.tmp"
        stage.write_text(json.dumps(self.nightly, indent=2) + "\n", encoding="utf-8")
        stage.replace(self.root / "nightly.json")

    async def run_pipeline(self, reason: str = "nightly") -> None:
        async with self.lock:
            now = self.clock()
            steps = ["catch_up", "features", "refit", "score_recent", "reload", "forecasts"]
            self.nightly = {
                "reason": reason, "started": now.isoformat(), "finished": None, "ok": None,
                "steps": {name: {"status": "pending"} for name in steps},
                "error_class": None, "error_message": None,
            }
            current = steps[0]
            try:
                self._persist()
                for current in steps:
                    entry = self.nightly["steps"][current]
                    if current in {"refit", "score_recent"} and self.production is None:
                        entry.update({"status": "skipped", "message": "Production unavailable"})
                        self._persist()
                        continue
                    entry.update({"status": "running", "started": self.clock().isoformat()})
                    self._persist()
                    logger.info("pipeline %s step=%s started", reason, current)
                    if current == "catch_up":
                        await worker_call(self.catch_up, now)
                    elif current == "features":
                        await worker_call(self.features)
                    elif current == "refit":
                        await worker_call(self.production.refit, now)
                    elif current == "score_recent":
                        await worker_call(self.production.score_recent, now)
                    elif current == "reload":
                        await worker_call(self.reload)
                    else:
                        # A long refit may finish in a later hour than the pipeline began.
                        await self._refresh(self.clock())
                    entry.update({"status": "ok", "finished": self.clock().isoformat()})
                    self._persist()
                    logger.info("pipeline %s step=%s ok", reason, current)
                self.nightly["ok"] = True
            except asyncio.CancelledError:
                self.nightly.update({"ok": False, "error_class": "CancelledError",
                                     "error_message": "Server stopped during pipeline"})
                self.nightly["steps"][current]["status"] = "cancelled"
                for entry in self.nightly["steps"].values():
                    if entry["status"] == "pending":
                        entry["status"] = "skipped"
                raise
            except Exception as error:
                message = error_message(error)
                self.nightly.update({"ok": False, "error_class": type(error).__name__,
                                     "error_message": message})
                self.nightly["steps"][current].update({
                    "status": "error", "error_class": type(error).__name__,
                    "error_message": message, "finished": self.clock().isoformat(),
                })
                for entry in self.nightly["steps"].values():
                    if entry["status"] == "pending":
                        entry["status"] = "skipped"
                logger.warning("pipeline %s step=%s failed %s: %s",
                               reason, current, type(error).__name__, message)
            finally:
                self.ledger = self._read("ledger.json")
                self.nightly["finished"] = self.clock().isoformat()
                try:
                    self._persist()
                except OSError as error:
                    logger.warning("pipeline status persistence failed %s", type(error).__name__)

    async def _poll_loop(self) -> None:
        while True:
            await self.poll_once()
            await self.sleep(max(1, 60 + self.jitter()))

    async def _hourly_loop(self) -> None:
        await self.refresh_forecast()
        while True:
            now = self.clock().astimezone(UTC)
            target = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            await self._until(target)
            await self.refresh_forecast()

    async def _until(self, target: datetime) -> None:
        # Recheck the wall clock after wakeups, including clock corrections.
        while (remaining := (target - self.clock()).total_seconds()) > 0:
            await self.sleep(remaining)

    async def _nightly_loop(self) -> None:
        while True:
            target = next_midnight(self.clock())
            await self._until(target)
            day = target.astimezone(ZoneInfo(config.KYIV_TZ)).date()
            if self._last_nightly_day != day:
                self._last_nightly_day = day
                await self.run_pipeline()

    async def start(self) -> None:
        if self._tasks:
            return
        # Start the live loop first; a catch-up can run without delaying API startup.
        self._tasks = [
            asyncio.create_task(self._poll_loop(), name="airalert-live"),
            asyncio.create_task(self._hourly_loop(), name="airalert-hourly"),
            asyncio.create_task(self._nightly_loop(), name="airalert-nightly"),
        ]
        if self.needs_catch_up(self.clock()):
            self._tasks.append(asyncio.create_task(self.run_pipeline("startup"),
                                                  name="airalert-catch-up"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def status(self) -> SystemStatus:
        now = self.clock()
        live = self.live.get(now)
        gaps = {(gap.start, gap.end): gap for gap in self.coverage()}
        for gap in self.ledger.get("gaps", []):
            span = Range(start=gap["start"], end=gap["end"])
            gaps[(span.start, span.end)] = span
        return SystemStatus(
            now=now, live_fetched_at=live.fetched_at, live_stale=live.stale,
            data_through=self.data_through() if self.data_through else self.model.data_through,
            ledger_watermark=self.ledger.get("watermark"),
            coverage_gaps=list(gaps.values()), last_nightly_at=self.nightly.get("started"),
            last_nightly_ok=self.nightly.get("ok"), next_nightly_at=next_midnight(now),
            production=self.model,
        )
