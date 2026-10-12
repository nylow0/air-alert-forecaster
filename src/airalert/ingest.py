"""I1 archive reconciliation and minute live state, called directly by S1's scheduler.

Archive publication is gated by measured CSV overlap. The ledger commits last,
after staged artifacts have been replaced; an interrupted day is safe to retry.
Live observations have their own clock and never advance historical coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from airalert.config import (
    CROSSWALK_PATH,
    DATA_DIR,
    DATA_GAPS,
    EPISODES_PATH,
    KYIV_TZ,
    PROCESSED_DIR,
    RAW_DIR,
    RECORDS_PATH,
    REPO_ROOT,
    SNAPSHOT_PATH,
)
from airalert.data import expand_records, load_raions, load_source, merge_episodes
from airalert.sources import Event, Severity, aiu_api, aiu_web, mapping, siren, ubilling
from airalert.sources.locations import Location, Locations
from airalert.sources.storage import atomic_json, cache_response

# Lead-reviewed round-3 overlap and outage repair, documented in docs/ingest.md.
# Local bootstrap policy is committed; validation results and ledgers are not.
REVIEWED_CUTOVER = datetime(2026, 8, 29, 6, tzinfo=UTC)
REVIEWED_VALIDATION_DAYS = tuple(
    date.fromisoformat(value) for value in (
        "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23",
        "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27",
        "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06",
    )
)


class Layer(TypedDict):
    active: bool
    since: str | None


class PolygonState(TypedDict):
    display_key: str
    raion_id: str | None
    oblast_id: str
    red: Layer | None
    yellow: Layer | None
    other_hazards: list[dict[str, object]]
    coverage: str
    stale: bool
    fetched_at: str | None


def _live_dir() -> Path:
    return DATA_DIR / "live"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _locations(root: Path) -> Locations:
    return Locations(siren.fetch_regions(root / "raw/siren/regions.json.gz"), load_raions())


def _location(events: Event, locations: Locations) -> Location:
    """AIU has numeric UIDs or a dictionary-name fallback; both must be reviewed."""
    if events.origin_uid in locations.by_uid:
        return locations.resolve(events.origin_uid)
    if events.parent_uid or events.parent_name:
        parent = locations.resolve(events.parent_uid, events.parent_name)
        # AIU city UIDs are virtual units, e.g. 6293 is Kharkiv city, parent 124.
        # The explicit reviewed raion parent is authoritative, not arithmetic on UIDs.
        return Location(events.origin_uid, events.location_name, parent.oblast_id,
                        parent.raion_ids, events.location_type)
    if events.origin_uid.startswith("name:"):
        return locations.resolve(name=events.origin_uid.removeprefix("name:"))
    if events.hazard != "air_raid" and events.oblast_name:
        oblast = locations.resolve(name=events.oblast_name)
        # Border-area informational notices have no raion geometry. Preserve
        # their origin in hazards with no raion, rather than paint an entire oblast.
        return Location(events.origin_uid, events.location_name, oblast.oblast_id, (), "unknown")
    return locations.resolve(events.origin_uid)


def event_records(events: list[Event], locations: Locations) -> pd.DataFrame:
    """Expand each unique origin, without turning a second color into another start."""
    rows: list[dict[str, object]] = []
    origins: dict[tuple[str, str], Event] = {}
    for event in events:
        key = event.source, event.event_id
        if key not in origins or origins[key].updated_at < event.updated_at:
            origins[key] = event
    for event in origins.values():
        loc = _location(event, locations)
        colors = {level.severity for level in event.levels}
        severity: Severity = "red" if "red" in colors else (
            "yellow" if "yellow" in colors else "unknown"
        )
        covered: tuple[str | None, ...] = loc.raion_ids
        if not covered and event.hazard != "air_raid":
            covered = (None,)
        for raion in covered:
            rows.append({
                "raion_id": raion, "oblast_id": loc.oblast_id, "level": loc.level,
                "started_at": event.started_at, "finished_at": event.finished_at,
                "severity": severity, "source": event.source, "origin_uid": loc.uid,
                "origin_event_id": event.event_id, "source_updated_at": event.updated_at,
                "hazard_type": event.hazard, "severity_explicit": event.severity_explicit,
                "start_is_exact": event.start_is_exact,
                "upstream_source": event.upstream_source,
            })
    columns = [
        "raion_id", "oblast_id", "level", "started_at", "finished_at", "severity", "source",
        "origin_uid", "origin_event_id", "source_updated_at", "hazard_type", "severity_explicit",
        "start_is_exact",
        "upstream_source",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    for column in ("started_at", "finished_at", "source_updated_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True).astype("datetime64[us, UTC]")
    return frame


def _read_json(path: Path) -> dict[str, object]:
    return mapping(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}


def _csv_sha256() -> str:
    """Fingerprint the actual newest raw CSV, including when outputs have not rebuilt yet."""
    candidates = sorted(RAW_DIR.glob("official_data_en_*.csv"))
    if not candidates:
        raise FileNotFoundError("No saved official CSV for archive validation")
    with candidates[-1].open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def normalize_archive_records(records: pd.DataFrame) -> pd.DataFrame:
    """Collapse one raion's notices in an inclusive 120s earliest-start window.

    Run after origin upserts across days, before CSV cutover selection. Windows
    are anchored at their earliest start, so a chain cannot absorb later starts
    more than 120s away. Raw partitions retain every origin for end corrections.
    """
    if records.empty:
        return records.copy()
    if not records.source.eq("aiu_web").all() or not records.hazard_type.eq("air_raid").all():
        raise ValueError("Only archive air-raid records may receive the 120s collapse")
    ordered = records.sort_values(["raion_id", "started_at"], kind="stable").reset_index(drop=True)
    clusters = np.empty(len(ordered), dtype=np.int64)
    cluster = 0
    for indices in ordered.groupby("raion_id", sort=False).indices.values():
        starts = ordered.started_at.iloc[indices].array.asi8
        begin = 0
        while begin < len(indices):
            stop = int(np.searchsorted(starts, starts[begin] + 120_000_000, side="right"))
            clusters[indices[begin:stop]] = cluster
            cluster += 1
            begin = stop
    ordered["_cluster"] = clusters
    grouped = ordered.groupby("_cluster", sort=False)
    result = grouped.head(1).set_index("_cluster")
    result["finished_at"] = grouped.finished_at.max()
    result.loc[ordered.finished_at.isna().groupby(clusters).any(), "finished_at"] = pd.NaT
    priorities = ordered.level.map({"hromada": 0, "raion": 1, "oblast": 2})
    if priorities.isna().any():
        raise ValueError("Archive collapse requires a reviewed source level")
    result["level"] = priorities.groupby(clusters).min().map(
        {0: "hromada", 1: "raion", 2: "oblast"}
    )
    colors = ordered.severity.map({"unknown": 0, "yellow": 1, "red": 2})
    result["severity"] = colors.groupby(clusters).max().map({0: "unknown", 1: "yellow", 2: "red"})
    result["severity_explicit"] = grouped.severity_explicit.any()
    result["source_updated_at"] = grouped.source_updated_at.max()
    return result.reset_index(drop=True)[records.columns]


def _matched_starts(
    csv: pd.DataFrame, archive: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, list[tuple[int, int]]]:
    """One-to-one sorted matching, same raion and absolute start difference <=120s."""
    columns = ["raion_id", "started_at"]
    left = csv.drop_duplicates(columns).sort_values(columns).reset_index(drop=True)
    right = archive.drop_duplicates(columns).sort_values(columns).reset_index(drop=True)
    matched_left: set[int] = set()
    matched_right: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for rid, group in left.groupby("raion_id", sort=False):
        other = right.loc[right.raion_id.eq(rid)]
        left_rows, right_rows = list(group.itertuples()), list(other.itertuples())
        i, j = 0, 0
        while i < len(left_rows) and j < len(right_rows):
            delta = (left_rows[i].started_at - right_rows[j].started_at).total_seconds()
            if abs(delta) <= 120:
                matched_left.add(left_rows[i].Index)
                matched_right.add(right_rows[j].Index)
                pairs.append((left_rows[i].Index, right_rows[j].Index))
                i, j = i + 1, j + 1
            elif delta < 0:
                i += 1
            else:
                j += 1
    return (
        left.drop(index=list(matched_left)), right.drop(index=list(matched_right)),
        pairs,
    )


def _gate_summary(days: list[dict[str, object]]) -> dict[str, object]:
    """Use count-weighted aggregate agreement and the lead's separate daily floor."""
    totals = {key: sum(int(row[key]) for row in days)
              for key in ("matched", "csv_starts", "archive_starts")}
    totals["archive_matched"] = sum(int(row.get("archive_matched", row["matched"])) for row in days)
    recall = totals["matched"] / totals["csv_starts"] if totals["csv_starts"] else 0.0
    precision = (totals["archive_matched"] / totals["archive_starts"]
                 if totals["archive_starts"] else 0.0)
    daily_passed = all(
        int(row["matched"]) >= .88 * int(row["csv_starts"])
        and int(row.get("archive_matched", row["matched"])) >= .88 * int(row["archive_starts"])
        for row in days
    )
    return {
        "passed": len(days) == 12 and recall >= .95 and precision >= .95 and daily_passed,
        "totals": {**totals, "recall": recall, "precision": precision},
    }


