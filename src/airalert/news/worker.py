"""Restartable optional sidecar. S1 awaits start(provider)/stop() in its lifespan."""

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from airalert.news import adjust, extract, incidents
from airalert.news.auth import Auth
from airalert.news.codex_runner import CodexError, CodexRunner, ToolAttempt
from airalert.news.geography import Geography
from airalert.news.policy import (
    EVALUATE_INTERVAL,
    EVALUATION_LIMIT,
    EXTRACT_INTERVAL,
    EXTRACTION_LIMIT,
    TOKEN_LIMIT,
    TOKEN_RESERVE,
)
from airalert.news.scraper import Scraper, SourceUnavailable
from airalert.news.settings import SettingsStore
from airalert.news.store import Store
from airalert.schemas import ForecastResponse, NewsFeed, NewsStatus

BaseProvider = Callable[[], ForecastResponse | None]
logger = logging.getLogger("airalert.news")


class Worker:
    def __init__(
        self,
        settings: SettingsStore | None = None,
        store: Store | None = None,
        auth: Auth | None = None,
        runner: CodexRunner | None = None,
        scraper: Scraper | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings or SettingsStore()
        self.store = store or Store()
        self.auth = auth or Auth()
        self.clock = clock
        self.runner = runner or CodexRunner()
        self.runner.breaker = self.trip
        self.scraper = scraper or Scraper(self.store, clock=clock)
        self.base_provider: BaseProvider = lambda: None
        self.status = NewsStatus(
            enabled=False,
            state="off",
            message=None,
            last_poll_at=None,
            last_extraction_at=None,
            last_evaluation_at=None,
            news_as_of=None,
            models=self.settings.load().models,
        )
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake = asyncio.Event()
        self._cycle_lock = asyncio.Lock()
        self._last_extract = self.store.last_run_time("extract")
        self._last_evaluate = self.store.last_run_time("evaluate")
        self._retry_at: datetime | None = None
        self._failures = 0
        self._immediate_used = False
        self.last_measurements: dict[str, object] = {}
        self._last_run = 0
        self._urgent_seen: set[tuple[str, int]] = set()

    def trip(self, message: str) -> None:
        self.store.control("breaker", message)
        self.status = self.status.model_copy(update={"state": "error", "message": message})

    def changed(self) -> None:
        """Settings writes wake the loop; in-flight outputs from old settings are discarded."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    async def start(self, base_provider: BaseProvider) -> None:
        self.base_provider = base_provider
        if self._task is not None and not self._task.done():
            self.changed()
            return
        self._loop = asyncio.get_running_loop()
        self.settings.on_change = self.changed
        self._task = asyncio.create_task(self._run(), name="airalert-news")

    async def stop(self) -> None:
        self.settings.on_change = None
        if self._task is not None:
            self._task.cancel()
            await asyncio.to_thread(self.runner.cancel)
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._loop = None
        self.status = self.status.model_copy(
            update={"state": "off", "message": "News worker stopped"}
        )

    async def close(self) -> None:
        await self.stop()
        await asyncio.to_thread(self.auth.stop_login)
        await self.scraper.close()
        self.store.close()

    async def _run(self) -> None:
        try:
            while True:
                self._wake.clear()
                cycle = asyncio.create_task(self.cycle())
                changed = asyncio.create_task(self._wake.wait())
                try:
                    ready, _ = await asyncio.wait(
                        {cycle, changed}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if changed in ready and not cycle.done():
                        cycle.cancel()
                        await asyncio.to_thread(self.runner.cancel)
                    with contextlib.suppress(asyncio.CancelledError):
                        await cycle
                finally:
                    changed.cancel()
                    if not cycle.done():
                        cycle.cancel()
                        await asyncio.to_thread(self.runner.cancel)
                    await asyncio.gather(cycle, changed, return_exceptions=True)
                if self._wake.is_set():
                    continue
                settings = self.settings.load()
                delay = settings.poll_seconds if settings.news_enabled else 3600
                if self.status.state == "running" and not self.store.pending(
                    {c.handle for c in settings.channels if c.enabled}
                ):
                    delay = max(delay, 300)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=delay * random.uniform(0.9, 1.1)
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Untrusted exception text never reaches the API or an application log.
            self.trip(
                "News worker stopped after an internal error; base forecasts remain available"
            )

    def _quota(self, stage: str) -> bool:
        extracts, evaluations, tokens = self.store.usage(self.clock())
        if (
            (stage == "extract" and extracts >= EXTRACTION_LIMIT)
            or (stage == "evaluate" and evaluations >= EVALUATION_LIMIT)
            or tokens + TOKEN_RESERVE > TOKEN_LIMIT
        ):
            self.status = self.status.model_copy(
                update={
                    "state": "quota_limited",
                    "message": "Daily news inference limit reached; base forecasts retained",
                }
            )
            return False
        return True

    async def _complete(
        self, stage: str, prompt: str, schema: dict[str, object], messages: list[extract.Message]
    ) -> dict[str, object] | None:
        settings = self.settings.load()
        if not settings.news_enabled or not self._quota(stage):
            return None
        model = getattr(settings.models, f"{stage}_model")
        effort = getattr(settings.models, f"{stage}_effort")
        run = self.store.run_start(stage, model, effort, self.clock(), messages)
        logger.info("news model stage=%s started", stage)
        self._last_run = run
        try:
            result = await asyncio.to_thread(self.runner.complete, prompt, schema, model, effort)
            finished = self.clock()
            self.store.run_finish(
                run, finished, "completed", result.input_tokens, result.output_tokens
            )
            self.store.run_output(run, result.output)
            logger.info("news model stage=%s completed seconds=%.3f", stage, result.seconds)
            self.last_measurements[stage] = {
                "seconds": round(result.seconds, 3),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            }
            if self.settings.load() != settings:
                return None
            return result.output
        except asyncio.CancelledError:
            await asyncio.to_thread(self.runner.cancel)
            self.store.run_finish(run, self.clock(), "cancelled", output_tokens=TOKEN_RESERVE)
            raise
        except (CodexError, OSError):
            # Reserve unknown usage on failures so retries cannot evade the token budget.
            self.store.run_finish(run, self.clock(), "failed", output_tokens=TOKEN_RESERVE)
            raise

    async def cycle(
        self, bootstrap_only: bool = False, skip_poll: bool = False, skip_extract: bool = False
    ) -> None:
        """One bounded polling/extraction/evaluation cycle, also used by the real-run probe."""
        async with self._cycle_lock:
            settings = self.settings.load()
            self.status = self.status.model_copy(
                update={
                    "enabled": settings.news_enabled,
                    "models": settings.models,
                }
            )
            if not settings.news_enabled:
                self.status = self.status.model_copy(update={"state": "off", "message": None})
                logger.info("news state=off telegram_polls=0 model_calls=0")
                return
            breaker = self.store.control("breaker")
            if breaker:
                self.status = self.status.model_copy(update={"state": "error", "message": breaker})
                return
            if self._retry_at is not None and self.clock() < self._retry_at:
                return
            self.status = self.status.model_copy(update={"state": "starting", "message": None})
            auth = await asyncio.to_thread(self.auth.status)
            if not auth.codex_installed or not auth.logged_in:
                self.status = self.status.model_copy(
                    update={
                        "state": "needs_login" if auth.codex_installed else "needs_codex",
                        "message": auth.message,
                    }
                )
                return
            try:
                self.settings.validate(settings, self.auth.models)
            except ValueError:
                self.status = self.status.model_copy(
                    update={
                        "state": "error",
                        "message": "Selected model or effort is unavailable; no substitution",
                    }
                )
                return
            enabled = {c.handle for c in settings.channels if c.enabled}
            failed_sources = 0
            for channel in settings.channels:
                if not channel.enabled or skip_poll:
                    continue
                try:
                    await self.scraper.poll(channel.handle, bootstrap_only=bootstrap_only)
                    state = self.store.channel(channel.handle)
                    failed_sources += bool(state["paused"] or state["failures"] or state["lagging"])
                except SourceUnavailable:
                    failed_sources += 1
            self.status = self.status.model_copy(
                update={
                    "last_poll_at": (
                        max(
                            (
                                datetime.fromisoformat(
                                    str(self.store.channel(c.handle)["last_poll"])
                                )
                                for c in settings.channels
                                if self.store.channel(c.handle)["last_poll"]
                            ),
                            default=None,
                        )
                        if skip_poll
                        else self.clock()
                    ),
                    "state": "stale" if failed_sources else "running",
                    "message": f"{failed_sources} news sources unavailable or lagging"
                    if failed_sources
                    else None,
                }
            )
            logger.info("news poll completed enabled_channels=%s failed_sources=%s",
                        len(enabled), failed_sources)
            if self.settings.load() != settings:
                return
            try:
                geography = Geography()
                pending = self.store.pending(enabled)
                repeated = [m for m in pending if self.store.repeated(m)]
                repeated_ids = {m.version_id for m in repeated}
                candidates, dropped = extract.batch(
                    m for m in pending if m.version_id not in repeated_ids
                )
                dropped.extend(repeated)
                # Filtered edits still invalidate an older extracted version.
                if dropped:
                    incidents.reconcile(self.store, [], dropped, self.clock())
                    self.store.processed(dropped)
                now = self.clock()
                if (
                    candidates
                    and not skip_extract
                    and (
                        self._last_extract is None
                        or now - self._last_extract >= timedelta(seconds=EXTRACT_INTERVAL)
                    )
                ):
                    self._last_extract = now
                    output = await self._complete(
                        "extract",
                        extract.prompt(candidates, geography),
                        extract.output_schema(extract.Extraction),
                        candidates,
                    )
                    if output is None:
                        return
                    finished = self.clock()
                    validated = extract.validate(output, candidates, geography, finished)
                    lookup = {m.source_key: m.version_id for m in candidates}
                    self.store.record_evidence(
                        self._last_run,
                        [
                            (lookup[e.source_key], e.quote)
                            for c in extract.Extraction.model_validate(output).events
                            for e in c.evidence
                        ],
                    )
                    incidents.reconcile(self.store, validated, candidates, finished)
                    self.store.processed(candidates)
                    self.status = self.status.model_copy(update={"last_extraction_at": finished})
                # Evaluation can start at extraction completion. Forecast availability stays strict.
                now = self.clock()
                current = [
                    i
                    for i in self.store.incidents()
                    if i.extraction_finished <= now
                    and i.event.observed_at <= now
                    and i.event.published_at <= now < i.event.expires_at
                    and i.event.status != "cleared"
                ]
                fingerprint = incidents.fingerprint(current)
                evaluation = self.store.latest_evaluation()
                models_key = settings.models.model_dump_json()
                changed = (
                    evaluation is None
                    or evaluation["fingerprint"] != fingerprint
                    or evaluation.get("models") != models_key
                )
                urgent_now = {
                    (i.event.id, i.revision)
                    for i in current
                    if i.event.type in {"bomber_takeoff", "mig31k_takeoff", "missile_launch"}
                }
                urgent = bool(urgent_now - self._urgent_seen)
                ready = (
                    self._last_evaluate is None
                    or self.clock() - self._last_evaluate >= timedelta(seconds=EVALUATE_INTERVAL)
                )
                if ready:
                    self._immediate_used = False
                if changed and current and (ready or urgent and not self._immediate_used):
                    if not ready:
                        self._immediate_used = True
                    self._last_evaluate = self.clock()
                    self._urgent_seen.update(urgent_now)
                    output = await self._complete(
                        "evaluate",
                        adjust.evaluation_prompt(current),
                        extract.output_schema(adjust.Evaluation),
                        [],
                    )
                    if output is None:
                        return
                    effects = adjust.validate_effects(output, current)
                    finished = self.clock()
                    self.store.evaluation(
                        fingerprint, finished, [e.model_dump() for e in effects], models_key
                    )
                    self.status = self.status.model_copy(update={"last_evaluation_at": finished})
                elif changed and not current:
                    self.store.evaluation(fingerprint, self.clock(), [], models_key)
                self._failures = 0
                self._retry_at = None
            except ToolAttempt:
                # Runner already persisted the breaker and discarded the complete output.
                return
            except (CodexError, ValueError, ValidationError, OSError) as exc:
                # Host ValueErrors contain fixed safe descriptions, never channel content.
                self.last_measurements["validation_error"] = (
                    str(exc)
                    if type(exc) is ValueError or isinstance(exc, CodexError)
                    else type(exc).__name__
                )
                logger.warning("news inference/validation failed category=%s",
                               str(exc) if type(exc) is ValueError else type(exc).__name__)
                self._failures += 1
                self._retry_at = self.clock() + timedelta(
                    seconds=min(1800, 120 * 2**self._failures)
                )
                self.status = self.status.model_copy(
                    update={
                        "state": "error",
                        "message": "News inference or host validation failed; retry is delayed",
                    }
                )
                if self._failures >= 3:
                    self.trip(
                        "News circuit breaker: three inference/validation failures; "
                        "base forecasts retained"
                    )

    def feed(self) -> NewsFeed:
        """Read the sidecar without waiting for auth, a network request or model inference."""
        settings = self.settings.load()
        status = self.status.model_copy(
            update={"enabled": settings.news_enabled, "models": settings.models}
        )
        if not settings.news_enabled:
            return NewsFeed(
                status=status.model_copy(update={"state": "off", "message": None}),
                base_at=None,
                events=[],
                adjustments=[],
            )
        try:
            base = self.base_provider()
        except Exception:
            base = None
        now = self.clock()
        events = [
            i.event
            for i in self.store.incidents()
            if i.usable(now)
            and all(
                s.handle in {c.handle for c in settings.channels if c.enabled}
                for s in i.event.sources
            )
        ]
        rows = []
        if base is None:
            message = "No current-hour base forecast; news adjustments are empty"
        elif base.mode != "latest" or base.at != now.replace(minute=0, second=0, microsecond=0):
            message = "Base forecast is not the current UTC hour; news adjustments are empty"
        else:
            message = None
            evaluation = self.store.latest_evaluation(base.at)
            if (
                evaluation is not None
                and status.state
                not in {
                    "error",
                    "needs_login",
                    "needs_codex",
                }
                and evaluation.get("models") == settings.models.model_dump_json()
            ):
                current = [
                    i
                    for i in self.store.incidents(base.at)
                    if all(
                        s.handle in {c.handle for c in settings.channels if c.enabled}
                        for s in i.event.sources
                    )
                ]
                effects = [
                    adjust.Effect.model_validate(e) for e in json.loads(str(evaluation["effects"]))
                ]
                rows = adjust.adjustments(
                    base,
                    current,
                    effects,
                    datetime.fromisoformat(str(evaluation["finished_at"])),
                    base.at,
                )
            if not rows:
                message = "No eligible evaluated incident was available before this hourly forecast"
            status = status.model_copy(update={"news_as_of": base.at})
        if message:
            status = status.model_copy(
                update={"message": (status.message + "; " if status.message else "") + message}
            )
        if (
            status.last_poll_at is not None
            and now - status.last_poll_at > timedelta(seconds=max(360, settings.poll_seconds * 2))
            and status.state == "running"
        ):
            status = status.model_copy(
                update={
                    "state": "stale",
                    "message": (status.message + "; " if status.message else "")
                    + "News polling is stale",
                }
            )
        if base is not None:
            self.store.save_adjustments(base.at, now, json.dumps([r.model_dump() for r in rows]))
        return NewsFeed(
            status=status, base_at=base.at if base else None, events=events, adjustments=rows
        )


_worker: Worker | None = None


def get_worker() -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker()
    return _worker


async def start(base_provider: BaseProvider) -> None:
    await get_worker().start(base_provider)


async def stop() -> None:
    global _worker
    if _worker is not None:
        await _worker.close()
        _worker = None
