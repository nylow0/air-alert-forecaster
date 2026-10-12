"""Train onset models without using validation for fitting or early stopping.

Validation probabilities use opposite-month isotonic fits. Seam-embargo hours
retain raw probabilities so every forecast key exists without overlapping
calibration labels. Test/latest use an isotonic fit on all eligible validation.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from numpy.typing import NDArray
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from airalert import config, metrics
from airalert.backtest import assign_split, scorable_mask, trainable_mask
from airalert.data import load_raions, load_snapshot

LOGISTIC_FEATURES = (
    "f_active", "f_starts_6h", "f_starts_24h", "f_adjacent_active_share",
    "f_country_active", "f_country_started_60m", "f_hour_sin", "f_hour_cos",
)
AGE_FEATURES = ("f_minutes_since_start", "f_minutes_since_end")
MISSING_AGE_MIN = 1_000_000.0
INNER_WEEKS = 8
MAX_ROUNDS = 700
EARLY_STOPPING_ROUNDS = 50
B1_CALIBRATION_MIN_GAIN = 0.01
LGB_PARAMS: dict[str, str | int | float | bool] = {
    "objective": "binary", "metric": "None", "num_leaves": 63,
    "learning_rate": 0.05, "min_data_in_leaf": 200, "feature_fraction": 0.8,
    "bagging_fraction": 0.8, "bagging_freq": 1, "num_threads": 4,
    "seed": 42, "feature_fraction_seed": 42, "bagging_seed": 42,
    "data_random_seed": 42, "deterministic": True, "force_col_wise": True,
    "verbosity": -1,
}


@dataclass
class WindowResult:
    """Small prediction arrays and provenance, never retained training matrices."""

    window: str
    predictions: dict[str, pd.DataFrame]
    raw_predictions: dict[str, pd.DataFrame]
    scores: dict[int, float]
    metadata: dict[str, object]


def training_masks(
    frame: pd.DataFrame, horizon: int, *, regime_start: pd.Timestamp | None = None,
    snapshot: pd.Timestamp | None = None,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return refit, inner-train and inner-holdout masks under the shared policy.

    The inner label window may end exactly at holdout_start. Every part also
    satisfies the outer embargo and gap/recovery exclusions in trainable_mask.
    """
    refit = trainable_mask(frame, horizon, snapshot=snapshot)
    if regime_start is not None:
        refit &= frame.t.ge(regime_start)
    holdout_start = pd.Timestamp(config.VAL_START) - pd.Timedelta(weeks=INNER_WEEKS)
    inner_train = refit & (frame.t + pd.Timedelta(hours=horizon)).le(holdout_start)
    holdout = refit & frame.t.ge(holdout_start)
    return refit, inner_train, holdout