def validate_archive(days: list[date], *, root: Path | None = None) -> dict[str, object]:
    """Compare normalized episode starts on 12 CSV days, retaining boundary context."""
    if len(set(days)) != 12:
        raise ValueError("Archive validation requires twelve distinct complete CSV days")
    root = root or _live_dir()
    locations = _locations(root)
    dictionary = aiu_web.fetch_dictionary(root / "raw/aiu_web")
    csv = expand_records(load_source(), load_raions())
    source = load_source()
    csv_end = max(source.started_at.max(), source.finished_at.max())
    for day in days:
        if aiu_web.day_bounds(day)[1] > csv_end:
            raise ValueError("Validation day is not fully within CSV coverage")
    # Include intervening days too: an unresolved open record at the end of one
    # sampled block would otherwise absorb episodes in the next sampled block.
    # Only the requested 12 days contribute to the gate arithmetic.
    first, last = min(days) - timedelta(days=1), max(days) + timedelta(days=1)
    context_days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    frames = [event_records(aiu_web.decode(
        aiu_web.fetch_day(day, root / "raw/aiu_web"), dictionary
    ), locations) for day in context_days]
    frame = pd.concat(frames, ignore_index=True).sort_values("source_updated_at").drop_duplicates(
        ["raion_id", "source", "origin_event_id"], keep="last"
    )
    archive_records = normalize_archive_records(frame.loc[frame.hazard_type.eq("air_raid")])
    csv_history_episodes = merge_episodes(csv).rename(
        columns={"start": "started_at", "end": "finished_at"}
    )
    archive_episodes = merge_episodes(archive_records).rename(
        columns={"start": "started_at", "end": "finished_at"}
    )
    context_start, context_end = aiu_web.day_bounds(first)[0], aiu_web.day_bounds(last)[1]
    csv_episodes = csv_history_episodes.loc[
        csv_history_episodes.started_at.ge(context_start)
        & csv_history_episodes.started_at.lt(context_end)
    ].reset_index(drop=True)
    raw_csv_context = csv.loc[
        csv.started_at.ge(context_start) & csv.started_at.lt(context_end)
    ].drop_duplicates(["raion_id", "started_at"]).sort_values(
        ["raion_id", "started_at"]
    ).reset_index(drop=True)
    # Match before slicing days: a midnight boundary cannot invalidate an 80s
    # match. Recall is attributed by CSV date, precision by archive date.
    missing_episodes, extra_episodes, episode_pairs = _matched_starts(
        csv_episodes, archive_episodes
    )
    raw_missing_all, raw_extra_all, _ = _matched_starts(
        raw_csv_context, archive_records
    )
    results: list[dict[str, object]] = []
    raw_results: list[dict[str, object]] = []
    daily_counts: list[dict[str, object]] = []
    duration_differences: list[float] = []
    disagreements: list[dict[str, object]] = []
    for day in sorted(set(days)):
        start, end = aiu_web.day_bounds(day)
        raw_csv = csv.loc[csv.started_at.ge(start) & csv.started_at.lt(end)]
        raw_archive = archive_records.loc[
            archive_records.started_at.ge(start) & archive_records.started_at.lt(end)
        ]
        raw_missing = raw_missing_all.loc[
            raw_missing_all.started_at.ge(start) & raw_missing_all.started_at.lt(end)
        ]
        raw_extra = raw_extra_all.loc[
            raw_extra_all.started_at.ge(start) & raw_extra_all.started_at.lt(end)
        ]
        raw_matches = len(raw_csv.drop_duplicates(["raion_id", "started_at"])) - len(raw_missing)
        raw_archive_matches = len(raw_archive) - len(raw_extra)
        raw_results.append({
            "day": day.isoformat(), "matched": raw_matches,
            "archive_matched": raw_archive_matches,
            "csv_starts": raw_matches + len(raw_missing),
            "archive_starts": len(raw_archive),
            "recall": raw_matches / (raw_matches + len(raw_missing)),
            "precision": raw_archive_matches / len(raw_archive) if len(raw_archive) else 0.0,
        })
        counts = pd.DataFrame({
            "csv": raw_csv.drop_duplicates(["raion_id", "started_at"]).groupby("raion_id").size(),
            "archive": raw_archive.groupby("raion_id").size(),
        }).reindex(load_raions().raion_id).fillna(0).astype(int)
        for rid, values in counts.iterrows():
            daily_counts.append({
                "day": day.isoformat(), "raion_id": rid,
                "csv_starts": int(values.csv), "archive_starts": int(values.archive),
                "ratio": float(values.archive / values.csv) if values.csv else None,
            })
        archive = archive_episodes.loc[
            archive_episodes.started_at.ge(start) & archive_episodes.started_at.lt(end)
        ]
        expected = csv_episodes.loc[
            csv_episodes.started_at.ge(start) & csv_episodes.started_at.lt(end)
        ]
        missing = missing_episodes.loc[
            missing_episodes.started_at.ge(start) & missing_episodes.started_at.lt(end)
        ]
        extra = extra_episodes.loc[
            extra_episodes.started_at.ge(start) & extra_episodes.started_at.lt(end)
        ]
        pairs = [(left, right) for left, right in episode_pairs if left in expected.index]
        matches = len(pairs)
        for left, right in pairs:
            csv_row, archive_row = csv_episodes.iloc[left], archive_episodes.iloc[right]
            if pd.notna(csv_row.finished_at) and pd.notna(archive_row.finished_at):
                difference = ((archive_row.finished_at - archive_row.started_at)
                              - (csv_row.finished_at - csv_row.started_at)).total_seconds() / 60
                duration_differences.append(difference)
        if not matches + len(missing):
            raise ValueError("Validation day has no CSV starts to compare")
        recall = matches / (matches + len(missing))
        archive_matches = len(archive) - len(extra)
        precision = archive_matches / len(archive) if len(archive) else 0.0
        breakdown: list[dict[str, object]] = []
        for kind, unmatched in (("csv_only", missing), ("archive_only", extra)):
            by_oblast = Counter(str(rid).split("/")[0] for rid in unmatched.raion_id)
            breakdown.append({"kind": kind, "by_oblast": dict(by_oblast), "count": len(unmatched)})
            for row in unmatched.itertuples():
                own = expected if kind == "csv_only" else archive
                own_row = own.loc[
                    own.raion_id.eq(row.raion_id) & own.started_at.eq(row.started_at)
                ].iloc[0]
                # Compare against full context, including starts before midnight
                # and ongoing episodes whose start predates this validation day.
                context = archive_episodes if kind == "csv_only" else csv_history_episodes
                context = context.loc[context.raion_id.eq(row.raion_id)]
                distances = context.started_at.sub(row.started_at).abs()
                nearest = distances.min().total_seconds() if not distances.empty else None
                covering = context.loc[
                    context.started_at.le(row.started_at)
                    & (context.finished_at.isna() | context.finished_at.gt(row.started_at))
                ]
                disagreements.append({
                    "day": day.isoformat(), "kind": kind, "raion_id": row.raion_id,
                    "started_at": _iso(row.started_at.to_pydatetime()),
                    "source_level": str(own_row.source_level),
                    "nearest_start_seconds": nearest,
                    "counterpart_covering_start": (
                        _iso(covering.iloc[0].started_at.to_pydatetime())
                        if not covering.empty else None
                    ),
                    "counterpart_covering_censored": (
                        bool(covering.iloc[0].censored) if not covering.empty else None
                    ),
                })
        results.append({
            "day": day.isoformat(), "csv_starts": matches + len(missing),
            "archive_starts": len(archive), "matched": matches, "archive_matched": archive_matches,
            "recall": recall, "precision": precision, "disagreements": breakdown,
        })
        print(json.dumps({k: v for k, v in results[-1].items() if k != "disagreements"}))
    differences = np.asarray(duration_differences)
    ratios = [float(row["ratio"]) for row in daily_counts if row["ratio"] is not None]
    prior = _read_json(root / "validation.json")
    report = {
        **_gate_summary(results), "minimum_agreement": .95, "minimum_daily_agreement": .88,
        "metric": "episode_starts", "normalization_window_seconds": 120,
        "matching": "merged episode starts; one-to-one within raion; tolerance 120 seconds",
        "daily_attribution": "recall uses CSV start day; precision uses archive start day",
        "csv_sha256": _csv_sha256(),
        "context_days": [day.isoformat() for day in context_days],
        "raw_records_after_normalization": {
            "days": raw_results, "totals": _gate_summary(raw_results)["totals"],
            "matching": "unique raion record starts; one-to-one within 120 seconds",
        },
        "episode_duration_differences_min": {
            "matched_with_known_ends": len(differences),
            "matched_total": sum(int(r["matched"]) for r in results),
            "median_signed": float(np.median(differences)) if len(differences) else None,
            "median_absolute": float(np.median(np.abs(differences))) if len(differences) else None,
            "p90_absolute": (float(np.quantile(np.abs(differences), .9))
                             if len(differences) else None),
        },
        "per_raion_daily_record_starts": {
            "mean_ratio": float(np.mean(ratios)), "ratio_cells": len(ratios),
            "zero_csv_nonzero_archive_cells": sum(
                row["csv_starts"] == 0 and row["archive_starts"] > 0 for row in daily_counts
            ), "rows": daily_counts,
        },
        "round1_raw_totals": prior.get("round1_raw_totals", prior.get("totals")),
        "archive_context": {
            "unresolved_air_records": json.loads(frame.loc[
                frame.hazard_type.eq("air_raid") & frame.finished_at.isna(),
                ["raion_id", "origin_uid", "origin_event_id", "started_at", "source_updated_at"],
            ].to_json(orient="records", date_format="iso")),
            "first_explicit_air_severity_start": (
                _iso(frame.loc[
                    frame.hazard_type.eq("air_raid") & frame.severity_explicit, "started_at"
                ].min().to_pydatetime())
                if (frame.hazard_type.eq("air_raid") & frame.severity_explicit).any() else None
            ),
        },
        "days": results, "disagreements": disagreements,
    }
    atomic_json(root / "validation.json", report)
    return report


