"""Shared forecast eligibility and explicit, validation-first model evaluation.

Models import trainable_mask/scorable_mask instead of reproducing date policy.
scorable_mask requires final=True to include the held-out test set.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, overload

import numpy as np
import pandas as pd

from airalert import config, metrics
from airalert.data import load_snapshot
from airalert.schemas import DurationScore, OnsetScore, Report, ScoreSlice

Split = Literal["train", "val", "test", "latest", "none"]


def v2_test_end(snapshot: datetime | pd.Timestamp | None = None) -> pd.Timestamp:
    """Stop twelve hours before the end of the last complete observed Kyiv day."""
    observed = _snapshot(snapshot)
    complete_end = observed.tz_convert(config.KYIV_TZ).normalize().tz_convert("UTC")
    return complete_end - pd.Timedelta(hours=config.V2_TEST_END_OFFSET_H)


def v2_assign_split(t: pd.Series, *, snapshot: pd.Timestamp | None = None) -> pd.Series:
    times = _utc_times(t)
    observed = _snapshot(snapshot)
    result = pd.Series("none", index=times.index, dtype="str")
    result.loc[times.ge(config.V2_TRAIN_START) & times.lt(config.V2_VAL_START)] = "train"
    result.loc[times.ge(config.V2_VAL_START) & times.lt(config.V2_TEST_START)] = "val"
    result.loc[times.ge(config.V2_TEST_START) & times.le(v2_test_end(observed))] = "test2"
    result.loc[times.eq(observed)] = "latest"
    return result


def v2_eligible_mask(
    frame: pd.DataFrame, horizon: int, split: str, *, final: bool = False,
    snapshot: pd.Timestamp | None = None,
) -> pd.Series:
    """Central v2 split, embargo, maturity and unchanged source-gap policy."""
    _check_horizon(horizon)
    if split == "test2" and not final:
        raise ValueError("test2 scoring requires final=True")
    if split not in ("train", "val", "test2"):
        raise ValueError(f"Unsupported v2 split {split}")
    times = _utc_times(frame.t)
    label = frame[f"y_{horizon}"]
    if not label.dropna().isin([0, 1]).all():
        raise ValueError("Onset labels must be binary or null")
    end = times + pd.Timedelta(hours=horizon)
    upper = {"train": config.V2_VAL_START, "val": config.V2_TEST_START,
             "test2": _snapshot(snapshot)}[split]
    return (v2_assign_split(times, snapshot=snapshot).eq(split) & label.notna()
            & end.le(upper) & end.le(_snapshot(snapshot)) & v2_data_gap_mask(frame, horizon))


def _utc_times(values: pd.Series) -> pd.Series:
    """Reject naive clocks rather than silently interpreting them as UTC."""
    if not isinstance(values.dtype, pd.DatetimeTZDtype):
        raise ValueError("Forecast times must be timezone-aware pandas datetimes")
    if values.isna().any():
        raise ValueError("Forecast times cannot be null")
    return values.dt.tz_convert("UTC")


def _snapshot(value: datetime | pd.Timestamp | None) -> pd.Timestamp:
    result = load_snapshot().snapshot if value is None else pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("Snapshot must be timezone-aware")
    return result.tz_convert("UTC")


@overload
def assign_split(
    t: pd.Series | pd.DatetimeIndex, *, snapshot: datetime | pd.Timestamp | None = None
) -> pd.Series: ...


@overload
def assign_split(
    t: datetime | str, *, snapshot: datetime | pd.Timestamp | None = None
) -> Split: ...


def assign_split(
    t: pd.Series | pd.DatetimeIndex | datetime | str,
    *,
    snapshot: datetime | pd.Timestamp | None = None,
) -> pd.Series | Split:
    """Assign UTC forecast times; test end is inclusive and latest is one hour.

    Read the observation clock from snapshot.json, never wall-clock time. An
    explicit snapshot supports small synthetic frames without production I/O.
    Series inputs preserve their index; DatetimeIndex inputs get a RangeIndex.
    """
    scalar = not isinstance(t, (pd.Series, pd.DatetimeIndex))
    times = _utc_times(pd.Series([pd.Timestamp(t)]) if scalar else pd.Series(t))
    observed = _snapshot(snapshot)
    result = pd.Series("none", index=times.index, dtype="str")
    result.loc[times.ge(config.TRAIN_START) & times.lt(config.VAL_START)] = "train"
    result.loc[times.ge(config.VAL_START) & times.lt(config.TEST_START)] = "val"
    test_end = observed - pd.Timedelta(hours=config.TEST_END_OFFSET_H)
    result.loc[times.ge(config.TEST_START) & times.le(test_end)] = "test"
    result.loc[times.eq(observed)] = "latest"
    return result.iloc[0] if scalar else result


def _check_horizon(horizon: int) -> None:
    if horizon not in config.HORIZONS:
        raise ValueError(f"Unsupported horizon {horizon}; expected {config.HORIZONS}")


def embargo_mask(df: pd.DataFrame, horizon: int) -> pd.Series:
    """Keep windows ending on a split boundary; reject those ending after it."""
    _check_horizon(horizon)
    times = _utc_times(df["t"])
    end = times + pd.Timedelta(hours=horizon)
    train = times.ge(config.TRAIN_START) & times.lt(config.VAL_START)
    val = times.ge(config.VAL_START) & times.lt(config.TEST_START)
    return ~((train & end.gt(config.VAL_START)) | (val & end.gt(config.TEST_START)))


def data_gap_mask(df: pd.DataFrame, horizon: int) -> pd.Series:
    """Keep rows with intact labels and lookbacks under config.DATA_GAPS.

    A label window (t, t+H] intersects [start, end) iff t < end and
    t+H >= start. Independently reject t in [start, end + recovery_hours).
    The returned boolean Series means KEEP and preserves the frame's index.
    """
    _check_horizon(horizon)
    times = _utc_times(df["t"])
    window_end = times + pd.Timedelta(hours=horizon)
    keep = pd.Series(True, index=df.index)
    recovery = pd.Timedelta(hours=config.DATA_GAP_FEATURE_RECOVERY_H)
    for start, end in config.DATA_GAPS:
        label_overlap = times.lt(end) & window_end.ge(start)
        corrupted_lookback = times.ge(start) & times.lt(pd.Timestamp(end) + recovery)
        keep &= ~(label_overlap | corrupted_lookback)
    return keep


def v2_data_gaps() -> tuple[tuple[pd.Timestamp, pd.Timestamp], ...]:
    """Union unresolved configured outages with reconciled snapshot coverage gaps."""
    gaps = {(pd.Timestamp(a), pd.Timestamp(b)) for a, b in config.DATA_GAPS}
    if config.SNAPSHOT_PATH.exists():
        metadata = json.loads(config.SNAPSHOT_PATH.read_text(encoding="utf-8"))
        for gap in metadata.get("coverage", {}).get("gaps", []):
            start, end = pd.Timestamp(gap["start"]), pd.Timestamp(gap["end"])
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                raise ValueError("Coverage gaps require aware, ordered timestamps")
            gaps.add((start.tz_convert("UTC"), end.tz_convert("UTC")))
    return tuple(sorted(gaps))


def v2_data_gap_mask(df: pd.DataFrame, horizon: int) -> pd.Series:
    """Apply the v1 overlap/recovery formula to the data-driven v2 coverage list."""
    _check_horizon(horizon)
    times = _utc_times(df.t)
    end = times + pd.Timedelta(hours=horizon)
    keep = pd.Series(True, index=df.index)
    for start, stop in v2_data_gaps():
        recovery = stop + pd.Timedelta(hours=config.DATA_GAP_FEATURE_RECOVERY_H)
        keep &= ~((times.lt(stop) & end.ge(start)) | (times.ge(start) & times.lt(recovery)))
    return keep


def _eligible(
    df: pd.DataFrame, horizon: int, snapshot: datetime | pd.Timestamp | None
) -> tuple[pd.Series, pd.Series]:
    splits = assign_split(df["t"], snapshot=snapshot)
    label = df[f"y_{horizon}"]
    if not label.dropna().isin([0, 1]).all():
        raise ValueError(f"y_{horizon} must contain only 0, 1, or null")
    keep = embargo_mask(df, horizon) & data_gap_mask(df, horizon) & label.notna()
    return splits, keep


def trainable_mask(
    df: pd.DataFrame, horizon: int, *, snapshot: datetime | pd.Timestamp | None = None
) -> pd.Series:
    """Keep TRAIN rows only, with known binary labels, embargo, and gap policy.

    Requires t and y_<horizon>; does not trust a caller's split column. No
    validation/test/latest row can become training data through this function.
    """
    splits, keep = _eligible(df, horizon, snapshot)
    return splits.eq("train") & keep


def scorable_mask(
    df: pd.DataFrame,
    horizon: int,
    *,
    final: bool = False,
    snapshot: datetime | pd.Timestamp | None = None,
) -> pd.Series:
    """Keep VAL rows; include TEST only on explicit final=True.

    Requires t and y_<horizon>. Both splits apply horizon-specific embargo,
    label availability, and the identical source-outage/recovery policy used
    for fitting. Train/latest/out-of-range rows are never scoring rows.
    """
    splits, keep = _eligible(df, horizon, snapshot)
    allowed = splits.eq("val") | (splits.eq("test") if final else False)
    return allowed & keep


def mask_diagnostics(df: pd.DataFrame, horizon: int) -> dict[str, int]:
    """Count exclusion reasons independently, plus their union and null labels."""
    embargo = ~embargo_mask(df, horizon)
    gap = ~data_gap_mask(df, horizon)
    null = df[f"y_{horizon}"].isna()
    return {
        "rows": len(df),
        "embargo": int(embargo.sum()),
        "data_gap": int(gap.sum()),
        "null_label": int(null.sum()),
        "excluded_union": int((embargo | gap | null).sum()),
    }


def _validate_keys(frame: pd.DataFrame, keys: list[str], description: str) -> None:
    if frame[keys].isna().any().any() or frame.duplicated(keys).any():
        raise ValueError(f"{description} contains null or duplicate keys {keys}")


def _onset_predictions(path: Path, split: str) -> tuple[str, pd.DataFrame]:
    """Filter Parquet before loading, so validation runs never read test rows.

    Restricted files may use a row_set column or an @row-set filename suffix.
    The suffix is excluded from the model id and must agree with any column.
    Missing full predictions are errors, never implicit restricted comparisons.
    """
    identity = path.stem.removeprefix("onset_")
    model, separator, named_set = identity.partition("@")
    if not model or (separator and not named_set):
        raise ValueError(f"Invalid model/row-set name: {path.name}")
    frame = pd.read_parquet(path, filters=[("split", "==", split)])
    required = {"t", "raion_id", "split", *(f"p_{h}" for h in config.HORIZONS)}
    if missing := required.difference(frame.columns):
        raise ValueError(f"{path.name} is missing {sorted(missing)}")
    frame["t"] = _utc_times(frame["t"])
    if not assign_split(frame.t).eq(split).all():
        raise ValueError(f"{path.name} declares a split inconsistent with forecast times")
    if "row_set" not in frame:
        frame["row_set"] = named_set if separator else "full"
    if frame.row_set.isna().any() or not all(
        isinstance(value, str) and bool(value.strip()) for value in frame.row_set.unique()
    ):
        raise ValueError(f"{path.name} needs nonempty string row_set values")
    if separator and not frame.row_set.eq(named_set).all():
        raise ValueError(f"{path.name} row_set conflicts with its filename suffix")
    _validate_keys(frame, ["t", "raion_id", "row_set"], path.name)
    probabilities = frame[[f"p_{h}" for h in config.HORIZONS]].to_numpy(dtype=float)
    if not np.isfinite(probabilities).all() or (
        (probabilities < 0) | (probabilities > 1)
    ).any():
        raise ValueError(f"{path.name} probabilities must be finite and in [0, 1]")
    return model, frame


def _score_slice(frame: pd.DataFrame, horizon: int) -> ScoreSlice:
    y = frame[f"y_{horizon}"].to_numpy(dtype=float)
    p = frame[f"p_{horizon}"].to_numpy(dtype=float)
    reference = frame[f"b0_{horizon}"].to_numpy(dtype=float)
    return ScoreSlice(
        n=len(frame), base_rate=float(y.mean()), brier=metrics.brier_score(y, p),
        bss=metrics.brier_skill_score(y, p, reference),
    )


def _score_onset(
    model: str, row_set: str, frame: pd.DataFrame, horizon: int, split: str
) -> OnsetScore:
    y = frame[f"y_{horizon}"].to_numpy(dtype=float)
    p = frame[f"p_{horizon}"].to_numpy(dtype=float)
    score = _score_slice(frame, horizon)
    return OnsetScore(
        model=model, horizon=horizon, split=split, row_set=row_set,
        **score.model_dump(), logloss=metrics.log_loss(y, p), pr_auc=metrics.pr_auc(y, p),
        reliability=metrics.reliability(y, p),
        by_oblast={
            str(oblast): _score_slice(group, horizon)
            for oblast, group in frame.groupby("oblast_id", observed=True, sort=True)
        },
        by_state={
            state: _score_slice(group, horizon)
            for state, value in (("quiet", 0), ("active", 1))
            if not (group := frame.loc[frame.f_active.eq(value)]).empty
        },
    )


def _onset_scores(split: str, *, final: bool) -> list[OnsetScore]:
    if split == "test" and not final:
        raise ValueError("Test scoring requires final=True")
    paths = sorted(config.PREDS_DIR.glob("onset_*.parquet"))
    if not paths:
        return []
    baseline_path = config.PREDS_DIR / "onset_B0.parquet"
    if baseline_path not in paths:
        raise FileNotFoundError("BSS requires onset_B0.parquet; train B0 before backtesting")
    columns = ["t", "raion_id", "oblast_id", "f_active", *(f"y_{h}" for h in config.HORIZONS)]
    labels = pd.read_parquet(config.FEATURES_DIR / f"{split}.parquet", columns=columns)
    labels["t"] = _utc_times(labels["t"])
    _validate_keys(labels, ["t", "raion_id"], f"{split} features")
    if not assign_split(labels.t).eq(split).all() or not labels.f_active.isin([0, 1]).all():
        raise ValueError(f"{split} features have inconsistent times or nonbinary f_active")
    _, baseline = _onset_predictions(baseline_path, split)
    if not baseline.row_set.eq("full").all():
        raise ValueError("B0 must predict the full forecast universe")
    reference = baseline.drop(columns=["split", "row_set"]).rename(
        columns={f"p_{h}": f"b0_{h}" for h in config.HORIZONS}
    )
    truth = labels.merge(reference, on=["t", "raion_id"], how="outer", validate="one_to_one",
                         indicator=True)
    if not truth._merge.eq("both").all():
        raise ValueError(f"B0 coverage does not match {split} feature keys exactly")
    truth = truth.drop(columns="_merge")
    scores: list[OnsetScore] = []
    for path in [baseline_path, *(path for path in paths if path != baseline_path)]:
        model, predictions = _onset_predictions(path, split)
        for row_set, rows in predictions.groupby("row_set", sort=True):
            joined = rows.merge(truth, on=["t", "raion_id"], how="outer" if row_set == "full"
                                else "left", validate="one_to_one", indicator=True)
            if not joined._merge.eq("both").all():
                raise ValueError(f"{path.name}/{row_set} has unmatched or missing forecast keys")
            for horizon in config.HORIZONS:
                selected = joined.loc[scorable_mask(joined, horizon, final=final)]
                if not selected.empty:
                    scores.append(_score_onset(model, str(row_set), selected, horizon, split))
    for horizon in config.HORIZONS:
        print(f"{split} H={horizon} masks: {mask_diagnostics(labels, horizon)}")
    return scores


def score_duration_frame(
    model: str, split: str, frame: pd.DataFrame, *, final: bool = False
) -> DurationScore:
    """Score an already eligible, episode-joined frame in minutes; fail on crossings."""
    if split == "test" and not final:
        raise ValueError("Test scoring requires final=True")
    y = (frame.end - frame.start).dt.total_seconds().to_numpy(dtype=float) / 60
    if (y < 0).any():
        raise ValueError("Actual episode durations cannot be negative")
    y = np.maximum(y, 1)
    quantiles = frame[["q10_min", "q50_min", "q90_min"]].to_numpy(dtype=float)
    if (quantiles < 0).any() or (np.diff(quantiles, axis=1) < 0).any():
        raise ValueError("Duration quantiles must be nonnegative and ordered")
    return DurationScore(
        model=model, split=split, n=len(frame),
        pinball_q10=metrics.pinball_loss(y, quantiles[:, 0], 0.1),
        pinball_q50=metrics.pinball_loss(y, quantiles[:, 1], 0.5),
        pinball_q90=metrics.pinball_loss(y, quantiles[:, 2], 0.9),
        mae_q50_min=metrics.mae_median(y, quantiles[:, 1]),
        coverage_80=metrics.interval_coverage(y, quantiles[:, 0], quantiles[:, 2]),
    )


def _duration_scores(split: str, *, final: bool = False) -> list[DurationScore]:
    if split == "test" and not final:
        raise ValueError("Test scoring requires final=True")
    paths = sorted(config.PREDS_DIR.glob("duration_*.parquet"))
    if not paths:
        return []
    # B3 owns this rule, including end-before-next-split, censoring, and gaps.
    from airalert.models.duration import episode_split, episode_split_mask

    lower = config.VAL_START if split == "val" else config.TEST_START
    snapshot = load_snapshot().snapshot
    upper = config.TEST_START if split == "val" else snapshot
    episodes = pd.read_parquet(
        config.EPISODES_PATH, filters=[("start", ">=", lower), ("start", "<", upper)]
    )
    _validate_keys(episodes, ["raion_id", "start"], "episodes")
    eligible = episodes.loc[episode_split_mask(episodes, split, snapshot)]
    scores: list[DurationScore] = []
    for path in paths:
        predictions = pd.read_parquet(path, filters=[("split", "==", split)])
        _validate_keys(predictions, ["raion_id", "start"], path.name)
        joined = predictions.merge(episodes, on=["raion_id", "start"], how="left",
                                   validate="one_to_one", indicator=True)
        if not joined._merge.eq("both").all():
            raise ValueError(f"{path.name} contains unknown episodes or incorrect splits")
        if not episode_split(joined, snapshot).eq(split).all():
            raise ValueError(f"{path.name} declares incorrect episode splits")
        selected = eligible.merge(predictions, on=["raion_id", "start"],
                                  how="left", validate="one_to_one", indicator=True)
        if not selected._merge.eq("both").all():
            raise ValueError(f"{path.name} is missing eligible {split} episodes")
        if not selected.empty:
            scores.append(score_duration_frame(
                path.stem.removeprefix("duration_"), split, selected, final=final
            ))
    return scores


def _draw_reliability(scores: list[OnsetScore], split: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    for horizon in config.HORIZONS:
        fig, axis = plt.subplots(figsize=(6.4, 5.4), facecolor="#000000")
        axis.set_facecolor("#000000")
        axis.plot([0, 1], [0, 1], color="#777777", linestyle="--", linewidth=1)
        for score in scores:
            if score.horizon != horizon:
                continue
            occupied = [cell for cell in score.reliability if cell.count]
            label = score.model + (f" @{score.row_set}" if score.row_set != "full" else "")
            axis.plot([cell.mean_pred for cell in occupied], [cell.frac_pos for cell in occupied],
                      marker="o", linewidth=1.5, label=label)
        axis.set(xlim=(0, 1), ylim=(0, 1), xlabel="Mean predicted probability",
                 ylabel="Observed onset frequency", title=f"{split} reliability, {horizon} h")
        axis.tick_params(colors="#eeeeee")
        for item in (axis.xaxis.label, axis.yaxis.label, axis.title):
            item.set_color("#eeeeee")
        for spine in axis.spines.values():
            spine.set_color("#666666")
        if axis.get_legend_handles_labels()[0]:
            axis.legend(facecolor="#000000", edgecolor="#555555", labelcolor="#eeeeee")
        fig.tight_layout()
        fig.savefig(config.FIGURES_DIR / f"reliability_{split}_{horizon}h.png", dpi=160)
        plt.close(fig)


def run(final: bool = False) -> None:
    """Score validation only unless --final explicitly opens held-out test data.

    Prediction reads use split predicates; default runs do not open test
    features or test episode targets, and merging omits stale test fragments.
    """
    for split in ("val", "test") if final else ("val",):
        report = Report(generated_at=datetime.now(UTC), onset=_onset_scores(split, final=final),
                        duration=_duration_scores(split, final=final), notes=[
                            "BSS uses B0 on identical rows, including each oblast/state slice.",
                            "Restricted comparisons declare row_set or an @row-set suffix.",
                            "Outage label windows and corrupted lookbacks are excluded.",
                        ])
        config.METRICS_DIR.mkdir(parents=True, exist_ok=True)
        path = config.SCORES_VAL_PATH if split == "val" else config.SCORES_TEST_PATH
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report.model_dump(mode="json"), indent=2,
                                        allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
        _draw_reliability(report.onset, split)
        print(f"Wrote {path.name}: {len(report.onset)} onset and "
              f"{len(report.duration)} duration scores")
        if split == "test":
            print("TEST SET SCORED (--final explicitly enabled)")
        for score in report.onset:
            if score.model == "B0":
                print(f"B0 {split}: H={score.horizon} n={score.n} base_rate={score.base_rate:.6f} "
                      f"Brier={score.brier:.6f} logloss={score.logloss:.6f} "
                      f"PR-AUC={score.pr_auc:.6f}")
    merged = metrics.merge_reports(include_test=final)
    print(f"Validated metrics.json: {len(merged.onset)} onset, "
          f"{len(merged.duration)} duration scores")


def write_v2_json(path: Path, value: object) -> None:
    """Replace complete v2 metadata without exposing a partially written JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".json.tmp")
    staging.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    staging.replace(path)


