"""Episode-start duration models; records supply features, episodes supply targets.

Unlike hourly onset features, duration features include records starting exactly
at s. Public split helpers are shared with the backtest duration scorer.
"""

import json
from dataclasses import dataclass
from functools import partial
from time import perf_counter
from typing import Literal

import lightgbm as lgb
import numpy as np
import pandas as pd
from numpy.typing import NDArray

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
from airalert.schemas import DurationScore

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
    log_predictions = np.column_stack([model.predict(features) for model in models])
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