def _require_validation(root: Path) -> None:
    report = _read_json(root / "validation.json")
    if (report.get("passed") is not True or report.get("metric") != "episode_starts"
            or report.get("normalization_window_seconds") != 120
            or not _gate_summary(report.get("days", []))["passed"]):
        raise RuntimeError("Archive gate has not passed; inspect data/live/validation.json")
    if report.get("csv_sha256") != _csv_sha256():
        raise RuntimeError("CSV changed since archive validation; revalidate")


def _publish(ledger: dict[str, object], root: Path) -> None:
    """Build from durable day partitions. Metadata/ledger commit after staged data files."""
    cutover = pd.Timestamp(str(ledger["cutover"]))
    csv = expand_records(load_source(), load_raions())
    csv["severity"], csv["source"] = "unknown", "csv"
    partitions = [root / "normalized" / f"{day}.parquet"
                  for day in sorted(ledger.get("days", []))]
    incoming = pd.concat([pd.read_parquet(path) for path in partitions], ignore_index=True)
    incoming = incoming.sort_values("source_updated_at").drop_duplicates(
        ["raion_id", "source", "origin_event_id"], keep="last"
    )
    air = normalize_archive_records(incoming.loc[incoming.hazard_type.eq("air_raid")])
    # Preserve pre-cutover CSV starts; archive supplies real finishes for continuing overlap.
    retained = csv.loc[csv.started_at.lt(cutover)].copy()
    continuing = air.loc[
        air.started_at.lt(cutover) & (air.finished_at.isna() | air.finished_at.gt(cutover))
    ]
    for record in continuing.itertuples():
        mask = retained.raion_id.eq(record.raion_id) & retained.started_at.sub(
            record.started_at
        ).abs().le(pd.Timedelta(seconds=120)) & (
            retained.finished_at.isna() | retained.finished_at.gt(cutover)
        )
        retained.loc[mask, "finished_at"] = record.finished_at
    air = air.loc[air.started_at.ge(cutover)]
    records = pd.concat([retained, air[csv.columns]], ignore_index=True).sort_values(
        ["raion_id", "started_at"], kind="stable", ignore_index=True
    )
    hazards = incoming.loc[~incoming.hazard_type.eq("air_raid")].copy()
    hazard_path = PROCESSED_DIR / "hazards.parquet"
    if hazard_path.exists():
        live_hazards = pd.read_parquet(hazard_path)
        hazards = pd.concat([
            hazards, live_hazards.loc[live_hazards.source.isin(["siren", "aiu_api"])],
        ], ignore_index=True)
    # Daily scalar archive levels are observations, not exact transition times. Live
    # threat createdAt timestamps are kept separately and never become extra starts.
    transitions = pd.DataFrame(columns=[
        "raion_id", "alert_start", "at", "severity", "source", "origin_uid", "observed_at",
    ])
    for col in ("alert_start", "at", "observed_at"):
        transitions[col] = pd.to_datetime(transitions[col], utc=True).astype("datetime64[us, UTC]")
    transitions_path = PROCESSED_DIR / "severity_transitions.parquet"
    if transitions_path.exists():
        transitions = pd.read_parquet(transitions_path)
    metadata = _read_json(SNAPSHOT_PATH)
    end_day = date.fromisoformat(str(ledger["watermark"]))
    _, reconciled_end = aiu_web.day_bounds(end_day)
    records = records.loc[records.started_at.lt(reconciled_end)].copy()
    records.loc[records.finished_at.gt(reconciled_end), "finished_at"] = pd.NaT
    episodes = merge_episodes(records)
    metadata.update({
        "snapshot": _iso(reconciled_end), "sources": ["csv", "aiu_web"],
        "csv_sha256": _csv_sha256(),
        "cutover": _iso(cutover.to_pydatetime()),
        "coverage": {
            "reconciled_through": end_day.isoformat(), "gaps": ledger.get("gaps", []),
            "archive_gate_passed": True, "csv_authoritative_before": _iso(cutover.to_pydatetime()),
            "archive_through_exclusive": _iso(reconciled_end),
        },
    })
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root, prefix="publish-") as directory:
        stage = Path(directory)
        artifacts = {
            RECORDS_PATH: records, EPISODES_PATH: episodes,
            PROCESSED_DIR / "hazards.parquet": hazards, transitions_path: transitions,
        }
        for path, frame in artifacts.items():
            frame.to_parquet(stage / path.name, index=False)
        publications = list(artifacts) + [SNAPSHOT_PATH]
        for path in publications:
            if path.exists():
                shutil.copyfile(path, stage / f"{path.name}.old")
        replaced: list[Path] = []
        try:
            for path in artifacts:
                (stage / path.name).replace(path)
                replaced.append(path)
            atomic_json(SNAPSHOT_PATH, metadata)
            replaced.append(SNAPSHOT_PATH)
            atomic_json(root / "ledger.json", ledger)
        except BaseException:
            for path in reversed(replaced):
                backup = stage / f"{path.name}.old"
                if backup.exists():
                    backup.replace(path)
                else:
                    path.unlink(missing_ok=True)
            raise


