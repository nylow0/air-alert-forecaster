"""Expand source coverage into as-of records and merge episodes for targets only."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx
import numpy as np
import pandas as pd

from airalert.config import (
    EPISODES_PATH,
    EXCLUDED_OBLASTS,
    MERGE_GAP_MIN,
    OFFICIAL_CSV_URL,
    PROCESSED_DIR,
    RAIONS_PATH,
    RAW_CSV_TEMPLATE,
    RAW_DIR,
    RECORDS_PATH,
    SNAPSHOT_PATH,
    SPECIAL_STATUS_OBLASTS,
)
from airalert.ids import oblast_id as make_oblast_id
from airalert.ids import raion_id as make_raion_id


@dataclass(frozen=True)
class Snapshot:
    """CSV provenance and UTC timestamps; regime_switch is midnight on its date."""

    snapshot: pd.Timestamp
    csv_sha256: str
    regime_switch: pd.Timestamp


def fetch() -> Path:
    """Reuse today's UTC copy and verify its hash; publish downloads only when complete."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / RAW_CSV_TEMPLATE.format(date=datetime.now(UTC).date().isoformat())
    checksum_path = path.with_suffix(".csv.sha256")
    reused = path.exists()
    if not reused:
        temporary_path = path.with_suffix(".csv.part")
        try:
            with httpx.stream(
                "GET", OFFICIAL_CSV_URL, follow_redirects=True, timeout=120
            ) as response:
                response.raise_for_status()
                with temporary_path.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if checksum_path.exists():
        expected = checksum_path.read_text(encoding="ascii").split()[0]
        if expected != digest:
            raise ValueError(f"SHA256 mismatch for {path}; preserve the copy for inspection")
    else:
        checksum_path.write_text(f"{digest}  {path.name}\n", encoding="ascii")
    print(f"{'Reused' if reused else 'Saved'} {path.relative_to(RAW_DIR.parent.parent)}")
    print(f"SHA256 {digest}")
    return path


def _utc(values: pd.Series) -> pd.Series:
    """Normalize timestamps to the microsecond UTC dtype used by every artifact."""
    return pd.to_datetime(values, utc=True, format="mixed").astype("datetime64[us, UTC]")


def _prepare_source(source: pd.DataFrame) -> pd.DataFrame:
    """Validate raw CSV fields without deduplicating or changing source coverage."""
    required = {"oblast", "raion", "level", "started_at", "finished_at"}
    if missing := required.difference(source.columns):
        raise ValueError(f"Missing source columns: {sorted(missing)}")
    result = source.copy()
    if result.oblast.isna().any() or not result.level.isin(["oblast", "raion", "hromada"]).all():
        raise ValueError("Every source record needs an oblast and a supported level")
    result["raion"] = result.raion.replace(r"^\s*$", pd.NA, regex=True)
    for column in ("started_at", "finished_at"):
        result[column] = _utc(result[column].replace("", pd.NA))
    if result.started_at.isna().any():
        raise ValueError("Source records cannot have missing start times")
    if result.finished_at.lt(result.started_at).any():
        raise ValueError("Negative source durations must be corrected before building")
    # Identifier functions run once per geographic name, never once per source record.
    names = {name: make_oblast_id(name) for name in result.oblast.unique()}
    result["oblast_id"] = result.oblast.map(names)
    return result


def load_source(path: Path | None = None) -> pd.DataFrame:
    """Read the newest dated CSV with UTC microsecond times and intact source rows."""
    if path is None:
        candidates = sorted(RAW_DIR.glob("official_data_en_*.csv"))
        if not candidates:
            raise FileNotFoundError("No dated official CSV; run uv run airalert fetch first")
        path = candidates[-1]
    return _prepare_source(pd.read_csv(path, dtype="str"))


