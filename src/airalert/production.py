"""Nightly atomic model generations and availability-aware hourly forecasts.

The scheduler calls refit/forecast/score_recent with aware datetimes. Forecast
files are immutable on first publication; rolling scores use their stored B0.
"""

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from airalert import config, metrics
from airalert.backtest import v2_data_gap_mask, write_v2_json
from airalert.data import load_episodes, load_raions, load_records, load_snapshot
from airalert.features import FeatureContext, build_features, load_feature_context
from airalert.labels import build_labels
from airalert.models import duration, onset
from airalert.schemas import ForecastRow, ProductionModel

_LOCK = threading.RLock()


def _utc(value: datetime) -> pd.Timestamp:
    clock = pd.Timestamp(value)
    if clock.tzinfo is None:
        raise ValueError("Production clocks must be timezone-aware")
    return clock.tz_convert("UTC").as_unit("us")


def _generation() -> Path:
    pointer = config.V2_PRODUCTION_DIR / "current.json"
    if not pointer.exists():
        raise FileNotFoundError("Production has not been refitted yet")
    name = json.loads(pointer.read_text(encoding="utf-8"))["generation"]
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("Invalid production generation pointer")
    return config.V2_PRODUCTION_DIR / name


def status() -> ProductionModel:
    """The pointer changes only after a complete model generation is durable."""
    try:
        generation = _generation()
    except FileNotFoundError:
        return ProductionModel(onset="untrained", duration=None, trained_at=None, data_through=None)
    metadata = json.loads((generation / "metadata.json").read_text(encoding="utf-8"))
    return ProductionModel.model_validate(metadata["status"])


def refit(now: datetime) -> ProductionModel:
    """Refit the locked horizon choices on every matured row, with a forward train tail."""
    with _LOCK:
        began = perf_counter()
        clock = _utc(now)
        saved = load_snapshot()
        # A label ending at this hour is not strictly before this hour.
        through = min(saved.snapshot, clock.floor("h") - pd.Timedelta(microseconds=1))
        selection = json.loads(config.V2_SELECTION_PATH.read_text(encoding="utf-8"))
        records, episodes = load_records(), load_episodes()
        context = load_feature_context()
        times = pd.date_range(config.V2_TRAIN_START, through.floor("h"), freq="h")
        training = build_features(records, times, context=context)
        labels = build_labels(episodes, times, through, raions=context.raions)
        for horizon in config.HORIZONS:
            training[f"y_{horizon}"] = labels[f"y_{horizon}"]
        generation = config.V2_PRODUCTION_DIR / f"generation-{uuid.uuid4().hex}"
        generation.mkdir(parents=True, exist_ok=False)
        b0_metadata = json.loads((config.V2_ARTIFACTS_DIR / "B0" / "metadata.json").read_text(
            encoding="utf-8"))
        # The chronological cutoff is the last fully observed instant, not wall-clock today.
        def refit_horizon(horizon: int) -> dict[str, object]:
            choice = selection["horizons"][str(horizon)]
            evaluation_model = joblib.load(config.V2_ARTIFACTS_DIR / choice["identity"]
                                          / f"model_{horizon}h.joblib")
            if not isinstance(evaluation_model, onset.HorizonModel):
                raise TypeError("Invalid v2 onset artifact")
            model, calibrator, provenance = onset.fit_chronological(
                evaluation_model.kind, training, horizon, through,
                window=evaluation_model.window, feature_names=evaluation_model.features,
                categories=evaluation_model.categories, regime=saved.regime_switch,
                k=evaluation_model.k, rounds=evaluation_model.rounds, artifact_dir=generation,
            )
            joblib.dump(model, generation / f"model_{horizon}h.joblib")
            # Save even if the chosen configuration serves raw probabilities.
            joblib.dump(calibrator, generation / f"calibrator_{horizon}h.joblib")
            b0 = b0_metadata[str(horizon)]
            reference = onset.fit_horizon(
                "B0", training, horizon, through, window=b0["window"], feature_names=[],
                categories=evaluation_model.categories, regime=saved.regime_switch, k=b0["k"],
            )
            joblib.dump(reference, generation / f"b0_{horizon}h.joblib")
            print(f"Production H={horizon}: {perf_counter() - began:.1f}s elapsed", flush=True)
            return {"candidate": choice["candidate"],
                    "calibrated": choice["calibrated"], **provenance}

        # Three disjoint horizon fits share read-only feature rows. Each LightGBM
        # fit retains the four-thread evaluation settings and its chosen rounds.
        with ThreadPoolExecutor(max_workers=config.V2_REFIT_WORKERS) as workers:
            jobs = {h: workers.submit(refit_horizon, h) for h in config.HORIZONS}
            details = {str(h): job.result() for h, job in jobs.items()}
        duration_id = _duration_id()
        result = ProductionModel(onset="selected", duration=duration_id,
                                 trained_at=datetime.now(UTC), data_through=through.to_pydatetime())
        metadata = {"status": result.model_dump(mode="json"), "horizons": details,
                    "elapsed_seconds": perf_counter() - began,
                    "selection": selection, "feature_context": {
                        "static_features": (
                            None if context.static_features is None
                            else context.static_features.to_dict(orient="records"))}}
        write_v2_json(generation / "metadata.json", metadata)
        write_v2_json(config.V2_PRODUCTION_DIR / "current.json", {"generation": generation.name})
        print(f"Production refit: {metadata['elapsed_seconds']:.2f}s; "
              f"data through {through.isoformat()}", flush=True)
        return result


