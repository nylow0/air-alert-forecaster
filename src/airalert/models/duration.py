"""Episode-start duration models; records supply features, episodes supply targets.

Unlike hourly onset features, duration features include records starting exactly
at s. Public split helpers are shared with the backtest duration scorer.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from time import monotonic, perf_counter, sleep
from typing import Literal

import lightgbm as lgb
import numpy as np
import pandas as pd
from numpy.typing import NDArray

from airalert import config
from airalert.config import (
    ARTIFACTS_DIR,
    DATA_GAPS,
    DURATION_PREDS_PATH_TEMPLATE,
    KYIV_TZ,
    MERGE_GAP_MIN,
    PREDS_DIR,
    TEST_END_OFFSET_H,
    TEST_START,
    TRAIN_START,
    VAL_START,
)
from airalert.data import load_episodes, load_raions, load_records, load_snapshot
from airalert.schemas import DurationScore, Report

QUANTILES = (0.1, 0.5, 0.9)
PREDICTION_COLUMNS = ("q10_min", "q50_min", "q90_min")
EpisodeSplit = Literal["train", "val", "test", "latest"]


def episode_split(episodes: pd.DataFrame, snapshot: pd.Timestamp) -> pd.Series:
    """Assign every prediction by its start, including unscorable episodes."""
    starts = pd.to_datetime(episodes.start, utc=True)
    test_end = snapshot - pd.Timedelta(hours=TEST_END_OFFSET_H)
    return pd.Series(
        np.select(
            [starts.lt(VAL_START), starts.lt(TEST_START), starts.le(test_end)],
            ["train", "val", "test"],
            default="latest",
        ),
        index=episodes.index,
        name="split",
    )


def episode_split_mask(
    episodes: pd.DataFrame, split: EpisodeSplit, snapshot: pd.Timestamp | None = None
) -> pd.Series:
    """Select observed targets with split-contained ends and no source-gap overlap.

    Train and val require end strictly before the next split. Test may end after
    its start window, but must be observed by the saved snapshot. Predictions do
    not use this mask, so censored/gap episodes remain available to replay.
    """
    snapshot = load_snapshot().snapshot if snapshot is None else snapshot
    starts = pd.to_datetime(episodes.start, utc=True)
    ends = pd.to_datetime(episodes.end, utc=True)
    mask = (
        episode_split(episodes, snapshot).eq(split)
        & starts.ge(TRAIN_START)
        & ends.notna()
        & ~episodes.censored
        & ends.le(snapshot)
    )
    if split == "train":
        mask &= ends.lt(VAL_START)
    elif split == "val":
        mask &= ends.lt(TEST_START)
    for gap_start, gap_end in DATA_GAPS:
        # Point episodes at gap.start also belong to the missing-data interval.
        mask &= ~(starts.lt(gap_end) & (ends.gt(gap_start) | starts.ge(gap_start)))
    return mask


def duration_minutes(episodes: pd.DataFrame) -> NDArray[np.float64]:
    """Use at least one minute for every observed target before taking logs."""
    minutes = (episodes.end - episodes.start).dt.total_seconds().to_numpy() / 60
    return np.maximum(minutes, 1.0)


def _times(values: pd.Series) -> NDArray[np.int64]:
    return pd.to_datetime(values, utc=True).astype("datetime64[us, UTC]").array.asi8


def _recent_history(
    starts: NDArray[np.int64],
    ends: NDArray[np.int64],
    queries: NDArray[np.int64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Query the last five ended prefix episodes without closing future merge gaps.

    Earlier groups have immutable ends. For the current group, replace its full
    end with the prefix maximum at s. This retains a provisional ended episode
    inside a short gap even if a later record will eventually extend it.
    """
    maximum_ends = np.maximum.accumulate(ends)
    new_group = np.ones(len(starts), dtype=bool)
    new_group[1:] = starts[1:] - maximum_ends[:-1] >= MERGE_GAP_MIN * 60_000_000
    first_indices = np.flatnonzero(new_group)
    group_starts = starts[first_indices]
    group_ends = np.maximum.reduceat(ends, first_indices)
    groups = np.cumsum(new_group) - 1
    positions = np.searchsorted(starts, queries, side="right") - 1
    safe_positions = np.maximum(positions, 0)
    current_groups = groups[safe_positions]
    prefix_ends = maximum_ends[safe_positions]
    ended = (positions >= 0) & (prefix_ends <= queries)
    last_ended = np.where(positions >= 0, current_groups - ~ended, -1)
    history_indices = last_ended[:, None] - np.arange(5)
    valid = history_indices >= 0
    safe_history = np.maximum(history_indices, 0)
    history_ends = group_ends[safe_history].copy()
    history_ends[ended, 0] = prefix_ends[ended]
    history_durations = np.maximum(
        (history_ends - group_starts[safe_history]) / 60_000_000, 1.0
    )
    history_durations[~valid] = np.nan
    median = np.full(len(queries), np.nan)
    available = last_ended >= 0
    median[available] = np.nanmedian(history_durations[available], axis=1)
    since_end = np.full(len(queries), np.nan)
    since_end[available] = (queries[available] - history_ends[available, 0]) / 60_000_000
    return median, since_end