def build_raions(source: pd.DataFrame) -> pd.DataFrame:
    """Use all dated raion/hromada names and special cities, independent of activity."""
    raw = _prepare_source(source)
    raw = raw.loc[~raw.oblast_id.isin(EXCLUDED_OBLASTS)]
    detailed = raw.loc[raw.level.isin(["raion", "hromada"])]
    invalid = detailed.raion.isna() & ~detailed.oblast_id.isin(SPECIAL_STATUS_OBLASTS)
    if invalid.any():
        raise ValueError("Raion/hromada source records must name their parent raion")
    names = detailed.loc[detailed.raion.notna(), ["oblast", "oblast_id", "raion"]]
    names = names.drop_duplicates().rename(columns={"oblast": "oblast_name", "raion": "raion_name"})
    names["raion_id"] = [
        make_raion_id(oblast, raion)
        for oblast, raion in names[["oblast_name", "raion_name"]].itertuples(index=False, name=None)
    ]
    cities = raw.loc[
        raw.oblast_id.isin(SPECIAL_STATUS_OBLASTS) & ~raw.oblast_id.isin(names.oblast_id),
        ["oblast", "oblast_id"],
    ].drop_duplicates()
    cities = cities.rename(columns={"oblast": "oblast_name"})
    cities["raion_name"] = cities.oblast_name
    cities["raion_id"] = cities.oblast_name.map(lambda name: make_raion_id(name, None))
    result = pd.concat([names, cities], ignore_index=True)
    if result.raion_id.duplicated().any():
        raise ValueError("Different dataset names collapsed to the same canonical raion ID")
    return result[["raion_id", "oblast_id", "oblast_name", "raion_name"]].sort_values(
        "raion_id", ignore_index=True
    )


def expand_records(source: pd.DataFrame, raions: pd.DataFrame) -> pd.DataFrame:
    """Expand oblasts by universe membership and map hromadas to their named parent.

    Keeps duplicates and null ends. Returns raion/start-sorted raw records with UTC
    microsecond timestamps. These records, masked as-of t, are the feature input.
    """
    raw = _prepare_source(source)
    raw = raw.loc[~raw.oblast_id.isin(EXCLUDED_OBLASTS)]
    universe = raions.loc[~raions.oblast_id.isin(EXCLUDED_OBLASTS)]
    if universe.raion_id.duplicated().any():
        raise ValueError("The raion universe must contain unique IDs")
    uncovered = set(raw.oblast_id).difference(universe.oblast_id)
    if uncovered:
        raise ValueError(f"Source oblasts absent from the universe: {sorted(uncovered)}")
    columns = ["raion_id", "oblast_id", "level", "started_at", "finished_at"]
    oblasts = raw.loc[raw.level.eq("oblast")].merge(
        universe[["raion_id", "oblast_id"]], on="oblast_id", how="left", validate="many_to_many"
    )
    districts = raw.loc[~raw.level.eq("oblast")].copy()
    lookup = universe[["oblast_id", "raion_name", "raion_id"]].rename(
        columns={"raion_name": "raion"}
    )
    districts = districts.merge(
        lookup, on=["oblast_id", "raion"], how="left", validate="many_to_one"
    )
    # A special city may be named as a raion-level record without a raion column.
    city_mask = districts.raion.isna() & districts.oblast_id.isin(SPECIAL_STATUS_OBLASTS)
    city_ids = universe.set_index("oblast_id").loc[
        lambda frame: frame.index.isin(SPECIAL_STATUS_OBLASTS), "raion_id"
    ]
    if not city_ids.index.is_unique:
        raise ValueError("Special-status cities must each be a single universe unit")
    districts.loc[city_mask, "raion_id"] = districts.loc[city_mask, "oblast_id"].map(city_ids)
    if districts.raion_id.isna().any():
        raise ValueError("A source raion or hromada parent is absent from the universe")
    result = pd.concat([oblasts[columns], districts[columns]], ignore_index=True)
    return result.sort_values(["raion_id", "started_at"], kind="stable", ignore_index=True)


