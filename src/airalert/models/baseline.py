"""Training-only climatology with oblast shrinkage, tuned on validation Brier.

Each horizon independently chooses its training window and pseudo-count. Test
labels are never loaded: held-out and latest files supply prediction keys only.
"""

import json
from dataclasses import dataclass
from time import perf_counter

import numpy as np
import pandas as pd

from airalert import config, metrics
from airalert.backtest import assign_split, mask_diagnostics, scorable_mask, trainable_mask
from airalert.data import load_snapshot

# Pseudo-counts are tuning candidates, in equivalent raion/cell training rows.
PSEUDO_COUNTS = (1.0, 24.0, 168.0, 720.0, 2160.0)
CELL = ["local_hour", "weekend"]


@dataclass(frozen=True)
class Climatology:
    """Sufficient statistics fitted only on eligible training rows."""

    raion: pd.DataFrame
    oblast: pd.DataFrame
    oblast_overall: pd.Series
    global_rate: float


def _calendar(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    local = result.t.dt.tz_convert(config.KYIV_TZ)
    result["local_hour"] = local.dt.hour.astype("int8")
    result["weekend"] = local.dt.dayofweek.ge(5).astype("int8")
    return result


def fit_rates(rows: pd.DataFrame, horizon: int) -> Climatology:
    """Fit sufficient statistics on a caller's trainable, window-filtered rows."""
    if rows.empty or not rows[f"y_{horizon}"].notna().all():
        raise ValueError("Climatology requires nonempty training rows with known labels")
    label = f"y_{horizon}"
    raion = rows.groupby(["raion_id", "oblast_id", *CELL], observed=True)[label].agg(
        positives="sum", count="size"
    ).reset_index()
    oblast = rows.groupby(["oblast_id", *CELL], observed=True)[label].agg(
        positives="sum", count="size"
    ).reset_index()
    oblast["oblast_rate"] = oblast.positives / oblast["count"]
    overall = rows.groupby("oblast_id", observed=True)[label].mean().astype(float)
    return Climatology(raion, oblast, overall, float(rows[label].mean()))


def predict_rates(rows: pd.DataFrame, fitted: Climatology, k: float) -> np.ndarray:
    """Apply raion shrinkage; unseen cells fall back to oblast, then global rate.

    The two many-to-one joins preserve row order, so results align directly
    with labels or forecast keys. Calendar cells use Kyiv time, including DST.
    """
    if k < 0:
        raise ValueError("Pseudo-count must be nonnegative")
    joined = rows[["raion_id", "oblast_id", *CELL]].merge(
        fitted.raion, on=["raion_id", "oblast_id", *CELL], how="left", validate="many_to_one"
    ).merge(
        fitted.oblast[["oblast_id", *CELL, "oblast_rate"]], on=["oblast_id", *CELL],
        how="left", validate="many_to_one"
    )
    prior = joined.oblast_rate.fillna(joined.oblast_id.map(fitted.oblast_overall)).fillna(
        fitted.global_rate
    ).to_numpy(dtype=float)
    counts = joined["count"].fillna(0).to_numpy(dtype=float)
    positives = joined.positives.fillna(0).to_numpy(dtype=float)
    denominator = counts + k
    prediction = prior.copy()
    np.divide(positives + k * prior, denominator, out=prediction, where=denominator > 0)
    return prediction


def _read_features(split: str, *, labels: bool) -> pd.DataFrame:
    columns = ["t", "raion_id", "oblast_id"]
    if labels:
        columns.extend(f"y_{h}" for h in config.HORIZONS)
    frame = pd.read_parquet(config.FEATURES_DIR / f"{split}.parquet", columns=columns)
    if frame.duplicated(["t", "raion_id"]).any() or frame[columns[:3]].isna().any().any():
        raise ValueError(f"{split} features have duplicate or null forecast keys")
    if not assign_split(frame.t).eq(split).all():
        raise ValueError(f"{split} features contain forecast times outside that split")
    return _calendar(frame)


def train() -> None:
    """Tune each horizon on val, persist train statistics, predict all replay keys."""
    began = perf_counter()
    snapshot = load_snapshot()
    training = _read_features("train", labels=True)
    validation = _read_features("val", labels=True)
    # Deliberately request no target columns from test/latest.
    outputs = {"val": validation}
    outputs.update({split: _read_features(split, labels=False) for split in ("test", "latest")})
    predictions = {
        split: frame[["t", "raion_id"]].assign(split=split) for split, frame in outputs.items()
    }
    artifact_dir = config.ARTIFACTS_DIR / "B0"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    comparisons: list[dict[str, object]] = []
    selections: list[dict[str, object]] = []
    for horizon in config.HORIZONS:
        eligible = training.loc[trainable_mask(training, horizon)]
        val = validation.loc[scorable_mask(validation, horizon)]
        if val.empty:
            raise ValueError(f"No scorable validation rows at horizon {horizon}")
        y = val[f"y_{horizon}"].to_numpy(dtype=float)
        winner: tuple[float, str, float, Climatology] | None = None
        for window, rows in (("full", eligible), ("raion-regime", eligible.loc[
            eligible.t.ge(snapshot.regime_switch)
        ])):
            fitted = fit_rates(rows, horizon)
            candidates = [(metrics.brier_score(y, predict_rates(val, fitted, k)), k)
                          for k in PSEUDO_COUNTS]
            score, k = min(candidates)
            comparisons.append({
                "horizon": horizon, "window": window, "train_n": len(rows),
                "val_n": len(val), "k": k, "val_brier": score,
                "candidates": [{"k": count, "val_brier": loss} for loss, count in candidates],
            })
            print(f"B0 H={horizon} {window}: train_n={len(rows)} val_n={len(val)} "
                  f"best_k={k:g} val_Brier={score:.6f}")
            if winner is None or score < winner[0]:
                winner = score, window, k, fitted
        assert winner is not None
        score, window, k, selected = winner
        selected.raion.to_parquet(artifact_dir / f"raion_{horizon}h.parquet", index=False)
        selected.oblast.to_parquet(artifact_dir / f"oblast_{horizon}h.parquet", index=False)
        selected.oblast_overall.rename("rate").reset_index().to_parquet(
            artifact_dir / f"oblast_overall_{horizon}h.parquet", index=False
        )
        selections.append({"horizon": horizon, "window": window, "k": k,
                           "val_brier": score, "global_rate": selected.global_rate})
        for split, rows in outputs.items():
            predictions[split][f"p_{horizon}"] = predict_rates(rows, selected, k)
        print(f"H={horizon} train masks: {mask_diagnostics(training, horizon)}")
        print(f"H={horizon} val masks: {mask_diagnostics(validation, horizon)}")
    metadata = {
        "model": "B0", "snapshot": snapshot.snapshot.isoformat().replace("+00:00", "Z"),
        "regime_switch": snapshot.regime_switch.isoformat().replace("+00:00", "Z"),
        "csv_sha256": snapshot.csv_sha256, "cell": "raion x Kyiv local hour x weekday/weekend",
        "formula": "(raion positives + k * oblast cell rate) / (raion rows + k)",
        "selection_criterion": "minimum validation Brier per horizon; full window wins exact ties",
        "fallback": "oblast cell, oblast overall, training global mean",
        "gap_feature_recovery_h": config.DATA_GAP_FEATURE_RECOVERY_H,
        "data_gaps": [
            {"start": start.isoformat().replace("+00:00", "Z"),
             "end": end.isoformat().replace("+00:00", "Z")}
            for start, end in config.DATA_GAPS
        ],
        "comparisons": comparisons, "selected": selections,
    }
    (artifact_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, allow_nan=False)
                                              + "\n", encoding="utf-8")
    result = pd.concat(predictions.values(), ignore_index=True)
    result["t"] = result.t.astype("datetime64[us, UTC]")
    config.PREDS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.PREDS_DIR / "onset_B0.parquet"
    temporary = path.with_suffix(".parquet.tmp")
    result.to_parquet(temporary, index=False)
    temporary.replace(path)
    print(f"Wrote onset_B0.parquet: {len(result)} rows "
          f"{result.groupby('split').size().to_dict()}; {perf_counter() - began:.2f}s")