def rebuild(*, root: Path | None = None) -> None:
    """Preserve the CSV cutoff and all durable v2 partitions on an episodes rebuild."""
    root = root or _live_dir()
    _require_validation(root)
    ledger = _read_json(root / "ledger.json")
    if not ledger.get("days"):
        raise RuntimeError("No reconciled archive partitions to rebuild")
    _publish(ledger, root)


def reconcile_days(
    days: list[date], *, root: Path | None = None, now: datetime | None = None,
) -> dict[str, object]:
    """Upsert complete Kyiv-day partitions; repeated reconciliation reuses raw cache."""
    root = root or _live_dir()
    _require_validation(root)
    locations, dictionary = _locations(root), aiu_web.fetch_dictionary(root / "raw/aiu_web")
    ledger = _read_json(root / "ledger.json")
    if "cutover" not in ledger:
        raise RuntimeError("Lead must choose a validated cutover before reconciliation")
    for day in sorted(set(days)):
        if aiu_web.day_bounds(day)[1] > (now or datetime.now(UTC)):
            raise ValueError("Only complete Kyiv days may be reconciled")
        watermark_day = date.fromisoformat(str(ledger["watermark"]))
        if day > watermark_day + timedelta(days=1):
            raise ValueError("Reconcile missing days in order; do not skip coverage")
        previous = dict(ledger)
        try:
            events = aiu_web.decode(aiu_web.fetch_day(day, root / "raw/aiu_web"), dictionary)
            frame = event_records(events, locations)
            partition = root / "normalized" / f"{day.isoformat()}.parquet"
            partition.parent.mkdir(parents=True, exist_ok=True)
            staging = partition.with_suffix(".part")
            frame.to_parquet(staging, index=False)
            staging.replace(partition)
            reconciled = sorted(set(list(ledger.get("days", [])) + [day.isoformat()]))
            ledger["days"] = reconciled
            first = pd.Timestamp(str(ledger["cutover"])).tz_convert(KYIV_TZ).date()
            watermark = first - timedelta(days=1)
            while (watermark + timedelta(days=1)).isoformat() in reconciled:
                watermark += timedelta(days=1)
            ledger["watermark"] = watermark.isoformat()
            ledger["gaps"] = [
                gap for gap in list(ledger.get("gaps", [])) if gap["day"] != day.isoformat()
            ]
            _publish(ledger, root)
        except Exception as error:
            ledger = previous
            gaps = [gap for gap in list(ledger.get("gaps", [])) if gap["day"] != day.isoformat()]
            start, end = aiu_web.day_bounds(day)
            gaps.append({"day": day.isoformat(), "start": _iso(start), "end": _iso(end),
                         "error_type": type(error).__name__})
            ledger["gaps"] = gaps
            atomic_json(root / "ledger.json", ledger)
            raise
    return ledger


