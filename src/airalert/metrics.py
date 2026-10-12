"""Array metrics and validated, atomic merging of independently owned reports."""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray
from sklearn.metrics import average_precision_score

from airalert import config
from airalert.schemas import ReliabilityBin, Report


def v2_freeze_marker(directory: Path | None = None) -> Path | None:
    """Committed claims freeze both evaluations, including interrupted claims."""
    root = config.V2_REPORTS_DIR if directory is None else directory
    return next((root / name for name in ("test2_claim.json", "duration_test2_claim.json")
                 if (root / name).exists()), None)


@dataclass
class V2EvaluationClaim:
    """Only the process that claimed test2 may publish its bounded final outputs."""

    path: Path
    contents: bytes
    outputs: tuple[Path, ...]

    def finish(self) -> None:
        with v2_report_write(self.path, claim=self):
            value = json.loads(self.contents)
            value.update(status="finished", finished_at=datetime.now(UTC).isoformat())
            staging = self.path.with_suffix(".json.tmp")
            staging.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            staging.replace(self.path)


def assert_v2_writable(path: Path, *, claim: V2EvaluationClaim | None = None) -> None:
    """Check at every writer, resolving aliases and rejecting even partial claims.

    The rolling track record is runtime state. All other files below a claimed
    evaluation directory are immutable. A claim authorizes only its final outputs
    while its exact original started-state bytes remain on disk.
    """
    target = path.resolve()
    canonical = config.V2_REPORTS_DIR.resolve()
    if target in (canonical / "track_record.json", canonical / "track_record.json.tmp"):
        return
    for root in (target, *target.parents):
        marker = v2_freeze_marker(root)
        if marker is None:
            continue
        markers = [root / name for name in ("test2_claim.json", "duration_test2_claim.json")
                   if (root / name).exists()]
        if (claim is not None and markers == [claim.path.resolve()]
                and target in (*claim.outputs, claim.path.resolve())
                and marker.read_bytes() == claim.contents
                and json.loads(claim.contents).get("status") == "started"):
            continue
        raise RuntimeError(
            f"v2 evaluation is frozen, already claimed by {marker.name}; cannot change {path}. "
            "A started claim may be unfinished: inspect its saved outputs with the lead. "
            "Keep the claim in place; use --out for a separate experiment directory."
        )


@contextmanager
def v2_report_write(path: Path, *, claim: V2EvaluationClaim | None = None) -> Iterator[None]:
    """Serialize committed publication with claims; OS locks are released on crashes.

    The lock lives in ignored evaluation artifacts. Production artifact writes
    and the rolling track record do not acquire or create this evaluation lock.
    """
    assert_v2_writable(path, claim=claim)
    target, root = path.resolve(), config.V2_REPORTS_DIR.resolve()
    if (not target.is_relative_to(root)
            or target in (root / "track_record.json", root / "track_record.json.tmp")):
        yield
        return
    lock = config.V2_ARTIFACTS_DIR / "evaluation.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            assert_v2_writable(path, claim=claim)
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def claim_v2_evaluation(
    directory: Path, kind: Literal["onset", "duration"], *, snapshot: str,
    details: dict[str, object] | None = None,
) -> V2EvaluationClaim:
    """Create before scoring with O_EXCL; crashes leave a durable started claim."""
    directory = directory.resolve()
    assert_v2_writable(directory)
    path = directory / ("test2_claim.json" if kind == "onset" else "duration_test2_claim.json")
    outputs = ((directory / "metrics.json", directory / "bootstrap.json",
                directory / "metrics" / "scores_test2.json") if kind == "onset"
               else (directory / "duration_scores.json",))
    value = {**(details or {}), "status": "started", "started_at": datetime.now(UTC).isoformat(),
             "snapshot": snapshot}
    contents = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with v2_report_write(directory):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as error:
            message = f"v2 evaluation already claimed by {path.name}; use --out"
            raise RuntimeError(message) from error
    return V2EvaluationClaim(path, contents, outputs)


@dataclass(frozen=True)
class V2EvaluationPaths:
    """Explicit experiments keep reports, fitted artifacts and predictions separate."""

    reports: Path
    predictions: Path
    artifacts: Path

    @property
    def fragments(self) -> Path:
        return self.reports / "metrics"

    @property
    def selection(self) -> Path:
        return self.reports / "selection.json"