def build_start_features(
    records: pd.DataFrame,
    episodes: pd.DataFrame,
    raions: pd.DataFrame,
    regime_switch: pd.Timestamp,
) -> pd.DataFrame:
    """Build vectorised as-of-s features using only episode keys and raw coverage.

    Active/recent-start counts count distinct raions. Ends after s contribute
    only an active flag. Categories use the fixed geographic universe; target
    ends and full-data merged episode boundaries are never read here.
    """
    rows = episodes[["raion_id", "oblast_id", "start"]].reset_index(drop=True)
    query_times = _times(rows.start)
    unique_times, time_indices = np.unique(query_times, return_inverse=True)
    local = rows.start.dt.tz_convert(KYIV_TZ)
    result = pd.DataFrame(index=rows.index)
    for column in ("raion_id", "oblast_id"):
        result[column] = pd.Categorical(rows[column], categories=sorted(raions[column].unique()))
    result["kyiv_hour"] = local.dt.hour.to_numpy()
    result["kyiv_weekday"] = local.dt.weekday.to_numpy()
    result["hour_bucket"] = result.kyiv_hour // 6
    result["raion_regime"] = rows.start.ge(regime_switch).to_numpy(dtype=np.float32)
    unique_records = records.drop_duplicates(
        ["raion_id", "level", "started_at", "finished_at"]
    ).sort_values(["raion_id", "started_at"], kind="stable")
    # Starting-level tie rule matches A1; only equal-start records can contribute.
    levels = unique_records.assign(
        priority=unique_records.level.map({"hromada": 0, "raion": 1, "oblast": 2})
    ).groupby(["raion_id", "started_at"], sort=False).priority.min()
    keys = pd.MultiIndex.from_arrays([rows.raion_id, rows.start])
    source = levels.reindex(keys).map({0: "hromada", 1: "raion", 2: "oblast"})
    result["source_level"] = pd.Categorical(
        source.to_numpy(), categories=["hromada", "raion", "oblast"]
    )
    oblasts = sorted(raions.oblast_id.unique())
    oblast_lookup = {name: index for index, name in enumerate(oblasts)}
    universe = raions.set_index("raion_id")
    shape = (len(unique_times), len(oblasts))
    active = np.zeros(shape, dtype=np.int16)
    recent_15 = np.zeros(shape, dtype=np.int16)
    recent_60 = np.zeros(shape, dtype=np.int16)
    median = np.full(len(rows), np.nan)
    since_end = np.full(len(rows), np.nan)
    row_groups = rows.groupby("raion_id", sort=False).indices
    # One loop over 119 geographic units; every timestamp query is an array.
    for raion_id, coverage in unique_records.groupby("raion_id", sort=False):
        starts = _times(coverage.started_at)
        ends = _times(coverage.finished_at).copy()
        ends[coverage.finished_at.isna().to_numpy()] = np.iinfo(np.int64).max
        prefix_ends = np.maximum.accumulate(ends)
        positions = np.searchsorted(starts, unique_times, side="right") - 1
        safe_positions = np.maximum(positions, 0)
        exists = positions >= 0
        oblast_index = oblast_lookup[universe.loc[raion_id, "oblast_id"]]
        active[:, oblast_index] += exists & (prefix_ends[safe_positions] > unique_times)
        elapsed = unique_times - starts[safe_positions]
        recent_15[:, oblast_index] += exists & (elapsed <= 15 * 60_000_000)
        recent_60[:, oblast_index] += exists & (elapsed <= 60 * 60_000_000)
        if raion_id in row_groups:
            indices = row_groups[raion_id]
            median[indices], since_end[indices] = _recent_history(
                starts, ends, query_times[indices]
            )
    oblast_indices = rows.oblast_id.map(oblast_lookup).to_numpy(dtype=np.int64)
    oblast_sizes = raions.groupby("oblast_id").size().reindex(oblasts).to_numpy()
    for name, values in (("active", active), ("starts_15m", recent_15), ("starts_60m", recent_60)):
        result[f"national_{name}"] = values.sum(axis=1)[time_indices]
        result[f"oblast_{name}"] = values[time_indices, oblast_indices]
    result["oblast_active_share"] = result.oblast_active / oblast_sizes[oblast_indices]
    result["recent_median_duration_min"] = median
    result["minutes_since_previous_end"] = since_end
    for column in result.select_dtypes(include="number").columns:
        result[column] = result[column].astype(np.float32)
    return result


@dataclass
class EmpiricalDuration:
    """Training-only empirical cells, ordered from coarse to specific."""

    global_quantiles: NDArray[np.float64]
    cells: list[tuple[list[str], pd.DataFrame]]

    @classmethod
    def fit(
        cls, features: pd.DataFrame, minutes: NDArray[np.float64], minimum_cell: int = 30
    ) -> "EmpiricalDuration":
        data = features[["oblast_id", "hour_bucket", "raion_regime"]].copy()
        data["duration"] = minutes
        cells: list[tuple[list[str], pd.DataFrame]] = []
        for keys in (
            ["raion_regime"],
            ["oblast_id", "raion_regime"],
            ["oblast_id", "hour_bucket", "raion_regime"],
        ):
            grouped = data.groupby(keys, observed=True).duration
            table = grouped.quantile(QUANTILES).unstack(level=-1)
            table.columns = list(PREDICTION_COLUMNS)
            table["n"] = grouped.size()
            cells.append((keys, table.loc[table.n.ge(minimum_cell)].reset_index()))
        return cls(np.quantile(minutes, QUANTILES), cells)

    def predict(self, features: pd.DataFrame) -> NDArray[np.float64]:
        predictions = np.tile(self.global_quantiles, (len(features), 1))
        for keys, table in self.cells:
            joined = features[keys].merge(table, on=keys, how="left", validate="many_to_one")
            available = joined.n.notna().to_numpy()
            predictions[available] = joined.loc[available, list(PREDICTION_COLUMNS)].to_numpy()
        return np.sort(np.maximum(predictions, 1.0), axis=1)


def score_validation(
    model_id: str, minutes: NDArray[np.float64], predictions: NDArray[np.float64]
) -> DurationScore:
    """Local validation scorer; deliberately has no test split argument."""
    errors = minutes[:, None] - predictions
    alpha = np.asarray(QUANTILES)
    pinball = np.maximum(alpha * errors, (alpha - 1) * errors).mean(axis=0)
    return DurationScore(
        model=model_id,
        split="val",
        n=len(minutes),
        pinball_q10=float(pinball[0]),
        pinball_q50=float(pinball[1]),
        pinball_q90=float(pinball[2]),
        mae_q50_min=float(np.abs(errors[:, 1]).mean()),
        coverage_80=float(((minutes >= predictions[:, 0]) & (minutes <= predictions[:, 2])).mean()),
    )


def _quantile_metric(
    predictions: NDArray[np.float64], dataset: lgb.Dataset, *, alpha: float
) -> tuple[str, float, bool]:
    """Stop boosting on validation pinball in minutes, the reported unit."""
    actual = np.exp(dataset.get_label())
    errors = actual - np.maximum(np.exp(predictions), 1.0)
    loss = np.maximum(alpha * errors, (alpha - 1) * errors)
    return "pinball_min", float(loss.mean()), False


def _predict_boosters(
    models: list[lgb.Booster], features: pd.DataFrame
) -> NDArray[np.float64]:
    log_predictions = np.column_stack([
        model.predict(features, num_threads=4) for model in models
    ])
    return np.sort(np.maximum(np.exp(log_predictions), 1.0), axis=1)