def catch_up(
    now: datetime, *, root: Path | None = None, cutover: datetime | None = None,
) -> dict[str, object]:
    """Resume every complete Kyiv day in order; the ledger only advances after publish."""
    if now.tzinfo is None:
        raise ValueError("catch_up requires an offset-aware time")
    root = root or _live_dir()
    _require_validation(root)
    ledger = _read_json(root / "ledger.json")
    if cutover is not None:
        if cutover.tzinfo is None:
            raise ValueError("cutover requires an offset-aware time")
        if ledger.get("cutover") and pd.Timestamp(str(ledger["cutover"])) != pd.Timestamp(cutover):
            raise ValueError("Existing ledger has a different cutover; preserve it for review")
    if "watermark" not in ledger:
        # Repair the configured outage first; otherwise overlap the last partial
        # CSV calendar day. The gate must pass before even initializing the ledger.
        if cutover is None:
            if DATA_GAPS:
                cutover = min(start for start, _ in DATA_GAPS)
            else:
                source = load_source()
                last = max(source.started_at.max(), source.finished_at.max())
                cutover = aiu_web.day_bounds(last.tz_convert(KYIV_TZ).date())[0]
        first = cutover.astimezone(ZoneInfo(KYIV_TZ)).date()
        ledger = {"cutover": _iso(cutover), "watermark": (first - timedelta(days=1)).isoformat(),
                  "days": [], "gaps": []}
        atomic_json(root / "ledger.json", ledger)
    day = date.fromisoformat(str(ledger["watermark"])) + timedelta(days=1)
    yesterday = now.astimezone(ZoneInfo(KYIV_TZ)).date() - timedelta(days=1)
    while day <= yesterday:
        ledger = reconcile_days([day], root=root, now=now)
        print(f"Archive reconciled through {day.isoformat()}", flush=True)
        day += timedelta(days=1)
    return ledger