def v2_evaluation_paths(out: Path | None = None) -> V2EvaluationPaths:
    if out is None:
        return V2EvaluationPaths(config.V2_REPORTS_DIR, config.V2_PREDS_DIR,
                                 config.V2_ARTIFACTS_DIR)
    root = out.resolve()
    if root.is_relative_to(config.V2_REPORTS_DIR.resolve()):
        raise ValueError("--out must be outside the committed reports/v2 evaluation directory")
    return V2EvaluationPaths(root, root / "preds", root / "artifacts")


def _paired(
    y: ArrayLike, prediction: ArrayLike
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    actual = np.asarray(y, dtype=np.float64)
    predicted = np.asarray(prediction, dtype=np.float64)
    if actual.ndim != 1 or actual.shape != predicted.shape or actual.size == 0:
        raise ValueError("Metrics require nonempty, aligned one-dimensional arrays")
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValueError("Metrics cannot contain null or nonfinite values")
    return actual, predicted


def _onset(y: ArrayLike, p: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    actual, predicted = _paired(y, p)
    if not np.isin(actual, [0, 1]).all() or ((predicted < 0) | (predicted > 1)).any():
        raise ValueError("Onset metrics require binary labels and probabilities in [0, 1]")
    return actual, predicted


def brier_score(y: ArrayLike, p: ArrayLike) -> float:
    """Mean squared probability error, including valid endpoint probabilities."""
    actual, predicted = _onset(y, p)
    return float(np.mean(np.square(predicted - actual)))


def brier_skill_score(y: ArrayLike, p: ArrayLike, p_b0: ArrayLike) -> float | None:
    """Compare B0 on these exact labels; zero reference error has no defined skill."""
    reference = brier_score(y, p_b0)
    score = brier_score(y, p)
    return None if reference == 0 else 1.0 - score / reference


def log_loss(y: ArrayLike, p: ArrayLike) -> float:
    """Binary cross entropy with clipping solely for numerical stability."""
    actual, predicted = _onset(y, p)
    clipped = np.clip(predicted, 1e-6, 1 - 1e-6)
    return float(-np.mean(actual * np.log(clipped) + (1 - actual) * np.log1p(-clipped)))


def pr_auc(y: ArrayLike, p: ArrayLike) -> float:
    """Average precision, with zero for a slice containing no positive labels."""
    actual, predicted = _onset(y, p)
    if not actual.any():
        return 0.0
    return float(average_precision_score(actual, predicted))


def reliability(y: ArrayLike, p: ArrayLike) -> list[ReliabilityBin]:
    """Ten equal-width bins; [lo, hi), with p=1 in the last bin, empty means null."""
    actual, predicted = _onset(y, p)
    bins = np.minimum((predicted * 10).astype(np.int64), 9)
    count = np.bincount(bins, minlength=10)
    sum_pred = np.bincount(bins, weights=predicted, minlength=10)
    sum_pos = np.bincount(bins, weights=actual, minlength=10)
    return [
        ReliabilityBin(
            lo=i / 10,
            hi=(i + 1) / 10,
            count=int(count[i]),
            mean_pred=float(sum_pred[i] / count[i]) if count[i] else None,
            frac_pos=float(sum_pos[i] / count[i]) if count[i] else None,
        )
        for i in range(10)
    ]


def pinball_loss(y: ArrayLike, prediction: ArrayLike, quantile: float) -> float:
    """Quantile loss in the input units, so duration evaluation passes minutes."""
    if not 0 < quantile < 1:
        raise ValueError("Quantile must be strictly between zero and one")
    actual, predicted = _paired(y, prediction)
    error = actual - predicted
    return float(np.mean(np.maximum(quantile * error, (quantile - 1) * error)))


def mae_median(y: ArrayLike, q50: ArrayLike) -> float:
    """Mean absolute error of the predicted median, not median-of-errors."""
    actual, predicted = _paired(y, q50)
    return float(np.mean(np.abs(actual - predicted)))


def interval_coverage(y: ArrayLike, q10: ArrayLike, q90: ArrayLike) -> float:
    """Fraction inside the inclusive P10/P90 interval; crossed bounds are an error."""
    actual, low = _paired(y, q10)
    _, high = _paired(y, q90)
    if (low > high).any():
        raise ValueError("Duration interval bounds cannot cross")
    return float(np.mean((actual >= low) & (actual <= high)))


def merge_reports(
    *, include_test: bool = True, directory: Path | None = None,
    destination: Path | None = None, extra_fragments: list[Path] | None = None,
    claim: V2EvaluationClaim | None = None,
) -> Report:
    """Concatenate Report fragments; only selection.json supplies model selection.

    Validation-only backtests set include_test=False to avoid publishing a stale
    scores_test.json from an earlier final run. Producers never edit each
    other's fragments. The validated combined Report is replaced atomically.
    """
    merged = Report(generated_at=datetime.now(UTC))
    directory = config.METRICS_DIR if directory is None else directory
    destination = config.METRICS_PATH if destination is None else destination
    assert_v2_writable(destination, claim=claim)
    for path in sorted([*directory.glob("*.json"), *(extra_fragments or [])]):
        if not include_test and path.stem in ("scores_test", "scores_test2"):
            continue
        fragment = Report.model_validate_json(path.read_text(encoding="utf-8"))
        merged.onset.extend(
            score for score in fragment.onset if include_test or score.split != "test"
        )
        merged.duration.extend(
            score for score in fragment.duration if include_test or score.split != "test"
        )
        merged.notes.extend(fragment.notes)
        if path.name == "selection.json":
            merged.selection = fragment.selection
    onset_keys = [(s.model, s.horizon, s.split, s.row_set) for s in merged.onset]
    duration_keys = [(s.model, s.split) for s in merged.duration]
    if len(set(onset_keys)) != len(onset_keys) or len(set(duration_keys)) != len(duration_keys):
        raise ValueError("Report fragments contain duplicate score identities")
    validated = Report.model_validate(merged.model_dump())
    with v2_report_write(destination, claim=claim):
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(validated.model_dump(mode="json"), indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    return validated


def monotone_projection(probabilities: ArrayLike) -> NDArray[np.float64]:
    """Use the same cumulative maximum for validation, test2 and production."""
    values = np.asarray(probabilities, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != len(config.HORIZONS):
        raise ValueError("Projection requires one column per ordered horizon")
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("Projection requires finite probabilities in [0, 1]")
    return np.maximum.accumulate(values, axis=1)


def day_block_bss(
    frame: pd.DataFrame, horizon: int, *, samples: int | None = None,
) -> dict[str, object]:
    """Resample whole Kyiv calendar days, pairing model and B0 error sums."""
    samples = config.V2_BOOTSTRAP_SAMPLES if samples is None else samples
    actual, predicted = _onset(frame[f"y_{horizon}"], frame[f"p_{horizon}"])
    _, reference = _onset(actual, frame[f"b0_{horizon}"])
    days = frame.t.dt.tz_convert(config.KYIV_TZ).dt.date
    errors = pd.DataFrame({"day": days.to_numpy(), "model": (predicted - actual) ** 2,
                           "reference": (reference - actual) ** 2})
    blocks = errors.groupby("day", sort=True)[["model", "reference"]].sum().to_numpy()
    if samples < 1:
        raise ValueError("Bootstrap samples must be positive")
    rng = np.random.default_rng(config.V2_BOOTSTRAP_SEED)
    picked = rng.integers(0, len(blocks), size=(samples, len(blocks)))
    sums = blocks[picked].sum(axis=1)
    valid = sums[:, 1] > 0
    skills = 1 - sums[valid, 0] / sums[valid, 1]
    interval = np.quantile(skills, [0.05, 0.95]) if len(skills) else [None, None]
    return {"horizon": horizon, "n": len(frame), "days": len(blocks), "samples": samples,
            "seed": config.V2_BOOTSTRAP_SEED,
            "bss": brier_skill_score(actual, predicted, reference),
            "low": None if interval[0] is None else float(interval[0]),
            "high": None if interval[1] is None else float(interval[1]), "confidence": 0.9}