def _fit_lightgbm(
    features: pd.DataFrame,
    episodes: pd.DataFrame,
    train_mask: pd.Series,
    val_mask: pd.Series,
    regime_switch: pd.Timestamp,
) -> tuple[list[lgb.Booster], dict[str, object]]:
    minutes_val = duration_minutes(episodes.loc[val_mask])
    validation_features = features.loc[val_mask]
    candidates: list[dict[str, object]] = []
    best_loss = np.inf
    best_models: list[lgb.Booster] = []
    best_candidate: dict[str, object] = {}
    for window in ("full", "raion-regime"):
        mask = train_mask.copy()
        if window == "raion-regime":
            mask &= episodes.start.ge(regime_switch)
        training = lgb.Dataset(
            features.loc[mask], label=np.log(duration_minutes(episodes.loc[mask])),
            categorical_feature=["raion_id", "oblast_id", "source_level"],
            params={"feature_pre_filter": False},
        )
        validation = lgb.Dataset(
            validation_features, label=np.log(minutes_val), reference=training
        )
        for leaves, minimum_leaf in ((15, 80), (31, 150)):
            models = [
                lgb.train(
                    {
                        "objective": "quantile", "alpha": alpha, "metric": "None",
                        "learning_rate": 0.05, "num_leaves": leaves,
                        "min_data_in_leaf": minimum_leaf, "feature_pre_filter": False,
                        "lambda_l2": 1.0, "num_threads": 4, "seed": 42,
                        "deterministic": True, "force_col_wise": True, "verbosity": -1,
                    },
                    training, num_boost_round=350, valid_sets=[validation],
                    feval=partial(_quantile_metric, alpha=alpha),
                    callbacks=[lgb.early_stopping(35, verbose=False)],
                )
                for alpha in QUANTILES
            ]
            scores = score_validation(
                "D1", minutes_val, _predict_boosters(models, validation_features)
            )
            loss = (scores.pinball_q10 + scores.pinball_q50 + scores.pinball_q90) / 3
            candidate: dict[str, object] = {
                "window": window, "train_n": int(mask.sum()), "num_leaves": leaves,
                "min_data_in_leaf": minimum_leaf,
                "best_iterations": [model.best_iteration for model in models],
                "mean_pinball_min": loss, "validation": scores.model_dump(),
            }
            candidates.append(candidate)
            print(
                f"D1 candidate {window}, leaves={leaves}, train={mask.sum():,}: "
                f"mean pinball={loss:.4f}; iterations={candidate['best_iterations']}",
                flush=True,
            )
            if loss < best_loss:
                best_loss, best_models, best_candidate = loss, models, candidate
    return best_models, {"selected": best_candidate, "candidates": candidates}