def polygon_state(
    events: list[Event], locations: Locations, crosswalk: pd.DataFrame, now: datetime,
) -> list[PolygonState]:
    """Aggregate both severity layers at the display unit, including excluded oblasts."""
    result: list[PolygonState] = []
    origins: dict[tuple[str, str, datetime], Event] = {}
    for event in events:
        _location(event, locations)  # Unknown geography fails the poll, never silently disappears.
        key = event.origin_uid, event.hazard, event.started_at
        existing = origins.get(key)
        if existing:
            event = replace(event, levels=tuple(set(existing.levels) | set(event.levels)))
        origins[key] = event
    for row in crosswalk.fillna("").itertuples(index=False):
        rid = row.raion_id or None
        applicable: list[tuple[Event, str]] = []
        for event in origins.values():
            loc = _location(event, locations)
            if rid and rid in loc.raion_ids or not rid and (
                loc.level == "oblast" and loc.oblast_id == row.oblast_id
            ):
                applicable.append((event, loc.level))
        coverage = "observed" if rid else "unknown"
        if applicable and (not rid or any(
            level == "oblast" and _location(event, locations).oblast_id != "kyiv-city"
            for event, level in applicable
        )):
            coverage = "inherited"
        layer: dict[str, Layer | None] = {"red": None, "yellow": None}
        for color in layer:
            starts = [level.since for event, _ in applicable if event.hazard == "air_raid"
                      for level in event.levels if level.severity == color]
            if starts:
                layer[color] = {"active": True, "since": _iso(min(starts))}
            elif coverage != "unknown":
                layer[color] = {"active": False, "since": None}
        other = [
            {"type": event.hazard, "since": _iso(event.started_at), "source": event.source}
            for event, _ in applicable if event.hazard != "air_raid"
        ]
        result.append({
            "display_key": str(row.shape_id), "raion_id": rid, "oblast_id": row.oblast_id,
            "red": layer["red"], "yellow": layer["yellow"], "other_hazards": other,
            "coverage": coverage, "stale": False, "fetched_at": _iso(now),
        })
    return result


