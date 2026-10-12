"""Join model outputs into hourly API rows without using future alert coverage."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from airalert import config
from airalert.backtest import assign_split
from airalert.schemas import ForecastResponse, ForecastRow, Report

SERVED_MODELS_KEY = b"airalert.served_models"


@dataclass(frozen=True)
class Paths:
    """Override artifact locations together when running the pipeline on synthetic data."""

    records: Path = config.RECORDS_PATH
    episodes: Path = config.EPISODES_PATH
    raions: Path = config.RAIONS_PATH
    snapshot: Path = config.SNAPSHOT_PATH
    preds: Path = config.PREDS_DIR
    features: Path = config.FEATURES_DIR
    selection: Path = config.SELECTION_PATH
    forecasts: Path = config.FORECASTS_PATH
    geojson: Path = config.RAIONS_GEOJSON_PATH
    metrics: Path = config.V2_METRICS_PATH
    v1_metrics: Path = config.METRICS_PATH
    crosswalk: Path = config.CROSSWALK_PATH
    live: Path = config.DATA_DIR / "live"
    issued: Path = config.V2_FORECASTS_DIR


@dataclass(frozen=True)
class ServedModels:
    onset: str
    duration: str | None


def response_from_frame(
    frame: pd.DataFrame, when: datetime, onset: str, duration: str | None, *, latest: bool,
) -> ForecastResponse:
    """Convert either a stored hour or production output; latest never carries truth labels."""
    values = frame.copy()
    if "t" in values:
        times = pd.to_datetime(values.pop("t"), utc=True)
        if not times.eq(pd.Timestamp(when)).all():
            raise ValueError("Forecast rows must match the requested hour")
    if set(values.columns) != set(ForecastRow.model_fields) or values.empty:
        raise ValueError("Forecast requires nonempty rows with exactly the ForecastRow fields")
    if values.raion_id.duplicated().any():
        raise ValueError("Duplicate forecast raion")
    if latest:
        for horizon in config.HORIZONS:
            values[f"actual_onset_{horizon}"] = None
    values = values.astype(object).where(values.notna(), None)
    rows = [ForecastRow.model_validate(row) for row in values.to_dict(orient="records")]
    for row in rows:
        if not 0 <= row.p_1 <= row.p_3 <= row.p_12 <= 1:
            raise ValueError("Forecast probabilities must be ordered and in [0, 1]")
        if row.active != (row.active_since is not None):
            raise ValueError("Only active forecasts require active_since")
        if row.active_since is not None and row.active_since >= when:
            raise ValueError("Active starts must be strictly before the forecast time")
        ends = [row.end_p10, row.end_p50, row.end_p90]
        present = [end for end in ends if end is not None]
        if present and (
            len(present) != 3 or row.active_since is None
            or not row.active_since <= present[0] <= present[1] <= present[2]
        ):
            raise ValueError("Duration quantiles require active coverage and ordered ends")
    return ForecastResponse(
        at=when, mode="latest" if latest else "replay", onset_model=onset,
        duration_model=duration, rows=rows,
    )


def _utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, format="mixed").astype("datetime64[us, UTC]")


def forecast_times(snapshot: pd.Timestamp) -> pd.DatetimeIndex:
    """Include every validation/test hour and the distinct snapshot hour."""
    test_end = snapshot - pd.Timedelta(hours=config.TEST_END_OFFSET_H)
    val = pd.date_range(config.VAL_START, config.TEST_START, freq="h", inclusive="left")
    test = pd.date_range(config.TEST_START, test_end, freq="h")
    return val.union(test).union(pd.DatetimeIndex([snapshot])).as_unit("us")


def select_models(paths: Paths) -> ServedModels:
    """Honor the selection fragment; missing selected predictions are an error."""
    selected = None
    if paths.selection.exists():
        selected = Report.model_validate_json(paths.selection.read_text(encoding="utf-8")).selection
        if selected is None:
            raise ValueError(f"No selection in {paths.selection}")
    onset = selected.onset if selected is not None else None
    if onset is None:
        onset = next(
            (model for model in ("M1", "B1", "B0")
             if (paths.preds / f"onset_{model}.parquet").exists()), None
        )
    if onset is None:
        raise FileNotFoundError("No onset predictions; train B0, B1 or M1 before precompute")
    duration = selected.duration if selected is not None else next(
        (model for model in ("D1", "D0")
         if (paths.preds / f"duration_{model}.parquet").exists()), None
    )
    for kind, model in (("onset", onset), ("duration", duration)):
        if model is not None and not (path := paths.preds / f"{kind}_{model}.parquet").exists():
            raise FileNotFoundError(f"Selected {kind} model {model} has no predictions: {path}")
    return ServedModels(onset, duration)


def active_state(records: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """Search only records starting before t, never full-data merged coverage.

    A running end tests real coverage, even inside a merge gap. The component
    start of that prefix is also the as-of merged start while coverage is active.
    Future ends can affect coverage only by being >t, exactly like a null as-of end.
    """
    ordered = records.copy()
    ordered["started_at"] = _utc(ordered.started_at)
    ordered["finished_at"] = _utc(ordered.finished_at)
    if ordered.started_at.isna().any() or ordered.finished_at.lt(ordered.started_at).any():
        raise ValueError("Records require starts and nonnegative durations")
    ordered = ordered.sort_values(["raion_id", "started_at"], kind="stable")
    groups = {key: group for key, group in ordered.groupby("raion_id", sort=False)}
    active = np.zeros(len(rows), dtype=bool)
    since = np.full(len(rows), np.datetime64("NaT", "us"))
    for raion_id, positions in rows.groupby("raion_id", sort=False).indices.items():
        group = groups.get(raion_id)
        if group is None or group.empty:
            continue
        starts = group.started_at.array.asi8
        ends = group.finished_at.array.asi8.copy()
        ends[group.finished_at.isna().to_numpy()] = np.iinfo(np.int64).max
        running_end = np.maximum.accumulate(ends)
        component = np.zeros(len(group), dtype=np.int64)
        component[1:] = np.where(
            starts[1:] - running_end[:-1] >= config.MERGE_GAP_MIN * 60 * 1_000_000,
            np.arange(1, len(group)), 0,
        )
        component = np.maximum.accumulate(component)
        times = rows.t.iloc[positions].array.asi8
        prefix = np.searchsorted(starts, times, side="left") - 1
        covered = (prefix >= 0) & (running_end[np.maximum(prefix, 0)] > times)
        locations = positions[covered]
        active[locations] = True
        since[locations] = starts[component[prefix[covered]]].astype("datetime64[us]")
    return pd.DataFrame({"active": active, "active_since": pd.to_datetime(since, utc=True)})


def _actual_onsets(rows: pd.DataFrame, episodes: pd.DataFrame, paths: Paths) -> pd.DataFrame:
    """Prefer published labels; episode starts supply labels before features arrive."""
    result = rows[["t", "raion_id"]].copy()
    grouped = {
        key: np.sort(_utc(group.start).array.asi8)
        for key, group in episodes.groupby("raion_id", sort=False)
    }
    for horizon in config.HORIZONS:
        labels = np.zeros(len(rows), dtype=bool)
        for raion_id, positions in rows.groupby("raion_id", sort=False).indices.items():
            starts = grouped.get(raion_id, np.empty(0, dtype=np.int64))
            times = rows.t.iloc[positions].array.asi8
            labels[positions] = (
                np.searchsorted(starts, times + horizon * 3_600_000_000, side="right")
                > np.searchsorted(starts, times, side="right")
            )
        result[f"actual_onset_{horizon}"] = pd.array(labels, dtype="boolean")
    for split in ("val", "test"):
        path = paths.features / f"{split}.parquet"
        if not path.exists():
            continue
        columns = ["t", "raion_id", *(f"y_{h}" for h in config.HORIZONS)]
        labels = pd.read_parquet(path, columns=columns)
        labels["t"] = _utc(labels.t)
        if labels.duplicated(["t", "raion_id"]).any():
            raise ValueError(f"Duplicate feature labels in {path}")
        subset = rows.loc[rows.split.eq(split), ["t", "raion_id"]]
        joined = subset.merge(labels, on=["t", "raion_id"], how="left", indicator=True)
        if joined._merge.ne("both").any():
            raise ValueError(f"Feature labels do not cover every {split} forecast")
        for horizon in config.HORIZONS:
            values = joined[f"y_{horizon}"]
            if not values.dropna().isin([0, 1]).all():
                raise ValueError(f"Invalid y_{horizon} in {path}")
            result.loc[subset.index, f"actual_onset_{horizon}"] = pd.array(values, dtype="boolean")
    for horizon in config.HORIZONS:
        unknown = rows.split.eq("latest")
        # Replay truth is unknown for intersecting windows. Corrupted lookback
        # hours are excluded by backtest, but can still have known replay truth.
        window_end = rows.t + pd.Timedelta(hours=horizon)
        for start, end in config.DATA_GAPS:
            unknown |= rows.t.lt(end) & window_end.ge(start)
        result.loc[unknown, f"actual_onset_{horizon}"] = pd.NA
    return result.drop(columns=["t", "raion_id"])


def validate_forecasts(
    rows: pd.DataFrame, raions: pd.DataFrame, snapshot: pd.Timestamp,
    *, expected_times: pd.DatetimeIndex | None = None,
) -> None:
    """Check complete hourly coverage and the UI invariants before publishing/serving."""
    expected_columns = {"t", *ForecastRow.model_fields}
    if set(rows.columns) != expected_columns:
        raise ValueError(f"Forecast columns must be exactly {sorted(expected_columns)}")
    if raions.empty or raions.raion_id.isna().any() or raions.raion_id.duplicated().any():
        raise ValueError("Raion universe must contain unique non-null IDs")
    if rows.duplicated(["t", "raion_id"]).any():
        raise ValueError("Duplicate forecast time/raion")
    times = forecast_times(snapshot) if expected_times is None else expected_times
    if set(rows.raion_id) != set(raions.raion_id) or rows.t.nunique() != len(times):
        raise ValueError("Forecasts do not cover the complete time/raion universe")
    if not rows.t.isin(times).all() or not rows.groupby("t").size().eq(len(raions)).all():
        raise ValueError("Every forecast hour must contain every universe raion exactly once")
    probabilities = rows[[f"p_{h}" for h in config.HORIZONS]].to_numpy(dtype=float)
    if not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
        raise ValueError("Forecast probabilities must be finite and in [0, 1]")
    if (np.diff(probabilities, axis=1) < 0).any():
        raise ValueError("Forecast probabilities must increase across horizons")
    if rows.active.isna().any() or not rows.active.isin([True, False]).all():
        raise ValueError("Forecast activity must be boolean")
    if not rows.active.eq(rows.active_since.notna()).all():
        raise ValueError("Only active forecasts require active_since")
    if (rows.active_since.ge(rows.t) & rows.active).any():
        raise ValueError("Active starts must be strictly before the forecast time")
    ends = rows[["end_p10", "end_p50", "end_p90"]]
    if ends.loc[~rows.active].notna().any().any():
        raise ValueError("Only active forecasts may have duration quantiles")
    if (ends.notna().sum(axis=1).isin([1, 2])).any():
        raise ValueError("Duration quantiles must be all present or all null")
    if (ends.end_p10.gt(ends.end_p50) | ends.end_p50.gt(ends.end_p90)).any():
        raise ValueError("Duration quantiles must be ordered")
    if ends.lt(rows.active_since, axis=0).any().any():
        raise ValueError("Duration ends cannot precede the alert start")
    actual = rows[[f"actual_onset_{h}" for h in config.HORIZONS]]
    if not actual.stack().dropna().isin([True, False]).all():
        raise ValueError("Actual onset labels must be boolean or null")
    if actual.loc[rows.t.eq(snapshot)].notna().any().any():
        raise ValueError("Latest forecasts cannot contain actual onset labels")
    for horizon in config.HORIZONS:
        for start, end in config.DATA_GAPS:
            overlap = rows.t.lt(end) & (rows.t + pd.Timedelta(hours=horizon)).ge(start)
            if rows.loc[overlap, f"actual_onset_{horizon}"].notna().any():
                raise ValueError("Gap-window actual onset labels must be null")


def run(paths: Paths | None = None) -> None:
    """Publish exact ForecastRow columns and persist served choices in Parquet metadata."""
    began = perf_counter()
    paths = Paths() if paths is None else paths
    if ((paths.preds / "v2" / "onset_selected.parquet").exists()
            or (paths.issued.is_relative_to(paths.preds)
                and any(paths.issued.glob("*.parquet")))):
        run_v2(paths)
        return
    metadata = json.loads(paths.snapshot.read_text(encoding="utf-8"))
    snapshot = pd.Timestamp(metadata["snapshot"]).as_unit("us")
    if snapshot.tzinfo is None or snapshot != snapshot.floor("h"):
        raise ValueError("Snapshot must be a timezone-aware hour")
    raions = pd.read_csv(paths.raions, dtype="str")
    models = select_models(paths)
    rows = pd.read_parquet(paths.preds / f"onset_{models.onset}.parquet")
    rows["t"] = _utc(rows.t)
    splits = assign_split(rows.t, snapshot=snapshot)
    if not rows.split.eq(splits).all():
        raise ValueError("Onset prediction splits disagree with forecast times")
    rows = rows.sort_values(["t", "raion_id"], ignore_index=True)
    probability_columns = [f"p_{h}" for h in config.HORIZONS]
    probabilities = rows[probability_columns].to_numpy(dtype=float)
    if not np.isfinite(probabilities).all():
        raise ValueError("Onset predictions must be finite")
    rows[probability_columns] = np.maximum.accumulate(np.clip(probabilities, 0, 1), axis=1)
    state = active_state(pd.read_parquet(paths.records), rows)
    rows[["active", "active_since"]] = state
    for column in ("end_p10", "end_p50", "end_p90"):
        rows[column] = pd.Series(pd.NaT, index=rows.index, dtype="datetime64[us, UTC]")
    if models.duration is not None:
        durations = pd.read_parquet(paths.preds / f"duration_{models.duration}.parquet")
        durations["start"] = _utc(durations.start)
        quantile_columns = ["q10_min", "q50_min", "q90_min"]
        quantiles = durations[quantile_columns].to_numpy(dtype=float)
        if not np.isfinite(quantiles).all() or (quantiles < 0).any():
            raise ValueError("Duration quantiles must be finite, nonnegative minutes")
        if (np.diff(quantiles, axis=1) < 0).any():
            raise ValueError("Duration prediction quantiles must be ordered")
        joined = rows[["raion_id", "active_since"]].merge(
            durations[["raion_id", "start", *quantile_columns]],
            left_on=["raion_id", "active_since"], right_on=["raion_id", "start"],
            how="left", validate="many_to_one",
        )
        for quantile in (10, 50, 90):
            rows[f"end_p{quantile}"] = _utc(
                rows.active_since + pd.to_timedelta(joined[f"q{quantile}_min"], unit="min")
            )
    actual = _actual_onsets(rows, pd.read_parquet(paths.episodes), paths)
    rows[actual.columns] = actual
    rows = rows[["t", *ForecastRow.model_fields]]
    validate_forecasts(rows, raions, snapshot)
    table = pa.Table.from_pandas(rows, preserve_index=False)
    table_metadata = dict(table.schema.metadata or {})
    table_metadata[SERVED_MODELS_KEY] = json.dumps({
        "onset": models.onset, "duration": models.duration,
        "snapshot": snapshot.isoformat(), "csv_sha256": metadata["csv_sha256"],
    }).encode("utf-8")
    paths.forecasts.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table.replace_schema_metadata(table_metadata), paths.forecasts)
    elapsed = perf_counter() - began
    print(f"Forecasts: {len(rows):,} rows; {len(raions)} raions; {rows.t.nunique():,} hours")
    print(f"Range: {rows.t.min().isoformat()} -> {rows.t.max().isoformat()}")
    print(f"Served models: onset={models.onset}; duration={models.duration or 'none'}")
    print(f"Wrote {paths.forecasts}; total {elapsed:.2f}s")


def _publish_v2(rows: pd.DataFrame, paths: Paths, metadata: dict[str, object]) -> None:
    """Replace a complete serving cache; immutable model/evaluation inputs stay untouched."""
    rows = rows[["t", *ForecastRow.model_fields]].sort_values(["t", "raion_id"], ignore_index=True)
    validate_forecasts(rows, pd.read_csv(paths.raions), pd.Timestamp(metadata["snapshot"]),
                       expected_times=pd.DatetimeIndex(rows.t.unique()))
    table = pa.Table.from_pandas(rows, preserve_index=False)
    tags = dict(table.schema.metadata or {})
    tags[SERVED_MODELS_KEY] = json.dumps(metadata).encode()
    paths.forecasts.parent.mkdir(parents=True, exist_ok=True)
    staging = paths.forecasts.with_suffix(".parquet.tmp")
    pq.write_table(table.replace_schema_metadata(tags), staging)
    staging.replace(paths.forecasts)


def _issued_frames(paths: Paths) -> list[pd.DataFrame]:
    """Serving repairs supersede legacy display rows, preserving original issued files."""
    files = {path.name: path for path in sorted(paths.issued.glob("*.parquet"))}
    for path in sorted(paths.issued.glob("corrections/*/*.parquet")):
        files[path.name] = path
    return [pd.read_parquet(path, columns=["t", *ForecastRow.model_fields])
            for _, path in sorted(files.items())]


def append_issued(paths: Paths) -> dict[str, object]:
    """Extend replay with first-issued hours, never overwrite their probabilities."""
    if not paths.forecasts.exists():
        return run_issued(paths)
    table = pq.read_table(paths.forecasts)
    metadata = json.loads(table.schema.metadata[SERVED_MODELS_KEY])
    if metadata.get("version") != "v2":
        return {"issued_hours": 0}
    rows = table.to_pandas()
    saved = json.loads(paths.snapshot.read_text(encoding="utf-8"))
    frames = _issued_frames(paths)
    if frames:
        issued = pd.concat(frames, ignore_index=True)
        rows = pd.concat([rows, issued], ignore_index=True).drop_duplicates(
            ["t", "raion_id"], keep="last")
        # Issued truth matures only against reconciled outcomes, never live polls.
        from airalert.labels import build_labels

        snapshot = pd.Timestamp(saved["snapshot"])
        labels = build_labels(pd.read_parquet(paths.episodes), pd.DatetimeIndex(issued.t.unique()),
                              snapshot, raions=pd.read_csv(paths.raions))
        labels = labels.set_index(["t", "raion_id"])
        keys = pd.MultiIndex.from_frame(rows[["t", "raion_id"]])
        for horizon in config.HORIZONS:
            values = labels[f"y_{horizon}"].reindex(keys)
            mask = keys.isin(labels.index)
            rows.loc[mask, f"actual_onset_{horizon}"] = pd.array(values[mask], dtype="boolean")
    metadata.update({"snapshot": saved["snapshot"], "csv_sha256": saved["csv_sha256"],
                     "latest": rows.t.max().isoformat()})
    _publish_v2(rows, paths, metadata)
    return {"issued_hours": len(frames), "rows": len(rows), "latest": metadata["latest"]}


def run_issued(paths: Paths) -> dict[str, object]:
    """Cold-start replay contains actual issued hours, with no backward model application."""
    frames = _issued_frames(paths)
    if not frames:
        raise FileNotFoundError("No issued forecasts; run uv run airalert setup")
    rows = pd.concat(frames, ignore_index=True)
    snapshot = json.loads(paths.snapshot.read_text(encoding="utf-8"))
    selection = json.loads(config.V2_SELECTION_PATH.read_text(encoding="utf-8"))
    # Split metadata describes the frozen evaluation; replay describes stored issuance.
    evaluation_end = (pd.Timestamp(selection["snapshot"]) - pd.Timedelta(
        hours=config.V2_TEST_END_OFFSET_H)).isoformat()
    metadata = {"version": "v2", "onset": "selected", "duration": "D2",
                "snapshot": snapshot["snapshot"], "csv_sha256": snapshot["csv_sha256"],
                "evaluation_end": evaluation_end, "replay_source": "issued",
                "latest": rows.t.max().isoformat()}
    _publish_v2(rows, paths, metadata)
    return {"historical": "issued only", **append_issued(paths)}


def run_v2(paths: Paths) -> dict[str, object]:
    """Build historical v2 replay once per input generation, then append issued hours."""
    from airalert.models.duration import predict_replay

    prediction_path = paths.preds / "v2" / "onset_selected.parquet"
    if not prediction_path.exists():
        if paths.forecasts.exists():
            tags = pq.read_schema(paths.forecasts).metadata or {}
            old = json.loads(tags.get(SERVED_MODELS_KEY, b"{}"))
            if old.get("version") == "v2":
                return {"historical": "reused", **append_issued(paths)}
        return run_issued(paths)
    snapshot = json.loads(paths.snapshot.read_text(encoding="utf-8"))
    signature = hashlib.sha256(json.dumps([
        snapshot, [(str(path.name), path.stat().st_size, path.stat().st_mtime_ns)
                   for path in (prediction_path, paths.records, paths.episodes,
                                paths.features / "v2" / "val.parquet",
                                paths.features / "v2" / "test2.parquet")]
    ], sort_keys=True).encode()).hexdigest()
    if paths.forecasts.exists():
        tags = pq.read_schema(paths.forecasts).metadata or {}
        old = json.loads(tags.get(SERVED_MODELS_KEY, b"{}"))
        if old.get("input_signature") == signature:
            return {"historical": "reused", **append_issued(paths)}
    rows = pd.read_parquet(prediction_path)
    rows = rows.loc[rows.split.isin(["val", "test", "test2"])].sort_values(
        ["t", "raion_id"], ignore_index=True)
    rows["t"] = _utc(rows.t)
    expected = pd.date_range(config.V2_VAL_START, rows.t.max(), freq="h").as_unit("us")
    records, raions = pd.read_parquet(paths.records), pd.read_csv(paths.raions)
    probabilities = rows[[f"p_{h}" for h in config.HORIZONS]].to_numpy()
    rows[[f"p_{h}" for h in config.HORIZONS]] = np.maximum.accumulate(probabilities, axis=1)
    rows[["active", "active_since"]] = active_state(records, rows)
    for column in ("end_p10", "end_p50", "end_p90"):
        rows[column] = pd.Series(pd.NaT, index=rows.index, dtype="datetime64[us, UTC]")
    active = rows.loc[rows.active]
    if not active.empty:
        ends = predict_replay(active, records, raions)
        for quantile in (10, 50, 90):
            rows.loc[active.index, f"end_p{quantile}"] = ends[f"end_p{quantile}"].to_numpy()
    for split, filename in (("val", "val"), ("test", "test2"), ("test2", "test2")):
        mask = rows.split.eq(split)
        if not mask.any():
            continue
        labels = pd.read_parquet(paths.features / "v2" / f"{filename}.parquet",
                                 columns=["t", "raion_id", *(f"y_{h}" for h in config.HORIZONS)])
        joined = rows.loc[mask, ["t", "raion_id"]].merge(labels, how="left",
                          on=["t", "raion_id"], validate="one_to_one", indicator=True)
        if joined._merge.ne("both").any():
            raise ValueError("V2 labels must cover every replay key")
        for horizon in config.HORIZONS:
            rows.loc[mask, f"actual_onset_{horizon}"] = pd.array(joined[f"y_{horizon}"],
                                                               dtype="boolean")
    rows = rows[["t", *ForecastRow.model_fields]]
    validate_forecasts(rows, raions, pd.Timestamp(snapshot["snapshot"]), expected_times=expected)
    metadata = {"version": "v2", "onset": "selected", "duration": "D2",
                "snapshot": snapshot["snapshot"], "csv_sha256": snapshot["csv_sha256"],
                "evaluation_end": rows.t.max().isoformat(), "input_signature": signature,
                "latest": rows.t.max().isoformat()}
    _publish_v2(rows, paths, metadata)
    result = {"historical": "rebuilt", **append_issued(paths)}
    print(f"V2 replay: {result}", flush=True)
    return result