def train(model_id: str) -> None:
    """Fit D0/D1 on eligible training episodes and publish all replay predictions."""
    if model_id not in {"D0", "D1"}:
        raise ValueError(f"Unknown duration model: {model_id}")
    began = perf_counter()
    snapshot = load_snapshot()
    episodes = load_episodes()
    train_mask = episode_split_mask(episodes, "train", snapshot.snapshot)
    val_mask = episode_split_mask(episodes, "val", snapshot.snapshot)
    if not train_mask.any() or not val_mask.any():
        raise ValueError("Duration fitting needs eligible train and val episodes")
    print(
        f"{model_id}: train episodes={train_mask.sum():,}; val episodes={val_mask.sum():,}; "
        f"raion-regime train={(train_mask & episodes.start.ge(snapshot.regime_switch)).sum():,}",
        flush=True,
    )
    features = build_start_features(load_records(), episodes, load_raions(), snapshot.regime_switch)
    print(
        f"Start-time features: {len(features):,} rows in {perf_counter() - began:.2f}s",
        flush=True,
    )
    artifact_dir = ARTIFACTS_DIR / model_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if model_id == "D0":
        model = EmpiricalDuration.fit(
            features.loc[train_mask], duration_minutes(episodes.loc[train_mask])
        )
        predictions = model.predict(features)
        for index, (_keys, table) in enumerate(model.cells):
            table.to_parquet(artifact_dir / f"cells_{index}.parquet", index=False)
        fit_details: dict[str, object] = {
            "minimum_cell": 30, "global_quantiles": model.global_quantiles.tolist(),
            "cell_keys": [keys for keys, _ in model.cells],
        }
    else:
        models, fit_details = _fit_lightgbm(
            features, episodes, train_mask, val_mask, snapshot.regime_switch
        )
        predictions = _predict_boosters(models, features)
        for column, booster in zip(PREDICTION_COLUMNS, models, strict=True):
            booster.save_model(str(artifact_dir / f"{column}.txt"))
    if not np.isfinite(predictions).all():
        raise ValueError("Duration predictions contain non-finite values")
    scores = score_validation(
        model_id, duration_minutes(episodes.loc[val_mask]), predictions[val_mask.to_numpy()]
    )
    metadata = {
        "model": model_id, "snapshot": snapshot.snapshot.isoformat().replace("+00:00", "Z"),
        "csv_sha256": snapshot.csv_sha256,
        "regime_switch": snapshot.regime_switch.date().isoformat(),
        "train_n": int(train_mask.sum()), "val_n": int(val_mask.sum()),
        "feature_columns": features.columns.tolist(), "fit": fit_details,
        "validation": scores.model_dump(),
    }
    (artifact_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    output = episodes[["raion_id", "start"]].copy()
    output["split"] = episode_split(episodes, snapshot.snapshot)
    output[list(PREDICTION_COLUMNS)] = predictions
    output = output.loc[episodes.start.ge(pd.Timestamp(VAL_START) - pd.Timedelta(days=7))]
    PREDS_DIR.mkdir(parents=True, exist_ok=True)
    prediction_path = str(DURATION_PREDS_PATH_TEMPLATE).format(model_id=model_id)
    output.to_parquet(prediction_path, index=False)
    print(
        f"{model_id} val n={scores.n:,}: pinball q10={scores.pinball_q10:.4f}, "
        f"q50={scores.pinball_q50:.4f}, q90={scores.pinball_q90:.4f}; "
        f"median MAE={scores.mae_q50_min:.4f} min; 80% coverage={scores.coverage_80:.4%}",
        flush=True,
    )
    print(f"Wrote {len(output):,} predictions to {prediction_path}; {perf_counter() - began:.2f}s")


# v2 stays in this module so the v1 CLI/backtest helpers retain their contracts.
CHECKPOINTS = (0, 15, 30, 60, 120, 240, 480)
CALIBRATION_DAYS = getattr(config, "V2_CALIBRATION_WEEKS", 4) * 7
V2_PREDICTIONS_DIR = PREDS_DIR / "duration_v2"
PRODUCTION_DIR = ARTIFACTS_DIR / "production" / "duration"


def _v2_boundaries() -> tuple[pd.Timestamp, pd.Timestamp]:
    """Read lead-owned constants at call time, with the brief's temporary defaults."""
    return (
        pd.Timestamp(getattr(config, "V2_VAL_START", "2026-08-01T00:00:00Z")),
        pd.Timestamp(getattr(config, "V2_TEST_START", "2026-09-08T00:00:00Z")),
    )


def _utc_time(value: datetime | pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("Duration prediction requires a timezone-aware time")
    return result.tz_convert("UTC").as_unit("us")


def _eligible_window(
    episodes: pd.DataFrame, lower: pd.Timestamp, upper: pd.Timestamp,
    *, observed_through: pd.Timestamp,
    gaps: tuple[tuple[datetime, datetime], ...] | None = None,
) -> pd.Series:
    """Require all targets to have ended before the next chronological window."""
    mask = (
        episodes.start.ge(lower) & episodes.start.lt(upper)
        & episodes.end.notna() & ~episodes.censored
        & episodes.end.lt(upper) & episodes.end.le(observed_through)
        & episodes.end.ge(episodes.start)
    )
    for gap_start, gap_end in _v2_data_gaps() if gaps is None else gaps:
        mask &= ~(
            episodes.start.lt(gap_end)
            & (episodes.end.gt(gap_start) | episodes.start.ge(gap_start))
        )
    return mask


def _v2_data_gaps() -> tuple[tuple[datetime, datetime], ...]:
    """Only an accepted canonical archive may replace the frozen CSV outage mask."""
    snapshot = json.loads(config.SNAPSHOT_PATH.read_text(encoding="utf-8"))
    coverage = snapshot.get("coverage", {})
    if coverage.get("archive_gate_passed") is True and "gaps" in coverage:
        return tuple((pd.Timestamp(gap["start"]).to_pydatetime(),
                      pd.Timestamp(gap["end"]).to_pydatetime())
                     for gap in coverage["gaps"])
    return config.DATA_GAPS


def _available_features(
    records: pd.DataFrame, rows: pd.DataFrame, raions: pd.DataFrame,
    regime_switch: pd.Timestamp,
) -> pd.DataFrame:
    """Also honor live observation availability, separately from event time.

    Historical CSV has no observation clock. Live queries are batched between
    observation times; within each batch the available record set is constant.
    """
    if "observed_at" not in records or records.observed_at.isna().all():
        return build_start_features(records, rows, raions, regime_switch)
    observations = pd.to_datetime(records.observed_at, utc=True)
    times = np.unique(_times(observations.dropna()))
    segments = np.searchsorted(times, _times(rows.start), side="left")
    parts = []
    for segment in np.unique(segments):
        indices = np.flatnonzero(segments == segment)
        boundary = rows.start.iloc[indices].min()
        available = records.loc[observations.isna() | observations.lt(boundary)]
        part = build_start_features(available, rows.iloc[indices], raions, regime_switch)
        part.index = indices
        parts.append(part)
    return pd.concat(parts).sort_index() if parts else build_start_features(
        records.iloc[:0], rows, raions, regime_switch
    )


def build_landmark_features(
    records: pd.DataFrame, rows: pd.DataFrame, raions: pd.DataFrame,
    regime_switch: pd.Timestamp,
) -> pd.DataFrame:
    """Keys are raion_id, start, checkpoint; no target end is inspected.

    Onset context is fixed at start. Checkpoint context includes starts <= c,
    treats ends > c as unknown, and uses raw coverage rather than merge gaps.
    Availability-tagged live records must also have been observed by the query.
    """
    keys = rows[["raion_id", "start", "checkpoint"]].reset_index(drop=True).copy()
    keys = keys.merge(raions[["raion_id", "oblast_id"]], on="raion_id", how="left",
                      validate="many_to_one")
    if keys.oblast_id.isna().any():
        raise ValueError("Duration rows contain raions outside the forecast universe")
    elapsed = (keys.checkpoint - keys.start).dt.total_seconds().to_numpy() / 60
    if (elapsed < 0).any() or not np.isfinite(elapsed).all():
        raise ValueError("Active episode starts must be known and no later than now")
    result = _available_features(records, keys, raions, regime_switch)
    current_rows = keys[["raion_id", "oblast_id"]].assign(start=keys.checkpoint)
    current = _available_features(records, current_rows, raions, regime_switch)
    context = [column for column in current.select_dtypes(include="number")
               if column.startswith(("national_", "oblast_"))]
    for column in context:
        result[f"now_{column}"] = current[column].to_numpy()
    result["elapsed_min"] = elapsed.astype(np.float32)
    result["log_elapsed"] = np.log1p(elapsed).astype(np.float32)
    result["oblast_active_change"] = current.oblast_active - result.oblast_active
    result["national_active_change"] = current.national_active - result.national_active
    for column in result.select_dtypes(include="number"):
        result[column] = result[column].astype(np.float32)
    return result


def _raw_active(records: pd.DataFrame, rows: pd.DataFrame) -> NDArray[np.bool_]:
    """Exclude training landmarks inside absorbed merge gaps, using raw coverage."""
    active = np.zeros(len(rows), dtype=bool)
    row_groups = rows.groupby("raion_id", sort=False).indices
    for raion_id, coverage in records.groupby("raion_id", sort=False):
        if raion_id not in row_groups:
            continue
        coverage = coverage.sort_values("started_at", kind="stable")
        starts = _times(coverage.started_at)
        ends = _times(coverage.finished_at).copy()
        ends[coverage.finished_at.isna().to_numpy()] = np.iinfo(np.int64).max
        indices = row_groups[raion_id]
        queries = _times(rows.checkpoint.iloc[indices])
        positions = np.searchsorted(starts, queries, side="right") - 1
        active[indices] = (positions >= 0) & (
            np.maximum.accumulate(ends)[np.maximum(positions, 0)] > queries
        )
    return active


def landmark_rows(episodes: pd.DataFrame, records: pd.DataFrame) -> pd.DataFrame:
    """One row per surviving episode/checkpoint, with a remaining-minute target.

    Zero-length episodes contribute only the onset, clipped to one minute.
    Longer checkpoints require actual survival and active raw coverage at c.
    """
    parts = []
    for elapsed in CHECKPOINTS:
        checkpoint = episodes.start + pd.Timedelta(minutes=elapsed)
        mask = episodes.end.gt(checkpoint) | ((elapsed == 0) & episodes.end.eq(checkpoint))
        part = episodes.loc[mask, ["raion_id", "start", "end"]].copy()
        part["checkpoint"] = checkpoint.loc[mask]
        part["elapsed_min"] = elapsed
        part["remaining_min"] = np.maximum(
            (part.end - part.checkpoint).dt.total_seconds().to_numpy() / 60, 1.0
        )
        parts.append(part)
    result = pd.concat(parts, ignore_index=True)
    keep = _raw_active(records, result) | result.elapsed_min.eq(0).to_numpy()
    return result.loc[keep].reset_index(drop=True)


@dataclass
class ConformalDuration:
    """Chronological CQR with a finite-sample rank and widening-only offsets."""

    offset_min: float
    median_offset_min: float
    n: int
    calibration_start: str
    evaluation_start: str
    last_target_end: str

    @classmethod
    def fit(
        cls, episodes: pd.DataFrame, actual: NDArray[np.float64],
        predictions: NDArray[np.float64], *, model_fit_end: pd.Timestamp,
        calibration_start: pd.Timestamp, evaluation_start: pd.Timestamp,
        calibrate_median: bool = False,
    ) -> "ConformalDuration":
        if model_fit_end > calibration_start or calibration_start >= evaluation_start:
            raise ValueError("Model fitting must precede calibration and evaluation")
        eligible = _eligible_window(
            episodes, calibration_start, evaluation_start, observed_through=evaluation_start
        ).to_numpy()
        y, q = actual[eligible], predictions[eligible]
        if not len(y) or not np.isfinite(y).all() or not np.isfinite(q).all():
            raise ValueError("Conformal fitting needs finite earlier calibration targets")
        scores = np.maximum(q[:, 0] - y, y - q[:, 2])
        rank = int(np.ceil(0.8 * (len(scores) + 1)))
        if rank > len(scores):
            raise ValueError("At least four calibration targets are needed for 80% CQR")
        offset = max(0.0, float(np.partition(scores, rank - 1)[rank - 1]))
        median_offset = float(np.median(y - q[:, 1])) if calibrate_median else 0.0
        return cls(
            offset, median_offset, len(y), calibration_start.isoformat(),
            evaluation_start.isoformat(), episodes.loc[eligible, "end"].max().isoformat(),
        )

    def predict(self, predictions: NDArray[np.float64]) -> NDArray[np.float64]:
        result = np.sort(np.maximum(predictions, 1.0), axis=1)
        result[:, 0] = np.maximum(result[:, 0] - self.offset_min, 1.0)
        result[:, 2] += self.offset_min
        result[:, 1] = np.clip(
            result[:, 1] + self.median_offset_min, result[:, 0], result[:, 2]
        )
        return result


def _fit_fixed(
    features: pd.DataFrame, target: NDArray[np.float64],
    *, quantiles: tuple[float, ...] = QUANTILES,
) -> list[lgb.Booster]:
    """Reuse v1's selected tree size and fixed rounds; never tune on v2 val/test2."""
    if not len(features):
        raise ValueError("Duration fitting needs ended training episodes")
    training = lgb.Dataset(
        features, label=np.log(np.maximum(target, 1.0)),
        categorical_feature=["raion_id", "oblast_id", "source_level"],
    )
    return [lgb.train(
        {
            "objective": "quantile", "alpha": alpha, "metric": "None",
            "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 80,
            "lambda_l2": 1.0, "num_threads": 4, "seed": 42,
            "deterministic": True, "force_col_wise": True, "verbosity": -1,
        }, training, num_boost_round=180,
    ) for alpha in quantiles]


def _save_bundle(
    directory: Path, models: list[lgb.Booster],
    calibrations: dict[str, ConformalDuration], metadata: dict[str, object],
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for column, model in zip(PREDICTION_COLUMNS, models, strict=True):
        model.save_model(str(directory / f"{column}.txt"))
    payload = {
        **metadata,
        "calibrations": {key: vars(value) for key, value in calibrations.items()},
    }
    temporary = directory / "metadata.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(directory / "metadata.json")


def _load_bundle(
    directory: Path,
) -> tuple[list[lgb.Booster], dict[str, ConformalDuration], dict[str, object]]:
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    models = [lgb.Booster(model_file=str(directory / f"{column}.txt"))
              for column in PREDICTION_COLUMNS]
    calibrations = {key: ConformalDuration(**value)
                    for key, value in metadata["calibrations"].items()}
    return models, calibrations, metadata


def _calibrate_landmarks(
    rows: pd.DataFrame, raw: NDArray[np.float64], *, fit_end: pd.Timestamp,
    calibration_start: pd.Timestamp, evaluation_start: pd.Timestamp,
) -> dict[str, ConformalDuration]:
    calibrations = {}
    for elapsed in CHECKPOINTS:
        mask = rows.elapsed_min.eq(elapsed).to_numpy()
        episodes = rows.loc[mask].assign(censored=False)
        calibrations[str(elapsed)] = ConformalDuration.fit(
            episodes, rows.remaining_min.to_numpy()[mask], raw[mask],
            model_fit_end=fit_end, calibration_start=calibration_start,
            evaluation_start=evaluation_start, calibrate_median=True,
        )
    return calibrations


def _apply_landmark_calibration(
    raw: NDArray[np.float64], elapsed: NDArray[np.float64],
    calibrations: dict[str, ConformalDuration],
) -> NDArray[np.float64]:
    # Serving arbitrary elapsed times uses the preceding trained landmark.
    buckets = np.searchsorted(np.asarray(CHECKPOINTS), elapsed, side="right") - 1
    result = raw.copy()
    for index in np.unique(buckets):
        mask = buckets == index
        result[mask] = calibrations[str(CHECKPOINTS[index])].predict(raw[mask])
    return result


def _v2_score(
    model_id: str, split: Literal["val", "test"], actual: NDArray[np.float64],
    predictions: NDArray[np.float64],
) -> tuple[DurationScore, dict[str, object]]:
    score = score_validation(model_id, actual, predictions).model_copy(update={"split": split})
    detail = {
        **score.model_dump(),
        "mean_width_min": float(np.mean(predictions[:, 2] - predictions[:, 0])),
        "p50_below_fraction": float(np.mean(actual <= predictions[:, 1])),
        "p50_signed_median_error_min": float(np.median(predictions[:, 1] - actual)),
        "p50_mean_error_min": float(np.mean(predictions[:, 1] - actual)),
    }
    return score, detail


def evaluate_v2(*, final: bool = False, reuse_models: bool = False) -> Report:
    """Fit/evaluate v2 validation, or score available test2 exactly once with --final.

    Test2 keeps the pre-validation boosters and recalibrates only on validation.
    Prediction files live in a subdirectory so v1 backtest never rescans them.
    """
    val_start, test_start = _v2_boundaries()
    cal_start = val_start - pd.Timedelta(days=CALIBRATION_DAYS)
    snapshot = load_snapshot()
    directory = ARTIFACTS_DIR / "D2"
    marker = directory / "test2_scored.json"
    if marker.exists():
        raise ValueError("Test2 was already scored; preserve the frozen evaluation")
    if final and snapshot.snapshot <= test_start:
        raise ValueError("Test2 remains unavailable at the saved snapshot")
    if final and not (directory / "evaluation_val.json").exists():
        raise ValueError("Freeze validation models before scoring test2")
    episodes, records, raions = load_episodes(), load_records(), load_raions()
    fit_mask = _eligible_window(
        episodes, snapshot.regime_switch, cal_start, observed_through=cal_start
    )
    calibration_mask = _eligible_window(
        episodes, cal_start, val_start, observed_through=val_start
    )
    validation_mask = _eligible_window(
        episodes, val_start, test_start, observed_through=snapshot.snapshot
    )
    selected = fit_mask | calibration_mask | validation_mask
    split: Literal["val", "test"] = "val"
    evaluation_mask = validation_mask
    calibration_lower, evaluation_lower = cal_start, val_start
    if final:
        split = "test"
        evaluation_mask = _eligible_window(
            episodes, test_start, snapshot.snapshot + pd.Timedelta(microseconds=1),
            observed_through=snapshot.snapshot,
        )
        selected |= evaluation_mask
        calibration_mask = validation_mask
        calibration_lower, evaluation_lower = val_start, test_start
    if not evaluation_mask.any():
        raise ValueError(f"No observed eligible {split} episodes")
    start_rows = episodes.loc[selected].reset_index(drop=True)
    onset_features = build_start_features(records, start_rows, raions, snapshot.regime_switch)
    if final or reuse_models:
        onset_models, _, onset_metadata = _load_bundle(ARTIFACTS_DIR / "D1c")
        landmark_models, _, _ = _load_bundle(directory)
        if pd.Timestamp(onset_metadata["fit_end"]) != cal_start:
            raise ValueError("Saved duration model does not match the v2 split contract")
        empirical_features = onset_features.loc[start_rows.start.lt(cal_start)]
    else:
        train_local = _eligible_window(
            start_rows, snapshot.regime_switch, cal_start, observed_through=cal_start
        )
        onset_models = _fit_fixed(
            onset_features.loc[train_local], duration_minutes(start_rows.loc[train_local])
        )
        empirical_features = onset_features.loc[train_local]
        onset_metadata = {"fit_end": cal_start.isoformat()}
    train_targets = duration_minutes(start_rows.loc[start_rows.start.lt(cal_start)])
    empirical = EmpiricalDuration.fit(empirical_features, train_targets)
    onset_raw = _predict_boosters(onset_models, onset_features)
    onset_calibration = ConformalDuration.fit(
        start_rows, duration_minutes(start_rows), onset_raw,
        model_fit_end=cal_start, calibration_start=calibration_lower,
        evaluation_start=evaluation_lower,
    )
    local_eval = _eligible_window(
        start_rows, evaluation_lower,
        test_start if not final else snapshot.snapshot + pd.Timedelta(microseconds=1),
        observed_through=snapshot.snapshot,
    ).to_numpy()
    eval_episodes = start_rows.loc[local_eval].reset_index(drop=True)
    onset_predictions = {
        "D0": empirical.predict(onset_features.loc[local_eval]),
        "D1": onset_raw[local_eval],
        "D1c": onset_calibration.predict(onset_raw[local_eval]),
    }
    # The original saved D1 is an additional reference, never a source of CQR residuals.
    frozen_models = [lgb.Booster(model_file=str(ARTIFACTS_DIR / "D1" / f"{column}.txt"))
                     for column in PREDICTION_COLUMNS]
    onset_predictions["D1v1"] = _predict_boosters(frozen_models, onset_features.loc[local_eval])
    scores, details = [], []
    for model_id, predictions in onset_predictions.items():
        score, detail = _v2_score(model_id, split, duration_minutes(eval_episodes), predictions)
        scores.append(score)
        details.append(detail)
        output = eval_episodes[["raion_id", "start"]].assign(split=split)
        output[list(PREDICTION_COLUMNS)] = predictions
        V2_PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
        output.to_parquet(V2_PREDICTIONS_DIR / f"duration_{model_id}_{split}.parquet", index=False)
    landmarks = landmark_rows(start_rows, records)
    features = build_landmark_features(records, landmarks, raions, snapshot.regime_switch)
    if not final and not reuse_models:
        fit_landmarks = _eligible_window(
            landmarks.assign(censored=False), snapshot.regime_switch, cal_start,
            observed_through=cal_start,
        ).to_numpy()
        landmark_models = _fit_fixed(
            features.loc[fit_landmarks], landmarks.remaining_min.to_numpy()[fit_landmarks]
        )
    raw = _predict_boosters(landmark_models, features)
    calibrations = _calibrate_landmarks(
        landmarks, raw, fit_end=cal_start,
        calibration_start=calibration_lower, evaluation_start=evaluation_lower,
    )
    landmark_eval = _eligible_window(
        landmarks.assign(censored=False), evaluation_lower,
        test_start if not final else snapshot.snapshot + pd.Timedelta(microseconds=1),
        observed_through=snapshot.snapshot,
    ).to_numpy()
    rows = landmarks.loc[landmark_eval].reset_index(drop=True)
    raw_eval = raw[landmark_eval]
    calibrated = _apply_landmark_calibration(raw_eval, rows.elapsed_min.to_numpy(), calibrations)
    # All baselines use the identical surviving/raw-active checkpoint cohort.
    indices = pd.MultiIndex.from_frame(eval_episodes[["raion_id", "start"]])
    row_keys = pd.MultiIndex.from_frame(rows[["raion_id", "start"]])
    positions = indices.get_indexer(row_keys)
    for elapsed in CHECKPOINTS:
        mask = rows.elapsed_min.eq(elapsed).to_numpy()
        actual = rows.remaining_min.to_numpy()[mask]
        if not len(actual):
            continue
        predictions_by_model = {
            f"{model}@{elapsed}": np.maximum(predictions[positions[mask]] - elapsed, 1.0)
            for model, predictions in onset_predictions.items()
        }
        predictions_by_model.update({f"D2raw@{elapsed}": raw_eval[mask],
                                     f"D2@{elapsed}": calibrated[mask]})
        for model_id, predictions in predictions_by_model.items():
            score, detail = _v2_score(model_id, split, actual, predictions)
            scores.append(score)
            details.append(detail)
        output = rows.loc[mask, ["raion_id", "start", "checkpoint"]].assign(split=split)
        output[list(PREDICTION_COLUMNS)] = calibrated[mask]
        output.to_parquet(
            V2_PREDICTIONS_DIR / f"duration_D2@{elapsed}_{split}.parquet", index=False
        )
    metadata = {
        "model": "D2", "snapshot": snapshot.snapshot.isoformat(),
        "csv_sha256": snapshot.csv_sha256, "fit_end": cal_start.isoformat(),
        "val_start": val_start.isoformat(), "test2_start": test_start.isoformat(),
        "regime_switch": snapshot.regime_switch.isoformat(),
        "checkpoints": list(CHECKPOINTS), "fit_n": int(fit_mask.sum()),
        "feature_columns": features.columns.tolist(),
        "calibration_policy": "CQR minute offsets per elapsed bucket; median residual shift",
        "data_gaps": [[start.isoformat(), end.isoformat()] for start, end in _v2_data_gaps()],
    }
    if not final:
        _save_bundle(ARTIFACTS_DIR / "D1c", onset_models, {"0": onset_calibration}, metadata)
        _save_bundle(directory, landmark_models, calibrations, metadata)
    report_path = config.REPORTS_DIR / "v2" / "duration_scores.json"
    notes = [
        "v2 validation starts 2026-08-01; test denotes untouched test2 from 2026-09-08.",
        "D1 is the v1 architecture refitted before chronological calibration; v1 is frozen.",
        "Widths and median calibration are in docs/duration-v2.md and D2/evaluation_<split>.json.",
        f"{split}: fit ends before {cal_start.isoformat()}; calibration targets end before "
        f"{evaluation_lower.isoformat()}; snapshot={snapshot.snapshot.isoformat()}.",
    ]
    if not final:
        notes.append("Test2 has not been scored. Archive backfill is still required.")
    else:
        prior = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
        scores = prior.duration + scores
        notes = [note for note in prior.notes if "has not been scored" not in note] + notes
    report = Report(generated_at=datetime.now(UTC), duration=scores, notes=notes)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    evaluation = {
        **metadata, "split": split, "details": details,
        "onset_calibration": vars(onset_calibration),
        "landmark_calibrations": {key: vars(value) for key, value in calibrations.items()},
    }
    (directory / f"evaluation_{split}.json").write_text(
        json.dumps(evaluation, indent=2) + "\n", encoding="utf-8"
    )
    if final:
        marker.write_text(json.dumps({"snapshot": snapshot.snapshot.isoformat()}) + "\n",
                          encoding="utf-8")
    print(f"Duration v2 {split}: {len(details)} scores; "
          f"onset coverage {details[1]['coverage_80']:.4%} -> {details[2]['coverage_80']:.4%}",
          flush=True)
    return report


def predict_replay(
    active: pd.DataFrame, records: pd.DataFrame, raions: pd.DataFrame,
) -> pd.DataFrame:
    """Batch replay ends using frozen D2 weights and each split's earlier calibration."""
    result = active[["raion_id", "active_since", "t"]].reset_index(drop=True)
    models, _, metadata = _load_bundle(ARTIFACTS_DIR / "D2")
    keys = result.rename(columns={"active_since": "start", "t": "checkpoint"})
    features = build_landmark_features(
        records, keys, raions, pd.Timestamp(metadata["regime_switch"])
    )
    raw = _predict_boosters(models, features)
    predictions = raw.copy()
    for split, mask in (("val", result.t.lt(config.V2_TEST_START)),
                        ("test", result.t.ge(config.V2_TEST_START))):
        if not mask.any():
            continue
        evaluation = json.loads((ARTIFACTS_DIR / "D2" / f"evaluation_{split}.json").read_text(
            encoding="utf-8"))
        calibrations = {key: ConformalDuration(**value)
                        for key, value in evaluation["landmark_calibrations"].items()}
        if any(pd.Timestamp(value.last_target_end) >= result.loc[mask, "t"].min()
               for value in calibrations.values()):
            raise ValueError("Replay calibration must precede every forecast")
        predictions[mask] = _apply_landmark_calibration(
            raw[mask], features.loc[mask, "elapsed_min"].to_numpy(), calibrations
        )
    if not np.isfinite(predictions).all():
        raise ValueError("Replay remaining durations must be finite")
    for index, quantile in enumerate((10, 50, 90)):
        result[f"end_p{quantile}"] = (result.t + pd.to_timedelta(
            predictions[:, index], unit="min")).astype("datetime64[us, UTC]")
    return result


def refit_production(now: datetime | pd.Timestamp) -> None:
    """Fit a full-data median and disjoint holdout-calibrated interval models.

    The configured recent calibration weeks stay out of interval-model fitting. The median
    booster uses every eligible ended episode; this avoids transferring CQR
    residuals to a refitted interval model that saw its calibration targets.
    """
    now = _utc_time(now)
    snapshot = load_snapshot()
    cutoff = min(now.floor("h") - pd.Timedelta(microseconds=1), snapshot.snapshot)
    calibration_start = cutoff - pd.Timedelta(days=CALIBRATION_DAYS)
    records, episodes, raions = load_records(), load_episodes(), load_raions()
    eligible = _eligible_window(
        episodes, pd.Timestamp(TRAIN_START), cutoff + pd.Timedelta(microseconds=1),
        observed_through=cutoff,
    )
    rows = landmark_rows(episodes.loc[eligible], records)
    features = build_landmark_features(records, rows, raions, snapshot.regime_switch)
    fit_mask = rows.end.lt(calibration_start).to_numpy()
    models = _fit_fixed(features.loc[fit_mask], rows.remaining_min.to_numpy()[fit_mask])
    raw = _predict_boosters(models, features)
    calibrations = _calibrate_landmarks(
        rows, raw, fit_end=calibration_start,
        calibration_start=calibration_start, evaluation_start=cutoff,
    )
    full_median = _fit_fixed(features, rows.remaining_min.to_numpy(), quantiles=(0.5,))[0]
    # Publish a new generation, then atomically replace the small pointer.
    generation = cutoff.strftime("%Y%m%dT%H%M%S%fZ") + "_" + datetime.now(UTC).strftime("%H%M%S%f")
    destination = PRODUCTION_DIR / generation
    _save_bundle(destination, models, calibrations, {
        "model": "D2", "snapshot": cutoff.isoformat(), "refitted_at": now.isoformat(),
        "fit_end": calibration_start.isoformat(),
        "regime_switch": snapshot.regime_switch.isoformat(), "ended_episodes": int(eligible.sum()),
        "landmarks": len(rows), "feature_columns": features.columns.tolist(),
        "median_policy": "all-ended-data median, clamped inside calibrated earlier-fit interval",
    })
    full_median.save_model(str(destination / "median_all.txt"))
    pointer = PRODUCTION_DIR / "current.json.tmp"
    pointer.write_text(json.dumps({"generation": generation}) + "\n", encoding="utf-8")
    pointer.replace(PRODUCTION_DIR / "current.json")
    print(f"Production duration: {int(eligible.sum()):,} ended episodes, {len(rows):,} landmarks",
          flush=True)


def _production_records(now: pd.Timestamp) -> pd.DataFrame:
    """Share onset's live coverage, retaining receipt clocks for original-start context."""
    from airalert.production import as_of_records, load_observations

    observed = load_observations(now)
    records = as_of_records(load_records(), observed, now)
    if observed.empty:
        return records
    keys = ["raion_id", "source", "level", "started_at"]
    available = observed.loc[observed.raion_id.notna() & observed.hazard_type.eq("air_raid")]
    if available.empty:
        return records
    receipts = available.groupby(keys, as_index=False).observed_at.min()
    # Archive records have no receipt clock. Live records cannot enter onset
    # context merely because their provider reports an earlier original start.
    return records.merge(receipts, on=keys, how="left", validate="many_to_one")


def predict_remaining(
    active: pd.DataFrame, now: datetime | pd.Timestamp,
) -> pd.DataFrame:
    """Return UTC end quantiles for caller-confirmed active raion/start keys.

    Quantiles condition on survival via landmark training. Live availability is
    respected. Between landmarks, calibration uses the preceding checkpoint;
    elapsed beyond 480 minutes is extrapolation, without a coverage guarantee.
    """
    now = _utc_time(now)
    result = active[["raion_id", "start"]].reset_index(drop=True).copy()
    result["start"] = pd.to_datetime(result.start, utc=True).astype("datetime64[us, UTC]")
    if result.start.isna().any() or result.start.gt(now).any():
        raise ValueError("Active starts must be known and no later than now")
    end_columns = ("end_p10", "end_p50", "end_p90")
    if not len(result):
        for column in end_columns:
            result[column] = pd.Series(dtype="datetime64[us, UTC]")
        return result
    pointer = json.loads((PRODUCTION_DIR / "current.json").read_text(encoding="utf-8"))
    directory = PRODUCTION_DIR / pointer["generation"]
    models, calibrations, metadata = _load_bundle(directory)
    if pd.Timestamp(metadata["snapshot"]) > now:
        raise ValueError("Production duration artifacts were fitted after the forecast time")
    rows = result.assign(checkpoint=now)
    features = build_landmark_features(
        _production_records(now), rows, load_raions(), pd.Timestamp(metadata["regime_switch"])
    )
    raw = _predict_boosters(models, features)
    predictions = _apply_landmark_calibration(raw, features.elapsed_min.to_numpy(), calibrations)
    median_path = directory / "median_all.txt"
    median = lgb.Booster(model_file=str(median_path)).predict(features, num_threads=4)
    predictions[:, 1] = np.clip(np.maximum(np.exp(median), 1.0),
                                predictions[:, 0], predictions[:, 2])
    if not np.isfinite(predictions).all():
        raise ValueError("Remaining-duration predictions must be finite")
    for index, column in enumerate(end_columns):
        result[column] = (now + pd.to_timedelta(predictions[:, index], unit="min")).astype(
            "datetime64[us, UTC]"
        )
    return result


def wait_for_backfill() -> None:
    """Poll the saved snapshot every five minutes for two hours, then stop.

    Test2 is scored only after accepted data reaches its start. Persist the
    bounded polling evidence without changing canonical ingestion artifacts.
    """
    began = monotonic()
    deadline = began + 2 * 60 * 60
    _, test_start = _v2_boundaries()
    polls: list[dict[str, object]] = []
    path = ARTIFACTS_DIR / "D2" / "backfill_wait.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        snapshot = load_snapshot()
        polls.append({"checked_at": datetime.now(UTC).isoformat(),
                      "snapshot": snapshot.snapshot.isoformat()})
        available = snapshot.snapshot > test_start
        elapsed = monotonic() - began
        expired = monotonic() >= deadline
        status = "available" if available else "timeout" if expired else "waiting"
        path.write_text(json.dumps({"status": status, "elapsed_seconds": elapsed,
                                    "polls": polls}, indent=2) + "\n", encoding="utf-8")
        print(f"Backfill poll {len(polls)}: {snapshot.snapshot.isoformat()}; "
              f"{status}, {elapsed / 60:.1f} minutes", flush=True)
        if available:
            if (ARTIFACTS_DIR / "D2" / "evaluation_val.json").exists():
                evaluate_v2(final=True)
            else:
                print("Backfill available; finish validation before --final", flush=True)
            return
        if status == "timeout":
            return
        next_poll = min(monotonic() + 5 * 60, deadline)
        while monotonic() < next_poll:
            sleep(min(60, next_poll - monotonic()))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Duration v2 evaluation and production refit")
    parser.add_argument("--final", action="store_true", help="Score untouched test2 once")
    parser.add_argument("--production", action="store_true", help="Refit the production hook")
    parser.add_argument("--wait-backfill", action="store_true",
                        help="Wait up to 2h, then score test2")
    arguments = parser.parse_args()
    if arguments.wait_backfill:
        wait_for_backfill()
    elif arguments.production:
        refit_production(datetime.now(UTC))
    else:
        evaluate_v2(final=arguments.final)