def v2_prediction_hashes() -> dict[str, str]:
    """Hash complete prediction files without opening any test2 target column."""
    hashes: dict[str, str] = {}
    for path in sorted(config.V2_PREDS_DIR.glob("onset_*.parquet")):
        with path.open("rb") as handle:
            hashes[path.name] = hashlib.file_digest(handle, "sha256").hexdigest()
    return hashes


def v2_score_predictions(split: str, *, final: bool = False) -> tuple[Report, pd.DataFrame]:
    """Score both unprojected and served probabilities on identical eligible rows."""
    if split == "test2" and not final:
        raise ValueError("test2 scoring requires final=True")
    columns = ["t", "raion_id", "oblast_id", "f_active", "f_source_archive",
               *(f"y_{h}" for h in config.HORIZONS)]
    truth = pd.read_parquet(config.V2_FEATURES_DIR / f"{split}.parquet", columns=columns)
    _validate_keys(truth, ["t", "raion_id"], f"v2 {split}")
    report = Report(generated_at=datetime.now(UTC))
    if truth.empty:
        report.notes.append("test2 has no observed eligible period; no test outcomes were scored.")
        return report, truth
    baseline = pd.read_parquet(config.V2_PREDS_DIR / "onset_B0-raw.parquet",
                               filters=[("split", "==", split)])
    reference = metrics.monotone_projection(baseline[[f"p_{h}" for h in config.HORIZONS]])
    baseline = baseline[["t", "raion_id"]].copy()
    baseline[[f"b0_{h}" for h in config.HORIZONS]] = reference
    truth = truth.merge(baseline, on=["t", "raion_id"], validate="one_to_one", how="left")
    if truth.filter(like="b0_").isna().any().any():
        raise ValueError("B0 does not cover every forecast key")
    selected_rows = pd.DataFrame()
    for path in sorted(config.V2_PREDS_DIR.glob("onset_*.parquet")):
        predictions = pd.read_parquet(path, filters=[("split", "==", split)])
        _validate_keys(predictions, ["t", "raion_id"], path.name)
        joined = truth.merge(predictions.drop(columns="split"), on=["t", "raion_id"],
                             validate="one_to_one", how="outer", indicator=True)
        if not joined._merge.eq("both").all():
            raise ValueError(f"{path.name} does not cover {split} exactly")
        joined = joined.drop(columns="_merge")
        model = path.stem.removeprefix("onset_")
        for row_set in ("unprojected", "served"):
            rows = joined.copy()
            if row_set == "served":
                rows[[f"p_{h}" for h in config.HORIZONS]] = metrics.monotone_projection(
                    rows[[f"p_{h}" for h in config.HORIZONS]]
                )
            for horizon in config.HORIZONS:
                eligible = rows.loc[v2_eligible_mask(rows, horizon, split, final=final)]
                if not eligible.empty:
                    # Report uses split=test; row_set distinguishes the untouched test2 era.
                    named_set = f"test2-{row_set}" if split == "test2" else row_set
                    report.onset.append(_score_onset(model, named_set, eligible, horizon,
                                                    "test" if split == "test2" else "val"))
            if model == "selected" and row_set == "served":
                selected_rows = rows
    report.notes.extend([
        "v2 test denotes untouched test2 beginning September 8; v1 files are frozen.",
        "BSS reference is monotone-projected B0-raw on identical eligible rows.",
        "served rows use cumulative maximum before all scoring and selection.",
    ])
    return report, selected_rows