def merge_episodes(records: pd.DataFrame) -> pd.DataFrame:
    """Merge coverage using each raion's running maximum end and a strict gap.

    Equal earliest starts prefer hromada, then raion, then oblast. Any null end
    makes its episode censored and absorbs all later records in that raion.
    Never use this full-data union as an as-of feature input: merge gaps leak.
    """
    ordered = records.copy()
    for column in ("started_at", "finished_at"):
        ordered[column] = _utc(ordered[column])
    if ordered.started_at.isna().any() or ordered.finished_at.lt(ordered.started_at).any():
        raise ValueError("Episode records require starts and nonnegative durations")
    if not ordered.level.isin(["oblast", "raion", "hromada"]).all():
        raise ValueError("Unknown source level")
    if ordered.raion_id.isna().any() or ordered.oblast_id.isna().any():
        raise ValueError("Episode records require geographic IDs")
    if ordered.groupby("raion_id").oblast_id.nunique().gt(1).any():
        raise ValueError("A raion ID cannot belong to multiple oblasts")
    ordered["_priority"] = ordered.level.map({"hromada": 0, "raion": 1, "oblast": 2})
    ordered = ordered.sort_values(
        ["raion_id", "started_at", "_priority"], kind="stable", ignore_index=True
    )
    ordered["_censored"] = ordered.finished_at.isna()
    # Use integer microseconds so a shifted sentinel cannot round subsecond gaps.
    ends = pd.Series(ordered.finished_at.array.asi8, index=ordered.index)
    ends = ends.mask(ordered._censored, np.iinfo(np.int64).max)
    running_end = ends.groupby(ordered.raion_id, sort=False).cummax()
    starts = pd.Series(ordered.started_at.array.asi8, index=ordered.index)
    previous_end = running_end.groupby(ordered.raion_id, sort=False).shift(fill_value=0)
    gap_us = MERGE_GAP_MIN * 60 * 1_000_000
    new_episode = ordered.raion_id.ne(ordered.raion_id.shift()) | (
        starts.sub(previous_end).ge(gap_us)
    )
    ordered["_episode"] = new_episode.cumsum()
    result = ordered.groupby("_episode", sort=False).agg(
        raion_id=("raion_id", "first"),
        oblast_id=("oblast_id", "first"),
        start=("started_at", "first"),
        end=("finished_at", "max"),
        source_level=("level", "first"),
        censored=("_censored", "any"),
    ).reset_index(drop=True)
    result.loc[result.censored, "end"] = pd.NaT
    return result


def daily_level_counts(source: pd.DataFrame) -> pd.DataFrame:
    """Count unexpanded source starts per UTC day, including empty intervening days."""
    starts = _utc(source.started_at)
    if starts.empty:
        raise ValueError("Cannot detect a regime in an empty source")
    days = pd.date_range(starts.min().floor("D"), starts.max().floor("D"), freq="D")
    return pd.crosstab(starts.dt.floor("D"), source.level).reindex(
        index=days, columns=["oblast", "raion", "hromada"], fill_value=0
    )


def detect_regime_switch(source: pd.DataFrame) -> pd.Timestamp:
    """First UTC day whose trailing 14-day raion share stays >50% to the snapshot.

    Uses source-row counts, before oblast expansion. All 14 days are required;
    a zero-record window fails the rule. This is retrospective regime metadata.
    """
    rolling = daily_level_counts(source).rolling(14, min_periods=14).sum()
    majority = rolling.raion.div(rolling.sum(axis=1)).gt(0.5)
    sustained = majority.iloc[::-1].cummin().iloc[::-1]
    candidates = sustained.index[sustained]
    if candidates.empty:
        raise ValueError("No sustained 14-day raion majority found in this CSV")
    return candidates[0].as_unit("us")