def calibration_masks(
    frame: pd.DataFrame, horizon: int, *, snapshot: pd.Timestamp | None = None,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Use June/July folds; neither calibration fit includes the seam embargo."""
    seam = pd.Timestamp(config.VAL_START) + pd.DateOffset(months=1)
    eligible = scorable_mask(frame, horizon, snapshot=snapshot)
    first = eligible & (frame.t + pd.Timedelta(hours=horizon)).le(seam)
    second = eligible & frame.t.ge(seam)
    seam_rows = frame.t.lt(seam) & (frame.t + pd.Timedelta(hours=horizon)).gt(seam)
    return first, second, seam_rows


def calibrate(
    validation: pd.DataFrame, raw: NDArray[np.float64], horizon: int,
    *, snapshot: pd.Timestamp | None = None,
) -> tuple[NDArray[np.float64], IsotonicRegression, dict[str, object]]:
    """Cross-fit reported validation and fit a separate serving calibrator."""
    first, second, seam_rows = calibration_masks(validation, horizon, snapshot=snapshot)
    if not first.any() or not second.any():
        raise ValueError("Isotonic calibration needs eligible rows in both validation months")
    labels = validation[f"y_{horizon}"].to_numpy(dtype=float, na_value=np.nan)
    first_fit = IsotonicRegression(out_of_bounds="clip").fit(raw[first], labels[first])
    second_fit = IsotonicRegression(out_of_bounds="clip").fit(raw[second], labels[second])
    seam = pd.Timestamp(config.VAL_START) + pd.DateOffset(months=1)
    cross_fitted = raw.copy()
    apply_first = validation.t.ge(seam).to_numpy()
    apply_second = (validation.t.lt(seam) & ~seam_rows).to_numpy()
    cross_fitted[apply_first] = first_fit.predict(raw[apply_first])
    cross_fitted[apply_second] = second_fit.predict(raw[apply_second])
    eligible = scorable_mask(validation, horizon, snapshot=snapshot).to_numpy()
    serving = IsotonicRegression(out_of_bounds="clip").fit(raw[eligible], labels[eligible])
    details: dict[str, object] = {
        "seam": seam.isoformat(), "first_fit_n": int(first.sum()),
        "second_fit_n": int(second.sum()), "raw_seam_n": int(seam_rows.sum()),
        "serving_fit_n": int(eligible.sum()),
        "raw_val_brier": metrics.brier_score(labels[eligible], raw[eligible]),
        "cross_fitted_val_brier": metrics.brier_score(labels[eligible], cross_fitted[eligible]),
    }
    return cross_fitted, serving, details


def logistic_matrix(frame: pd.DataFrame, *, full_history: bool) -> pd.DataFrame:
    """Handpicked persistence features with log ages and explicit missing flags."""
    result = frame[list(LOGISTIC_FEATURES)].copy()
    for feature in AGE_FEATURES:
        values = frame[feature]
        result[f"log1p_{feature}"] = np.log1p(values.fillna(MISSING_AGE_MIN))
        result[f"{feature}_missing"] = values.isna().astype("float32")
    if full_history:
        result["f_regime_raion"] = frame.f_regime_raion
    if not np.isfinite(result.to_numpy()).all():
        raise ValueError("Logistic inputs must be finite after missing-age handling")
    return result


def _read_features(
    split: str, feature_names: list[str], categories: dict[str, list[str]],
    snapshot: pd.Timestamp,
) -> pd.DataFrame:
    columns = ["t", "raion_id", "oblast_id", *feature_names]
    # Reading test/latest never requests targets, including through a whole-frame read.
    if split in ("train", "val"):
        columns.extend(f"y_{h}" for h in config.HORIZONS)
    frame = pd.read_parquet(config.FEATURES_DIR / f"{split}.parquet", columns=columns)
    frame["t"] = frame.t.astype("datetime64[us, UTC]")
    if frame.duplicated(["t", "raion_id"]).any():
        raise ValueError(f"Duplicate forecast keys in {split} features")
    if not assign_split(frame.t, snapshot=snapshot).eq(split).all():
        raise ValueError(f"Forecast times outside {split} in its feature file")
    for column, values in categories.items():
        frame[column] = pd.Categorical(frame[column], categories=values)
        if frame[column].isna().any():
            raise ValueError(f"Unknown or null {column} in {split} features")
    return frame


def _brier_eval(prediction: NDArray[np.float64], dataset: lgb.Dataset) -> tuple[str, float, bool]:
    labels = dataset.get_label()
    if labels is None:
        raise ValueError("Inner holdout requires labels")
    return "brier", metrics.brier_score(labels, prediction), False


def _fit_lightgbm(
    matrix: pd.DataFrame, labels: pd.Series,
    masks: tuple[pd.Series, pd.Series, pd.Series],
) -> tuple[lgb.Booster, dict[str, object]]:
    refit, inner_train, holdout = masks
    categorical = ["raion_id", "oblast_id"]
    dataset = lgb.Dataset(matrix.loc[inner_train], label=labels.loc[inner_train],
                          categorical_feature=categorical)
    holdout_data = lgb.Dataset(matrix.loc[holdout], label=labels.loc[holdout],
                              categorical_feature=categorical, reference=dataset)
    stopped = lgb.train(
        LGB_PARAMS, dataset, num_boost_round=MAX_ROUNDS,
        valid_sets=[holdout_data], valid_names=["inner_holdout"], feval=_brier_eval,
        callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, first_metric_only=True,
                                    verbose=False)],
    )
    iterations = stopped.best_iteration
    details: dict[str, object] = {
        "best_iteration": iterations,
        "inner_holdout_brier": stopped.best_score["inner_holdout"]["brier"],
    }
    print(f"  inner holdout: iterations={iterations} "
          f"Brier={details['inner_holdout_brier']:.6f}; refitting all train", flush=True)
    del stopped, dataset, holdout_data
    fitted = lgb.train(
        LGB_PARAMS,
        lgb.Dataset(matrix.loc[refit], label=labels.loc[refit], categorical_feature=categorical),
        num_boost_round=iterations,
    )
    return fitted, details


def _fit_logistic(matrix: pd.DataFrame, labels: pd.Series, mask: pd.Series) -> Pipeline:
    fitted = make_pipeline(
        StandardScaler(), LogisticRegression(C=1.0, solver="lbfgs", max_iter=300, tol=1e-5),
    )
    with threadpool_limits(limits=4):
        fitted.fit(matrix.loc[mask], labels.loc[mask].to_numpy(dtype=np.int8))
    return fitted


def _prediction_frames(outputs: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {split: rows[["t", "raion_id"]].assign(split=split).copy()
            for split, rows in outputs.items()}


def _mask_summary(frame: pd.DataFrame, mask: pd.Series, horizon: int) -> dict[str, object]:
    times = frame.loc[mask, "t"]
    if times.empty:
        raise ValueError("Training window or inner holdout has no eligible rows")
    return {
        "n": len(times), "min_t": times.min().isoformat(), "max_t": times.max().isoformat(),
        "max_label_end": (times.max() + pd.Timedelta(hours=horizon)).isoformat(),
    }


def _fit_window(
    model_id: str, window: str, training: pd.DataFrame, outputs: dict[str, pd.DataFrame],
    feature_names: list[str], provenance: dict[str, object],
    regime_switch: pd.Timestamp, snapshot: pd.Timestamp,
) -> WindowResult:
    began = perf_counter()
    artifact_dir = config.ARTIFACTS_DIR / f"{model_id}-{window}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    full_history = window == "full"
    features = [*feature_names, "raion_id", "oblast_id"]
    if model_id == "B1":
        matrix = logistic_matrix(training, full_history=full_history)
        output_matrices = {split: logistic_matrix(rows, full_history=full_history)
                           for split, rows in outputs.items()}
    else:
        matrix = training[features]
        output_matrices = {split: rows[features] for split, rows in outputs.items()}
    predicted = _prediction_frames(outputs)
    raw_predicted = _prediction_frames(outputs)
    scores: dict[int, float] = {}
    horizon_details: list[dict[str, object]] = []
    validation = outputs["val"]
    for horizon in config.HORIZONS:
        masks = training_masks(training, horizon, snapshot=snapshot,
                               regime_start=None if full_history else regime_switch)
        refit, inner_train, holdout = masks
        details: dict[str, object] = {
            "horizon": horizon, "refit": _mask_summary(training, refit, horizon),
            "inner_train": _mask_summary(training, inner_train, horizon),
            "inner_holdout": _mask_summary(training, holdout, horizon),
        }
        print(f"{model_id} {window} H={horizon}: train_n={int(refit.sum())} "
              f"inner_train_n={int(inner_train.sum())} holdout_n={int(holdout.sum())}", flush=True)
        raw: dict[str, NDArray[np.float64]] = {}
        if model_id == "M1":
            fitted, stopping = _fit_lightgbm(matrix, training[f"y_{horizon}"], masks)
            details.update(stopping)
            fitted.save_model(str(artifact_dir / f"model_{horizon}h.txt"))
            gain = pd.DataFrame({"feature": fitted.feature_name(),
                                 "gain": fitted.feature_importance(importance_type="gain")})
            gain.to_csv(artifact_dir / f"importance_{horizon}h.csv", index=False)
            raw = {split: fitted.predict(values, num_threads=4)
                   for split, values in output_matrices.items()}
        else:
            logistic = _fit_logistic(matrix, training[f"y_{horizon}"], refit)
            joblib.dump(logistic, artifact_dir / f"model_{horizon}h.joblib")
            with threadpool_limits(limits=4):
                raw = {split: logistic.predict_proba(values)[:, 1]
                       for split, values in output_matrices.items()}
        cross_fitted, calibrator, calibration = calibrate(
            validation, raw["val"], horizon, snapshot=snapshot,
        )
        raw_brier = float(calibration["raw_val_brier"])
        calibrated_brier = float(calibration["cross_fitted_val_brier"])
        use_calibration = model_id == "M1" or (
            calibrated_brier < raw_brier * (1 - B1_CALIBRATION_MIN_GAIN)
        )
        calibration["applied"] = use_calibration
        details["calibration"] = calibration
        if use_calibration:
            joblib.dump(calibrator, artifact_dir / f"calibrator_{horizon}h.joblib")
        for split in outputs:
            raw_predicted[split][f"p_{horizon}"] = raw[split]
            predicted[split][f"p_{horizon}"] = (
                cross_fitted if split == "val" else calibrator.predict(raw[split])
            ) if use_calibration else raw[split]
        scores[horizon] = calibrated_brier if use_calibration else raw_brier
        horizon_details.append(details)
        print(f"  val raw_Brier={raw_brier:.6f} crossfit_Brier={calibrated_brier:.6f} "
              f"calibration={'yes' if use_calibration else 'no'}; "
              f"{perf_counter() - began:.1f}s elapsed", flush=True)
    metadata: dict[str, object] = {
        **provenance, "model": model_id, "window": window,
        "features": list(matrix.columns), "horizons": horizon_details,
        "parameters": LGB_PARAMS if model_id == "M1" else {
            "C": 1.0, "solver": "lbfgs", "max_iter": 300, "tol": 1e-5,
            "missing_age_min": MISSING_AGE_MIN, "calibration_min_relative_gain":
            B1_CALIBRATION_MIN_GAIN,
        },
    }
    (artifact_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    return WindowResult(window, predicted, raw_predicted, scores, metadata)


def validate_predictions(
    predictions: pd.DataFrame, outputs: dict[str, pd.DataFrame], snapshot: pd.Timestamp,
) -> None:
    """Guard exact replay-key coverage and probability bounds before publication."""
    keys = ["t", "raion_id", "split"]
    required = [*keys, *(f"p_{h}" for h in config.HORIZONS)]
    if set(predictions.columns) != set(required) or predictions[keys].isna().any().any():
        raise ValueError("Predictions require exactly the contract columns and non-null keys")
    if predictions.duplicated(["t", "raion_id"]).any():
        raise ValueError("Duplicate prediction keys")
    if not assign_split(predictions.t, snapshot=snapshot).eq(predictions.split).all():
        raise ValueError("Prediction splits disagree with forecast times")
    expected = pd.concat([rows[["t", "raion_id"]].assign(split=split)
                          for split, rows in outputs.items()], ignore_index=True)
    coverage = expected.merge(predictions[keys], on=keys, how="outer",
                              validate="one_to_one", indicator=True)
    if not coverage._merge.eq("both").all():
        raise ValueError("Prediction keys must cover feature keys exactly")
    values = predictions[[f"p_{h}" for h in config.HORIZONS]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("Prediction probabilities must be finite and in [0, 1]")


def write_predictions(
    model_id: str, frames: dict[str, pd.DataFrame], outputs: dict[str, pd.DataFrame],
    snapshot: pd.Timestamp,
) -> Path:
    result = pd.concat(frames.values(), ignore_index=True)
    result["t"] = result.t.astype("datetime64[us, UTC]")
    result["raion_id"] = result.raion_id.astype("str")
    validate_predictions(result, outputs, snapshot)
    config.PREDS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.PREDS_DIR / f"onset_{model_id}.parquet"
    temporary = path.with_suffix(".parquet.tmp")
    result.to_parquet(temporary, index=False)
    temporary.replace(path)
    print(f"Wrote {path.name}: {len(result)} rows "
          f"{result.groupby('split').size().to_dict()}", flush=True)
    return path


def _draw_importance(artifact_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    with plt.style.context("dark_background"):
        for horizon in config.HORIZONS:
            gain = pd.read_csv(artifact_dir / f"importance_{horizon}h.csv")
            top = gain.nlargest(15, "gain").sort_values("gain")
            fig, ax = plt.subplots(figsize=(10, 7), facecolor="#000000")
            ax.set_facecolor("#000000")
            ax.barh(top.feature, top.gain, color="#5fa8ff")
            ax.set_xlabel("Training gain")
            ax.set_title(f"M1: top 15 features, {horizon} h onset")
            ax.spines[["top", "right"]].set_visible(False)
            fig.tight_layout()
            fig.savefig(config.FIGURES_DIR / f"onset_importance_{horizon}h.png", dpi=160)
            plt.close(fig)


def train(model_id: str) -> None:
    """Compare both training windows on val, then publish replay predictions."""
    if model_id not in ("B1", "M1"):
        raise ValueError(f"Unsupported onset model: {model_id}")
    began = perf_counter()
    saved = load_snapshot()
    universe = load_raions()
    categories = {name: sorted(universe[name].unique()) for name in ("raion_id", "oblast_id")}
    names = pq.read_schema(config.TRAIN_FEATURES_PATH).names
    feature_names = [name for name in names if name.startswith("f_")]
    needed = {*LOGISTIC_FEATURES, *AGE_FEATURES, "f_regime_raion"}
    if not needed.issubset(feature_names):
        raise ValueError("Full feature tables must be published before onset training")
    training = _read_features("train", feature_names, categories, saved.snapshot)
    outputs = {split: _read_features(split, feature_names, categories, saved.snapshot)
               for split in ("val", "test", "latest")}
    hashes: dict[str, str] = {}
    for split in ("train", "val"):
        with (config.FEATURES_DIR / f"{split}.parquet").open("rb") as source:
            hashes[split] = hashlib.file_digest(source, "sha256").hexdigest()
    provenance: dict[str, object] = {
        "snapshot": saved.snapshot.isoformat(), "csv_sha256": saved.csv_sha256,
        "regime_switch": saved.regime_switch.isoformat(), "feature_sha256": hashes,
        "categories": categories, "gap_feature_recovery_h": config.DATA_GAP_FEATURE_RECOVERY_H,
        "data_gaps": [{"start": start.isoformat(), "end": end.isoformat()}
                      for start, end in config.DATA_GAPS],
        "inner_holdout_start": (
            pd.Timestamp(config.VAL_START) - pd.Timedelta(weeks=INNER_WEEKS)
        ).isoformat(), "max_rounds": MAX_ROUNDS, "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
        "refit": "all trainable rows in the window at the inner-holdout iteration count",
        "calibration": "opposite-month isotonic on val; raw seam embargo; all-val for serving",
        "selection": "sum of per-horizon validation Brier ranks; H=3 breaks ties",
    }
    results = [_fit_window(model_id, window, training, outputs, feature_names, provenance,
                           saved.regime_switch, saved.snapshot)
               for window in ("full", "raionregime")]
    scores = pd.DataFrame([result.scores for result in results])
    rank_sum = scores.rank(method="min").sum(axis=1)
    winner_index = min(range(len(results)), key=lambda i: (rank_sum.iloc[i], results[i].scores[3]))
    winner = results[winner_index]
    comparison = [{"window": result.window, "val_brier": result.scores,
                   "rank_sum": float(rank_sum.iloc[i])} for i, result in enumerate(results)]
    for i, result in enumerate(results):
        identity = model_id if i == winner_index else f"{model_id}-{result.window}"
        write_predictions(identity, result.predictions, outputs, saved.snapshot)
    source_dir = config.ARTIFACTS_DIR / f"{model_id}-{winner.window}"
    served_dir = config.ARTIFACTS_DIR / model_id
    shutil.copytree(source_dir, served_dir, dirs_exist_ok=True)
    winner.metadata.update({"served_as": model_id, "window_comparison": comparison})
    (served_dir / "metadata.json").write_text(
        json.dumps(winner.metadata, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    if model_id == "M1":
        write_predictions("M1-raw", winner.raw_predictions, outputs, saved.snapshot)
        raw_dir = config.ARTIFACTS_DIR / "M1-raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for horizon in config.HORIZONS:
            shutil.copy2(source_dir / f"model_{horizon}h.txt", raw_dir / f"model_{horizon}h.txt")
        raw_metadata = {**winner.metadata, "served_as": "M1-raw", "calibration": "none"}
        (raw_dir / "metadata.json").write_text(
            json.dumps(raw_metadata, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )
        _draw_importance(served_dir)
    print(f"{model_id} selected window={winner.window}; comparisons={comparison}; "
          f"{perf_counter() - began:.2f}s", flush=True)
