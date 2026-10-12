"""Resumable bootstrap from committed geography and locked production settings.

Checkpoints live with local data. A failed step invalidates later checkpoints,
while archive days and complete model generations retain their own atomic resume.
No evaluation, selection or scoring entrypoint is called here.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from airalert import config


@dataclass(frozen=True)
class Step:
    name: str
    label: str
    action: Callable[[], object]
    inputs: Callable[[], list[Path]]
    outputs: Callable[[], list[Path]]
    ready: Callable[[], bool] = lambda: True
    parameters: str = ""


def _json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _signature(paths: list[Path], parameters: str = "") -> str:
    """Hash small contracts and CSVs; large generated tables use file-generation stamps."""
    values: list[object] = [parameters]
    for path in paths:
        if not path.is_file():
            values.append([path.name, "missing"])
        elif path.suffix in {".py", ".json", ".csv", ".sha256"}:
            with path.open("rb") as source:
                values.append([path.name, hashlib.file_digest(source, "sha256").hexdigest()])
        else:
            info = path.stat()
            values.append([path.name, info.st_size, info.st_mtime_ns])
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def _raw_csv() -> Path:
    candidates = sorted(config.RAW_DIR.glob("official_data_en_*.csv"))
    return candidates[-1] if candidates else config.RAW_DIR / "missing.csv"


def _model_files(pointer: Path, names: tuple[str, ...]) -> list[Path]:
    name = _json(pointer).get("generation")
    if not isinstance(name, str) or Path(name).name != name:
        return [pointer, pointer.parent / "missing-generation"]
    return [pointer, *(pointer.parent / name / filename for filename in names)]


def serving_ready() -> bool:
    """Prepared checkouts need no setup marker; an empty clone gets a concise CLI error."""
    return all(path.is_file() for path in (
        config.FORECASTS_PATH, config.SNAPSHOT_PATH, config.RAIONS_PATH,
        config.RECORDS_PATH, config.EPISODES_PATH, config.RAIONS_GEOJSON_PATH,
        config.V2_PRODUCTION_DIR / "current.json",
        config.V2_PRODUCTION_DIR / "duration" / "current.json",
    ))


def build_steps(now: datetime) -> list[Step]:
    """Use the same ingestion, refit and publication functions as nightly maintenance."""
    from airalert import data, features, ingest, precompute, production
    from airalert.models import duration

    root = config.DATA_DIR / "live"
    code = Path(__file__).parent
    yesterday = now.astimezone(ZoneInfo(config.KYIV_TZ)).date() - timedelta(days=1)
    cutover = ingest.REVIEWED_CUTOVER
    days = ingest.REVIEWED_VALIDATION_DAYS
    data_paths = [config.RECORDS_PATH, config.EPISODES_PATH,
                  config.RAIONS_PATH, config.SNAPSHOT_PATH]
    geography = [
        config.ADJACENCY_PATH, config.CENTROIDS_PATH, config.V2_STATIC_FEATURES_PATH,
    ]

    def csv_ready() -> bool:
        path = _raw_csv()
        checksum = path.with_suffix(".csv.sha256")
        if not path.is_file() or not checksum.is_file():
            return False
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest() == (
                checksum.read_text(encoding="ascii").split()[0])

    def records_ready() -> bool:
        return _json(config.SNAPSHOT_PATH).get("csv_sha256") == ingest._csv_sha256()

    def gate_ready() -> bool:
        try:
            ingest._require_validation(root)
        except (OSError, ValueError, RuntimeError):
            return False
        validated = _json(root / "validation.json").get("days", [])
        return {row["day"] for row in validated} == {day.isoformat() for day in days}

    def validate() -> None:
        report = ingest.validate_archive(list(days), root=root)
        if report["passed"] is not True:
            raise RuntimeError("Archive overlap gate failed; inspect data/live/validation.json")

    def records() -> None:
        # An existing ledger makes episodes rebuild the hybrid source. Revalidate
        # first if a forced/new CSV invalidated its accepted source fingerprint.
        if (root / "ledger.json").exists() and not gate_ready():
            validate()
        data.build_episodes()

    def backfill_ready() -> bool:
        ledger = _json(root / "ledger.json")
        saved = _json(config.SNAPSHOT_PATH)
        return (ledger.get("cutover") == ingest._iso(cutover)
                and str(ledger.get("watermark", "")) >= yesterday.isoformat()
                and not ledger.get("gaps")
                and saved.get("coverage", {}).get("reconciled_through") == ledger.get("watermark")
                and all((root / "normalized" / f"{day}.parquet").is_file()
                        for day in ledger.get("days", [])))

    def live_ready() -> bool:
        state = _json(root / "state.json")
        if state.get("stale") or not state.get("fetched_at"):
            return False
        fetched = datetime.fromisoformat(str(state["fetched_at"]))
        return timedelta(0) <= datetime.now(UTC) - fetched < timedelta(minutes=3)

    onset_names = ("metadata.json", *(f"{prefix}_{h}h.joblib" for h in config.HORIZONS
                    for prefix in ("model", "calibrator", "calibration_prefix", "b0")))
    duration_names = ("metadata.json", "q10_min.txt", "q50_min.txt", "q90_min.txt",
                      "median_all.txt")
    pointer = config.V2_PRODUCTION_DIR / "current.json"
    duration_pointer = config.V2_PRODUCTION_DIR / "duration" / "current.json"
    # Resolve the hour when reached: downloads/fits can cross an hour boundary.
    def issued_path() -> Path:
        return config.V2_FORECASTS_DIR / f"{datetime.now(UTC).strftime('%Y%m%dT%HZ')}.parquet"

    return [
        Step("fetch", "Fetch CSV", data.fetch, lambda: [code / "config.py"],
             lambda: [_raw_csv(), _raw_csv().with_suffix(".csv.sha256")], csv_ready),
        Step("records", "Build records and episodes", records,
             lambda: [_raw_csv(), code / "data.py"], lambda: data_paths, records_ready),
        Step("gate", "Validate reviewed archive overlap", validate,
             lambda: [_raw_csv(), code / "ingest.py", code / "sources/aiu_web.py"],
             lambda: [root / "validation.json"], gate_ready,
             parameters=",".join(day.isoformat() for day in days)),
        Step("backfill", "Backfill and catch up through yesterday",
             lambda: ingest.catch_up(datetime.now(UTC), root=root, cutover=cutover),
             lambda: [_raw_csv(), root / "validation.json", code / "ingest.py"],
             lambda: [root / "ledger.json", *data_paths], backfill_ready,
             parameters=f"{cutover.isoformat()}|{yesterday.isoformat()}"),
        Step("features", "Build v2 features", features.build_v2,
             lambda: [*data_paths, *geography, code / "features.py", code / "labels.py"],
             lambda: [config.V2_FEATURES_DIR / f"{split}.parquet"
                      for split in ("train", "val", "test2", "latest")]),
        Step("onset", "Fit locked production onset", lambda: production.refit(datetime.now(UTC)),
             lambda: [*data_paths, *geography, config.V2_SELECTION_PATH, code / "config.py",
                      code / "production.py", code / "models/onset.py",
                      code / "models/baseline.py"],
             lambda: _model_files(pointer, onset_names)),
        Step("duration", "Fit production duration", lambda: duration.refit_production(
             datetime.now(UTC)),
             lambda: [*data_paths, code / "config.py", code / "models/duration.py"],
             lambda: _model_files(duration_pointer, duration_names)),
        Step("live", "Poll live state", lambda: ingest.live_state(datetime.now(UTC), root=root),
             lambda: [code / "ingest.py"], lambda: [root / "state.json"], live_ready),
        Step("forecast", "Issue current-hour forecast",
             lambda: production.forecast(datetime.now(UTC)),
             lambda: [pointer, duration_pointer, config.SNAPSHOT_PATH, issued_path()],
             lambda: [issued_path()]),
        Step("serving", "Build serving forecasts", lambda: precompute.run_v2(precompute.Paths()),
             lambda: [config.SNAPSHOT_PATH, code / "precompute.py",
                      *sorted(config.V2_FORECASTS_DIR.glob("*.parquet"))],
             lambda: [config.FORECASTS_PATH]),
    ]


def run(force: bool = False) -> dict[str, object]:
    """Run or resume setup; --force repeats steps while preserving cached source responses."""
    from airalert.sources.storage import atomic_json

    began = perf_counter()
    root = config.DATA_DIR / "live"
    root.mkdir(parents=True, exist_ok=True)
    checkpoint = root / "setup.json"
    saved = _json(checkpoint)
    progress = saved.get("steps", {}) if saved.get("version") == 1 else {}
    state = {"version": 1, "steps": progress}
    steps = build_steps(datetime.now(UTC))
    for index, step in enumerate(steps, start=1):
        started = perf_counter()
        signature = _signature(step.inputs(), step.parameters)
        previous = progress.get(step.name, {})
        if (not force and previous.get("status") == "ok"
                and previous.get("inputs") == signature
                and all(path.is_file() for path in step.outputs()) and step.ready()):
            print(f"[{index}/{len(steps)}] {step.label}: skipped, complete "
                  f"({perf_counter() - started:.2f}s)", flush=True)
            continue
        for later in steps[index - 1:]:
            progress.pop(later.name, None)
        progress[step.name] = {"status": "running", "inputs": signature}
        atomic_json(checkpoint, state)
        print(f"[{index}/{len(steps)}] {step.label}: started", flush=True)
        try:
            step.action()
            if not all(path.is_file() for path in step.outputs()):
                raise RuntimeError(f"{step.label} did not publish its required outputs")
        except BaseException as error:
            progress[step.name].update({"status": "error", "error_type": type(error).__name__,
                                        "seconds": round(perf_counter() - started, 3)})
            atomic_json(checkpoint, state)
            print(f"[{index}/{len(steps)}] {step.label}: failed "
                  f"({perf_counter() - started:.2f}s)", flush=True)
            raise
        # Forecast inputs include its first-issued file, created by the action.
        progress[step.name] = {"status": "ok", "inputs": _signature(step.inputs(), step.parameters),
                               "seconds": round(perf_counter() - started, 3)}
        atomic_json(checkpoint, state)
        print(f"[{index}/{len(steps)}] {step.label}: complete "
              f"({progress[step.name]['seconds']:.2f}s)", flush=True)
    state["total_seconds"] = round(perf_counter() - began, 3)
    atomic_json(checkpoint, state)
    print(f"Setup complete in {state['total_seconds']:.2f}s. Run:", flush=True)
    print("  uv run airalert serve\n  cd web && bun install && bun run dev", flush=True)
    return state