def build_episodes() -> None:
    """Rebuild A1 artifacts from the newest dated CSV, never from prior outputs."""
    began = perf_counter()
    candidates = sorted(RAW_DIR.glob("official_data_en_*.csv"))
    if not candidates:
        raise FileNotFoundError("No dated official CSV; run uv run airalert fetch first")
    path = candidates[-1]
    with path.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    checksum_path = path.with_suffix(".csv.sha256")
    if checksum_path.exists() and checksum_path.read_text(encoding="ascii").split()[0] != digest:
        raise ValueError(f"SHA256 mismatch for {path}; preserve the copy for inspection")
    source = load_source(path)
    loaded = perf_counter()
    raions = build_raions(source)
    assert len(raions) == 119, (
        f"Expected 119 units, found {len(raions)}. Investigate CSV geography changes; "
        "never filter the universe by recent activity."
    )
    records = expand_records(source, raions)
    expanded = perf_counter()
    episodes = merge_episodes(records)
    merged = perf_counter()
    snapshot = max(source.started_at.max(), source.finished_at.max()).floor("h")
    switch = detect_regime_switch(source)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    raions.to_csv(RAIONS_PATH, index=False)
    records.to_parquet(RECORDS_PATH, index=False)
    episodes.to_parquet(EPISODES_PATH, index=False)
    SNAPSHOT_PATH.write_text(
        json.dumps({
            "snapshot": snapshot.isoformat().replace("+00:00", "Z"),
            "csv_sha256": digest,
            "regime_switch": switch.date().isoformat(),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    ended = perf_counter()
    print(f"Source {path.name}: {len(source):,} rows; SHA256 {digest}")
    print(f"Universe: {len(raions):,} units; excluded source rows: "
          f"{source.oblast_id.isin(EXCLUDED_OBLASTS).sum():,}")
    print(f"Records: {len(records):,}; episodes: {len(episodes):,}; "
          f"censored: {episodes.censored.sum():,}")
    print(f"Snapshot: {snapshot.isoformat()}; regime switch: {switch.date().isoformat()}")
    print(f"Timings: load {loaded - began:.2f}s; expand {expanded - loaded:.2f}s; "
          f"merge {merged - expanded:.2f}s; write {ended - merged:.2f}s; "
          f"total {ended - began:.2f}s")


def load_records() -> pd.DataFrame:
    """Raw coverage sorted by raion/start; UTC us starts, nullable UTC us ends.

    Source duplicates remain. Before computing features at t, retain starts < t
    and mask ends > t to null. No merge-gap closure is applied to these records.
    """
    result = pd.read_parquet(RECORDS_PATH)
    for column in ("started_at", "finished_at"):
        result[column] = _utc(result[column])
    return result.sort_values(["raion_id", "started_at"], kind="stable", ignore_index=True)


def load_episodes() -> pd.DataFrame:
    """Target episodes sorted by raion/start; UTC us times, null end iff censored.

    source_level breaks equal-start ties toward the finer level. Only labels
    and duration targets may use these merged intervals, never as-of features.
    """
    result = pd.read_parquet(EPISODES_PATH)
    for column in ("start", "end"):
        result[column] = _utc(result[column])
    return result.sort_values(["raion_id", "start"], kind="stable", ignore_index=True)


def load_raions() -> pd.DataFrame:
    """Fixed universe sorted by raion_id; four non-null string columns, unique IDs."""
    return pd.read_csv(RAIONS_PATH, dtype="str").sort_values("raion_id", ignore_index=True)


def load_snapshot() -> Snapshot:
    """Return provenance, the UTC snapshot hour, and the UTC regime-switch midnight."""
    metadata = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return Snapshot(
        snapshot=pd.Timestamp(metadata["snapshot"]).as_unit("us"),
        csv_sha256=metadata["csv_sha256"],
        regime_switch=pd.Timestamp(metadata["regime_switch"], tz="UTC").as_unit("us"),
    )
