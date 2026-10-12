"""Train onset models without using validation for fitting or early stopping.

Calibration always moves forward in time. v2 validation uses a saved train-tail
fit, and untouched test2 uses a separate saved validation fit.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter, sleep

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
from airalert.backtest import (
    assign_split,
    scorable_mask,
    trainable_mask,
    v2_data_gap_mask,
    v2_data_gaps,
    v2_eligible_mask,
    v2_prediction_hashes,
    write_v2_json,
)
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
    """First-month fit and second-month application; exclude seam-crossing labels."""
    seam = pd.Timestamp(config.VAL_START) + pd.DateOffset(months=1)
    eligible = scorable_mask(frame, horizon, snapshot=snapshot)
    first = eligible & (frame.t + pd.Timedelta(hours=horizon)).le(seam)
    second = eligible & frame.t.ge(seam)
    seam_rows = frame.t.lt(seam) & (frame.t + pd.Timedelta(hours=horizon)).gt(seam)
    return first, second, seam_rows


def calibrate(
    validation: pd.DataFrame, raw: NDArray[np.float64], horizon: int,
    *, snapshot: pd.Timestamp | None = None, artifact_dir: Path | None = None,
) -> tuple[NDArray[np.float64], IsotonicRegression, dict[str, object]]:
    """Forward-fit legacy validation; the first month retains raw probabilities."""
    first, second, seam_rows = calibration_masks(validation, horizon, snapshot=snapshot)
    if not first.any() or not second.any():
        raise ValueError("Isotonic calibration needs eligible rows in both validation months")
    labels = validation[f"y_{horizon}"].to_numpy(dtype=float, na_value=np.nan)
    first_fit = IsotonicRegression(out_of_bounds="clip").fit(raw[first], labels[first])
    seam = pd.Timestamp(config.VAL_START) + pd.DateOffset(months=1)
    cross_fitted = raw.copy()
    apply_first = validation.t.ge(seam).to_numpy()
    cross_fitted[apply_first] = first_fit.predict(raw[apply_first])
    eligible = scorable_mask(validation, horizon, snapshot=snapshot).to_numpy()
    serving = IsotonicRegression(out_of_bounds="clip").fit(raw[eligible], labels[eligible])
    if artifact_dir is not None:
        joblib.dump(first_fit, artifact_dir / f"calibrator_forward_{horizon}h.joblib")
        joblib.dump(serving, artifact_dir / f"calibrator_{horizon}h.joblib")
    details: dict[str, object] = {
        "seam": seam.isoformat(), "first_fit_n": int(first.sum()),
        "raw_first_n": int((~apply_first).sum()), "raw_seam_n": int(seam_rows.sum()),
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
            validation, raw["val"], horizon, snapshot=snapshot, artifact_dir=artifact_dir,
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
        "calibration": "forward-month isotonic; first month raw; all-val for serving",
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


@dataclass
class HorizonModel:
    """The complete prediction contract for one selected horizon, saved by joblib."""

    kind: str
    fitted: object
    features: list[str]
    categories: dict[str, list[str]]
    window: str
    k: float = 0.0
    rounds: int = 0

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """CLI-created bundles must reference the importable class used by the server."""
        from airalert.models.onset import HorizonModel as PersistedHorizonModel

        return PersistedHorizonModel, (self.kind, self.fitted, self.features, self.categories,
                                       self.window, self.k, self.rounds)


def predict_horizon(model: HorizonModel, frame: pd.DataFrame) -> NDArray[np.float64]:
    from airalert.models import baseline

    if model.kind == "B0":
        if not isinstance(model.fitted, baseline.Climatology):
            raise TypeError("B0 artifact must contain climatology statistics")
        return baseline.predict_rates(baseline._calendar(frame), model.fitted, model.k)
    if model.kind == "B1":
        if not isinstance(model.fitted, Pipeline):
            raise TypeError("B1 artifact must contain a fitted pipeline")
        matrix = logistic_matrix(frame, full_history=model.window == "full")
        with threadpool_limits(limits=4):
            return model.fitted.predict_proba(matrix)[:, 1]
    if not isinstance(model.fitted, lgb.Booster):
        raise TypeError("M1 artifact must contain a LightGBM booster")
    matrix = frame[[*model.features, "raion_id", "oblast_id"]].copy()
    for column, categories in model.categories.items():
        matrix[column] = pd.Categorical(matrix[column], categories=categories)
        if matrix[column].isna().any():
            raise ValueError(f"Unknown {column} during inference")
    return model.fitted.predict(matrix, num_threads=4)


def fit_horizon(
    kind: str, frame: pd.DataFrame, horizon: int, cutoff: pd.Timestamp,
    *, window: str, feature_names: list[str], categories: dict[str, list[str]],
    regime: pd.Timestamp, k: float = 0.0, rounds: int = 0,
) -> HorizonModel:
    """Fit on matured pre-cutoff rows; choose iterations only on an inner holdout."""
    from airalert.models import baseline

    mask = (frame.t + pd.Timedelta(hours=horizon)).le(cutoff) & frame.t.lt(cutoff)
    mask &= frame[f"y_{horizon}"].notna() & v2_data_gap_mask(frame, horizon)
    mask &= frame.t.ge(regime if window == "raionregime" else config.V2_TRAIN_START)
    if not mask.any():
        raise ValueError("No matured rows available for fitting")
    if kind == "B0":
        columns = ["t", "raion_id", "oblast_id", f"y_{horizon}"]
        fitted = baseline.fit_rates(baseline._calendar(frame.loc[mask, columns]), horizon)
    elif kind == "B1":
        fitted = _fit_logistic(logistic_matrix(frame, full_history=window == "full"),
                               frame[f"y_{horizon}"], mask)
    else:
        matrix = frame[[*feature_names, "raion_id", "oblast_id"]].copy()
        for column, values in categories.items():
            matrix[column] = pd.Categorical(matrix[column], categories=values)
        if rounds:
            fitted = lgb.train(LGB_PARAMS, lgb.Dataset(
                matrix.loc[mask], label=frame.loc[mask, f"y_{horizon}"],
                categorical_feature=["raion_id", "oblast_id"],
            ), num_boost_round=rounds)
        else:
            seam = cutoff - pd.Timedelta(weeks=INNER_WEEKS)
            inner = mask & (frame.t + pd.Timedelta(hours=horizon)).le(seam)
            holdout = mask & frame.t.ge(seam)
            if not inner.any() or not holdout.any():
                raise ValueError("Insufficient history for inner chronological stopping")
            fitted, details = _fit_lightgbm(matrix, frame[f"y_{horizon}"], (mask, inner, holdout))
            rounds = int(details["best_iteration"])
    return HorizonModel(kind, fitted, feature_names, categories, window, k, rounds)


def fit_calibrator(
    rows: pd.DataFrame, raw: NDArray[np.float64], horizon: int, apply_from: pd.Timestamp,
) -> tuple[IsotonicRegression, dict[str, object]]:
    """Fail before fitting if any calibration label looks beyond its application boundary."""
    if rows.empty or not rows.t.lt(apply_from).all():
        raise ValueError("Calibrator fit rows must strictly precede every application row")
    if not (rows.t + pd.Timedelta(hours=horizon)).le(apply_from).all():
        raise ValueError("Calibration labels cross the application boundary")
    if len(raw) != len(rows) or not rows[f"y_{horizon}"].notna().all():
        raise ValueError("Calibration requires aligned predictions and matured labels")
    fitted = IsotonicRegression(out_of_bounds="clip").fit(raw, rows[f"y_{horizon}"])
    return fitted, {"n": len(rows), "min_t": rows.t.min().isoformat(),
                    "max_t": rows.t.max().isoformat(),
                    "max_label_end": (rows.t.max() + pd.Timedelta(hours=horizon)).isoformat(),
                    "apply_from": apply_from.isoformat()}


def fit_chronological(
    kind: str, training: pd.DataFrame, horizon: int, boundary: pd.Timestamp,
    *, window: str, feature_names: list[str], categories: dict[str, list[str]],
    regime: pd.Timestamp, k: float = 0.0, rounds: int = 0, artifact_dir: Path | None = None,
    calibration_tail: pd.Timedelta | None = None,
) -> tuple[HorizonModel, IsotonicRegression, dict[str, object]]:
    """Calibrate forward predictions of a prefix model, then refit all matured rows."""
    tail_length = (pd.Timedelta(weeks=config.V2_CALIBRATION_WEEKS)
                   if calibration_tail is None else calibration_tail)
    if tail_length <= pd.Timedelta(0):
        raise ValueError("Chronological calibration needs a positive tail length")
    cutoff = boundary - tail_length
    prefix = fit_horizon(kind, training, horizon, cutoff, window=window,
                         feature_names=feature_names, categories=categories, regime=regime,
                         k=k, rounds=rounds)
    if artifact_dir is not None:
        joblib.dump(prefix, artifact_dir / f"calibration_prefix_{horizon}h.joblib")
    mask = training.t.ge(cutoff) & training.t.lt(boundary)
    mask &= (training.t + pd.Timedelta(hours=horizon)).le(boundary)
    mask &= training[f"y_{horizon}"].notna() & v2_data_gap_mask(training, horizon)
    tail = training.loc[mask]
    calibration, details = fit_calibrator(tail, predict_horizon(prefix, tail), horizon, boundary)
    final_model = fit_horizon(kind, training, horizon, boundary, window=window,
                              feature_names=feature_names, categories=categories, regime=regime,
                              k=k, rounds=prefix.rounds)
    details.update({"prefix_model_data_through": cutoff.isoformat(),
                    "refit_data_through": boundary.isoformat(), "rounds": prefix.rounds,
                    "calibration_predictions": "forward prefix-model predictions",
                    "calibration_tail_seconds": tail_length.total_seconds()})
    return final_model, calibration, details


def train_v2(*, out: Path | None = None) -> dict[str, object]:
    """Compare candidates on chronological validation; lock selection before test2 reads."""
    from airalert.models import baseline

    paths = metrics.v2_evaluation_paths(out)
    metrics.assert_v2_writable(paths.reports)
    began = perf_counter()
    saved = load_snapshot()
    training = pd.read_parquet(config.V2_FEATURES_DIR / "train.parquet")
    validation = pd.read_parquet(config.V2_FEATURES_DIR / "val.parquet")
    outputs = {"val": validation}
    for split in ("test2", "latest"):
        names = pq.read_schema(config.V2_FEATURES_DIR / f"{split}.parquet").names
        outputs[split] = pd.read_parquet(config.V2_FEATURES_DIR / f"{split}.parquet",
                                        columns=[n for n in names if not n.startswith("y_")])
    universe = load_raions()
    categories = {n: sorted(universe[n].unique()) for n in ("raion_id", "oblast_id")}
    features = [n for n in training if n.startswith("f_")]
    base_features = [n for n in features if not n.startswith("f_geo_")]
    candidates = [("B0", "B0", "full", base_features),
                  ("B1", "B1", "raionregime", base_features),
                  ("M1", "M1", "raionregime", base_features),
                  ("M1-full", "M1", "full", base_features)]
    if features != base_features:
        candidates += [("M1+geo", "M1", "raionregime", features),
                       ("M1-full+geo", "M1", "full", features)]
    details: dict[str, dict[str, object]] = {}
    paths.predictions.mkdir(parents=True, exist_ok=True)
    for identity, kind, window, names in candidates:
        artifact = paths.artifacts / identity
        artifact.mkdir(parents=True, exist_ok=True)
        raw_frames = _prediction_frames(outputs)
        calibrated_frames = _prediction_frames(outputs)
        details[identity] = {}
        for horizon in config.HORIZONS:
            k = 0.0
            fitted_window = window
            if kind == "B0":
                cutoff = pd.Timestamp(config.V2_VAL_START) - pd.Timedelta(
                    weeks=config.V2_CALIBRATION_WEEKS)
                fitted_window, k = baseline.tune_v2(training, horizon, cutoff, saved.regime_switch)
            model, val_calibrator, provenance = fit_chronological(
                kind, training, horizon, pd.Timestamp(config.V2_VAL_START),
                window=fitted_window, feature_names=names, categories=categories,
                regime=saved.regime_switch, k=k, artifact_dir=artifact,
            )
            joblib.dump(model, artifact / f"model_{horizon}h.joblib")
            joblib.dump(val_calibrator, artifact / f"calibrator_val_{horizon}h.joblib")
            raw = {split: predict_horizon(model, rows) if len(rows) else np.empty(0)
                   for split, rows in outputs.items()}
            val_mask = v2_eligible_mask(validation, horizon, "val", snapshot=saved.snapshot)
            test_calibrator, test_details = fit_calibrator(
                validation.loc[val_mask], raw["val"][val_mask], horizon,
                pd.Timestamp(config.V2_TEST_START),
            )
            joblib.dump(test_calibrator, artifact / f"calibrator_test2_{horizon}h.joblib")
            for split in outputs:
                raw_frames[split][f"p_{horizon}"] = raw[split]
                calibrator = val_calibrator if split == "val" else test_calibrator
                calibrated_frames[split][f"p_{horizon}"] = (
                    calibrator.predict(raw[split]) if len(raw[split]) else np.empty(0)
                )
            details[identity][str(horizon)] = {"window": fitted_window, "k": k,
                                               "validation_calibration": provenance,
                                               "test2_calibration": test_details}
            print(f"v2 {identity} H={horizon}: {perf_counter() - began:.1f}s elapsed", flush=True)
        for suffix, frames in (("raw", raw_frames), ("cal", calibrated_frames)):
            pd.concat(frames.values(), ignore_index=True).to_parquet(
                paths.predictions / f"onset_{identity}-{suffix}.parquet", index=False)
        write_v2_json(artifact / "metadata.json", details[identity])
    return select_v2(details, outputs, elapsed_seconds=perf_counter() - began, out=out)


def select_v2(
    details: dict[str, dict[str, object]], outputs: dict[str, pd.DataFrame],
    *, elapsed_seconds: float, out: Path | None = None,
) -> dict[str, object]:
    """Lock independent served-Brier horizon choices without reading any test2 targets."""
    from airalert.backtest import v2_score_predictions
    from airalert.schemas import Report, Selection

    paths = metrics.v2_evaluation_paths(out)
    metrics.assert_v2_writable(paths.reports)
    validation = outputs["val"]
    saved = load_snapshot()
    geography_available = any(n.startswith("f_geo_") for n in validation)
    report, _ = v2_score_predictions("val", prediction_dir=paths.predictions)
    choices: dict[str, dict[str, object]] = {}
    for horizon in config.HORIZONS:
        scores = [s for s in report.onset if s.horizon == horizon and s.row_set == "served"
                  and s.model != "selected"]
        chosen = min(scores, key=lambda s: (s.brier, s.model))
        identity, _, suffix = chosen.model.rpartition("-")
        choices[str(horizon)] = {"candidate": chosen.model, "identity": identity,
                                  "calibrated": suffix == "cal", "val_brier": chosen.brier,
                                  **details[identity][str(horizon)]}
    for split, rows in outputs.items():
        selected = rows[["t", "raion_id"]].assign(split=split).copy()
        for horizon in config.HORIZONS:
            candidate = choices[str(horizon)]["candidate"]
            predicted = pd.read_parquet(paths.predictions / f"onset_{candidate}.parquet",
                                         filters=[("split", "==", split)])
            selected[f"p_{horizon}"] = predicted[f"p_{horizon}"].to_numpy()
        selected.to_parquet(paths.predictions / f"selected_{split}.parquet", index=False)
    pd.concat([pd.read_parquet(paths.predictions / f"selected_{s}.parquet")
               for s in outputs], ignore_index=True).to_parquet(
                   paths.predictions / "onset_selected.parquet", index=False)
    selection: dict[str, object] = {"generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
                                    "snapshot": saved.snapshot.isoformat(), "horizons": choices,
                                    "rule": "minimum served validation Brier per horizon",
                                    "elapsed_seconds": elapsed_seconds,
                                    "data_gaps": [[a.isoformat(), b.isoformat()]
                                                  for a, b in v2_data_gaps()],
                                    "prediction_sha256": v2_prediction_hashes(paths.predictions),
                                    "geography_available": geography_available}
    write_v2_json(paths.selection, selection)
    fragment = Report(generated_at=pd.Timestamp.now(tz="UTC"), selection=Selection(
        onset="selected", duration=("D2" if (config.V2_PRODUCTION_DIR / "duration"
                                              / "current.json").exists() else None),
        note="Per-horizon choices locked in selection.json before test2 scoring."))
    write_v2_json(paths.fragments / "selection.json", fragment.model_dump(mode="json"))
    source = validation.groupby("f_source_archive", observed=True).agg(
        rows=("t", "size"), starts_24h=("f_starts_24h", "mean"),
        starts_168h=("f_starts_168h", "mean"), active_share=("f_active", "mean"))
    _, selected_rows = v2_score_predictions("val", prediction_dir=paths.predictions)
    source_scores: list[dict[str, object]] = []
    for horizon in config.HORIZONS:
        eligible = selected_rows.loc[v2_eligible_mask(selected_rows, horizon, "val")]
        for flag, group in eligible.groupby("f_source_archive", observed=True):
            source_scores.append({"archive": bool(flag), "horizon": horizon, "n": len(group),
                                   "brier": metrics.brier_score(group[f"y_{horizon}"],
                                                                 group[f"p_{horizon}"]),
                                   "bss": metrics.brier_skill_score(group[f"y_{horizon}"],
                                                                     group[f"p_{horizon}"],
                                                                     group[f"b0_{horizon}"])})
    write_v2_json(paths.reports / "source_shift.json", {
        "flag": "first archive start for this raion is strictly before t",
        "validation_groups": source.reset_index().to_dict(orient="records"),
        "served_validation_scores": source_scores,
        "warning": "Groups differ in date/raion mix; descriptive rather than causal comparison.",
        "geography": [s.model_dump(mode="json") for s in report.onset
                      if "+geo" in s.model and s.row_set == "served"],
    })
    print(f"Locked v2 selection: { {h: c['candidate'] for h, c in choices.items()} }", flush=True)
    return selection


def refresh_v2_validation(*, out: Path | None = None) -> dict[str, object]:
    """Refresh calibration/selection after the lead changes the gap policy, before final."""
    paths = metrics.v2_evaluation_paths(out)
    metrics.assert_v2_writable(paths.reports)
    began = perf_counter()
    validation = pd.read_parquet(config.V2_FEATURES_DIR / "val.parquet")
    outputs: dict[str, pd.DataFrame] = {"val": validation}
    details: dict[str, dict[str, object]] = {}
    for path in sorted(paths.predictions.glob("onset_*-raw.parquet")):
        identity = path.stem.removeprefix("onset_").removesuffix("-raw")
        artifact = paths.artifacts / identity
        # Older CLI bundles named __main__.HorizonModel. In this CLI context the
        # class is available; re-saving invokes its stable importable reducer.
        for bundle in [*artifact.glob("model_*.joblib"),
                       *artifact.glob("calibration_prefix_*.joblib")]:
            model = joblib.load(bundle)
            joblib.dump(model, bundle)
        metadata = json.loads((artifact / "metadata.json").read_text(encoding="utf-8"))
        raw = pd.read_parquet(path)
        for split in ("test2", "latest"):
            outputs[split] = raw.loc[raw.split.eq(split), ["t", "raion_id"]]
        calibrated = raw.copy()
        val_rows = raw.loc[raw.split.eq("val")]
        for horizon in config.HORIZONS:
            mask = v2_eligible_mask(validation, horizon, "val")
            calibrator, provenance = fit_calibrator(
                validation.loc[mask], val_rows[f"p_{horizon}"].to_numpy()[mask], horizon,
                pd.Timestamp(config.V2_TEST_START))
            joblib.dump(calibrator, artifact / f"calibrator_test2_{horizon}h.joblib")
            metadata[str(horizon)]["test2_calibration"] = provenance
            val_calibrator = joblib.load(artifact / f"calibrator_val_{horizon}h.joblib")
            for split in ("val", "test2", "latest"):
                selected = raw.split.eq(split)
                fitted = val_calibrator if split == "val" else calibrator
                if selected.any():
                    calibrated.loc[selected, f"p_{horizon}"] = fitted.predict(
                        raw.loc[selected, f"p_{horizon}"])
        calibrated.to_parquet(paths.predictions / f"onset_{identity}-cal.parquet", index=False)
        write_v2_json(artifact / "metadata.json", metadata)
        details[identity] = metadata
    return select_v2(details, outputs, elapsed_seconds=perf_counter() - began, out=out)


def wait_for_backfill(*, out: Path | None = None) -> None:
    """Watch the saved coverage at five-minute intervals for at most two hours."""
    paths = metrics.v2_evaluation_paths(out)
    metrics.assert_v2_writable(paths.reports)
    began = perf_counter()
    target = pd.Timestamp.now(tz=config.KYIV_TZ).normalize().tz_convert("UTC")
    polls: list[dict[str, object]] = []
    while True:
        snapshot = load_snapshot().snapshot
        metadata = json.loads(config.SNAPSHOT_PATH.read_text(encoding="utf-8"))
        available = (snapshot >= target and metadata.get("coverage", {}).get(
            "archive_gate_passed", False))
        elapsed = perf_counter() - began
        state = "available" if available else "timeout" if elapsed >= config.V2_BACKFILL_WAIT_S \
            else "waiting"
        polls.append({"checked_at": pd.Timestamp.now(tz="UTC").isoformat(),
                      "snapshot": snapshot.isoformat()})
        write_v2_json(paths.reports / "backfill_watch.json", {
            "state": state, "target": target.isoformat(), "polls": polls,
            "elapsed_seconds": elapsed, "poll_seconds": config.V2_BACKFILL_POLL_S,
            "maximum_seconds": config.V2_BACKFILL_WAIT_S})
        print(f"Backfill: {state}; {snapshot.isoformat()}; {elapsed:.1f}s", flush=True)
        if state != "waiting":
            return
        sleep(min(config.V2_BACKFILL_POLL_S, config.V2_BACKFILL_WAIT_S - elapsed))


def main(argv: list[str] | None = None) -> None:
    """Expose the real CLI to isolated tests without invoking main-checkout scoring."""
    import argparse

    parser = argparse.ArgumentParser(description="Isolated v2 onset evaluation")
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument("--refresh-validation", action="store_true")
    parser.add_argument("--watch-backfill", action="store_true")
    parser.add_argument("--out", nargs="?", type=Path,
                        const=config.V2_ARTIFACTS_DIR / "experiments",
                        help="Experiment directory; --out alone uses artifacts/v2/experiments")
    arguments = parser.parse_args(argv)
    from airalert.backtest import run_v2
    from airalert.features import build_v2

    metrics.assert_v2_writable(metrics.v2_evaluation_paths(arguments.out).reports)
    if arguments.watch_backfill:
        wait_for_backfill(out=arguments.out)
    elif arguments.refresh_validation:
        refresh_v2_validation(out=arguments.out)
    elif not arguments.score_only:
        build_v2()
        train_v2(out=arguments.out)
    if not arguments.watch_backfill:
        run_v2(final=arguments.final, out=arguments.out)


if __name__ == "__main__":
    main()