def run_v2(*, final: bool = False) -> Report:
    """Validation is repeatable. A durable claim permits test2 scoring only once."""
    claim = config.V2_REPORTS_DIR / "test2_claim.json"
    if final and claim.exists():
        raise RuntimeError("test2 was already claimed; inspect its saved result")
    if not final:
        report, _ = v2_score_predictions("val")
        write_v2_json(config.V2_METRICS_DIR / "scores_val.json", report.model_dump(mode="json"))
    else:
        selection_bytes = config.V2_SELECTION_PATH.read_bytes()
        selection = json.loads(selection_bytes)
        snapshot = load_snapshot().snapshot
        if selection["snapshot"] != snapshot.isoformat():
            raise ValueError("Selection snapshot differs from the data being scored")
        if selection["data_gaps"] != [[a.isoformat(), b.isoformat()] for a, b in v2_data_gaps()]:
            raise ValueError("Gap policy changed after selection; refresh validation before final")
        if selection["prediction_sha256"] != v2_prediction_hashes():
            raise ValueError("Predictions changed after selection; lock validation before final")
        if v2_test_end(snapshot) < pd.Timestamp(config.V2_TEST_START):
            print("No test2 coverage. Final scoring was not claimed.", flush=True)
            return metrics.merge_reports(directory=config.V2_METRICS_DIR,
                                         destination=config.V2_METRICS_PATH, include_test=False)
        config.V2_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        with claim.open("x", encoding="utf-8") as handle:
            json.dump({"started_at": datetime.now(UTC).isoformat(),
                       "selection_sha256": hashlib.sha256(selection_bytes).hexdigest(),
                       "snapshot": snapshot.isoformat()}, handle, indent=2)
        report, served = v2_score_predictions("test2", final=True)
        intervals = [metrics.day_block_bss(
            served.loc[v2_eligible_mask(served, horizon, "test2", final=True)], horizon
        ) for horizon in config.HORIZONS]
        if config.V2_SELECTION_PATH.read_bytes() != selection_bytes:
            raise RuntimeError("Selection changed during final scoring")
        if selection["prediction_sha256"] != v2_prediction_hashes():
            raise RuntimeError("Predictions changed during final scoring")
        write_v2_json(config.V2_REPORTS_DIR / "bootstrap.json", intervals)
        write_v2_json(config.V2_METRICS_DIR / "scores_test2.json", report.model_dump(mode="json"))
        print("TEST2 SCORED ONCE; locked selection unchanged", flush=True)
    merged = metrics.merge_reports(directory=config.V2_METRICS_DIR,
                                   destination=config.V2_METRICS_PATH, include_test=final,
                                   extra_fragments=([config.V2_REPORTS_DIR / "duration_scores.json"]
                                       if (config.V2_REPORTS_DIR / "duration_scores.json").exists()
                                       else []))
    print(f"v2 report: {len(merged.onset)} onset scores", flush=True)
    return merged
