"""Serve complete cached forecasts and live state while background jobs publish updates."""

import importlib
import inspect
import ipaddress
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Annotated
from urllib.parse import urlsplit

import pandas as pd
import pyarrow.parquet as pq
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request

from airalert import config
from airalert.live import LiveCache, display_geography
from airalert.precompute import SERVED_MODELS_KEY, Paths, response_from_frame, validate_forecasts
from airalert.scheduler import Scheduler, utc_now, worker_call
from airalert.schemas import (
    EpisodeSpan,
    ForecastResponse,
    HistoryPoint,
    HistoryResponse,
    LiveState,
    Meta,
    NewsFeed,
    NewsModels,
    NewsStatus,
    Range,
    Report,
    SystemStatus,
)

logger = logging.getLogger("airalert.api")


@dataclass(frozen=True)
class _Loaded:
    meta: Meta
    forecasts: pd.DataFrame
    times: dict[pd.Timestamp, pd.DataFrame]
    histories: dict[str, pd.DataFrame]
    episodes: dict[str, pd.DataFrame]
    geojson: dict[str, object]


def _load(paths: Paths) -> _Loaded:
    """Load one complete artifact generation; v1 forecasts survive a newer ingest snapshot."""
    for path in (paths.forecasts, paths.snapshot, paths.raions, paths.episodes, paths.geojson):
        if not path.is_file():
            raise FileNotFoundError(f"Required serving artifact missing: {path}")
    snapshot_data = json.loads(paths.snapshot.read_text(encoding="utf-8"))
    snapshot = pd.Timestamp(snapshot_data["snapshot"]).as_unit("us")
    if snapshot.tzinfo is None or snapshot != snapshot.floor("h"):
        raise ValueError("Snapshot must be a timezone-aware hour")
    table = pq.read_table(paths.forecasts)
    stored = (table.schema.metadata or {}).get(SERVED_MODELS_KEY)
    if stored is None:
        raise ValueError("Forecasts lack served-model metadata; run airalert precompute")
    models = json.loads(stored)
    forecast_snapshot = pd.Timestamp(models["snapshot"]).as_unit("us")
    if forecast_snapshot > snapshot:
        raise ValueError("Forecast snapshot is ahead of the data snapshot")
    if models["csv_sha256"] != snapshot_data["csv_sha256"]:
        raise ValueError("Forecast CSV hash disagrees with snapshot.json")
    forecasts = table.to_pandas()
    raions = pd.read_csv(paths.raions, dtype="str")
    validate_forecasts(forecasts, raions, forecast_snapshot)
    episodes = pd.read_parquet(paths.episodes)
    for column in ("start", "end"):
        episodes[column] = pd.to_datetime(episodes[column], utc=True).astype("datetime64[us, UTC]")
    geojson = json.loads(paths.geojson.read_text(encoding="utf-8"))
    if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
        raise ValueError("Raion geography must be a GeoJSON FeatureCollection")
    geojson = display_geography(geojson, paths.crosswalk)
    replay = Range(
        start=config.TEST_START,
        end=(forecast_snapshot - pd.Timedelta(hours=config.TEST_END_OFFSET_H)).to_pydatetime(),
    )
    meta = Meta(
        snapshot=snapshot.to_pydatetime(), csv_sha256=snapshot_data["csv_sha256"],
        regime_switch=snapshot_data["regime_switch"], horizons=list(config.HORIZONS),
        raion_count=len(raions), latest_at=forecast_snapshot.to_pydatetime(), replay=replay,
        splits={
            "train": Range(start=config.TRAIN_START, end=config.VAL_START),
            "val": Range(start=config.VAL_START, end=config.TEST_START),
            "test": replay,
        },
        onset_model=models["onset"], duration_model=models["duration"],
        data_gaps=(
            [Range(start=gap["start"], end=gap["end"])
             for gap in snapshot_data["coverage"]["gaps"]]
            if snapshot_data.get("coverage", {}).get("archive_gate_passed") else
            [Range(start=start, end=end) for start, end in config.DATA_GAPS]
        ),
    )
    return _Loaded(
        meta=meta, forecasts=forecasts,
        times={key: group for key, group in forecasts.groupby("t", sort=False)},
        histories={key: group for key, group in forecasts.groupby("raion_id", sort=False)},
        episodes={key: group for key, group in episodes.groupby("raion_id", sort=False)},
        geojson=geojson,
    )


def _aware(value: datetime, parameter: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, f"{parameter} must include a timezone, for example Z")
    return value.astimezone(UTC)


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except Exception as error:
        logger.warning("optional module %s unavailable: %s", name, type(error).__name__)
        return None


