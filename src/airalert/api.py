"""Read precomputed artifacts once and serve the shared Pydantic contracts."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pandas as pd
import pyarrow.parquet as pq
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request

from airalert import config
from airalert.precompute import SERVED_MODELS_KEY, Paths, validate_forecasts
from airalert.schemas import (
    EpisodeSpan,
    ForecastResponse,
    ForecastRow,
    HistoryPoint,
    HistoryResponse,
    Meta,
    Range,
    Report,
)


@dataclass(frozen=True)
class _Loaded:
    meta: Meta
    forecasts: pd.DataFrame
    times: dict[pd.Timestamp, pd.DataFrame]
    histories: dict[str, pd.DataFrame]
    episodes: dict[str, pd.DataFrame]
    geojson: dict[str, object]


def _load(paths: Paths) -> _Loaded:
    """Fail startup on incomplete/stale artifacts; metrics can arrive independently."""
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
    if pd.Timestamp(models["snapshot"]) != snapshot:
        raise ValueError("Forecasts are stale for this snapshot; run airalert precompute")
    if models["csv_sha256"] != snapshot_data["csv_sha256"]:
        raise ValueError("Forecast CSV hash disagrees with snapshot.json")
    forecasts = table.to_pandas()
    raions = pd.read_csv(paths.raions, dtype="str")
    validate_forecasts(forecasts, raions, snapshot)
    episodes = pd.read_parquet(paths.episodes)
    for column in ("start", "end"):
        episodes[column] = pd.to_datetime(episodes[column], utc=True).astype("datetime64[us, UTC]")
    geojson = json.loads(paths.geojson.read_text(encoding="utf-8"))
    if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
        raise ValueError("Raion geography must be a GeoJSON FeatureCollection")
    replay = Range(
        start=config.TEST_START,
        end=(snapshot - pd.Timedelta(hours=config.TEST_END_OFFSET_H)).to_pydatetime(),
    )
    meta = Meta(
        snapshot=snapshot.to_pydatetime(), csv_sha256=snapshot_data["csv_sha256"],
        regime_switch=snapshot_data["regime_switch"], horizons=list(config.HORIZONS),
        raion_count=len(raions), latest_at=snapshot.to_pydatetime(), replay=replay,
        splits={
            "train": Range(start=config.TRAIN_START, end=config.VAL_START),
            "val": Range(start=config.VAL_START, end=config.TEST_START),
            "test": replay,
        },
        onset_model=models["onset"], duration_model=models["duration"],
        data_gaps=[Range(start=start, end=end) for start, end in config.DATA_GAPS],
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


def create_app(paths: Paths | None = None) -> FastAPI:
    """Use the lifespan startup with TestClient as a context manager in contract tests."""
    paths = Paths() if paths is None else paths

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.loaded = _load(paths)
        yield

    app = FastAPI(title="Air Alert Forecaster", lifespan=lifespan)

    @app.get("/api/meta")
    def meta(request: Request) -> Meta:
        loaded: _Loaded = request.app.state.loaded
        return loaded.meta

    @app.get("/api/raions")
    def raions(request: Request) -> dict[str, object]:
        loaded: _Loaded = request.app.state.loaded
        return loaded.geojson

    @app.get("/api/forecast")
    def forecast(request: Request, at: datetime | None = None) -> ForecastResponse:
        loaded: _Loaded = request.app.state.loaded
        when = loaded.meta.latest_at if at is None else _aware(at, "at")
        if when.minute or when.second or when.microsecond:
            raise HTTPException(422, "at must be exactly on an hour")
        frame = loaded.times.get(pd.Timestamp(when))
        if frame is None:
            raise HTTPException(
                404, "No forecast at this hour; use a validation, test or latest hour",
            )
        values = frame.drop(columns="t")
        values = values.astype(object).where(values.notna(), None)
        return ForecastResponse(
            at=when, mode="latest" if when == loaded.meta.latest_at else "replay",
            onset_model=loaded.meta.onset_model, duration_model=loaded.meta.duration_model,
            rows=[ForecastRow.model_validate(row) for row in values.to_dict(orient="records")],
        )

    @app.get("/api/raion/{raion_id:path}/history")
    def history(
        request: Request, raion_id: str,
        start: Annotated[datetime, Query(alias="from")],
        end: Annotated[datetime, Query(alias="to")],
    ) -> HistoryResponse:
        loaded: _Loaded = request.app.state.loaded
        start, end = _aware(start, "from"), _aware(end, "to")
        if end <= start or end - start > timedelta(days=31):
            raise HTTPException(422, "History requires from < to and a range of at most 31 days")
        if start < config.VAL_START or end > loaded.meta.latest_at:
            raise HTTPException(422, "History must be within validation start and the snapshot")
        frame = loaded.histories.get(raion_id)
        if frame is None:
            raise HTTPException(404, f"Unknown raion: {raion_id}")
        subset = frame.loc[frame.t.ge(start) & frame.t.le(end), ["t", "p_1", "p_3", "p_12"]]
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


def serve(port: int = 8000) -> None:
    """Serve local artifacts; Vite proxies /api to this loopback listener."""
    uvicorn.run(create_app(), host="127.0.0.1", port=port)