def load_observations(t: pd.Timestamp) -> pd.DataFrame:
    """Read only received-before-hour observations, including the actual receipt clock."""
    frames: list[pd.DataFrame] = []
    for path in sorted(config.V2_LIVE_OBSERVATIONS_DIR.glob("*.parquet")):
        frame = pd.read_parquet(path, filters=[("observed_at", "<", t.to_pydatetime())])
        if not frame.empty:
            frames.append(frame)
        elif pd.read_parquet(path, columns=["observed_at"]).empty:
            # Empty successful polls carry no row receipt clock. Filesystem write
            # time is a conservative availability bound until I1 adds a manifest.
            received = pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC").as_unit("us")
            if received < t:
                frames.append(pd.DataFrame({"observed_at": [received], "raion_id": [None],
                                            "hazard_type": ["poll"], "started_at": [pd.NaT],
                                            "finished_at": [pd.NaT]}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def as_of_records(
    records: pd.DataFrame, observations: pd.DataFrame, t: pd.Timestamp,
) -> pd.DataFrame:
    """Combine record prefixes with only live snapshots received strictly before t.

    A disappearing live origin ends at its first successful absent snapshot,
    conservatively, unless a known end was explicitly observed. Failed polls
    have no observation file, so they cannot clear alerts. Severity is unused.
    """
    columns = ["raion_id", "oblast_id", "level", "started_at", "finished_at", "source"]
    view = records.copy()
    if "source" not in view:
        view["source"] = "csv"
    if "observed_at" in view:
        view = view.loc[view.observed_at.isna() | view.observed_at.lt(t)]
    view = view.loc[view.started_at.lt(t), columns].copy()
    view.loc[view.finished_at.gt(t), "finished_at"] = pd.NaT
    if observations.empty:
        return view.reset_index(drop=True)
    observed = observations.loc[observations.observed_at.lt(t)].copy()
    if observed.empty:
        return view.reset_index(drop=True)
    observed["observed_at"] = pd.to_datetime(observed.observed_at, utc=True).astype(
        "datetime64[us, UTC]")
    polls = np.sort(observed.observed_at.unique())
    observed = observed.loc[observed.hazard_type.eq("air_raid") & observed.raion_id.notna()
                            & observed.started_at.lt(t)].copy()
    # A successful complete poll can close an archive-censored alert once the
    # raion is absent. The first absent receipt is a conservative known end.
    for raion_id, positions in view.loc[view.finished_at.isna()].groupby("raion_id").groups.items():
        seen = observed.loc[observed.raion_id.eq(raion_id), "observed_at"].unique()
        absent = np.setdiff1d(polls, seen)
        if len(absent):
            first_absent = absent[0]
            eligible_positions = view.loc[positions].index[view.loc[positions].started_at.lt(
                first_absent)]
            view.loc[eligible_positions, "finished_at"] = first_absent
    if observed.empty:
        return view.reset_index(drop=True)
    for column in ("started_at", "finished_at"):
        observed[column] = pd.to_datetime(observed[column], utc=True).astype("datetime64[us, UTC]")
    keys = ["raion_id", "source", "origin_uid", "origin_event_id", "started_at"]
    ordered = observed.sort_values("observed_at", kind="stable")
    last = ordered.drop_duplicates(keys, keep="last").copy()
    positions = np.searchsorted(polls, last.observed_at.to_numpy(), side="right")
    disappeared = positions < len(polls)
    ends = last.finished_at.copy()
    ends.loc[disappeared & ends.isna()] = polls[positions[disappeared & ends.isna()]]
    last["finished_at"] = ends
    last.loc[last.finished_at.gt(t), "finished_at"] = pd.NaT
    live = last[columns]
    # Mirrors of the same origin onset and canonical records never add another count.
    return pd.concat([view, live], ignore_index=True).drop_duplicates(
        ["raion_id", "level", "started_at", "finished_at"]
    ).reset_index(drop=True)


def forecast_inputs(
    records: pd.DataFrame, observations: pd.DataFrame, t: pd.Timestamp,
    *, context: FeatureContext | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Expose the pure as-of input boundary for integration and leakage tests."""
    from airalert.precompute import active_state

    view = as_of_records(records, observations, t)
    features = build_features(view, [t], context=context)
    state = active_state(view, features)
    if not np.array_equal(features.f_active.to_numpy(dtype=bool), state.active):
        raise ValueError("Forecast active state disagrees with as-of features")
    return features, state, view


def _duration_id() -> str | None:
    if (config.V2_PRODUCTION_DIR / "duration" / "current.json").exists():
        return "D2"
    for model in ("D1", "D0"):
        if (config.ARTIFACTS_DIR / model / "metadata.json").exists():
            return model
    return None


def _end_quantiles(
    active: pd.DataFrame, view: pd.DataFrame, t: pd.Timestamp, model_id: str | None,
) -> pd.DataFrame:
    """Use M2b's survival API when present; otherwise expose onset-time estimates."""
    result = pd.DataFrame(index=active.index)
    for quantile in (10, 50, 90):
        result[f"end_p{quantile}"] = pd.Series(pd.NaT, index=active.index,
                                               dtype="datetime64[us, UTC]")
    rows = active.loc[active.active].copy()
    if rows.empty or model_id is None:
        return result
    remaining = getattr(duration, "predict_remaining", None)
    if remaining is not None and model_id == "D2":
        predicted = remaining(rows.rename(columns={"active_since": "start"})[
            ["raion_id", "start"]], t)
        if not isinstance(predicted, pd.DataFrame):
            raise TypeError("predict_remaining must return a DataFrame")
        for quantile in (10, 50, 90):
            column = f"end_p{quantile}"
            result.loc[rows.index, column] = predicted[column].to_numpy()
        return result
    raions = load_raions()
    start_rows = rows.rename(columns={"active_since": "start"})[["raion_id", "start"]].merge(
        raions[["raion_id", "oblast_id"]], on="raion_id", how="left", validate="many_to_one")
    features = duration.build_start_features(
        view, start_rows, raions, load_snapshot().regime_switch)
    artifact = config.ARTIFACTS_DIR / model_id
    metadata = json.loads((artifact / "metadata.json").read_text(encoding="utf-8"))
    features = features[metadata["feature_columns"]]
    if model_id == "D1":
        quantiles = np.column_stack([
            np.maximum(np.exp(lgb.Booster(model_file=str(artifact / f"q{q}_min.txt")).predict(
                features, num_threads=4)), 1.0) for q in (10, 50, 90)
        ])
    else:
        empirical = duration.EmpiricalDuration(
            np.asarray(metadata["fit"]["global_quantiles"]),
            [(keys, pd.read_parquet(artifact / f"cells_{index}.parquet"))
             for index, keys in enumerate(metadata["fit"]["cell_keys"])],
        )
        quantiles = empirical.predict(features)
    quantiles.sort(axis=1)
    for index, quantile in enumerate((10, 50, 90)):
        result.loc[rows.index, f"end_p{quantile}"] = rows.active_since + pd.to_timedelta(
            quantiles[:, index], unit="min")
    return result


def forecast(now: datetime, *, persist: bool = True) -> pd.DataFrame:
    """Forecast floor(now) from a frozen generation and first-write stored observations."""
    with _LOCK:
        clock = _utc(now)
        t = clock.floor("h")
        path = config.V2_FORECASTS_DIR / f"{t.strftime('%Y%m%dT%HZ')}.parquet"
        fields = list(ForecastRow.model_fields)
        if persist and path.exists():
            return pd.read_parquet(path)[["t", *fields]]
        generation = _generation()
        metadata = json.loads((generation / "metadata.json").read_text(encoding="utf-8"))
        through = pd.Timestamp(metadata["status"]["data_through"])
        if through >= t:
            raise ValueError("Model outcomes must be strictly before the forecast hour")
        context = load_feature_context()
        saved_static = metadata.get("feature_context", {}).get("static_features")
        # Freeze geography columns with the model, even if a new optional CSV arrives later.
        context = FeatureContext(context.raions, context.adjacency, context.centroids,
                                 context.regime_switch,
                                 None if saved_static is None else pd.DataFrame(saved_static))
        features, state, view = forecast_inputs(load_records(), load_observations(t), t,
                                               context=context)
        output = features[["t", "raion_id"]].copy()
        raw, reference = [], []
        for horizon in config.HORIZONS:
            model = joblib.load(generation / f"model_{horizon}h.joblib")
            if not isinstance(model, onset.HorizonModel):
                raise TypeError("Invalid production onset model")
            predicted = onset.predict_horizon(model, features)
            if metadata["horizons"][str(horizon)]["calibrated"]:
                calibrator = joblib.load(generation / f"calibrator_{horizon}h.joblib")
                predicted = calibrator.predict(predicted)
            raw.append(predicted)
            reference_model = joblib.load(generation / f"b0_{horizon}h.joblib")
            reference.append(onset.predict_horizon(reference_model, features))
        output[[f"p_{h}" for h in config.HORIZONS]] = metrics.monotone_projection(
            np.column_stack(raw))
        output[["active", "active_since"]] = state
        output[["end_p10", "end_p50", "end_p90"]] = _end_quantiles(
            output, view, t, metadata["status"]["duration"])
        for horizon in config.HORIZONS:
            output[f"actual_onset_{horizon}"] = None
        # Validate the actual server payload, including all timestamps and nullable outcomes.
        for record in output[fields].astype(object).where(output[fields].notna(), None).to_dict(
            orient="records"):
            ForecastRow.model_validate(record)
        if persist:
            stored = output.copy()
            stored[[f"b0_{h}" for h in config.HORIZONS]] = metrics.monotone_projection(
                np.column_stack(reference))
            stored["issued_at"] = max(clock, pd.Timestamp.now(tz="UTC"))
            stored["model_generation"] = generation.name
            config.V2_FORECASTS_DIR.mkdir(parents=True, exist_ok=True)
            staging = path.with_name(f".{path.stem}-{uuid.uuid4().hex}.tmp")
            stored.to_parquet(staging, index=False)
            try:
                # Hardlink publication is atomic and fails if another caller won.
                os.link(staging, path)
            except FileExistsError:
                output = pd.read_parquet(path)[["t", *fields]]
            finally:
                staging.unlink()
        return output[["t", *fields]]


def score_recent(now: datetime) -> dict[str, object]:
    """Score only timely, stored forecasts with reconciled outcomes, using their original B0."""
    clock = _utc(now)
    through = min(load_snapshot().snapshot, clock)
    frames = [pd.read_parquet(path) for path in sorted(config.V2_FORECASTS_DIR.glob("*.parquet"))
              if pd.to_datetime(path.stem, format="%Y%m%dT%HZ", utc=True)
              >= clock - pd.Timedelta(days=max(config.V2_TRACK_RECORD_DAYS))]
    stored = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    result: dict[str, object] = {"generated_at": clock.isoformat(),
                                "data_through": through.isoformat(), "windows": {}}
    windows: dict[str, list[dict[str, object]]] = {}
    if not stored.empty:
        # Backdated replays and forecasts first issued after the hourly grace are excluded.
        stored = stored.loc[stored.issued_at.le(stored.t + pd.Timedelta(
            seconds=config.V2_FORECAST_GRACE_S))
            & stored.t.ge(clock - pd.Timedelta(days=max(config.V2_TRACK_RECORD_DAYS)))].copy()
        if not stored.empty:
            labels = build_labels(load_episodes(), pd.DatetimeIndex(stored.t.unique()), through,
                                  raions=load_raions())
            stored = stored.merge(labels, on=["t", "raion_id"], validate="one_to_one")
    for days in config.V2_TRACK_RECORD_DAYS:
        scores: list[dict[str, object]] = []
        for horizon in config.HORIZONS:
            eligible = stored
            if not stored.empty:
                mask = stored.t.ge(clock - pd.Timedelta(days=days))
                mask &= stored[f"y_{horizon}"].notna() & v2_data_gap_mask(stored, horizon)
                eligible = stored.loc[mask]
            scores.append({"horizon": horizon, "n": len(eligible),
                           "brier": (None if eligible.empty else metrics.brier_score(
                               eligible[f"y_{horizon}"], eligible[f"p_{horizon}"])),
                           "bss": (None if eligible.empty else metrics.brier_skill_score(
                               eligible[f"y_{horizon}"], eligible[f"p_{horizon}"],
                               eligible[f"b0_{horizon}"])),
                           "b0_brier": (None if eligible.empty else metrics.brier_score(
                               eligible[f"y_{horizon}"], eligible[f"b0_{horizon}"]))})
        windows[str(days)] = scores
    result["windows"] = windows
    result["note"] = "Only first-issued forecasts within 60s of the hour; B0 frozen at issuance."
    write_v2_json(config.V2_REPORTS_DIR / "track_record.json", result)
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Production pipeline called by S1")
    parser.add_argument("action", choices=("refit", "forecast", "score", "status"))
    args = parser.parse_args()
    now = datetime.now(UTC)
    if args.action == "refit":
        print(refit(now).model_dump_json())
    elif args.action == "forecast":
        rows = forecast(now)
        print(f"Forecast {rows.t.iloc[0]}: {len(rows)} raions; "
              f"{int(rows.active.sum())} active; immutable hourly publication")
    elif args.action == "score":
        print(json.dumps(score_recent(now), indent=2))
    else:
        print(status().model_dump_json())