def _local_mutation(request: Request) -> None:
    """Protect optional production controls with the same origin rules as news controls."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    try:
        client = ipaddress.ip_address(request.client.host if request.client else "")
        if not client.is_loopback:
            raise ValueError("Non-loopback client")
        target = urlsplit(str(request.base_url))
        if target.hostname != "localhost" and not ipaddress.ip_address(
            target.hostname or "",
        ).is_loopback:
            raise ValueError("Non-loopback host")
        origin = request.headers.get("origin")
        if origin is not None:
            source = urlsplit(origin)
            if (
                source.scheme, source.hostname,
                source.port or (443 if source.scheme == "https" else 80),
            ) != (
                target.scheme, target.hostname,
                target.port or (443 if target.scheme == "https" else 80),
            ) or source.path not in {"", "/"} or source.query or source.fragment \
                    or source.username or source.password:
                raise ValueError("Cross-origin request")
        if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            raise ValueError("Cross-origin request")
    except ValueError as error:
        raise HTTPException(403, "Controls require a loopback, same-origin client") from error


def create_app(
    paths: Paths | None = None, *, scheduler_enabled: bool | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> FastAPI:
    """Lifespan starts local workers; disable scheduling explicitly in isolated HTTP tests."""
    paths = Paths() if paths is None else paths
    enabled = (
        os.environ.get("AIRALERT_DISABLE_SCHEDULER", "").lower() not in {"1", "true", "yes"}
        if scheduler_enabled is None else scheduler_enabled
    )
    production = _optional_module("airalert.production")
    news_router = _optional_module("airalert.news.router")
    news_worker = _optional_module("airalert.news.worker") if news_router else None
    router = getattr(news_router, "router", None)
    news_endpoint = next(
        (route.endpoint for route in router.routes if getattr(route, "path", None) == "/api/news"),
        None,
    ) if isinstance(router, APIRouter) else None
    if news_endpoint is None:
        news_worker = None

    def fallback() -> ForecastResponse:
        loaded: _Loaded = app.state.loaded
        return response_from_frame(
            loaded.times[pd.Timestamp(loaded.meta.latest_at)], loaded.meta.latest_at,
            loaded.meta.onset_model, loaded.meta.duration_model, latest=True,
        )

    def reload_data() -> None:
        app.state.loaded = _load(paths)

    def poll(now: datetime) -> dict[str, object]:
        from airalert.ingest import live_state

        return live_state(now, root=paths.live)

    def catch_up(now: datetime) -> object:
        from airalert.ingest import catch_up

        return catch_up(now, root=paths.live)

    def features() -> object:
        from airalert.features import build

        return build()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.loaded = _load(paths)
        app.state.live = LiveCache(app.state.loaded.geojson, paths.live / "state.json")
        scheduler = Scheduler(
            live=app.state.live, root=paths.live, fallback=fallback, reload=reload_data,
            coverage=lambda: app.state.loaded.meta.data_gaps, production=production,
            data_through=lambda: app.state.loaded.meta.snapshot,
            poll=poll, catch_up=catch_up, features=features, clock=clock,
        )
        app.state.scheduler = scheduler
        app.state.news_message = "News module is unavailable; news agent is off"
        news_started = False
        try:
            if enabled:
                await scheduler.start()
            if news_worker is not None:
                try:
                    await news_worker.start(scheduler.base_forecast)
                    news_started = True
                except Exception as error:
                    logger.warning("news worker unavailable: %s", type(error).__name__)
                    app.state.news_message = "News worker could not start; news agent is off"
                    try:
                        await news_worker.stop()
                    except Exception:
                        logger.warning("news worker cleanup failed")
            app.state.news_available = news_started
            yield
        finally:
            if news_started:
                try:
                    await news_worker.stop()
                except Exception as error:
                    logger.warning("news worker shutdown failed: %s", type(error).__name__)
            await scheduler.stop()

    app = FastAPI(title="Air Alert Forecaster", lifespan=lifespan)

    @app.get("/api/live")
    async def live(request: Request) -> LiveState:
        return request.app.state.live.get(clock())

    @app.get("/api/system")
    async def system(request: Request) -> SystemStatus:
        return request.app.state.scheduler.status()

    @app.get("/api/news", response_model=NewsFeed)
    async def news(request: Request) -> NewsFeed:
        if request.app.state.news_available:
            if inspect.iscoroutinefunction(news_endpoint):
                return await news_endpoint()
            return await worker_call(news_endpoint)
        return NewsFeed(
            status=NewsStatus(
                enabled=False, state="off", message=request.app.state.news_message,
                last_poll_at=None, last_extraction_at=None, last_evaluation_at=None,
                news_as_of=None, models=NewsModels(
                    extract_model="gpt-6-luna", extract_effort="high",
                    evaluate_model="gpt-6.1-sol", evaluate_effort="high",
                ),
            ),
            base_at=request.app.state.scheduler.latest.at, events=[], adjustments=[],
        )

    if news_worker is not None:
        # Keep N1's settings/auth dependencies and endpoint implementation intact.
        feature_router = APIRouter()
        feature_router.routes.extend(route for route in router.routes if route.path != "/api/news")
        app.include_router(feature_router)
    if production is not None:
        router = getattr(production, "router", None)
        if isinstance(router, APIRouter):
            app.include_router(router, dependencies=[Depends(_local_mutation)])

    @app.get("/api/meta")
    async def meta(request: Request) -> Meta:
        loaded: _Loaded = request.app.state.loaded
        latest = request.app.state.scheduler.latest
        return loaded.meta.model_copy(update={
            "latest_at": latest.at, "onset_model": latest.onset_model,
            "duration_model": latest.duration_model,
        })

    @app.get("/api/raions")
    async def raions(request: Request) -> dict[str, object]:
        loaded: _Loaded = request.app.state.loaded
        return loaded.geojson

    @app.get("/api/forecast")
    async def forecast(request: Request, at: datetime | None = None) -> ForecastResponse:
        loaded: _Loaded = request.app.state.loaded
        latest = request.app.state.scheduler.latest
        if at is None:
            return latest
        when = _aware(at, "at")
        if when.minute or when.second or when.microsecond:
            raise HTTPException(422, "at must be exactly on an hour")
        if when == latest.at:
            return latest
        frame = loaded.times.get(pd.Timestamp(when))
        if frame is None:
            raise HTTPException(
                404, "No forecast at this hour; use a validation, test or latest hour",
            )
        return response_from_frame(
            frame, when, loaded.meta.onset_model, loaded.meta.duration_model,
            latest=when == loaded.meta.latest_at,
        )

    @app.get("/api/raion/{raion_id:path}/history")
    async def history(
        request: Request, raion_id: str,
        start: Annotated[datetime, Query(alias="from")],
        end: Annotated[datetime, Query(alias="to")],
    ) -> HistoryResponse:
        loaded: _Loaded = request.app.state.loaded
        start, end = _aware(start, "from"), _aware(end, "to")
        if end <= start or end - start > timedelta(days=31):
            raise HTTPException(422, "History requires from < to and a range of at most 31 days")
        latest = request.app.state.scheduler.latest
        if start < config.VAL_START or end > latest.at:
            raise HTTPException(422, "History must be within validation start and the latest hour")
        frame = loaded.histories.get(raion_id)
        if frame is None:
            raise HTTPException(404, f"Unknown raion: {raion_id}")
        subset = frame.loc[frame.t.ge(start) & frame.t.le(end), ["t", "p_1", "p_3", "p_12"]]
        if start <= latest.at <= end and not subset.t.eq(latest.at).any():
            row = next((row for row in latest.rows if row.raion_id == raion_id), None)
            if row is not None:
                subset = pd.concat([subset, pd.DataFrame([{
                    "t": latest.at, "p_1": row.p_1, "p_3": row.p_3, "p_12": row.p_12,
                }])], ignore_index=True)
        spans: list[EpisodeSpan] = []
        episodes = loaded.episodes.get(raion_id)
        if episodes is not None:
            overlap = episodes.start.le(end) & (episodes.end.isna() | episodes.end.gt(start))
            for episode_start, episode_end in episodes.loc[overlap, ["start", "end"]].itertuples(
                index=False, name=None,
            ):
                spans.append(EpisodeSpan(
                    start=episode_start, end=None if pd.isna(episode_end) else episode_end,
                ))
        return HistoryResponse(
            raion_id=raion_id, start=start, end=end,
            series=[HistoryPoint.model_validate(row) for row in subset.to_dict(orient="records")],
            episodes=spans,
        )

    @app.get("/api/report")
    def report() -> Report:
        if not paths.metrics.is_file():
            raise HTTPException(404, "Model report is not available yet; run airalert backtest")
        return Report.model_validate_json(paths.metrics.read_text(encoding="utf-8"))

    return app


def serve(port: int = 8000, *, scheduler_enabled: bool | None = None) -> None:
    """Run local workers by default; AIRALERT_DISABLE_SCHEDULER=1 disables data jobs."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(create_app(scheduler_enabled=scheduler_enabled), host="127.0.0.1", port=port)