def _save_level_observations(events: list[Event], locations: Locations, now: datetime) -> None:
    """Persist exact level-created times, never backdate a current scalar API color.

The siren live schema omits original AIR onset. Its alert_start stays null until
an exact-onset source resolves it; these rows must not become onset labels.
"""
    rows: list[dict[str, object]] = []
    for event in events:
        if event.hazard != "air_raid":
            continue
        location = _location(event, locations)
        for level in event.levels:
            if not level.time_known:
                continue
            for rid in location.raion_ids:
                rows.append({
                    "raion_id": rid,
                    "alert_start": event.started_at if event.start_is_exact else None,
                    "at": level.since, "severity": level.severity, "source": event.source,
                    "origin_uid": location.uid, "observed_at": now,
                })
    path = PROCESSED_DIR / "severity_transitions.parquet"
    frame = pd.DataFrame(rows, columns=[
        "raion_id", "alert_start", "at", "severity", "source", "origin_uid", "observed_at",
    ])
    for column in ("alert_start", "at", "observed_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True).astype("datetime64[us, UTC]")
    if path.exists():
        frame = pd.concat([pd.read_parquet(path), frame], ignore_index=True)
    frame = frame.drop_duplicates(
        ["raion_id", "source", "origin_uid", "at", "severity"], keep="first"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".part")
    frame.to_parquet(staging, index=False)
    staging.replace(path)


def _save_hazards(observed: pd.DataFrame) -> None:
    """Keep non-air hazards outside records; publish an empty typed table when absent."""
    path = PROCESSED_DIR / "hazards.parquet"
    hazards = observed.loc[~observed.hazard_type.eq("air_raid")].copy()
    if path.exists():
        hazards = pd.concat([pd.read_parquet(path), hazards], ignore_index=True)
    hazards = hazards.sort_values("source_updated_at").drop_duplicates(
        ["raion_id", "source", "origin_uid", "hazard_type", "started_at"], keep="last"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".part")
    hazards.to_parquet(staging, index=False)
    staging.replace(path)


def live_state(now: datetime, *, root: Path | None = None) -> dict[str, object]:
    """Poll once; failure keeps every prior layer and its successful observation time."""
    if now.tzinfo is None:
        raise ValueError("live_state requires an offset-aware time")
    root = root or _live_dir()
    path = root / "state.json"
    previous = _read_json(path)
    try:
        locations = _locations(root)
        payload, raw = siren.fetch_alerts()
        events = siren.decode(payload)
        permanent_origins = {event.origin_uid for event in events if event.hazard == "air_raid"}
        if not {"16", "9999"}.issubset(permanent_origins):
            raise ValueError("Live snapshot lacks known permanent oblast alerts")
        cache_response(root / "raw/siren" / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.json.gz", raw)
        secondary = "disabled"
        token = aiu_api.token_from_env(REPO_ROOT / ".env")
        if token:
            try:
                api_payload, api_raw = aiu_api.fetch_alerts(token)
                events += aiu_api.decode(api_payload)
                cache_response(root / "raw/aiu_api" / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.json.gz",
                               api_raw)
                secondary = "observed"
            except Exception:
                secondary = "failed"
        received_at = max(now.astimezone(UTC), datetime.now(UTC))
        polygons = polygon_state(
            events, locations, pd.read_csv(CROSSWALK_PATH, dtype=str), received_at
        )
        for polygon in polygons:
            if polygon["oblast_id"] == "sevastopol-city":
                polygon.update({"coverage": "unknown", "red": None, "yellow": None})
        sevastopol_status = "unknown"
        try:
            fallback_payload, fallback_raw = ubilling.fetch_alerts()
            since = ubilling.sevastopol_since(fallback_payload)
            cache_response(root / "raw/ubilling" / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.json.gz",
                           fallback_raw)
            if since is not None:
                sevastopol_status = "reported_permanent_alert"
                for polygon in polygons:
                    if polygon["oblast_id"] == "sevastopol-city":
                        polygon.update({"coverage": "inherited",
                                        "red": {"active": True, "since": _iso(since)},
                                        "yellow": None})
        except Exception:
            sevastopol_status = "failed"
        state: dict[str, object] = {
            "fetched_at": _iso(received_at), "attempted_at": _iso(now), "stale": False,
            "sources": {"siren": "observed", "aiu_api": secondary,
                        "ubilling": sevastopol_status}, "polygons": polygons,
        }
        # Keep origin observations for an availability-aware online feature consumer.
        observed = event_records(events, locations)
        observed["observed_at"] = pd.Timestamp(received_at).as_unit("us")
        observations = root / "observations" / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.parquet"
        observations.parent.mkdir(parents=True, exist_ok=True)
        observed.to_parquet(observations, index=False)
        _save_level_observations(events, locations, received_at)
        _save_hazards(observed)
    except Exception as error:
        if previous:
            state = previous
            for polygon in state["polygons"]:
                polygon["stale"] = True
        else:
            polygons = [{
                "display_key": str(row.shape_id), "raion_id": row.raion_id or None,
                "oblast_id": row.oblast_id, "red": None, "yellow": None,
                "other_hazards": [], "coverage": "unknown", "stale": True, "fetched_at": None,
            } for row in pd.read_csv(CROSSWALK_PATH, dtype=str).fillna("").itertuples(index=False)]
            state = {"fetched_at": None, "polygons": polygons}
        state.update({"attempted_at": _iso(now), "stale": True, "error_type": type(error).__name__})
    atomic_json(path, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--days", nargs="+", type=date.fromisoformat, required=True)
    backfill = commands.add_parser("backfill")
    backfill.add_argument("--cutover", type=datetime.fromisoformat)
    backfill.add_argument("--now", type=datetime.fromisoformat)
    commands.add_parser("live-once")
    args = parser.parse_args()
    if args.command == "validate":
        report = validate_archive(args.days)
        print(json.dumps({"archive_gate_passed": report["passed"]}))
    elif args.command == "backfill":
        root = _live_dir()
        _require_validation(root)
        if args.cutover and args.cutover.tzinfo is None:
            parser.error("cutover requires an offset")
        print(json.dumps(catch_up(args.now or datetime.now(UTC), cutover=args.cutover)))
    else:
        state = live_state(datetime.now(UTC))
        polygons = state["polygons"]
        print(json.dumps({
            "fetched_at": state["fetched_at"], "stale": state["stale"],
            "polygons": len(polygons),
            "red": sum(bool(row["red"] and row["red"]["active"]) for row in polygons),
            "yellow": sum(bool(row["yellow"] and row["yellow"]["active"]) for row in polygons),
            "coverage": dict(Counter(row["coverage"] for row in polygons)),
            "error_type": state.get("error_type"),
        }))


if __name__ == "__main__":
    main()
